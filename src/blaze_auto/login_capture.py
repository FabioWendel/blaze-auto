"""Captura somente dados da própria conta em respostas autenticadas da Blaze."""
from __future__ import annotations

import io
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv.parser import parse_stream


REQUIRED_KEYS = ("BLAZE_AUTHORIZATION", "BLAZE_WALLET_ID", "BLAZE_USERNAME", "BLAZE_RANK")
OPTIONAL_KEYS = ("BLAZE_SESSION_ID", "BLAZE_CLIENT_VERSION")
API_HOSTS = frozenset({"blaze.bet.br", "www.blaze.bet.br", "api.blaze.bet.br"})
BOOTSTRAP_PATH = "/api/bootstrap/me"
PROFILE_PATHS = frozenset({BOOTSTRAP_PATH, "/api/users/me", "/api/users/me/profile", "/api/user/me", "/api/profile"})
WALLET_PATHS = frozenset({"/api/wallets", "/api/users/me/wallets", "/api/wallets/me"})


def api_path(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in API_HOSTS
                or parsed.port not in (None, 443) or parsed.username or parsed.password
                or not parsed.path.startswith("/api/")):
            return ""
        return parsed.path.rstrip("/")
    except ValueError:
        return ""


def safe_text(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 4096 or "${" in value:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ""
    return value.strip()


def positive_id(value: Any) -> str:
    text = str(value)
    return text if re.fullmatch(r"[1-9][0-9]*", text) else ""


def profile_object(body: Any, depth: int = 0) -> dict[str, Any]:
    if not isinstance(body, dict) or depth > 3:
        return {}
    if safe_text(body.get("username")):
        return body
    for key in ("user", "profile", "data", "result"):
        found = profile_object(body.get(key), depth + 1)
        if found:
            return found
    return {}


def brl_wallets(body: Any, depth: int = 0) -> set[str]:
    if depth > 4:
        return set()
    if isinstance(body, list):
        return set().union(*(brl_wallets(item, depth + 1) for item in body))
    if not isinstance(body, dict):
        return set()
    currency = body.get("currency", body.get("type"))
    if isinstance(currency, dict):
        # O site usa wallet.currency.type; name pode ser o nome por extenso.
        currency = currency.get("type", currency.get("code", currency.get("ticker", currency.get("name"))))
    wallet_id = positive_id(body.get("id", body.get("wallet_id")))
    found = {wallet_id} if safe_text(currency).upper() == "BRL" and wallet_id else set()
    for key in ("wallets", "wallet", "data", "items", "result"):
        found.update(brl_wallets(body.get(key), depth + 1))
    return found


class SessionCapture:
    def __init__(self, wallet_id: str = "") -> None:
        self._values: dict[str, str] = {}
        self._wallet_ids: set[str] = set()
        self._preferred_wallet_id = wallet_id

    def observe(self, url: str, status: int, method: str,
                headers: dict[str, str], body: Any = None) -> None:
        path = api_path(url)
        if not path or not 200 <= status < 300:
            return
        headers = {key.lower(): value for key, value in headers.items()}
        auth = safe_text(headers.get("authorization"))
        if not re.fullmatch(r"Bearer [A-Za-z0-9._~+/=-]+", auth, flags=re.IGNORECASE):
            return
        auth = "Bearer " + auth.split(" ", 1)[1]
        if self._values.get("BLAZE_AUTHORIZATION") != auth:
            # Nunca combina a carteira/perfil de uma sessão com outro token.
            self._values.clear()
            self._wallet_ids.clear()
        self._values["BLAZE_AUTHORIZATION"] = auth
        for header, key in (("x-session-id", "BLAZE_SESSION_ID"), ("x-client-version", "BLAZE_CLIENT_VERSION")):
            value = safe_text(headers.get(header))
            if value:
                self._values[key] = value
        if method != "GET":
            return  # Não lê corpo de login/senha nem depende de enviar aposta.
        if path in PROFILE_PATHS:
            profile = profile_object(body)
            username = safe_text(profile.get("username"))
            if username:
                self._values["BLAZE_USERNAME"] = username
            rank = profile.get("rank")
            if rank is None and isinstance(profile.get("xp"), dict):
                rank = profile["xp"].get("rank")
            if isinstance(rank, dict):
                rank = rank.get("name")
            rank_text = safe_text(rank)
            if rank_text:
                self._values["BLAZE_RANK"] = rank_text
            for key in ("wallets", "wallet"):
                self._wallet_ids.update(brl_wallets(profile.get(key)))
            if path == BOOTSTRAP_PATH:
                # No bootstrap as carteiras são irmãs de user, não filhas dele.
                # brl_wallets percorre apenas os contêineres conhecidos e não
                # coleta IDs de transações/notificações presentes na resposta.
                self._wallet_ids = brl_wallets(body)
        elif path in WALLET_PATHS:
            self._wallet_ids = brl_wallets(body)
        self._values.pop("BLAZE_WALLET_ID", None)
        if self._preferred_wallet_id and self._preferred_wallet_id in self._wallet_ids:
            self._values["BLAZE_WALLET_ID"] = self._preferred_wallet_id
        elif not self._preferred_wallet_id and len(self._wallet_ids) == 1:
            self._values["BLAZE_WALLET_ID"] = next(iter(self._wallet_ids))

    def missing(self) -> list[str]:
        return [key for key in REQUIRED_KEYS if not self._values.get(key)]

    def env_values(self) -> dict[str, str]:
        if self.missing():
            raise ValueError("captura incompleta; .env não foi alterado")
        # Limpa opcionais antigos se a nova sessão não os fornecer.
        return {key: self._values.get(key, "") for key in (*REQUIRED_KEYS, *OPTIONAL_KEYS)}


def observe_response(capture: SessionCapture, response: Any) -> None:
    """Callback sem logs de URL, headers, payload, senha ou exceções brutas."""
    try:
        path = api_path(response.url)
        if not path or not 200 <= response.status < 300:
            return
        request = response.request
        headers = request.all_headers()
        body = None
        if request.method == "GET" and path in PROFILE_PATHS | WALLET_PATHS:
            body = response.json()
        capture.observe(response.url, response.status, request.method, headers, body)
    except Exception:
        # Uma resposta incompleta/HTML não autoriza preencher dados por suposição.
        return


def merge_env(original: str, values: dict[str, str]) -> str:
    if (set(values) - set((*REQUIRED_KEYS, *OPTIONAL_KEYS))
            or any(not values.get(key) for key in REQUIRED_KEYS)
            or any(not isinstance(value, str) or (value and safe_text(value) != value) for value in values.values())):
        raise ValueError("dados de configuração inválidos")
    lines = []
    written: set[str] = set()
    has_room = False
    for binding in parse_stream(io.StringIO(original)):
        if binding.error:
            raise ValueError(".env contém sintaxe inválida; arquivo preservado")
        has_room = has_room or binding.key == "BLAZE_ROOM_ID"
        if binding.key in values:
            if binding.key in written:
                continue
            value = values[binding.key].replace("\\", "\\\\").replace("'", "\\'")
            lines.append(f"{binding.key}='{value}'\n")
            written.add(binding.key)
        else:
            lines.append(binding.original.string)
    merged = "".join(lines)
    if merged and not merged.endswith("\n"):
        merged += "\n"
    for key, value in values.items():
        if key not in written:
            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            merged += f"{key}='{escaped}'\n"
    if not has_room:
        merged += "BLAZE_ROOM_ID=4\n"
    return merged


def save_env(path: Path, values: dict[str, str]) -> None:
    if path.is_symlink():
        raise ValueError(".env não pode ser um link simbólico")
    original = path.read_bytes() if path.exists() else None
    merged = merge_env(original.decode("utf-8-sig") if original is not None else "", values)
    # Arquivo temporário no mesmo diretório: replace atômico, sem cópia de backup.
    descriptor, temp_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(merged)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink() or (path.read_bytes() if path.exists() else None) != original:
            raise ValueError(".env mudou durante a captura; arquivo preservado")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
