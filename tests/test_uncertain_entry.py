from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import time

import pytest

from blaze_auto import auto_bot, reconcile
from blaze_auto.api_client import BlazeUncertainOutcome
from blaze_auto.crash_watcher import SocketConnectionStatus
from blaze_auto.strategy import append_signal, entered_round_ids, read_signals, risk_status, uncertain_signal


def unresolved(path, status="unknown", round_id="uncertain-round"):
    append_signal(path, {
        "signal_id": round_id + ":MABBM:5.00", "entry_round_id": round_id,
        "entry_time": "2026-08-26T23:00:00Z", "stake": "1.00",
        "auto_cashout_at": "5.00", "mode": "live", "status": status,
    })


@pytest.mark.parametrize("status", ["unknown", "sending", "error"])
def test_restart_blocks_before_any_network_even_after_day_changes(tmp_path, monkeypatch, status):
    path = tmp_path / "signals.csv"
    unresolved(path, status)
    def no_network(*args, **kwargs):
        pytest.fail("must not connect when an unresolved entry exists")
    monkeypatch.setattr(auto_bot, "bootstrap_rounds", no_network)
    monkeypatch.setattr(auto_bot, "account_from_environment", no_network)
    args = auto_bot.build_parser().parse_args(["--live", "--signals", str(path)])
    assert auto_bot.run(args) == 3
    assert read_signals(path)[0]["status"] == status
    risk = risk_status(path, datetime(2026, 8, 27, tzinfo=timezone.utc), Decimal("5"), Decimal("5"), 20)
    assert not risk.allowed


def test_reset_stops_loop_and_persists_unknown(tmp_path, monkeypatch):
    path = tmp_path / "signals.csv"
    history = [{"id": str(i), "crash_point": point} for i, point in enumerate([3, 6, 1, 1, 3])]
    monkeypatch.setattr(auto_bot, "bootstrap_rounds", lambda *args: history)
    monkeypatch.setattr(auto_bot, "account_from_environment", lambda: object())
    calls = []
    class Api:
        def __init__(self, *args, **kwargs):
            pass
        def enter(self, *args, **kwargs):
            # Reservation must already exist before the request leaves.
            assert read_signals(path)[0]["status"] == "sending"
            calls.append(args)
            raise BlazeUncertainOutcome("reset")
    class Watcher:
        stopped = False
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass
        def stop(self):
            Watcher.stopped = True
        def pop_completed_rounds(self):
            return []
        def snapshot(self):
            return SimpleNamespace(status="waiting", round_id="next-round", received_at=time.time())
        def last_error(self):
            return ""
        def connection_status(self):
            return SocketConnectionStatus("CONECTADO", 1, 0, 0, "next-round", "waiting")
    monkeypatch.setattr(auto_bot, "CrashApiClient", Api)
    monkeypatch.setattr(auto_bot, "BlazeCrashWatcher", Watcher)
    args = auto_bot.build_parser().parse_args(["--live", "--signals", str(path), "--max-session-entries", "0"])
    assert auto_bot.run(args) == 3
    assert len(calls) == 1
    assert Watcher.stopped
    row = read_signals(path)[0]
    assert row["status"] == "unknown"
    assert row["profit"] == ""


def test_reconciliation_requires_confirmation_and_preserves_round_guard(tmp_path):
    path = tmp_path / "signals.csv"
    unresolved(path, "error")
    command = ["--signals", str(path), "--round-id", "uncertain-round", "--outcome", "not-placed"]
    assert reconcile.run(reconcile.build_parser().parse_args(command)) == 2
    assert uncertain_signal(path)
    assert reconcile.run(reconcile.build_parser().parse_args(command + ["--confirmed"])) == 0
    assert uncertain_signal(path) is None
    assert "uncertain-round" in entered_round_ids(path)
    row = read_signals(path)[0]
    assert row["status"] == "not_placed"
    assert row["profit"] == "0.00"


def test_confirmed_loss_counts_in_daily_limits(tmp_path):
    path = tmp_path / "signals.csv"
    unresolved(path)
    command = ["--signals", str(path), "--round-id", "uncertain-round", "--outcome", "loss", "--profit=-1.00", "--confirmed"]
    assert reconcile.run(reconcile.build_parser().parse_args(command)) == 0
    risk = risk_status(path, datetime(2026, 8, 26, tzinfo=timezone.utc), Decimal("1"), Decimal("5"), 20)
    assert not risk.allowed
    assert risk.daily_entries == 1
    assert risk.daily_profit == Decimal("-1.00")
