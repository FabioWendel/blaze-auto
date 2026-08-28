"""Baixa apenas por GET o histórico privado de apostas da própria conta."""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values

from .history import iso_z, parse_iso


URL = "https://blaze.bet.br/api/game_provider_rounds"


class DownloadError(RuntimeError):
    pass


def fetch_page(session: Any, page: int, start: str, end: str) -> dict[str, Any]:
    for attempt in range(4):
        time.sleep(0.35)
        try:
            response = session.get(URL, params={"page": page, "start": start, "end": end},
                                   timeout=20, allow_redirects=False)
        except requests.RequestException:
            if attempt == 3:
                raise DownloadError(f"página {page}: falha de conexão após 4 tentativas") from None
            time.sleep(2 ** attempt)
            continue
        status = response.status_code
        if status == 429 or status >= 500:
            if attempt == 3:
                raise DownloadError(f"página {page}: HTTP {status} após 4 tentativas")
            # A long Retry-After must be respected, not shortened into retries.
            try:
                delay = max(5 * (2 ** attempt), float(response.headers.get("Retry-After", "0")))
            except ValueError:
                delay = 5 * (2 ** attempt)
            if delay > 60:
                raise DownloadError("API pediu espera maior que 60s; download parcial preservado")
            print(f"Página {page}: HTTP {status}; aguardando {delay:.0f}s.", flush=True)
            time.sleep(delay)
            continue
        if status != 200:
            raise DownloadError(f"página {page}: HTTP {status}; nenhum redirecionamento seguido")
        try:
            body = response.json()
        except ValueError:
            raise DownloadError(f"página {page}: JSON inválido") from None
        if (not isinstance(body, dict) or not isinstance(body.get("records"), list)
                or type(body.get("total_pages")) is not int or body["total_pages"] < 0
                or any(not isinstance(row, dict) for row in body["records"])):
            raise DownloadError(f"página {page}: formato histórico inesperado")
        return body
    raise DownloadError(f"página {page}: tentativas esgotadas")


def save_json(path: Path, body: Any) -> None:
    # A fresh output folder is used; never overwrite a previous download.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(body, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def download(session: Any, start: str, end: str, output: Path) -> dict[str, Any]:
    start_time, end_time = parse_iso(start), parse_iso(end)
    if start_time >= end_time:
        raise DownloadError("data inicial deve ser anterior à final")
    output.mkdir(parents=True, exist_ok=False)
    pages_dir = output / "pages"
    pages_dir.mkdir()
    save_json(output / "request.json", {"url": URL, "start": start, "end": end,
              "scope": "account_betting_history_not_public_rounds", "contains_private_account_data": True})
    records: list[dict[str, Any]] = []
    total_pages = 1
    pages_downloaded = 0
    terminal_empty_page = None
    empty_probes: list[int] = []
    games_played: list[dict[str, Any]] = []
    page = 1
    while page <= total_pages:
        body = fetch_page(session, page, start, end)
        pages_downloaded += 1
        save_json(pages_dir / f"page_{page:04d}.json", body)
        reported = max(1, body["total_pages"])
        if page == 1:
            total_pages = reported
            games_played = body.get("games_played", [])
        elif reported != total_pages:
            raise DownloadError("paginação mudou durante a coleta; páginas parciais preservadas")
        if not body["records"] and page < total_pages:
            # This endpoint can report a fixed ceiling (250), not the actual
            # number of pages. Check the next and final advertised pages;
            # preserve that this is not an exhaustive scan of empty pages.
            terminal_empty_page = page
            for probe_page in sorted({page + 1, total_pages}):
                probe = fetch_page(session, probe_page, start, end)
                pages_downloaded += 1
                save_json(pages_dir / f"page_{probe_page:04d}.json", probe)
                if probe["records"] or max(1, probe["total_pages"]) != total_pages:
                    raise DownloadError("paginação esparsa/inconsistente; download requer conferência, originais preservados")
                empty_probes.append(probe_page)
            print(f"Fim vazio na página {page}; páginas {empty_probes} também vazias. "
                  f"Total anunciado ({total_pages}) não corresponde às páginas com registros.", flush=True)
            break
        records.extend(body["records"])
        if page == 1 or page % 10 == 0 or page == total_pages:
            print(f"Páginas {page}/{total_pages} | registros recebidos={len(records)}", flush=True)
        page += 1

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    outside, invalid_dates, duplicates = 0, 0, 0
    for row in records:
        try:
            created = parse_iso(row["created_at"])
        except (ValueError, TypeError, KeyError):
            invalid_dates += 1
            continue
        if not start_time <= created <= end_time:
            outside += 1
            continue
        if not row.get("id"):
            raise DownloadError("registro sem ID; páginas originais preservadas")
        key = (str(row.get("slug") or ""), str(row["id"]))
        if key in unique:
            if row != unique[key]:
                raise DownloadError("registro duplicado divergente; páginas originais preservadas")
            duplicates += 1
        unique[key] = row
    rows = sorted(unique.values(), key=lambda row: parse_iso(row["created_at"]))
    counts = Counter(str(row.get("slug") or "unknown") for row in rows)
    metadata = {
        "source": URL, "scope": "account_betting_history_not_public_rounds",
        "requested_start_utc": start, "requested_end_utc": end,
        "downloaded_at_utc": iso_z(datetime.now(timezone.utc)),
        "all_reported_pages_downloaded": terminal_empty_page is None,
        "pages_downloaded": pages_downloaded, "reported_total_pages": total_pages,
        "terminal_empty_page": terminal_empty_page, "empty_probe_pages": empty_probes,
        "raw_record_count": len(records), "record_count": len(rows), "duplicates": duplicates,
        "outside_interval": outside, "invalid_dates": invalid_dates,
        "first_record_utc": rows[0]["created_at"] if rows else None,
        "last_record_utc": rows[-1]["created_at"] if rows else None,
        "records_by_slug": dict(counts), "games_played": games_played,
        "warning": "Não é histórico completo de rodadas públicas. Pode haver limites de retenção da API.",
    }
    save_json(output / "account_history.json", {"metadata": metadata, "records": rows})
    double_rows = [row for row in rows if str(row.get("slug") or "").lower() in {"double", "double_room_1"}]
    if double_rows:
        save_json(output / "double_account_history.json", {
            "metadata": {**metadata, "filter_slugs": ["double", "double_room_1"], "record_count": len(double_rows)},
            "records": double_rows,
        })
    save_json(output / "summary.json", metadata)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True, help="nova pasta privada para JSON e páginas originais")
    args = parser.parse_args()
    values = dotenv_values(".env", interpolate=False)
    token = (values.get("BLAZE_AUTHORIZATION") or "").strip()
    if not token:
        print("ERRO: capture BLAZE_AUTHORIZATION no .env antes de baixar.")
        raise SystemExit(1)
    with requests.Session() as session:
        session.headers.update({"accept": "application/json", "origin": "https://blaze.bet.br",
                                "referer": "https://blaze.bet.br/pt/games/double",
                                "authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}"})
        if values.get("BLAZE_SESSION_ID"):
            session.headers["x-session-id"] = values["BLAZE_SESSION_ID"]
        try:
            metadata = download(session, args.start, args.end, Path(args.output))
        except DownloadError as exc:
            print(f"ERRO: {exc}", flush=True)
            raise SystemExit(1)
        except (OSError, ValueError):
            print("ERRO: confira as datas e use uma pasta nova com permissão de escrita.", flush=True)
            raise SystemExit(1)
    print(f"Concluído: {metadata['record_count']} registros da conta. Arquivos privados em {args.output}", flush=True)


if __name__ == "__main__":
    main()
