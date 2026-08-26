from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


FIELDS = ["id", "updated_at", "crash_point", "is_bonus_round", "total_bets_placed"]


def load_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open("r", newline="", encoding="utf-8") as stream:
        return {row["id"] for row in csv.DictReader(stream) if row.get("id")}


def append_round(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in FIELDS})
