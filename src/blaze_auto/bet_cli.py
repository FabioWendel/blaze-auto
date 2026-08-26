from __future__ import annotations

import argparse
import json
import os
import time

from dotenv import load_dotenv

from .api_client import BlazeApiError, CrashAccount, CrashApiClient
from .crash_watcher import DEFAULT_CRASH_ROOM, DEFAULT_WS_URL, BlazeCrashWatcher


def account_from_environment() -> CrashAccount:
    load_dotenv()
    required = {
        "BLAZE_AUTHORIZATION": os.getenv("BLAZE_AUTHORIZATION", ""),
        "BLAZE_WALLET_ID": os.getenv("BLAZE_WALLET_ID", ""),
        "BLAZE_USERNAME": os.getenv("BLAZE_USERNAME", ""),
        "BLAZE_RANK": os.getenv("BLAZE_RANK", ""),
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise ValueError(
            f"variáveis obrigatórias ausentes: {', '.join(missing)}. "
            "Configure-as no PowerShell ou no arquivo .env da pasta do projeto"
        )
    try:
        wallet_id = int(required["BLAZE_WALLET_ID"])
        room_id = int(os.getenv("BLAZE_ROOM_ID", "4"))
    except ValueError:
        raise ValueError("BLAZE_WALLET_ID e BLAZE_ROOM_ID devem ser inteiros") from None
    return CrashAccount(
        authorization=required["BLAZE_AUTHORIZATION"],
        wallet_id=wallet_id,
        username=required["BLAZE_USERNAME"],
        rank=required["BLAZE_RANK"],
        room_id=room_id,
        session_id=os.getenv("BLAZE_SESSION_ID", ""),
        client_version=os.getenv("BLAZE_CLIENT_VERSION", ""),
    )


def wait_for_waiting_round(args: argparse.Namespace) -> str:
    watcher = BlazeCrashWatcher(
        url=args.ws_url,
        room=args.room,
        reconnect_seconds=3.0,
        bets_room=None,
    )
    watcher.start()
    deadline = time.monotonic() + args.wait_timeout
    try:
        while time.monotonic() < deadline:
            snapshot = watcher.snapshot()
            if snapshot.status == "waiting" and snapshot.round_id:
                return snapshot.round_id
            error = watcher.last_error()
            if error:
                print(f"WS aguardando reconexão: {error}", flush=True)
            time.sleep(0.05)
    finally:
        watcher.stop()
    raise TimeoutError("nenhuma rodada em waiting apareceu dentro do tempo limite")


def run(args: argparse.Namespace) -> int:
    if not args.live:
        print("BLOQUEADO: use --live para autorizar uma transação real.")
        return 2
    try:
        account = account_from_environment()
        client = CrashApiClient(account, timeout=args.http_timeout)
        if args.command == "enter":
            round_id = args.round_id or wait_for_waiting_round(args)
            mode = f"auto cashout {args.auto_cashout_at}x" if args.auto_cashout_at else "cashout manual"
            print(f"Enviando entrada real | rodada={round_id} | R$ {args.amount} | {mode}", flush=True)
            result = client.enter(args.amount, round_id, args.auto_cashout_at)
        else:
            print("Enviando cashout real...", flush=True)
            result = client.cashout()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BlazeApiError, TimeoutError, ValueError) as exc:
        print(f"ERRO: {exc}", flush=True)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Entrada e cashout reais no Blaze Crash")
    parser.add_argument("--live", action="store_true", help="autoriza a chamada POST real")
    parser.add_argument("--http-timeout", type=float, default=15.0)
    subparsers = parser.add_subparsers(dest="command", required=True)

    enter = subparsers.add_parser("enter", help="entra na próxima rodada waiting")
    enter.add_argument("--amount", required=True, help="valor em BRL, por exemplo 0.10")
    enter.add_argument("--auto-cashout-at", help="multiplicador, por exemplo 5.00; omita para manual")
    enter.add_argument("--round-id", help="ID explícito; por padrão detecta a rodada waiting")
    enter.add_argument("--wait-timeout", type=float, default=30.0)
    enter.add_argument("--ws-url", default=DEFAULT_WS_URL)
    enter.add_argument("--room", default=DEFAULT_CRASH_ROOM)

    subparsers.add_parser("cashout", help="retira a entrada aberta")
    return parser


def main() -> None:
    raise SystemExit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
