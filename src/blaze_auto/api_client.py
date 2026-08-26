from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import requests


API_BASE_URL = "https://blaze.bet.br/api/singleplayer-originals/originals/crash_v2/round"
ENTER_URL = f"{API_BASE_URL}/enter"
CASHOUT_URL = f"{API_BASE_URL}/cashout"


class BlazeApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class CrashAccount:
    authorization: str
    wallet_id: int
    username: str
    rank: str
    room_id: int = 4
    session_id: str = ""
    client_version: str = ""


class CrashApiClient:
    """Executor HTTP do Crash.

    Requisições POST não são repetidas automaticamente para evitar apostas
    duplicadas quando a resposta do servidor for perdida.
    """

    def __init__(
        self,
        account: CrashAccount,
        timeout: float = 15.0,
        session: requests.Session | None = None,
    ) -> None:
        self.account = account
        self.timeout = timeout
        self.session = session or requests.Session()

    def enter(
        self,
        amount: str | Decimal,
        client_round_id: str,
        auto_cashout_at: str | Decimal | None = None,
    ) -> dict[str, Any]:
        amount_text = decimal_text(amount, "amount", minimum=Decimal("0.01"))
        auto_cashout_text = None
        if auto_cashout_at is not None:
            auto_cashout_text = decimal_text(
                auto_cashout_at,
                "auto_cashout_at",
                minimum=Decimal("1.01"),
            )
        if not client_round_id.strip():
            raise ValueError("client_round_id não pode ficar vazio")
        payload = {
            "amount": amount_text,
            "type": "BRL",
            "auto_cashout_at": auto_cashout_text,
            "room_id": self.account.room_id,
            "username": self.account.username,
            "rank": self.account.rank,
            "client_round_id": client_round_id,
            "wallet_id": self.account.wallet_id,
        }
        return self._post(ENTER_URL, payload)

    def cashout(self) -> dict[str, Any]:
        payload = {
            "room_id": self.account.room_id,
            "wallet_id": self.account.wallet_id,
        }
        return self._post(CASHOUT_URL, payload)

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise BlazeApiError(f"falha de conexão: {exc}") from exc

        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if not response.ok:
            message = body.get("message") if isinstance(body, dict) else None
            raise BlazeApiError(f"HTTP {response.status_code}: {message or body}")
        return body if isinstance(body, dict) else {"data": body}

    def _headers(self) -> dict[str, str]:
        authorization = self.account.authorization.strip()
        if not authorization.lower().startswith("bearer "):
            authorization = f"Bearer {authorization}"
        headers = {
            "accept": "application/json, text/plain, */*",
            "authorization": authorization,
            "content-type": "application/json",
            "origin": "https://blaze.bet.br",
            "referer": "https://blaze.bet.br/pt/games/crash",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149 Safari/537.36"
            ),
        }
        if self.account.session_id:
            headers["x-session-id"] = self.account.session_id
        if self.account.client_version:
            headers["x-client-version"] = self.account.client_version
        return headers


def decimal_text(value: str | Decimal, field: str, minimum: Decimal) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} inválido: {value}") from None
    if not parsed.is_finite() or parsed < minimum:
        raise ValueError(f"{field} deve ser pelo menos {minimum}")
    return f"{parsed:.2f}"
