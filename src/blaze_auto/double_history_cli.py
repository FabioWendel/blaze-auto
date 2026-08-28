"""Histórico público Double: JSON, paginação estável e retomada sem credenciais."""
from __future__ import annotations

import argparse
import json
import math
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .history import iso_z, parse_iso


URL = "https://blaze.bet.br/api/singleplayer-originals/originals/roulette_games/recent/history/1"
MANAUS = timezone(timedelta(hours=-4))


class DownloadError(RuntimeError):
    pass


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


class RateGate:
    def __init__(self, interval: float = 1.15) -> None:
        self.interval = interval
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self.lock:
                delay = self.next_at - time.monotonic()
                if delay <= 0:
                    self.next_at = time.monotonic() + self.interval
                    return
            time.sleep(min(delay, 1))

    def backoff(self, delay: float) -> None:
        with self.lock:
            self.next_at = max(self.next_at, time.monotonic() + delay)


class PageClient:
    def __init__(self, output: Path, start: str, end: str) -> None:
        self.pages = output / "pages"
        self.pages.mkdir(exist_ok=True)
        self.start, self.end = start, end
        self.gate = RateGate()
        self.gate.backoff(15)  # Also respect cooldown when resuming a run.
        self.local = threading.local()

    def fetch(self, page: int) -> dict[str, Any]:
        path = self.pages / f"page_{page:05d}.json"
        if path.exists():
            return validate_page(read_json(path))
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.headers.update({"accept": "application/json", "referer": "https://blaze.bet.br/pt/games/double"})
        for attempt in range(5):
            self.gate.wait()
            try:
                response = self.local.session.get(URL, params={"page": page, "startDate": self.start, "endDate": self.end},
                                                  timeout=20, allow_redirects=False)
            except requests.RequestException:
                if attempt == 4:
                    raise DownloadError(f"página {page}: falha de rede; execute novamente para retomar") from None
                self.gate.backoff(2 ** attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 4:
                    raise DownloadError(f"página {page}: HTTP {response.status_code}; páginas já baixadas preservadas")
                try:
                    server_wait = float(response.headers.get("Retry-After", "0"))
                    if not math.isfinite(server_wait):
                        server_wait = 0
                except ValueError:
                    server_wait = 0
                delay = max(server_wait, 15 * (2 ** attempt))
                if response.status_code == 429:
                    with self.gate.lock:
                        self.gate.interval = min(5, self.gate.interval * 1.25)
                print(f"Página {page}: HTTP {response.status_code}; pausa compartilhada de {delay:.0f}s.", flush=True)
                self.gate.backoff(delay)
                continue
            if response.status_code != 200:
                raise DownloadError(f"página {page}: HTTP {response.status_code}; sem redirecionar")
            try:
                body = validate_page(response.json())
            except ValueError:
                raise DownloadError(f"página {page}: JSON inválido") from None
            write_json(path, body)
            return body
        raise DownloadError(f"página {page}: tentativas esgotadas")


def validate_page(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or not isinstance(body.get("records"), list):
        raise DownloadError("formato inesperado do histórico público")
    count = body.get("total_pages")
    if isinstance(count, str) and count.isascii() and count.isdigit():
        count = int(count)
    if type(count) is not int or count < 0 or any(not isinstance(row, dict) for row in body["records"]):
        raise DownloadError("formato inesperado do histórico público")
    return {**body, "total_pages": count}


def validate_round(row: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
    try:
        created = parse_iso(row["created_at"])
    except (KeyError, ValueError, TypeError):
        raise DownloadError("registro com data inválida") from None
    color, roll = row.get("color"), row.get("roll")
    if (not row.get("id") or not start <= created <= end or type(color) is not int
            or type(roll) is not int or not 0 <= roll <= 14
            or color != (0 if roll == 0 else 1 if roll <= 7 else 2)):
        raise DownloadError("registro fora do intervalo ou cor/número/ID inválido")
    return row


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Descriptive counts only: do not interpret the largest bin as prediction."""
    hours = {hour: Counter() for hour in range(24)}
    days: Counter[str] = Counter()
    for row in rows:
        local = parse_iso(row["created_at"]).astimezone(MANAUS)
        hours[local.hour][row["color"]] += 1
        days[local.date().isoformat()] += 1
    return {"timezone": "America/Manaus (UTC-04:00)", "rounds_by_local_day": dict(sorted(days.items())),
            "hours": [{"hour": hour, "total": sum(count.values()),
                       "red": count[1], "black": count[2], "white": count[0]} for hour, count in hours.items()],
            "warning": "Frequências descritivas, não evidência de vantagem. Dias/horas parciais têm amostras menores."}


def download(output: Path, start_text: str, end_text: str, workers: int = 2) -> dict[str, Any]:
    start, requested_end = parse_iso(start_text), parse_iso(end_text)
    if start >= requested_end or workers not in (1, 2):
        raise DownloadError("intervalo inválido ou workers fora de 1–2")
    output.mkdir(parents=True, exist_ok=True)
    request_path = output / "request.json"
    if request_path.exists():
        request = read_json(request_path)
        if request.get("url") != URL or request.get("requested_start_utc") != iso_z(start) or request.get("requested_end_utc") != iso_z(requested_end):
            raise DownloadError("a pasta contém outro intervalo; escolha outra pasta")
    else:
        cutoff = min(requested_end, datetime.now(timezone.utc) - timedelta(minutes=2))
        if start >= cutoff:
            raise DownloadError("intervalo ainda não possui cobertura concluída")
        request = {"url": URL, "requested_start_utc": iso_z(start), "requested_end_utc": iso_z(requested_end),
                   "effective_end_utc": iso_z(cutoff), "timezone": "America/Manaus (UTC-04:00)",
                   "cutoff_note": "Fim fixado no menor entre o solicitado e agora menos 2 minutos, para excluir rodadas ainda em andamento."}
        write_json(request_path, request)
    end = parse_iso(request["effective_end_utc"])
    client = PageClient(output, request["requested_start_utc"], request["effective_end_utc"])
    first = client.fetch(1)
    # Verified endpoint behavior: total_pages is a record count, despite its
    # name; pagination itself returns 100 rows per page.
    expected_records = first["total_pages"]
    page_count = max(1, math.ceil(expected_records / 100))
    print(f"Fim fixo: {request['effective_end_utc']} | registros esperados={expected_records} | páginas={page_count}", flush=True)
    results = {1: first}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(client.fetch, page): page for page in range(2, page_count + 1)}
        try:
            for future in as_completed(futures):
                page = futures[future]
                results[page] = future.result()
                if len(results) % 25 == 0 or len(results) == page_count:
                    print(f"Páginas concluídas: {len(results)}/{page_count}", flush=True)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    extra = client.fetch(page_count + 1)
    if extra["records"] or extra["total_pages"] != expected_records:
        raise DownloadError("contagem/paginação mudou; dados preservados, mas cobertura não confirmada")
    unique: dict[str, dict[str, Any]] = {}
    raw_count = 0
    for page in range(1, page_count + 1):
        body = results[page]
        if body["total_pages"] != expected_records:
            raise DownloadError("quantidade de registros mudou entre páginas; não consolidado")
        expected_size = min(100, max(0, expected_records - (page - 1) * 100))
        if len(body["records"]) != expected_size:
            raise DownloadError(f"página {page} tem quantidade inesperada de registros")
        for raw in body["records"]:
            row = validate_round(raw, start, end)
            raw_count += 1
            if str(row["id"]) in unique:
                raise DownloadError("ID duplicado na paginação; cobertura precisa de conferência")
            unique[str(row["id"])] = row
    rows = sorted(unique.values(), key=lambda row: parse_iso(row["created_at"]))
    if raw_count != expected_records:
        raise DownloadError("contagem final diverge da API")
    gaps = [(parse_iso(b["created_at"]) - parse_iso(a["created_at"])).total_seconds() for a, b in zip(rows, rows[1:])]
    metadata = {**request, "scope": "public_double_rounds_room_1", "downloaded_at_utc": iso_z(datetime.now(timezone.utc)),
                "complete_for_effective_interval": True, "complete_for_requested_interval": end == requested_end,
                "pages_with_records": page_count, "empty_page_verified": page_count + 1,
                "record_count": len(rows), "duplicates": 0,
                "first_round_utc": rows[0]["created_at"] if rows else None,
                "last_round_utc": rows[-1]["created_at"] if rows else None,
                "counts_by_color": {"red": sum(r["color"] == 1 for r in rows), "black": sum(r["color"] == 2 for r in rows),
                                    "white": sum(r["color"] == 0 for r in rows)},
                "largest_gap_seconds": max(gaps, default=0), "gaps_over_90_seconds": sum(gap > 90 for gap in gaps)}
    write_json(output / "double_history.json", {"metadata": metadata, "records": rows})
    write_json(output / "hourly_counts.json", {"source": URL, "record_count": len(rows), **aggregate(rows)})
    write_json(output / "summary.json", metadata)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", default="data/double_history/public")
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    try:
        metadata = download(Path(args.output), args.start, args.end, args.workers)
    except (DownloadError, OSError, ValueError) as exc:
        print(f"ERRO: {exc}", flush=True)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Interrompido; execute o mesmo comando para retomar as páginas já salvas.", flush=True)
        raise SystemExit(2)
    print(f"Concluído: {metadata['record_count']} rodadas públicas validadas em {args.output}", flush=True)


if __name__ == "__main__":
    main()
