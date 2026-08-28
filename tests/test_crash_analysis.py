import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from blaze_auto.crash_analysis import Round, analyze, evaluate, limited_trades, load_rounds


START = datetime(2026, 7, 1, tzinfo=timezone.utc)


def rounds(points):
    return [Round(str(i), START + timedelta(minutes=i), Decimal(str(p))) for i, p in enumerate(points)]


def write_csv(path, points):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "status", "created_at", "crash_point"])
        for row in points:
            writer.writerow([row.id, "complete", row.time.isoformat(), row.point])


def test_enters_after_medium_not_on_medium_result():
    result = evaluate(rounds([1, 1, 1, 1, 3, 0]), "BBBBM", "1.50", START, START + timedelta(days=1))
    assert result["raw"]["entries"] == 1
    assert result["raw"]["wins"] == 0
    assert result["raw"]["profit_units"] == -1


def test_trigger_without_following_round_is_not_trade():
    assert evaluate(rounds([1, 1, 1, 1, 3]), "BBBBM", "1.50", START,
                    START + timedelta(days=1))["raw"]["entries"] == 0


def test_exact_cashout_wins_and_zero_loses():
    raw = evaluate(rounds([1, 1.5, 0]), "B", "1.50", START, START + timedelta(days=1))["raw"]
    assert (raw["entries"], raw["wins"], raw["profit_units"]) == (2, 1, -.5)


def test_split_uses_prior_context_without_counting_trigger_or_future():
    rows = rounds([1, 1, 1, 1, 3, 1.5, 10])
    raw = evaluate(rows, "BBBBM", "1.50", rows[5].time, rows[6].time)["raw"]
    assert (raw["entries"], raw["profit_units"]) == (1, .5)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1"])
def test_loader_rejects_invalid_points(tmp_path, bad):
    path = tmp_path / "history.csv"
    write_csv(path, rounds([bad]))
    with pytest.raises(ValueError):
        load_rounds(path)


def test_loader_rejects_duplicate_rounds(tmp_path):
    path = tmp_path / "history.csv"
    rows = rounds([0])
    write_csv(path, rows + rows)
    with pytest.raises(ValueError):
        load_rounds(path)


def test_loader_sorts_and_preserves_zero(tmp_path):
    path = tmp_path / "history.csv"
    rows = rounds([0, 2])
    write_csv(path, rows[::-1])
    assert load_rounds(path) == rows


def test_limited_replay_stops_at_loss_and_resets_next_day():
    trades = [(row, Decimal(-1)) for row in rounds([0] * 10)]
    tomorrow = Round("tomorrow", START + timedelta(days=1), Decimal(2))
    result = limited_trades(trades + [(tomorrow, Decimal(1))])
    assert len(result) == 6
    assert result[-1][0] == tomorrow


def test_limited_replay_caps_entries_per_day():
    trades = [(row, Decimal(".1") if i % 2 else Decimal("-.1"))
              for i, row in enumerate(rounds([2] * 30))]
    assert len(limited_trades(trades)) == 20


def test_analysis_is_reproducible_disjoint_and_does_not_modify_source(tmp_path):
    path = tmp_path / "history.csv"
    rows = [Round(str(i), START + timedelta(hours=i), Decimal("1.5")) for i in range(24 * 10)]
    write_csv(path, rows)
    before = path.read_bytes()
    report = analyze(path)
    assert report == analyze(path)
    assert path.read_bytes() == before
    assert report["selection"]["candidates"] == 32
    for result in report["results"]:
        assert sum(result[part]["raw"]["entries"] for part in ("train", "validation", "test")) == result["all"]["raw"]["entries"]
