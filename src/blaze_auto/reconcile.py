"""Conferência manual de tentativa incerta; não chama a API nem aposta."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .strategy import read_signals, update_signal


def run(args: argparse.Namespace) -> int:
    if not args.confirmed:
        print("BLOQUEADO: confira o histórico/saldo na Blaze e informe --confirmed.")
        return 2
    path = Path(args.signals)
    matches = [row for row in read_signals(path) if row.get("entry_round_id") == args.round_id]
    if len(matches) != 1 or matches[0].get("status") not in {"sending", "unknown", "error"}:
        print("ERRO: é necessário exatamente um registro incerto para essa rodada.")
        return 1
    row = matches[0]
    try:
        if args.outcome == "not-placed":
            if args.profit is not None:
                raise ValueError("não informe --profit para uma aposta não registrada")
            profit = Decimal("0")
        else:
            if args.profit is None:
                raise ValueError("informe --profit com o lucro líquido confirmado na conta")
            profit = Decimal(args.profit)
            if not profit.is_finite() or profit != profit.quantize(Decimal("0.01")):
                raise ValueError("lucro deve ser finito e ter até duas casas decimais")
            if (args.outcome == "win" and profit <= 0) or (args.outcome == "loss" and profit >= 0):
                raise ValueError("o sinal do lucro não corresponde ao resultado informado")
    except (InvalidOperation, ValueError) as exc:
        print(f"ERRO: {exc}")
        return 1
    timestamp = datetime.now(timezone.utc).isoformat()
    update_signal(path, row["signal_id"], {
        "status": "not_placed" if args.outcome == "not-placed" else args.outcome,
        "profit": f"{profit:.2f}",
        "message": f"{row.get('message', '')} | conferência manual {timestamp}: {args.outcome}",
    })
    print("Registro reconciliado. Nenhuma aposta foi enviada. O ID permanece protegido contra duplicação.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", default="data/auto_live_signals.csv")
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--outcome", choices=["not-placed", "win", "loss"], required=True)
    parser.add_argument("--profit", help="lucro líquido confirmado; perda deve ser negativa")
    parser.add_argument("--confirmed", action="store_true", help="confirma que você conferiu a conta")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
