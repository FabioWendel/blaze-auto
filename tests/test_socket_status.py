import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from blaze_auto import auto_bot, crash_watcher, socket_status
from blaze_auto.crash_watcher import BlazeCrashWatcher, SocketConnectionStatus
from blaze_auto.socket_status import SocketStatusLogger, format_socket_status


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=100.0)
    fake_time = SimpleNamespace(monotonic=lambda: value.now, time=lambda: value.now)
    monkeypatch.setattr(crash_watcher, "time", fake_time)
    monkeypatch.setattr(socket_status, "time", fake_time)
    return value


def tick_message():
    return "42" + json.dumps(["data", {"id": "crash.tick", "payload": {
        "id": "round-1", "status": "waiting", "crash_point": None,
        "private_extra": "must-not-be-printed",
    }}])


def test_connection_activity_and_reconnect_do_not_reuse_old_tick(clock):
    watcher = BlazeCrashWatcher()
    ws = SimpleNamespace(send=lambda text: None)
    assert watcher.connection_status().state == "CONECTANDO"
    watcher._on_open(ws)
    assert watcher.connection_status().state == "CONECTADO"
    assert watcher.connection_status().tick_age is None
    watcher._on_message(ws, tick_message())
    clock.now += 3
    watcher._on_message(ws, "2")
    status = watcher.connection_status()
    assert status.message_age == 0
    assert status.tick_age == 3  # A ping is not a new crash tick.
    assert status.round_id == "round-1"
    assert "SEM TICK RECENTE" in format_socket_status(status)
    watcher._on_error(ws, RuntimeError("private-error"))
    assert watcher.connection_status().state == "RECONECTANDO"
    assert "private-error" not in format_socket_status(watcher.connection_status())
    watcher._on_close(ws, None, None)
    watcher._on_open(ws)
    status = watcher.connection_status()
    assert status.state == "CONECTADO"
    assert status.message_age is None
    assert status.tick_age is None
    assert status.round_id == ""
    clock.now += 4
    watcher._on_pong(ws, b"")
    assert watcher.connection_status().message_age == 0
    assert watcher.connection_status().tick_age is None
    watcher.stop()
    assert watcher.connection_status().state == "PARADO"


def test_logger_periodic_and_connection_changes_without_spam(clock, capsys):
    state = SocketConnectionStatus("CONECTANDO", 1, None, None, "", "")
    watcher = SimpleNamespace(connection_status=lambda: state)
    logger = SocketStatusLogger(10)
    logger.log_if_due(watcher)
    assert "SOCKET | CONECTANDO" in capsys.readouterr().out
    clock.now += 1
    logger.log_if_due(watcher)
    assert capsys.readouterr().out == ""
    state = replace(state, state="CONECTADO")
    logger.log_if_due(watcher)
    assert "CONECTADO" in capsys.readouterr().out
    clock.now += 9.9
    logger.log_if_due(watcher)
    assert capsys.readouterr().out == ""
    clock.now += 0.1
    logger.log_if_due(watcher)
    assert "CONECTADO" in capsys.readouterr().out
    state = replace(state, state="RECONECTANDO", attempts=2)
    logger.log_if_due(watcher)
    assert "tentativa de conexão=2" in capsys.readouterr().out


@pytest.mark.parametrize("age,label", [(None, "AGUARDANDO PRIMEIRO TICK"), (2, "(RECENTE)"), (2.1, "SEM TICK RECENTE")])
def test_tick_freshness_is_independent_of_connection(age, label):
    status = SocketConnectionStatus("CONECTADO", 1, 0, age, "round", "complete")
    text = format_socket_status(status)
    assert "SOCKET | CONECTADO" in text
    assert label in text


def test_raw_payload_is_never_in_status(clock):
    watcher = BlazeCrashWatcher()
    watcher._on_open(None)
    watcher._on_message(None, tick_message())
    text = format_socket_status(watcher.connection_status())
    assert "must-not-be-printed" not in text
    assert "rodada=round-1" in text


def test_dead_background_thread_is_not_reported_connected(clock):
    watcher = BlazeCrashWatcher()
    watcher._on_open(None)
    watcher._thread = SimpleNamespace(is_alive=lambda: False)
    assert watcher.connection_status().state == "DESCONECTADO"


def test_callbacks_are_registered_without_real_socket(monkeypatch, clock):
    watcher = BlazeCrashWatcher()
    captured = {}

    class FakeApp:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def run_forever(self, **kwargs):
            captured["on_open"](self)
            assert watcher.connection_status().state == "CONECTADO"
            captured["on_pong"](self, b"")
            assert watcher.connection_status().message_age == 0
            watcher._stop.set()

    monkeypatch.setitem(sys.modules, "websocket", SimpleNamespace(WebSocketApp=FakeApp))
    watcher._run()
    assert watcher.connection_status().attempts == 1
    assert watcher.connection_status().state == "PARADO"


@pytest.mark.parametrize("interval", ["-1", "nan", "inf"])
def test_invalid_log_interval_fails_before_network(interval, monkeypatch):
    def no_network(*args):
        pytest.fail("should not connect")
    monkeypatch.setattr(auto_bot, "bootstrap_rounds", no_network)
    args = auto_bot.build_parser().parse_args(["--socket-log-interval", interval])
    assert auto_bot.run(args) == 1


def test_default_status_interval():
    from blaze_auto import double_bot
    assert auto_bot.build_parser().parse_args([]).socket_log_interval == 0
    assert double_bot.build_parser().parse_args([]).socket_log_interval == 0
    double_bot.validate(double_bot.build_parser().parse_args([]))


def test_disabled_logger_does_not_print_or_query_watcher(capsys):
    watcher = SimpleNamespace(connection_status=lambda: pytest.fail("disabled logger must not query watcher"))
    for logger in (SocketStatusLogger(), SocketStatusLogger(0, "double.tick")):
        logger.log_if_due(watcher)
        logger.log_if_due(watcher)
    assert capsys.readouterr().out == ""
