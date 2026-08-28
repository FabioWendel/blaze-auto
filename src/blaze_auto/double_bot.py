"""Double: última cor inicia; perdas dobram a entrada e alternam a cor."""
from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .api_client import BlazeUncertainOutcome
from .auto_bot import enter_in_window, fresh_tick, signal_discard_reason, utc_now
from .bet_cli import account_from_environment
from .double import BlazeDoubleWatcher, COLOR_NAMES, DoubleApiClient
from .socket_status import SocketStatusLogger
from .strategy import (SIGNAL_FIELDS, append_signal, entered_round_ids, pending_signal,
                       read_signals, risk_status, uncertain_signal, update_signal)


DOUBLE_FIELDS = SIGNAL_FIELDS + ["game", "chain_id", "level", "target_color", "result_color", "roll"]


@dataclass(frozen=True)
class EntryPlan:
    trigger_id: str
    chain_id: str
    color: int
    level: int = 0

    def __post_init__(self) -> None:
        if type(self.color) is not int or self.color not in (1, 2):
            raise ValueError("a sequência deve apostar em vermelho ou preto")

    def stake(self, base: Decimal) -> Decimal:
        return base * (2 ** self.level)


def money(value: str) -> Decimal:
    try:
        number = Decimal(value)
        if not number.is_finite() or number < Decimal("0.01") or number != number.quantize(Decimal("0.01")):
            raise ValueError
        return number
    except (InvalidOperation, ValueError):
        raise ValueError("valores devem ser positivos, finitos e ter até 2 casas decimais") from None


def validate(args: argparse.Namespace) -> tuple[Decimal, Decimal, Decimal]:
    base, loss, gain = money(args.stake), money(args.daily_stop_loss), money(args.daily_take_profit)
    if not 0 <= args.max_gales <= 10:
        raise ValueError("--max-gales deve estar entre 0 e 10 (0 = nenhuma dobragem)")
    if args.max_daily_entries < 1 or args.max_session_entries < 0:
        raise ValueError("limite diário deve ser positivo; limite da sessão deve ser >= 0")
    if args.max_entry_attempts < 1:
        raise ValueError("--max-entry-attempts deve ser positivo")
    for value in (args.interval, args.http_timeout, args.entry_window_seconds):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("intervalos e prazos devem ser positivos e finitos")
    if not math.isfinite(args.socket_log_interval) or args.socket_log_interval < 0:
        raise ValueError("--socket-log-interval deve ser finito e >= 0 (0 desliga)")
    return base, loss, gain


def run(args: argparse.Namespace) -> int:
    try:
        base, stop_loss, take_profit = validate(args)
    except ValueError as exc:
        print(f"ERRO: {exc}", flush=True)
        return 1
    path = Path(args.signals or f"data/double_{'live' if args.live else 'paper'}_signals.csv")
    # Never infer acceptance from a public result after a restart. Also block
    # accepted bets whose result was not observed before interruption.
    if uncertain_signal(path) or pending_signal(path):
        print(f"BLOQUEADO: existe aposta pendente/incerta em {path}. Confira a conta e use blaze_auto.reconcile.", flush=True)
        return 3
    existing = read_signals(path)
    if any(row.get("game") != "double" or row.get("mode") != ("live" if args.live else "paper") for row in existing):
        print("BLOQUEADO: use um ledger exclusivo para este jogo e modo.", flush=True)
        return 1
    try:
        api = DoubleApiClient(account_from_environment(), timeout=args.http_timeout) if args.live else None
    except ValueError as exc:
        print(f"ERRO: {exc}", flush=True)
        return 1

    watcher = BlazeDoubleWatcher()
    logger = SocketStatusLogger(args.socket_log_interval, "double.tick")
    plan: EntryPlan | None = None
    pending: dict[str, Any] | None = None
    entered_ids = entered_round_ids(path)
    seen_ids: set[str] = set()
    session_entries = 0
    connection_attempt = 0
    mode = "LIVE" if args.live else "PAPER"
    print(f"DOUBLE {mode} | repete a última cor concluída (vermelho/preto); perdeu → dobra e alterna", flush=True)
    print(f"Base R$ {base:.2f} | máximo {args.max_gales} dobragens | perda da sequência completa "
          f"R$ {base * (2 ** (args.max_gales + 1) - 1):.2f} (limites podem interromper antes)", flush=True)
    print(f"Stop-loss R$ {stop_loss:.2f} | stop-gain R$ {take_profit:.2f} | "
          f"até {args.max_daily_entries} entradas/dia UTC | ledger: {path}", flush=True)
    print("Aguardando NOVO resultado vermelho/preto. Branco não inicia sequência e conta como perda em aposta aberta. "
          "Sem proteção no branco. Ganho reinicia a espera.", flush=True)
    watcher.start()
    try:
        while True:
            logger.log_if_due(watcher)
            health = watcher.connection_status()
            if plan and (health.attempts != connection_attempt or health.state != "CONECTADO" or watcher.last_error()):
                print("SEQUÊNCIA DESCARTADA | conexão interrompida | aguardando novo vermelho/preto", flush=True)
                plan = None
            connection_attempt = health.attempts
            for result in watcher.pop_completed_rounds():
                round_id = result["id"]
                if round_id in seen_ids:
                    continue
                seen_ids.add(round_id)
                if pending:
                    if pending["entry_round_id"] != round_id:
                        # Missing our result must never authorize another bet.
                        continue
                    won = int(pending["target_color"]) == result["color"]
                    profit = Decimal(pending["stake"]) * (1 if won else -1)
                    update_signal(path, pending["signal_id"], {
                        "status": "win" if won else "loss", "profit": f"{profit:.2f}",
                        "result_round_id": round_id, "result_time": result["updated_at"],
                        "result_color": result["color"], "roll": result["roll"],
                        "message": "resultado calculado pela cor; confira o saldo na conta",
                    })
                    print(f"RESULTADO {'WIN' if won else 'LOSS'} | veio {COLOR_NAMES[result['color']]} "
                          f"({result['roll']}) | lucro R$ {profit:.2f}", flush=True)
                    level, chain_id = int(pending["level"]), pending["chain_id"]
                    next_color = 3 - int(pending["target_color"])
                    pending = None
                    if won:
                        plan = None  # Wait a later red/black; don't reuse the winning result.
                        print("SEQUÊNCIA ENCERRADA | valor inicial restaurado | aguardando novo vermelho/preto", flush=True)
                    elif level >= args.max_gales:
                        print("PARADO | limite de dobragens atingido. Nenhuma nova entrada.", flush=True)
                        return 0
                    else:
                        plan = EntryPlan(round_id, chain_id, next_color, level + 1)
                    continue
                if plan:
                    print("SEQUÊNCIA DESCARTADA | a próxima rodada terminou sem entrada", flush=True)
                    plan = None
                if result["color"] in (1, 2) and round_id not in entered_ids:
                    plan = EntryPlan(round_id, round_id, result["color"])
                    print(f"GATILHO {COLOR_NAMES[plan.color].upper()} | rodada={round_id} "
                          "| repetindo a cor na próxima abertura", flush=True)

            if args.max_session_entries and session_entries >= args.max_session_entries and not pending:
                return 0
            if not pending:
                risk = risk_status(path, utc_now(), stop_loss, take_profit, args.max_daily_entries)
                if not risk.allowed:
                    print(f"PARADO | {risk.reason}", flush=True)
                    return 0
            snapshot = watcher.snapshot()
            if plan:
                current_health = watcher.connection_status()
                reason = ("conexão mudou durante a leitura" if current_health.attempts != connection_attempt
                          or current_health.state != "CONECTADO"
                          else signal_discard_reason(watcher, snapshot, plan.trigger_id))
                if reason:
                    print(f"SEQUÊNCIA DESCARTADA | {reason} | aguardando novo vermelho/preto", flush=True)
                    plan = None
            if (plan and not pending and snapshot.status == "waiting" and snapshot.round_id
                    and snapshot.round_id != plan.trigger_id and snapshot.round_id not in entered_ids
                    and fresh_tick(watcher, snapshot)):
                stake = plan.stake(base)
                # Refuse a doubling that would take the daily net loss beyond
                # its budget, instead of checking only after losing the money.
                if risk.daily_profit - stake < -stop_loss:
                    print(f"PARADO | próxima entrada R$ {stake:.2f} ultrapassaria o stop-loss diário", flush=True)
                    return 0
                now = utc_now().isoformat(timespec="milliseconds")
                row = {
                    "signal_id": f"double:{snapshot.round_id}", "game": "double",
                    "entry_round_id": snapshot.round_id, "entry_time": now, "detected_at": now,
                    "trigger_round_id": plan.trigger_id, "chain_id": plan.chain_id,
                    "level": plan.level, "target_color": plan.color, "pattern": "LAST_COLOR_ALTERNATING",
                    "stake": f"{stake:.2f}", "mode": mode.lower(),
                    "status": "sending" if args.live else "paper_entered",
                    "message": "reservado antes do POST" if args.live else "entrada simulada",
                }
                append_signal(path, row, fields=DOUBLE_FIELDS)
                entered_ids.add(snapshot.round_id)
                if api is not None:
                    try:
                        def send_entry(timeout: float) -> Any:
                            reply = api.enter(row["stake"], int(row["target_color"]),
                                              row["entry_round_id"], timeout=timeout)
                            current = watcher.snapshot()
                            if (current.round_id != row["entry_round_id"] or current.status != "waiting"
                                    or not fresh_tick(watcher, current)):
                                raise BlazeUncertainOutcome("Janela mudou durante o POST; confira a conta")
                            return reply
                        accepted = enter_in_window(api, watcher, path, row, args.entry_window_seconds,
                                                   args.max_entry_attempts, send_entry=send_entry)
                    except BlazeUncertainOutcome as exc:
                        update_signal(path, row["signal_id"], {"status": "unknown", "message": str(exc)})
                        print(f"BOT PAUSADO | rodada={row['entry_round_id']} | {exc} | "
                              "sem reenvio. Confira a conta e reconcilie.", flush=True)
                        return 3
                    if not accepted:
                        plan = None  # A rejected bet isn't a loss and can't double.
                        continue
                pending = row
                plan = None
                session_entries += 1
                print(f"ENTRADA {mode} | rodada={row['entry_round_id']} | {COLOR_NAMES[int(row['target_color'])]} "
                      f"| R$ {stake:.2f} | dobragem={row['level']}/{args.max_gales}", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        if pending:
            print("Interrompido com aposta pendente. Confira o resultado e reconcilie antes de reiniciar.", flush=True)
        return 0
    finally:
        watcher.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="autoriza apostas reais; padrão é simulação")
    parser.add_argument("--stake", default="0.10")
    parser.add_argument("--max-gales", type=int, default=3, help="0 a 10; máximo de dobragens após a entrada inicial")
    parser.add_argument("--daily-stop-loss", default="5.00")
    parser.add_argument("--daily-take-profit", default="5.00")
    parser.add_argument("--max-daily-entries", type=int, default=20)
    parser.add_argument("--max-session-entries", type=int, default=0, help="0 = contínuo; inclui dobragens")
    parser.add_argument("--signals", help="CSV exclusivo para jogo e modo")
    parser.add_argument("--http-timeout", type=float, default=10.0)
    parser.add_argument("--entry-window-seconds", type=float, default=3.0)
    parser.add_argument("--max-entry-attempts", type=int, default=3)
    parser.add_argument("--socket-log-interval", type=float, default=0.0,
                        help="mostra saúde do socket a cada N segundos; padrão 0 = desligado")
    parser.add_argument("--interval", type=float, default=0.05)
    return parser


def main() -> None:
    raise SystemExit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
