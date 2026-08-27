from __future__ import annotations

import argparse
import math
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .api_client import BlazeApiError, BlazeEntryNotSent, BlazeUncertainOutcome, CrashApiClient
from .bet_cli import account_from_environment
from .crash_watcher import DEFAULT_CRASH_ROOM, DEFAULT_WS_URL, BlazeCrashWatcher
from .history import fetch_history_page, normalize_record
from .strategy import (
    DEFAULT_PATTERN,
    append_signal,
    calculate_profit,
    entered_round_ids,
    matches_pattern,
    pending_signal,
    risk_status,
    uncertain_signal,
    update_signal,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def bootstrap_rounds(room_id: int, timeout: float) -> list[dict[str, Any]]:
    end = utc_now()
    page = fetch_history_page(1, room_id, end - timedelta(days=2), end, timeout, retries=3)
    normalized = [row for raw in page.records if (row := normalize_record(raw)) is not None]
    return sorted(normalized, key=lambda row: row["created_at"])


MAX_TICK_AGE_SECONDS = 2.0
ENTRY_RETRY_DELAY_SECONDS = 0.5


def fresh_tick(watcher: BlazeCrashWatcher, snapshot: Any) -> bool:
    return (
        not watcher.last_error()
        and bool(snapshot.round_id)
        and 0 <= time.time() - snapshot.received_at <= MAX_TICK_AGE_SECONDS
    )


def enter_in_window(api: CrashApiClient, watcher: BlazeCrashWatcher, path: Path,
                    row: dict[str, Any], window_seconds: float, max_attempts: int) -> bool:
    """Repete somente falha comprovadamente anterior ao envio, na mesma rodada."""
    deadline = time.monotonic() + window_seconds
    signal_id = row["signal_id"]

    def window_open() -> bool:
        snapshot = watcher.snapshot()
        return (
            time.monotonic() < deadline
            and snapshot.round_id == row["entry_round_id"]
            and snapshot.status == "waiting"
            and fresh_tick(watcher, snapshot)
        )

    for attempt in range(1, max_attempts + 1):
        if not window_open():
            break
        update_signal(path, signal_id, {"status": "sending", "message": f"reservado antes do POST; tentativa {attempt}"})
        # A gravação em disco também pode consumir o restante da janela.
        if not window_open():
            break
        try:
            api.enter(row["stake"], row["entry_round_id"], row["auto_cashout_at"],
                      timeout=deadline - time.monotonic())
        except BlazeUncertainOutcome:
            raise
        except BlazeEntryNotSent as exc:
            update_signal(path, signal_id, {"status": "rejected", "message": f"tentativa {attempt}: {exc}"})
            if attempt == max_attempts:
                break
            print(f"ENTRADA NÃO ENVIADA | tentativa={attempt} | verificando janela da mesma rodada", flush=True)
            retry_at = time.monotonic() + ENTRY_RETRY_DELAY_SECONDS
            while time.monotonic() < retry_at and window_open():
                time.sleep(min(0.05, max(0, retry_at - time.monotonic())))
        except BlazeApiError as exc:
            update_signal(path, signal_id, {"status": "rejected", "message": str(exc)})
            print(f"ENTRADA RECUSADA | {exc} | aguardando novo padrão", flush=True)
            return False
        else:
            update_signal(path, signal_id, {"status": "entered", "message": f"API aceitou entrada; tentativa {attempt}"})
            row["status"] = "entered"
            return True
    update_signal(path, signal_id, {"status": "rejected", "message": "sem entrada: janela indisponível ou limite de tentativas atingido"})
    print("ENTRADA DESCARTADA | janela indisponível ou tentativas esgotadas | aguardando novo padrão", flush=True)
    return False


def run(args: argparse.Namespace) -> int:
    if (not math.isfinite(args.entry_window_seconds) or args.entry_window_seconds <= 0
            or args.max_entry_attempts < 1):
        print("ERRO: janela e máximo de tentativas devem ser positivos e finitos.")
        return 1
    pattern = args.pattern.strip().upper()
    if not pattern or any(char not in {"B", "M", "A"} for char in pattern):
        print("ERRO: --pattern aceita somente B, M e A.")
        return 1
    try:
        stake = Decimal(args.stake)
        cashout = Decimal(args.auto_cashout_at)
    except InvalidOperation:
        print("ERRO: stake e cashout devem ser números válidos.")
        return 1
    if stake <= 0 or cashout <= 1:
        print("ERRO: stake deve ser positivo e cashout maior que 1.")
        return 1
    signals_path = Path(args.signals or ("data/auto_live_signals.csv" if args.live else "data/auto_paper_signals.csv"))
    uncertain = uncertain_signal(signals_path)
    if uncertain:
        print(
            f"BLOQUEADO: entrada incerta na rodada {uncertain['entry_round_id']}. "
            "Confira o histórico da conta e use blaze_auto.reconcile. "
            "Reiniciar não libera novas apostas.",
            flush=True,
        )
        return 3
    try:
        api = CrashApiClient(account_from_environment(), timeout=args.http_timeout) if args.live else None
        history = bootstrap_rounds(args.room_id, args.http_timeout)
    except (ValueError, BlazeApiError, Exception) as exc:
        print(f"ERRO no bootstrap: {exc}")
        return 1

    points = [float(row["crash_point"]) for row in history]
    seen_ids = {str(row["id"]) for row in history}
    pending = pending_signal(signals_path)
    entered_ids = entered_round_ids(signals_path)
    armed_trigger_id = ""
    if pending:
        completed = next(
            (row for row in history if str(row["id"]) == pending.get("entry_round_id")),
            None,
        )
        if completed:
            pending_stake = Decimal(pending["stake"])
            pending_cashout = Decimal(pending["auto_cashout_at"])
            status, profit = calculate_profit(
                pending_stake,
                pending_cashout,
                float(completed["crash_point"]),
            )
            update_signal(
                signals_path,
                pending["signal_id"],
                {
                    "status": status,
                    "result_round_id": completed["id"],
                    "result_time": completed["created_at"],
                    "crash_point": completed["crash_point"],
                    "profit": f"{profit:.2f}",
                    "message": f"resultado {status} recuperado no reinício",
                },
            )
            print(f"Sinal anterior recuperado: {status} | lucro R$ {profit:.2f}", flush=True)
            pending = None
    if not pending and history and matches_pattern(points, pattern):
        armed_trigger_id = str(history[-1]["id"])

    watcher = BlazeCrashWatcher(
        url=args.ws_url,
        room=args.room,
        reconnect_seconds=args.reconnect_seconds,
        bets_room=None,
    )
    watcher.start()
    mode = "LIVE" if args.live else "PAPER"
    print(
        f"AUTO {mode} | padrão={pattern} | stake=R$ {stake:.2f} | cashout={cashout:.2f}x",
        flush=True,
    )
    print(f"Histórico inicial: {len(history)} rodadas | sinais: {signals_path}", flush=True)
    session_entries = 0
    try:
        while True:
            for result in watcher.pop_completed_rounds():
                round_id = str(result["id"])
                if pending and pending.get("entry_round_id") == round_id:
                    pending_stake = Decimal(pending["stake"])
                    pending_cashout = Decimal(pending["auto_cashout_at"])
                    status, profit = calculate_profit(
                        pending_stake,
                        pending_cashout,
                        float(result["crash_point"]),
                    )
                    update_signal(
                        signals_path,
                        pending["signal_id"],
                        {
                            "status": status,
                            "result_round_id": round_id,
                            "result_time": result["updated_at"],
                            "crash_point": result["crash_point"],
                            "profit": f"{profit:.2f}",
                            "message": f"resultado {status}",
                        },
                    )
                    print(
                        f"RESULTADO {status.upper()} | veio {result['crash_point']:.2f}x"
                        f" | lucro R$ {profit:.2f}",
                        flush=True,
                    )
                    pending = None

                if round_id not in seen_ids:
                    # Um gatilho vale somente para a rodada imediatamente seguinte.
                    armed_trigger_id = ""
                    seen_ids.add(round_id)
                    points.append(float(result["crash_point"]))
                    points = points[-200:]
                    if not pending and matches_pattern(points, pattern):
                        armed_trigger_id = round_id
                        print(f"PADRÃO {pattern} DETECTADO | gatilho={round_id}", flush=True)

            snapshot = watcher.snapshot()
            if armed_trigger_id and (
                watcher.last_error()
                or (snapshot.round_id and not fresh_tick(watcher, snapshot))
                or (snapshot.round_id and snapshot.round_id != armed_trigger_id
                    and snapshot.status != "waiting")
            ):
                print("SINAL DESCARTADO | janela fechada ou socket sem dados recentes | aguardando novo padrão", flush=True)
                armed_trigger_id = ""
            if (
                armed_trigger_id
                and not pending
                and snapshot.status == "waiting"
                and snapshot.round_id
                and snapshot.round_id != armed_trigger_id
                and snapshot.round_id not in entered_ids
                and fresh_tick(watcher, snapshot)
            ):
                risk = risk_status(
                    signals_path,
                    utc_now(),
                    Decimal(args.daily_stop_loss),
                    Decimal(args.daily_take_profit),
                    args.max_daily_entries,
                )
                if not risk.allowed:
                    print(f"ENTRADA BLOQUEADA | {risk.reason}", flush=True)
                    armed_trigger_id = ""
                    continue
                signal_id = f"{snapshot.round_id}:{pattern}:{cashout}"
                now_text = utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
                row = {
                    "signal_id": signal_id,
                    "detected_at": now_text,
                    "trigger_round_id": armed_trigger_id,
                    "entry_round_id": snapshot.round_id,
                    "entry_time": now_text,
                    "pattern": pattern,
                    "stake": f"{stake:.2f}",
                    "auto_cashout_at": f"{cashout:.2f}",
                    "mode": mode.lower(),
                    "status": "sending" if args.live else "paper_entered",
                    "message": "reservado antes do POST" if args.live else "entrada simulada",
                }
                append_signal(signals_path, row)
                entered_ids.add(snapshot.round_id)
                if args.live:
                    try:
                        assert api is not None
                        if not enter_in_window(api, watcher, signals_path, row,
                                               args.entry_window_seconds, args.max_entry_attempts):
                            armed_trigger_id = ""
                            continue
                    except BlazeUncertainOutcome as exc:
                        update_signal(signals_path, signal_id, {"status": "unknown", "message": str(exc)})
                        print(
                            f"BOT PAUSADO | rodada={snapshot.round_id} | {exc}. "
                            "Nenhum reenvio foi feito. Confira a conta e reconcilie o registro.",
                            flush=True,
                        )
                        return 3
                pending = row
                armed_trigger_id = ""
                session_entries += 1
                print(
                    f"ENTRADA {mode} | rodada={snapshot.round_id} | R$ {stake:.2f}"
                    f" | auto={cashout:.2f}x",
                    flush=True,
                )
                if args.max_session_entries and session_entries >= args.max_session_entries:
                    print("Limite da sessão alcançado; aguardando resultado da última entrada.", flush=True)

            if (
                args.max_session_entries
                and session_entries >= args.max_session_entries
                and not pending
            ):
                return 0
            if args.verbose and watcher.last_error():
                print(f"WS | {watcher.last_error()}", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        watcher.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bot automático por sequência para Blaze Crash")
    parser.add_argument("--live", action="store_true", help="envia apostas reais; sem isso usa paper")
    parser.add_argument("--pattern", default=DEFAULT_PATTERN)
    parser.add_argument("--stake", default="0.10")
    parser.add_argument("--auto-cashout-at", default="5.00")
    parser.add_argument("--daily-stop-loss", default="5.00")
    parser.add_argument("--daily-take-profit", default="5.00")
    parser.add_argument("--max-daily-entries", type=int, default=20)
    parser.add_argument("--max-session-entries", type=int, default=1, help="0 = contínuo")
    parser.add_argument("--signals", help="CSV do ledger; padrão separa paper e live")
    parser.add_argument("--room-id", type=int, default=4)
    parser.add_argument("--room", default=DEFAULT_CRASH_ROOM)
    parser.add_argument("--ws-url", default=DEFAULT_WS_URL)
    parser.add_argument("--http-timeout", type=float, default=20.0)
    parser.add_argument("--entry-window-seconds", type=float, default=3.0,
                        help="teto local para tentativas; sempre exige waiting recente da mesma rodada")
    parser.add_argument("--max-entry-attempts", type=int, default=3,
                        help="inclui a primeira tentativa; repete somente ConnectTimeout")
    parser.add_argument("--reconnect-seconds", type=float, default=3.0)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> None:
    raise SystemExit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
