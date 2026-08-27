from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


DEFAULT_PATTERN = "MABBM"
SIGNAL_FIELDS = [
    "signal_id",
    "detected_at",
    "trigger_round_id",
    "entry_round_id",
    "entry_time",
    "pattern",
    "stake",
    "auto_cashout_at",
    "mode",
    "status",
    "result_round_id",
    "result_time",
    "crash_point",
    "profit",
    "message",
]


@dataclass(frozen=True)
class RiskStatus:
    allowed: bool
    reason: str
    daily_profit: Decimal
    daily_entries: int


def point_label(point: float) -> str:
    if point < 2:
        return "B"
    if point < 5:
        return "M"
    return "A"


def encode_pattern(points: list[float], length: int) -> str | None:
    if len(points) < length:
        return None
    return "".join(point_label(point) for point in points[-length:])


def matches_pattern(points: list[float], pattern: str = DEFAULT_PATTERN) -> bool:
    return encode_pattern(points, len(pattern)) == pattern


def calculate_profit(stake: Decimal, cashout: Decimal, crash_point: float) -> tuple[str, Decimal]:
    if Decimal(str(crash_point)) >= cashout:
        return "win", stake * (cashout - Decimal("1"))
    return "loss", -stake


def read_signals(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def append_signal(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=SIGNAL_FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in SIGNAL_FIELDS})
        stream.flush()
        os.fsync(stream.fileno())


def update_signal(path: Path, signal_id: str, updates: dict[str, Any]) -> None:
    rows = read_signals(path)
    found = False
    for row in rows:
        if row.get("signal_id") == signal_id:
            row.update({key: str(value) for key, value in updates.items()})
            found = True
            break
    if not found:
        raise KeyError(f"sinal não encontrado: {signal_id}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=SIGNAL_FIELDS)
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in SIGNAL_FIELDS} for row in rows])
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def pending_signal(path: Path) -> dict[str, str] | None:
    for row in reversed(read_signals(path)):
        if row.get("status") in {"entered", "paper_entered"}:
            return row
    return None


def uncertain_signal(path: Path) -> dict[str, str] | None:
    """Inclui errors legados: a versão anterior não distinguia rejeição de reset."""
    for row in read_signals(path):
        if row.get("mode") != "paper" and row.get("status") in {"sending", "unknown", "error"}:
            return row
    return None


def entered_round_ids(path: Path) -> set[str]:
    return {row.get("entry_round_id", "") for row in read_signals(path) if row.get("entry_round_id")}


def risk_status(
    path: Path,
    now: datetime,
    daily_stop_loss: Decimal,
    daily_take_profit: Decimal,
    max_daily_entries: int,
) -> RiskStatus:
    day = now.astimezone(timezone.utc).date().isoformat()
    rows = [row for row in read_signals(path) if (row.get("entry_time") or "").startswith(day)]
    profit = sum((Decimal(row["profit"]) for row in rows if row.get("profit")), Decimal("0"))
    entries = len([row for row in rows if row.get("status") not in {"rejected", "not_placed"}])
    uncertain = uncertain_signal(path)
    if uncertain:
        return RiskStatus(False, f"entrada incerta na rodada {uncertain['entry_round_id']}", profit, entries)
    if daily_stop_loss and profit <= -abs(daily_stop_loss):
        return RiskStatus(False, f"stop-loss diário atingido ({profit})", profit, entries)
    if daily_take_profit and profit >= abs(daily_take_profit):
        return RiskStatus(False, f"stop-gain diário atingido ({profit})", profit, entries)
    if max_daily_entries and entries >= max_daily_entries:
        return RiskStatus(False, f"limite diário de {max_daily_entries} entradas atingido", profit, entries)
    return RiskStatus(True, "ok", profit, entries)
