from types import SimpleNamespace

import pytest

from blaze_auto import auto_bot
from blaze_auto.api_client import BlazeApiError, BlazeEntryNotSent, BlazeUncertainOutcome
from blaze_auto.crash_watcher import SocketConnectionStatus
from blaze_auto.strategy import append_signal, read_signals


class Clock:
    seconds = 0.0

    def time(self):
        return 1000 + self.seconds

    def monotonic(self):
        return self.seconds

    def sleep(self, seconds):
        self.seconds += seconds


@pytest.fixture
def attempt(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(auto_bot, "time", clock)
    path = tmp_path / "signals.csv"
    row = dict(signal_id="signal", entry_round_id="target", stake="1.00",
               auto_cashout_at="5.00", mode="live", status="sending")
    append_signal(path, row)
    state = SimpleNamespace(status="waiting", round_id="target", received_at=1000)
    watcher = SimpleNamespace(snapshot=lambda: state, last_error=lambda: "")
    calls = []
    responses = []

    def enter(*args, **kwargs):
        assert read_signals(path)[0]["status"] == "sending"
        assert args == ("1.00", "target", "5.00")
        assert 0 < kwargs["timeout"] <= 3
        calls.append(args)
        response = responses.pop(0)
        if callable(response):
            response = response()
        if isinstance(response, Exception):
            raise response
        return response

    return SimpleNamespace(clock=clock, path=path, row=row, state=state,
                           watcher=watcher, api=SimpleNamespace(enter=enter),
                           calls=calls, responses=responses)


def run_attempt(a):
    return auto_bot.enter_in_window(a.api, a.watcher, a.path, a.row, 3, 3)


def test_safe_retry_succeeds_only_in_same_window(attempt):
    a = attempt
    a.responses.extend([BlazeEntryNotSent("connect"), BlazeEntryNotSent("connect"), {"ok": True}])
    assert run_attempt(a)
    assert len(a.calls) == 3
    assert a.clock.seconds == pytest.approx(1)
    rows = read_signals(a.path)
    assert len(rows) == 1
    assert rows[0]["status"] == "entered"


@pytest.mark.parametrize("change,reason", [
    ("graphing", "janela_nao_aberta (estado=graphing)"),
    ("round", "rodada_alterada"), ("stale", "tick_desatualizado"),
    ("disconnect", "socket_indisponivel"), ("deadline", "prazo_local_esgotado"),
])
def test_failure_is_skipped_when_window_closes(attempt, change, reason, capsys):
    a = attempt

    def fail():
        if change == "graphing":
            a.state.status = "graphing"
        elif change == "round":
            a.state.round_id = "later-round"
        elif change == "stale":
            a.state.received_at = 990
        elif change == "disconnect":
            a.watcher.last_error = lambda: "disconnected"
        else:
            a.clock.seconds += 3.1
            a.state.received_at = a.clock.time()
        return BlazeEntryNotSent("connect")

    a.responses.append(fail)
    assert not run_attempt(a)
    assert len(a.calls) == 1
    assert read_signals(a.path)[0]["status"] == "rejected"
    assert reason in read_signals(a.path)[0]["message"]
    assert f"motivo={reason}" in capsys.readouterr().out


def test_maximum_attempts_is_bounded(attempt):
    a = attempt
    a.responses.extend([BlazeEntryNotSent("connect")] * 3)
    assert not run_attempt(a)
    assert len(a.calls) == 3
    assert "limite_de_tentativas" in read_signals(a.path)[0]["message"]


def test_permanent_rejection_is_not_retried(attempt):
    a = attempt
    a.responses.append(BlazeApiError("HTTP 401"))
    assert not run_attempt(a)
    assert len(a.calls) == 1


def test_uncertain_outcome_propagates_without_retry(attempt):
    a = attempt
    a.responses.append(BlazeUncertainOutcome("reset"))
    with pytest.raises(BlazeUncertainOutcome):
        run_attempt(a)
    assert len(a.calls) == 1
    # The run loop catches this and persists unknown (covered separately).
    assert read_signals(a.path)[0]["status"] == "sending"


def test_already_closed_window_never_sends(attempt):
    a = attempt
    a.state.status = "graphing"
    assert not run_attempt(a)
    assert not a.calls
    assert read_signals(a.path)[0]["status"] == "rejected"


@pytest.mark.parametrize("first", ["rejected", "graphing", "missed"])
def test_old_signal_does_not_leak_to_later_round(tmp_path, monkeypatch, first):
    clock = Clock()
    monkeypatch.setattr(auto_bot, "time", clock)
    monkeypatch.setattr(auto_bot, "bootstrap_rounds", lambda *args: [{"id": "trigger", "crash_point": 3}])
    monkeypatch.setattr(auto_bot, "account_from_environment", lambda: object())
    calls = []
    path = tmp_path / "signals.csv"

    class Api:
        def __init__(self, *args, **kwargs):
            pass

        def enter(self, stake, round_id, cashout, **kwargs):
            calls.append(round_id)
            if round_id == "one":
                raise BlazeApiError("HTTP 401")
            return {"ok": True}

    steps = []
    if first != "missed":
        steps.append(("waiting" if first == "rejected" else "graphing", "one", None))
    steps.extend([
        ("complete", "one", 1),
        ("waiting", "two", None),
        ("complete", "two", 3),  # A genuinely new M signal.
        ("waiting", "three", None),
        ("complete", "three", 1),
    ])

    class Watcher:
        stopped = False

        def __init__(self, **kwargs):
            self.index = -1

        def start(self):
            pass

        def stop(self):
            Watcher.stopped = True

        def last_error(self):
            return ""

        def connection_status(self):
            return SocketConnectionStatus("CONECTADO", 1, 0, 0, "", "")

        def pop_completed_rounds(self):
            self.index += 1
            assert self.index < len(steps), "bot should finish after the accepted entry"
            status, round_id, point = steps[self.index]
            return [] if point is None else [dict(id=round_id, crash_point=point, updated_at="2026-08-27T00:00:00Z")]

        def snapshot(self):
            status, round_id, _ = steps[self.index]
            return SimpleNamespace(status=status, round_id=round_id, received_at=clock.time())

    monkeypatch.setattr(auto_bot, "CrashApiClient", Api)
    monkeypatch.setattr(auto_bot, "BlazeCrashWatcher", Watcher)
    args = auto_bot.build_parser().parse_args(["--live", "--pattern", "M", "--signals", str(path)])
    assert auto_bot.run(args) == 0
    assert calls == (["one", "three"] if first == "rejected" else ["three"])
    assert Watcher.stopped


@pytest.mark.parametrize("flags", [["--entry-window-seconds", "0"], ["--entry-window-seconds", "nan"],
                                   ["--entry-window-seconds", "inf"], ["--max-entry-attempts", "0"]])
def test_invalid_window_configuration_is_rejected(flags):
    assert auto_bot.run(auto_bot.build_parser().parse_args(flags)) == 1


@pytest.mark.parametrize("round_id,status,age,error,reason", [
    ("trigger", "complete", 10, "", ""),
    ("trigger", "complete", 10, "disconnected", "socket_indisponivel"),
    ("target", "waiting", 0, "", ""),
    ("target", "waiting", 10, "", "tick_desatualizado (idade=10.00s"),
    ("target", "waiting", -1, "", "idade_tick_invalida"),
    ("target", "waiting", 0, "disconnected", "socket_indisponivel"),
    ("target", "graphing", 0, "", "janela_nao_aberta (estado=graphing)"),
    ("target", "complete", 10, "", "janela_nao_aberta (estado=complete)"),
    ("", "", 0, "", ""),
])
def test_signal_discard_reasons(monkeypatch, round_id, status, age, error, reason):
    clock = Clock()
    monkeypatch.setattr(auto_bot, "time", clock)
    snapshot = SimpleNamespace(round_id=round_id, status=status, received_at=clock.time() - age)
    watcher = SimpleNamespace(last_error=lambda: error)
    actual = auto_bot.signal_discard_reason(watcher, snapshot, "trigger")
    assert actual.startswith(reason) if reason else actual == ""


@pytest.mark.parametrize("scenario,reason", [
    ("normal", ""), ("stale_waiting", "tick_desatualizado"),
    ("missed_waiting", "janela_nao_aberta"), ("disconnect", "socket_indisponivel"),
])
def test_idle_trigger_waits_for_fresh_next_round(tmp_path, monkeypatch, capsys, scenario, reason):
    clock = Clock()
    monkeypatch.setattr(auto_bot, "time", clock)
    monkeypatch.setattr(auto_bot, "bootstrap_rounds", lambda *args: [])
    monkeypatch.setattr(auto_bot, "account_from_environment", lambda: object())
    calls = []
    path = tmp_path / "signals.csv"
    # seconds, status, round id, tick time, error, completed point
    steps = [
        (0, "complete", "trigger", 1000, "", 3),
        (3, "complete", "trigger", 1000, "", None),
        (10, "complete", "trigger", 1000, "", None),
        (11, "waiting", "target", 1011, "", None),
        (11.5, "waiting", "target", 1011.5, "", None),
        (12, "complete", "target", 1012, "", 1),
    ]
    if scenario == "stale_waiting":
        steps[3] = (11, "waiting", "target", 1000, "", None)
    elif scenario == "missed_waiting":
        steps[3] = (11, "graphing", "target", 1011, "", None)
    elif scenario == "disconnect":
        steps[2] = (10, "complete", "trigger", 1000, "disconnected", None)

    class Api:
        def __init__(self, *args, **kwargs):
            pass

        def enter(self, stake, round_id, cashout, **kwargs):
            assert scenario == "normal"
            assert clock.seconds == 11
            assert round_id == "target"
            calls.append(round_id)
            return {"ok": True}

    class Watcher:
        stopped = False

        def __init__(self, **kwargs):
            self.index = -1

        def start(self):
            pass

        def stop(self):
            Watcher.stopped = True

        def last_error(self):
            return steps[self.index][4]

        def connection_status(self):
            return SocketConnectionStatus("CONECTADO", 1, 0, 0, "", "")

        def pop_completed_rounds(self):
            self.index += 1
            if self.index == len(steps):
                raise KeyboardInterrupt
            seconds, status, round_id, tick_time, error, point = steps[self.index]
            clock.seconds = seconds
            return [] if point is None else [dict(id=round_id, crash_point=point, updated_at="2026-08-27T00:00:00Z")]

        def snapshot(self):
            seconds, status, round_id, tick_time, error, point = steps[self.index]
            return SimpleNamespace(status=status, round_id=round_id, received_at=tick_time)

    monkeypatch.setattr(auto_bot, "CrashApiClient", Api)
    monkeypatch.setattr(auto_bot, "BlazeCrashWatcher", Watcher)
    args = auto_bot.build_parser().parse_args(["--live", "--pattern", "M", "--signals", str(path)])
    assert auto_bot.run(args) == 0
    assert calls == (["target"] if scenario == "normal" else [])
    assert Watcher.stopped
    output = capsys.readouterr().out
    assert "SOCKET | CONECTADO" in output
    assert output.count("SINAL ARMADO") == 1
    if reason:
        assert f"motivo={reason}" in output
        assert "SINAL DESCARTADO" in output
        assert not path.exists()
    else:
        assert "SINAL DESCARTADO" not in output
        assert read_signals(path)[0]["status"] == "loss"
