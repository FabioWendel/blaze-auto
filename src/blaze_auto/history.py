from __future__ import annotations

import csv
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


HISTORY_URL = (
    "https://blaze.bet.br/api/singleplayer-originals/originals/"
    "crash_games/recent/history/{room_id}"
)
HISTORY_FIELDS = [
    "id",
    "status",
    "created_at",
    "crash_point",
    "server_seed",
    "is_bonus_round",
]


class HistoryDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class HistoryPage:
    page: int
    records: list[dict[str, Any]]
    total_pages: int


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def extract_records(body: Any) -> tuple[list[dict[str, Any]], int]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)], 1
    if not isinstance(body, dict):
        raise HistoryDownloadError("resposta histórica não é lista nem objeto")
    records = body.get("records")
    if not isinstance(records, list):
        raise HistoryDownloadError("resposta histórica não contém records")
    try:
        total_pages = max(1, int(body.get("total_pages") or 1))
    except (TypeError, ValueError):
        total_pages = 1
    return [row for row in records if isinstance(row, dict)], total_pages


def fetch_history_page(
    page: int,
    room_id: int,
    start: datetime,
    end: datetime,
    timeout: float,
    retries: int,
    request_delay: float = 0.0,
) -> HistoryPage:
    url = HISTORY_URL.format(room_id=room_id)
    params = {"page": page, "startDate": iso_z(start), "endDate": iso_z(end)}
    headers = {
        "accept": "application/json, text/plain, */*",
        "referer": "https://blaze.bet.br/pt/games/crash",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if request_delay:
                time.sleep(request_delay)
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                try:
                    server_wait = float(retry_after) if retry_after else 0.0
                except ValueError:
                    server_wait = 0.0
                wait = max(server_wait, min(15 * (2**attempt), 120)) + random.uniform(0, 2)
                if attempt < retries:
                    print(f"Página {page}: limite 429; aguardando {wait:.1f}s.", flush=True)
                    time.sleep(wait)
                    continue
            response.raise_for_status()
            records, total_pages = extract_records(response.json())
            return HistoryPage(page, records, total_pages)
        except (requests.RequestException, ValueError, HistoryDownloadError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2**attempt, 8))
    raise HistoryDownloadError(f"página {page} falhou após {retries + 1} tentativa(s): {last_error}")


def normalize_record(row: dict[str, Any]) -> dict[str, Any] | None:
    round_id = row.get("id")
    created_at = row.get("created_at")
    crash_point = row.get("crash_point")
    if not round_id or not created_at or crash_point is None:
        return None
    try:
        point = float(crash_point)
        parse_iso(str(created_at))
    except (TypeError, ValueError):
        return None
    return {
        "id": str(round_id),
        "status": str(row.get("status") or ""),
        "created_at": str(created_at),
        "crash_point": point,
        "server_seed": str(row.get("server_seed") or ""),
        "is_bonus_round": bool(row.get("is_bonus_round")),
    }


def filter_interval(
    rows: list[dict[str, Any]], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = normalize_record(raw)
        if row is None:
            continue
        created_at = parse_iso(row["created_at"])
        if start <= created_at <= end:
            unique[row["id"]] = row
    return sorted(unique.values(), key=lambda row: parse_iso(row["created_at"]))


def download_history(
    start: datetime,
    end: datetime,
    room_id: int = 4,
    workers: int = 1,
    batch_size: int = 20,
    timeout: float = 20.0,
    retries: int = 3,
    max_pages: int = 0,
    request_delay: float = 0.35,
) -> tuple[list[dict[str, Any]], int]:
    first = fetch_history_page(1, room_id, start, end, timeout, retries, request_delay)
    total_pages = min(first.total_pages, max_pages) if max_pages else first.total_pages
    collected = list(first.records)
    pages_fetched = 1
    print(f"API informou {first.total_pages} página(s); coletando até {total_pages}.", flush=True)

    next_page = 2
    stop = _contains_older_than(first.records, start)
    while next_page <= total_pages and not stop:
        page_numbers = list(range(next_page, min(next_page + batch_size, total_pages + 1)))
        results: list[HistoryPage] = []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(
                    fetch_history_page,
                    page,
                    room_id,
                    start,
                    end,
                    timeout,
                    retries,
                    request_delay,
                ): page
                for page in page_numbers
            }
            for future in as_completed(futures):
                results.append(future.result())
        for result in sorted(results, key=lambda item: item.page):
            collected.extend(result.records)
            pages_fetched += 1
            if _contains_older_than(result.records, start):
                stop = True
        print(
            f"Páginas: {pages_fetched}/{total_pages} | registros brutos: {len(collected)}",
            flush=True,
        )
        next_page = page_numbers[-1] + 1

    return filter_interval(collected, start, end), pages_fetched


def _contains_older_than(rows: list[dict[str, Any]], start: datetime) -> bool:
    dates = []
    for row in rows:
        try:
            dates.append(parse_iso(str(row.get("created_at") or "")))
        except ValueError:
            continue
    return bool(dates and min(dates) < start)


def write_history(path: Path, rows: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    metadata_path = path.with_suffix(path.suffix + ".meta.json")
    with metadata_path.open("w", encoding="utf-8") as stream:
        json.dump(metadata, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
