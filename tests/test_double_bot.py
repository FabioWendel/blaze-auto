from decimal import Decimal
from types import SimpleNamespace
import time

import pytest

from blaze_auto import double_bot, reconcile
from blaze_auto.api_client import BlazeApiError, BlazeUncertainOutcome
from blaze_auto.crash_watcher import SocketConnectionStatus
from blaze_auto.strategy import append_signal, read_signals


def event(round_id, color=1, status="complete", *, snapshot_id=None, snapshot_status=None, error="", attempts=1):
    result = dict(id=round_id, color=color, roll=0 if color == 0 else 4 if color == 1 else 10,
                  updated_at="2026-08-28T12:00:00Z")
    return dict(results=[result] if status == "complete" else [],
                round_id=snapshot_id or round_id, status=snapshot_status or status, error=error, attempts=attempts)


@pytest.fixture
def replay(tmp_path, monkeypatch):
    path = tmp_path / "double.csv"
    events = []
    calls = []
    responses = []

    class Watcher:
        stopped = False
        index = -1
        current = dict(round_id="", status="", results=[], error="", attempts=1)
        def start(self):
            pass
        def stop(self):
            self.stopped = True
        def connection_status(self):
            return SocketConnectionStatus("CONECTADO", self.current["attempts"], 0, 0,
                                          self.current["round_id"], self.current["status"])
        def pop_completed_rounds(self):
            self.index += 1
            if self.index >= len(events):
                raise KeyboardInterrupt
            self.current = events[self.index]
            return self.current["results"]
        def snapshot(self):
            return SimpleNamespace(status=self.current["status"], round_id=self.current["round_id"], received_at=time.time())
        def last_error(self):
            return self.current["error"]

    watcher = Watcher()
    monkeypatch.setattr(double_bot, "BlazeDoubleWatcher", lambda: watcher)
    monkeypatch.setattr(double_bot, "account_from_environment", lambda: object())
    monkeypatch.setattr(double_bot.SocketStatusLogger, "log_if_due", lambda *a: None)
    def enter(*args, **kwargs):
        assert read_signals(path)[-1]["status"] == "sending"
        calls.append(args)
        if responses:
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
        return {"bet": {"id": "test"}}
    monkeypatch.setattr(double_bot, "DoubleApiClient", lambda *a, **k: SimpleNamespace(enter=enter))

    def run(extra=()):
        args = double_bot.build_parser().parse_args(["--signals", str(path), "--interval", "0.0001", *extra])
        return double_bot.run(args)
    return SimpleNamespace(path=path, events=events, calls=calls, responses=responses, watcher=watcher, run=run)


def test_alternating_doubles_white_loss_and_reset_after_win(replay):
    r = replay
    r.events.extend([event("trigger"), event("a", status="waiting"), event("a", 2),
                     event("b", status="waiting"), event("b", 0),
                     event("c", status="waiting"), event("c", 1),
                     event("d", status="waiting"), event("d", 1),
                     event("e", status="waiting"), event("e", 1)])
    assert r.run() == 0
    rows = read_signals(r.path)
    assert [row["stake"] for row in rows] == ["0.10", "0.20", "0.40", "0.10"]
    assert [row["target_color"] for row in rows] == ["1", "2", "1", "1"]
    assert [row["status"] for row in rows] == ["loss", "loss", "win", "win"]
    assert [row["profit"] for row in rows] == ["-0.10", "-0.20", "0.40", "0.10"]
    assert not r.calls
    assert r.watcher.stopped


def test_max_gales_stops_instead_of_doubling_indefinitely(replay):
    r = replay
    r.events.extend([event("trigger"), event("a", status="waiting"), event("a", 2),
                     event("b", status="waiting"), event("b", 1), event("c", status="waiting")])
    assert r.run(["--max-gales", "1"]) == 0
    assert [row["stake"] for row in read_signals(r.path)] == ["0.10", "0.20"]


def test_prospective_daily_loss_prevents_next_double(replay):
    r = replay
    r.events.extend([event("trigger"), event("a", status="waiting"), event("a", 2), event("b", status="waiting")])
    assert r.run(["--stake", "1", "--daily-stop-loss", "2"]) == 0
    assert len(read_signals(r.path)) == 1


@pytest.mark.parametrize("flags", [["--max-session-entries", "1"], ["--max-daily-entries", "1"],
                                   ["--daily-take-profit", "0.10"]])
def test_limits_stop_before_next_entry_even_with_waiting_in_same_poll(replay, flags):
    r = replay
    r.events.extend([event("trigger"), event("a", status="waiting"),
                     event("a", 1, snapshot_id="b", snapshot_status="waiting"),
                     event("b", 1), event("c", status="waiting")])
    assert r.run(flags) == 0
    assert len(read_signals(r.path)) == 1


def test_closed_window_discards_signal_no_carryover_to_later_round(replay):
    r = replay
    r.events.extend([event("trigger"), event("missed", status="rolling"),
                     event("later", status="waiting"), event("later", 2)])
    assert r.run() == 0
    assert not r.path.exists()


def test_reconnection_between_trigger_and_waiting_discards_sequence(replay):
    r = replay
    r.events.extend([event("trigger"), event("a", status="waiting", attempts=2),
                     event("a", 0, attempts=2), event("b", status="waiting", attempts=2)])
    assert r.run() == 0
    assert not r.path.exists()


def test_duplicate_ticks_never_create_second_entry(replay):
    r = replay
    r.events.extend([event("trigger"), event("trigger"), event("a", status="waiting"),
                     event("a", status="waiting"), event("a", 1), event("a", 1),
                     event("b", status="waiting")])
    assert r.run() == 0
    assert len(read_signals(r.path)) == 1


def test_restart_blocks_accepted_bet_until_result_reconciled(replay, monkeypatch):
    r = replay
    r.events.extend([event("trigger"), event("a", status="waiting")])
    assert r.run(["--live"]) == 0
    assert read_signals(r.path)[0]["status"] == "entered"
    monkeypatch.setattr(double_bot, "account_from_environment", lambda: pytest.fail("must block before credentials"))
    assert r.run(["--live"]) == 3


def test_same_ledger_cannot_mix_games_or_modes(replay):
    append_signal(replay.path, dict(signal_id="crash", mode="live", status="win", profit="1.00"))
    assert replay.run(["--live"]) == 1
    assert replay.watcher.index == -1


def test_rejection_does_not_double_or_retry_unknown(replay):
    r = replay
    r.responses.extend([BlazeApiError("HTTP 401")])
    r.events.extend([event("trigger"), event("a", status="waiting"), event("a", 2),
                     event("b", status="waiting"), event("b", 1),
                     event("c", status="waiting"), event("c", 1)])
    assert r.run(["--live"]) == 0
    assert r.calls == [("0.10", 1, "a"), ("0.10", 1, "c")]
    assert [row["status"] for row in read_signals(r.path)] == ["rejected", "win"]


def test_uncertain_post_stops_and_restart_blocks_before_network(replay, monkeypatch):
    r = replay
    r.responses.append(BlazeUncertainOutcome("reset"))
    r.events.extend([event("trigger"), event("a", status="waiting")])
    assert r.run(["--live"]) == 3
    assert len(r.calls) == 1
    assert read_signals(r.path)[0]["status"] == "unknown"
    monkeypatch.setattr(double_bot, "account_from_environment", lambda: pytest.fail("no credentials/network on restart"))
    assert r.run(["--live"]) == 3


@pytest.mark.parametrize("status", ["sending", "unknown", "entered", "paper_entered"])
def test_pending_reconciliation_preserves_double_columns(tmp_path, status):
    path = tmp_path / "double.csv"
    append_signal(path, dict(signal_id="s", game="double", entry_round_id="a", status=status,
                             mode="live", target_color=2, level=1), fields=double_bot.DOUBLE_FIELDS)
    args = reconcile.build_parser().parse_args(["--signals", str(path), "--round-id", "a", "--outcome", "loss",
                                               "--profit=-0.20", "--confirmed"])
    assert reconcile.run(args) == 0
    row = read_signals(path)[0]
    assert row["target_color"] == "2" and row["level"] == "1" and row["game"] == "double"
    assert row["status"] == "loss"


@pytest.mark.parametrize("option,value", [("--stake", "NaN"), ("--stake", "Infinity"), ("--stake", "0.001"),
                                         ("--max-gales", "-1"), ("--max-gales", "11"),
                                         ("--daily-stop-loss", "0"), ("--interval", "nan"),
                                         ("--max-session-entries", "-1")])
def test_invalid_config_never_starts_watcher(replay, option, value):
    assert replay.run([option, value]) == 1
    assert replay.watcher.index == -1


@pytest.mark.parametrize("color", [1, 2])
def test_entry_plan_explicit_color_and_stake(color):
    plans = [double_bot.EntryPlan("r", "chain", color, i) for i in range(4)]
    assert all(p.color == color for p in plans)
    assert [p.stake(Decimal("1")) for p in plans] == [1, 2, 4, 8]


@pytest.mark.parametrize("color", [0, 3, True, "1"])
def test_white_or_invalid_color_cannot_be_an_entry_plan(color):
    with pytest.raises(ValueError):
        double_bot.EntryPlan("r", "chain", color)


@pytest.mark.parametrize("initial_color", [1, 2])
def test_start_from_last_color_and_alternate_after_white_loss(replay, initial_color):
    r = replay
    other = 3 - initial_color
    r.events.extend([event("trigger", initial_color), event("a", status="waiting"),
                     event("a", 0), event("b", status="waiting"), event("b", initial_color),
                     event("c", status="waiting"), event("c", initial_color)])
    assert r.run(["--live"]) == 0  # fixture mocks the API; no network or real money
    assert r.calls == [("0.10", initial_color, "a"), ("0.20", other, "b"), ("0.40", initial_color, "c")]
    rows = read_signals(r.path)
    assert [row["status"] for row in rows] == ["loss", "loss", "win"]
    assert all(row["pattern"] == "LAST_COLOR_ALTERNATING" for row in rows)


def test_initial_white_waits_for_next_nonwhite_color(replay):
    r = replay
    r.events.extend([event("initial", 0), event("a", status="waiting"), event("a", 0),
                     event("b", status="waiting"), event("b", 2), event("c", status="waiting"), event("c", 2)])
    assert r.run() == 0
    rows = read_signals(r.path)
    assert len(rows) == 1
    assert rows[0]["entry_round_id"] == "c" and rows[0]["target_color"] == "2"


def test_after_win_next_observed_color_starts_new_base_sequence(replay):
    r = replay
    r.events.extend([event("trigger", 1), event("a", status="waiting"), event("a", 1),
                     event("b", status="waiting"), event("b", 2), event("c", status="waiting"), event("c", 2)])
    assert r.run() == 0
    rows = read_signals(r.path)
    assert [(row["entry_round_id"], row["target_color"], row["stake"], row["level"]) for row in rows] == [
        ("a", "1", "0.10", "0"), ("c", "2", "0.10", "0")]


def test_rolling_color_cannot_start_a_sequence(replay):
    r = replay
    r.events.extend([event("trigger", 2, status="rolling"), event("a", status="waiting")])
    assert r.run() == 0
    assert not r.path.exists()


@pytest.mark.parametrize("color", [1, 2])
def test_http_envelope_without_nested_id_reaches_settlement(replay, monkeypatch, capsys, color):
    from blaze_auto.api_client import CrashAccount
    from blaze_auto.double import DoubleApiClient
    r = replay
    posts = []
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            assert read_signals(r.path)[-1]["status"] == "sending"
            posts.append((url, kwargs))
            return SimpleNamespace(status_code=201, json=lambda: {
                "color": color, "bet": {"amount": "0.10", "currency_type": "BRL"},
            })
    monkeypatch.setattr(double_bot, "account_from_environment", lambda: CrashAccount("fake-test-token", 123, "test", "gold"))
    monkeypatch.setattr(double_bot, "DoubleApiClient", lambda account, **kw: DoubleApiClient(account, session_factory=Session, **kw))
    r.events.extend([event("trigger", color), event("a", status="waiting"), event("a", color)])
    assert r.run(["--live"]) == 0  # Entire HTTP session is fake.
    assert len(posts) == 1
    assert read_signals(r.path)[0]["status"] == "win"
    output = capsys.readouterr().out
    assert "BOT PAUSADO" not in output
    assert "RESULTADO WIN" in output
    assert "SOCKET |" not in output


def test_unknown_outcome_reports_reason_and_round_without_retry(replay, capsys):
    r = replay
    r.events.extend([event("trigger", 1), event("a", status="waiting")])
    r.responses.append(BlazeUncertainOutcome("Double: objeto bet ausente ou inválido"))
    assert r.run(["--live"]) == 3
    assert len(r.calls) == 1
    output = capsys.readouterr().out
    assert "rodada=a" in output and "objeto bet ausente ou inválido" in output
    assert read_signals(r.path)[0]["status"] == "unknown"
