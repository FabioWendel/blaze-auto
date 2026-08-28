import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace

import pytest
from dotenv import dotenv_values

from blaze_auto import login_cli
from blaze_auto.login_capture import SessionCapture
from test_login_capture import FAKE_AUTH, PROFILE, PROFILE_URL, Response, WALLET_URL, WALLETS


@pytest.fixture
def browser_flow(monkeypatch):
    state = SimpleNamespace(now=0, closed=False, connected=True, options=None, callback=None, responses=[])
    monkeypatch.setattr(login_cli, "time", SimpleNamespace(monotonic=lambda: state.now))
    def advance(milliseconds):
        state.now += milliseconds / 1000
        if state.responses:
            state.callback(state.responses.pop(0))
    def goto(url, **kwargs):
        assert url == login_cli.LOGIN_URL
    page = SimpleNamespace(goto=goto, wait_for_timeout=advance)
    def on(event, callback):
        assert event == "response"
        state.callback = callback
    def remove_listener(event, callback):
        assert event == "response"
        assert callback is state.callback
        state.callback = None
    context = SimpleNamespace(new_page=lambda: page, pages=[page], on=on, remove_listener=remove_listener)
    def close():
        state.closed = True
    browser = SimpleNamespace(new_context=lambda **kwargs: context,
                              is_connected=lambda: state.connected, close=close)
    def launch(**kwargs):
        state.options = kwargs
        return browser
    state.playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    return state


@pytest.mark.parametrize("name,channel", [("chrome", "chrome"), ("edge", "msedge"), ("chromium", None)])
def test_login_saves_complete_capture_and_closes_owned_browser(browser_flow, tmp_path, capsys, name, channel):
    state = browser_flow
    state.responses.extend([Response(body=PROFILE), Response(WALLET_URL, body=WALLETS)])
    path = tmp_path / ".env"
    assert login_cli.capture_login(state.playwright, name, 2, SessionCapture(), path, launch_mode="playwright")
    assert state.closed
    assert state.options["headless"] is False
    assert state.options.get("channel") == channel
    assert dotenv_values(path)["BLAZE_AUTHORIZATION"] == FAKE_AUTH
    output = capsys.readouterr().out
    assert FAKE_AUTH not in output
    assert "test_user" not in output
    assert "123" not in output
    assert "Configuração salva" in output


@pytest.mark.parametrize("close_early", [False, True])
def test_incomplete_capture_preserves_existing_env(browser_flow, tmp_path, close_early):
    state = browser_flow
    state.responses.append(Response(PROFILE_URL, body=PROFILE))
    state.connected = not close_early
    path = tmp_path / ".env"
    path.write_text("OTHER=existing\n")
    assert not login_cli.capture_login(state.playwright, "chrome", 1, SessionCapture(), path, launch_mode="playwright")
    assert state.closed
    assert path.read_text() == "OTHER=existing\n"


def test_write_error_still_closes_browser(browser_flow, tmp_path, monkeypatch):
    state = browser_flow
    state.responses.extend([Response(body=PROFILE), Response(WALLET_URL, body=WALLETS)])
    def fail(*args):
        raise PermissionError
    monkeypatch.setattr(login_cli, "save_env", fail)
    with pytest.raises(PermissionError):
        login_cli.capture_login(state.playwright, "chrome", 2, SessionCapture(), tmp_path / ".env", launch_mode="playwright")
    assert state.closed


def test_bootstrap_alone_completes_login(browser_flow, tmp_path, capsys):
    state = browser_flow
    state.responses.append(Response("https://api.blaze.bet.br/api/bootstrap/me", body={
        "user": {"username": "test_user", "xp": {"rank": "gold"}},
        "wallets": [{"id": 123, "currency": {"type": "BRL"}}],
    }))
    path = tmp_path / ".env"
    assert login_cli.capture_login(state.playwright, "chrome", 2, SessionCapture(), path, launch_mode="playwright")
    assert state.closed
    assert dotenv_values(path)["BLAZE_RANK"] == "gold"
    output = capsys.readouterr().out
    assert "Configuração salva" in output
    assert FAKE_AUTH not in output


def test_progress_shows_only_captured_field_names(browser_flow, tmp_path, capsys):
    state = browser_flow
    state.responses.append(Response(PROFILE_URL, body=PROFILE))
    assert not login_cli.capture_login(state.playwright, "chrome", 1, SessionCapture(), tmp_path / ".env", launch_mode="playwright")
    output = capsys.readouterr().out
    assert "Capturados (valores ocultos): BLAZE_AUTHORIZATION, BLAZE_USERNAME, BLAZE_RANK" in output
    assert "Aguardando: BLAZE_WALLET_ID" in output
    assert FAKE_AUTH not in output
    assert "test_user" not in output


def test_menu_runs_after_save_with_live_browser_and_capture_disabled(browser_flow, tmp_path):
    state = browser_flow
    state.responses.extend([Response(body=PROFILE), Response(WALLET_URL, body=WALLETS)])
    path = tmp_path / ".env"
    called = []
    def menu(browser, context):
        assert not state.closed
        assert browser.is_connected()
        assert context.pages
        assert state.callback is None
        assert dotenv_values(path)["BLAZE_AUTHORIZATION"] == FAKE_AUTH
        called.append(True)
    assert login_cli.capture_login(state.playwright, "chrome", 2, SessionCapture(), path,
                                   launch_mode="playwright", after_login=menu)
    assert called == [True]
    assert state.closed  # Only after menu returns.


def test_incomplete_capture_never_opens_menu(browser_flow, tmp_path):
    state = browser_flow
    def menu(*args):
        pytest.fail("must not open menu after incomplete capture")
    path = tmp_path / ".env"
    assert not login_cli.capture_login(state.playwright, "chrome", 1, SessionCapture(), path,
                                       launch_mode="playwright", after_login=menu)
    assert not path.exists()


@pytest.mark.parametrize("login_only", [False, True])
def test_cli_defaults_to_menu_and_supports_login_only(monkeypatch, login_only):
    fake_sync = ModuleType("playwright.sync_api")
    fake_sync.sync_playwright = lambda: nullcontext(object())
    monkeypatch.setitem(sys.modules, "playwright", ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)
    calls = []
    def capture(*args, **kwargs):
        calls.append(kwargs["after_login"])
        return True
    monkeypatch.setattr(login_cli, "capture_login", capture)
    args = login_cli.build_parser().parse_args(["--login-only"] if login_only else [])
    assert login_cli.run(args) == 0
    assert calls == [None if login_only else login_cli.choose_game]


@pytest.mark.parametrize("args", [["--timeout-seconds", "0"], ["--timeout-seconds", "nan"],
                                  ["--timeout-seconds", "inf"], ["--wallet-id", "abc"]])
def test_invalid_options_fail_before_browser(args):
    assert login_cli.run(login_cli.build_parser().parse_args(args)) == 1
