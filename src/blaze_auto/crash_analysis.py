"""Backtest descritivo, offline, usando as mesmas faixas e lucro do bot.

Não seleciona configurações para apostas reais. O CSV original fica intacto.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .strategy import calculate_profit, point_label


PATTERNS = ("B", "BB", "BBB", "BBBB", "BM", "BBM", "BBBM", "BBBBM")
CASHOUTS = ("1.20", "1.30", "1.50", "2.00")


@dataclass(frozen=True)
class Round:
    id: str
    time: datetime
    point: Decimal


def load_rounds(path: Path) -> list[Round]:
    result = []
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for line, row in enumerate(csv.DictReader(stream), start=2):
            try:
                point = Decimal(row["crash_point"])
                time = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                if (not row["id"] or row["id"] in seen or row["status"] != "complete"
                        or not point.is_finite() or point < 0 or time.tzinfo is None):
                    raise ValueError
                result.append(Round(row["id"], time.astimezone(timezone.utc), point))
                seen.add(row["id"])
            except (KeyError, ValueError, ArithmeticError) as exc:
                raise ValueError(f"Registro inválido/duplicado na linha {line}; análise cancelada.") from exc
    result.sort(key=lambda row: row.time)
    if any(a.time == b.time for a, b in zip(result, result[1:])):
        raise ValueError("Rodadas distintas com horário igual: ordem ambígua.")
    if not result:
        raise ValueError("Histórico vazio.")
    return result


def summarize(trades: list[tuple[Round, Decimal]], days: float) -> dict:
    profit = peak = drawdown = Decimal(0)
    wins = streak = longest = 0
    for _, value in trades:
        profit += value
        peak = max(peak, profit)
        drawdown = max(drawdown, peak - profit)
        wins += value > 0
        streak = 0 if value > 0 else streak + 1
        longest = max(longest, streak)
    n = len(trades)
    return {
        "entries": n, "wins": wins, "losses": n - wins,
        "opportunities_per_24h": round(n / days, 3) if days > 0 else None,
        "win_rate_pct": round(100 * wins / n, 4) if n else None,
        "profit_units": float(profit),
        "roi_pct": round(float(profit) * 100 / n, 4) if n else None,
        "max_drawdown_units": float(drawdown), "longest_loss_streak": longest,
    }


def limited_trades(trades: list[tuple[Round, Decimal]]) -> list[tuple[Round, Decimal]]:
    """Ilustração: stake 1, perda/ganho 5, 20 entradas por dia UTC.

    Não reproduz latência, recusas nem reinícios. Limites sobre o saldo
    realizado, como risk_status do Crash; a última perda pode ultrapassar 5.
    """
    state = {}
    accepted = []
    for row, profit in trades:
        day = row.time.date()
        n, balance = state.get(day, (0, Decimal(0)))
        if n >= 20 or balance >= 5 or balance <= -5:
            continue
        accepted.append((row, profit))
        state[day] = n + 1, balance + profit
    return accepted


def evaluate(rounds: list[Round], pattern: str, cashout: str, start: datetime, end: datetime) -> dict:
    history = ""
    trades = []
    for row in rounds:
        # Test BEFORE appending this result: this is the NEXT round, not the trigger.
        if start <= row.time < end and (not pattern or history.endswith(pattern)):
            _, profit = calculate_profit(Decimal(1), Decimal(cashout), row.point)
            trades.append((row, profit))
        history = (history + point_label(row.point))[-max(1, len(pattern)):]
    days = (end - start).total_seconds() / 86400
    return {"raw": summarize(trades, days),
            "limited_illustration": summarize(limited_trades(trades), days)}


def analyze(path: Path) -> dict:
    from datetime import timedelta

    rounds = load_rounds(path)
    dates = sorted({row.time.date() for row in rounds})
    if len(dates) < 5:
        raise ValueError("Use pelo menos cinco dias para separar treino/validação/teste.")
    first_cut = datetime.combine(dates[int(len(dates) * .6)], datetime.min.time(), timezone.utc)
    last_cut = datetime.combine(dates[int(len(dates) * .8)], datetime.min.time(), timezone.utc)
    end = rounds[-1].time + timedelta(microseconds=1)
    ranges = {"train": (rounds[0].time, first_cut), "validation": (first_cut, last_cut),
              "test": (last_cut, end), "all": (rounds[0].time, end)}
    candidates = [(p, c) for p in PATTERNS for c in CASHOUTS]
    configs = candidates + [("MABBM", "5.00")] + [("", c) for c in (*CASHOUTS, "5.00")]
    results = []
    for pattern, cashout in configs:
        results.append({"pattern": pattern or "ANY", "cashout": cashout,
                        "breakeven_win_rate_pct": 100 / float(cashout),
                        **{name: evaluate(rounds, pattern, cashout, *bounds)
                           for name, bounds in ranges.items()}})
    # Frozen criterion: highest train ROI, at least 500 train opportunities.
    eligible = [r for r in results[:len(candidates)] if r["train"]["raw"]["entries"] >= 500]
    selected = max(eligible, key=lambda r: r["train"]["raw"]["roi_pct"], default=None)
    gaps = [(b.time - a.time).total_seconds() for a, b in zip(rounds, rounds[1:])]
    return {
        "source": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "records": len(rounds), "zeros_preserved": sum(r.point == 0 for r in rounds),
        "max_gap_seconds": max(gaps, default=0), "gaps_over_300_seconds": sum(g > 300 for g in gaps),
        "splits_utc": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in ranges.items()},
        "selection": {"candidates": len(candidates), "criterion": "train ROI; >=500 entries",
                      "pattern": selected["pattern"] if selected else None,
                      "cashout": selected["cashout"] if selected else None},
        "limitations": [
            "Retrospectivo: este histórico já foi explorado anteriormente; não é teste prospectivo.",
            "32 comparações exploratórias; ROI positivo isolado não demonstra vantagem estatística.",
            "Entrada ideal na próxima rodada; sem atrasos, recusas, arredondamento monetário ou bônus.",
            "Sinais podem se sobrepor, mas há no máximo uma aposta por rodada; stake fixa 1 unidade.",
            "Sem prova de cobertura contínua: lacunas podem ocultar rodadas; não são removidas.",
            "Contagens por 24h são oportunidades brutas, não promessa de entradas executadas.",
        ],
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/crash_history_30d.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/crash_analysis/report.json"))
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("a saída não pode substituir o histórico")
    report = analyze(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Análise offline: {report['records']} rodadas | relatório: {args.output}")
    print("Seleção apenas no treino:", report["selection"])
    print("Padrão / retirada | sinais/dia | ROI treino / validação / teste")
    for row in report["results"]:
        rois = " / ".join(str(row[part]["raw"]["roi_pct"]) + "%" for part in ("train", "validation", "test"))
        print(f"{row['pattern']:5} {row['cashout']}x | {row['all']['raw']['opportunities_per_24h']:8} | {rois}")


if __name__ == "__main__":
    main()
