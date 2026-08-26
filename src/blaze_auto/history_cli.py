from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .history import HistoryDownloadError, download_history, iso_z, parse_iso, write_history


def run(args: argparse.Namespace) -> int:
    end = parse_iso(args.end) if args.end else datetime.now(timezone.utc)
    start = parse_iso(args.start) if args.start else end - timedelta(days=args.days)
    if start >= end:
        print("ERRO: a data inicial deve ser anterior à data final.")
        return 1
    output = Path(args.output)
    print(f"Intervalo UTC: {iso_z(start)} até {iso_z(end)}", flush=True)
    try:
        rows, pages = download_history(
            start=start,
            end=end,
            room_id=args.room_id,
            workers=args.workers,
            batch_size=args.batch_size,
            timeout=args.timeout,
            retries=args.retries,
            max_pages=args.max_pages,
            request_delay=args.request_delay,
        )
    except HistoryDownloadError as exc:
        print(f"ERRO: {exc}")
        return 1
    metadata = {
        "start_utc": iso_z(start),
        "end_utc": iso_z(end),
        "room_id": args.room_id,
        "pages_fetched": pages,
        "record_count": len(rows),
        "chronological_order": "ascending",
    }
    write_history(output, rows, metadata)
    print(f"Concluído: {len(rows)} rodadas salvas em {output.resolve()}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Baixa o histórico do Blaze Crash")
    parser.add_argument("--days", type=int, default=30, help="dias anteriores ao fim; padrão: 30")
    parser.add_argument("--start", help="início ISO-8601 UTC; substitui --days")
    parser.add_argument("--end", help="fim ISO-8601 UTC; padrão: agora")
    parser.add_argument("--room-id", type=int, default=4)
    parser.add_argument("--output", default="data/crash_history_30d.csv")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--request-delay", type=float, default=0.35, help="pausa antes de cada página")
    parser.add_argument("--max-pages", type=int, default=0, help="limite para teste; 0 = todas")
    return parser


def main() -> None:
    raise SystemExit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
