from datetime import datetime, timezone
from decimal import Decimal

from blaze_auto.strategy import calculate_profit, encode_pattern, matches_pattern, point_label, risk_status


def test_point_bands_and_mabbm_pattern():
    assert [point_label(value) for value in [3.2, 5.0, 1.1, 1.9, 2.5]] == list("MABBM")
    points = [99, 3.2, 5.0, 1.1, 1.9, 2.5]
    assert encode_pattern(points, 5) == "MABBM"
    assert matches_pattern(points, "MABBM")


def test_profit_at_auto_cashout():
    assert calculate_profit(Decimal("0.10"), Decimal("5"), 5.0) == ("win", Decimal("0.40"))
    assert calculate_profit(Decimal("0.10"), Decimal("5"), 4.99) == ("loss", Decimal("-0.10"))


def test_empty_ledger_allows_entry(tmp_path):
    status = risk_status(
        tmp_path / "signals.csv",
        datetime(2026, 8, 26, tzinfo=timezone.utc),
        Decimal("5"),
        Decimal("5"),
        20,
    )
    assert status.allowed
    assert status.daily_entries == 0
