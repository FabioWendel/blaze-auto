from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .collector import append_round, load_ids
from .crash_watcher import (
    DEFAULT_CRASH_BETS_ROOM,
    DEFAULT_CRASH_ROOM,
    DEFAULT_WS_URL,
    BlazeCrashWatcher,
)


def run(args: argparse.Namespace) -> int:
    output = Path(args.output)
    seen = load_ids(output) if args.collect else set()
    watcher = BlazeCrashWatcher(
        url=args.ws_url,
        room=args.room,
        reconnect_seconds=args.reconnect_seconds,
        bets_room=None if args.no_bets_room else args.bets_room,
    )
    watcher.start()
    last_state: tuple[str, str] | None = None
    completed_count = 0
    rooms = args.room if args.no_bets_room else f"{args.room} + {args.bets_room}"
    print(f"Conectando às salas {rooms}... Pressione Ctrl+C para sair.", flush=True)
    last_bets_signature: tuple[str, int, int, int, int] | None = None
    try:
        while True:
            snapshot = watcher.snapshot()
            state = (snapshot.round_id, snapshot.status)
            if snapshot.round_id and state != last_state:
                print(
                    f"ESTADO | rodada={snapshot.round_id} | status={snapshot.status}"
                    f" | atualizado={snapshot.updated_at}",
                    flush=True,
                )
                if args.raw and snapshot.raw:
                    print(json.dumps(snapshot.raw, ensure_ascii=False), flush=True)
                last_state = state

            if args.show_bets:
                bets = watcher.bets_snapshot()
                bets_signature = (
                    bets.round_id,
                    bets.total_bets,
                    bets.created_bets,
                    bets.won_bets,
                    bets.lost_bets,
                )
                if bets.round_id and bets_signature != last_bets_signature:
                    print(
                        f"APOSTAS | rodada={bets.round_id} | visíveis={bets.total_bets}"
                        f" | valor=R$ {bets.total_amount:.2f} | abertas={bets.created_bets}"
                        f" | ganhas={bets.won_bets} | perdidas={bets.lost_bets}",
                        flush=True,
                    )
                    last_bets_signature = bets_signature

            for row in watcher.pop_completed_rounds():
                print(
                    f"RESULTADO | rodada={row['id']} | crash={row['crash_point']:.2f}x"
                    f" | bônus={'sim' if row['is_bonus_round'] else 'não'}",
                    flush=True,
                )
                if args.collect and row["id"] not in seen:
                    append_round(output, row)
                    seen.add(row["id"])
                completed_count += 1
                if args.max_rounds and completed_count >= args.max_rounds:
                    return 0

            error = watcher.last_error()
            if error and args.verbose:
                print(f"WS | {error}", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        watcher.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitora o evento Blaze crash.tick")
    parser.add_argument("--collect", action="store_true", help="salva rodadas completas em CSV")
    parser.add_argument("--output", default="data/crash_rounds.csv", help="arquivo CSV de saída")
    parser.add_argument("--max-rounds", type=int, default=0, help="encerra após N resultados; 0 = contínuo")
    parser.add_argument("--raw", action="store_true", help="mostra o payload bruto quando o estado muda")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--reconnect-seconds", type=float, default=3.0)
    parser.add_argument("--ws-url", default=DEFAULT_WS_URL)
    parser.add_argument("--room", default=DEFAULT_CRASH_ROOM)
    parser.add_argument("--bets-room", default=DEFAULT_CRASH_BETS_ROOM)
    parser.add_argument("--no-bets-room", action="store_true", help="não assina a sala crash.tick-bets")
    parser.add_argument("--show-bets", action="store_true", help="mostra agregados da sala de apostas")
    return parser


def main() -> None:
    raise SystemExit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
