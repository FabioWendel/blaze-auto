import time
from types import SimpleNamespace

import pytest

from blaze_auto import auto_bot
from blaze_auto.crash_presets import resolve_preset
from blaze_auto.strategy import read_signals


@pytest.mark.parametrize("argv,expected", [
    ([], ("MABBM", "5.00")),
    (["--preset", "baixas-media"], ("BBBBM", "1.50")),
    (["--preset", "baixas-media", "--pattern", "bb", "--auto-cashout-at", "5.00"], ("BB", "5.00")),
    (["--pattern", "BBBB"], ("BBBB", "5.00")),
])
def test_preset_defaults_and_explicit_overrides(argv, expected):
    args = auto_bot.build_parser().parse_args(argv)
    assert resolve_preset(args.preset, args.pattern, args.auto_cashout_at) == expected
    assert not args.live
    assert args.socket_log_interval == 0
    assert args.max_daily_entries == 20


@pytest.mark.parametrize("option", ["--stake", "--auto-cashout-at"])
@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_non_finite_amounts_rejected_before_network(option, value, monkeypatch):
    monkeypatch.setattr(auto_bot, "bootstrap_rounds", lambda *a: pytest.fail("no network"))
    assert auto_bot.run(auto_bot.build_parser().parse_args([option, value])) == 1


def test_experimental_preset_enters_next_round_once_and_settles_paper(tmp_path, monkeypatch, capsys):
    path = tmp_path / "signals.csv"
    monkeypatch.setattr(auto_bot, "bootstrap_rounds", lambda *a: [
        {"id": str(i), "crash_point": p} for i, p in enumerate([1, 1, 1, 1, 3])])
    monkeypatch.setattr(auto_bot, "account_from_environment", lambda: pytest.fail("paper must not read account"))
    class Watcher:
        poll = 0
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass
        def stop(self):
            pass
        def last_error(self):
            return ""
        def pop_completed_rounds(self):
            self.poll += 1
            if self.poll == 1:
                return []
            assert self.poll == 2
            return [{"id": "entry", "crash_point": 1.5, "updated_at": "2026-08-28T12:00:00Z"}]
        def snapshot(self):
            return SimpleNamespace(status="waiting", round_id="entry", received_at=time.time())
    monkeypatch.setattr(auto_bot, "BlazeCrashWatcher", Watcher)
    args = auto_bot.build_parser().parse_args(["--preset", "baixas-media", "--signals", str(path), "--interval", "0.001"])
    assert auto_bot.run(args) == 0
    rows = read_signals(path)
    assert len(rows) == 1
    assert rows[0]["pattern"] == "BBBBM"
    assert rows[0]["auto_cashout_at"] == "1.50"
    assert rows[0]["trigger_round_id"] == "4"
    assert rows[0]["entry_round_id"] == "entry"
    assert rows[0]["status"] == "win"
    assert rows[0]["profit"] == "0.05"
    assert "EXPERIMENTAL" in capsys.readouterr().out
