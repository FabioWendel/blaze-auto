import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from blaze_auto import login_browser as launcher, login_cli


def test_direct_command_uses_local_debugging_and_separate_profile():
    command = launcher.browser_command(Path("chrome.exe"), Path("temporary-profile"), 45678)
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=45678" in command
    assert "--user-data-dir=temporary-profile" in command
    assert "--enable-automation" not in command
    assert not any("disable-blink-features" in arg or "disable-web-security" in arg for arg in command)
    assert not any("headless" in arg for arg in command)
    assert command[-1] == "about:blank"


def test_normal_is_the_default_mode():
    args = login_cli.build_parser().parse_args([])
    assert args.launch_mode == "normal"
    assert args.browser == "chrome"


def test_explicit_executable_must_exist(tmp_path):
    with pytest.raises(launcher.LoginBrowserError):
        launcher.browser_executable(None, "chrome", str(tmp_path / "missing.exe"))
    executable = tmp_path / "browser.exe"
    executable.touch()
    assert launcher.browser_executable(None, "chrome", str(executable)) == executable.resolve()


def test_stopped_browser_does_not_wait_for_debugging():
    with pytest.raises(launcher.LoginBrowserError, match="encerrou"):
        launcher.wait_debugger(SimpleNamespace(poll=lambda: 1), 12345)


@pytest.mark.parametrize("fail_connect", [False, True])
def test_direct_launcher_reuses_default_context_and_cleans_owned_resources(tmp_path, monkeypatch, fail_connect):
    executable = tmp_path / "browser.exe"
    executable.touch()
    state = SimpleNamespace(command=[], closed=False, exited=False, profile=None)
    context = object()

    class Process:
        def poll(self):
            return 0 if state.exited else None

        def wait(self, timeout):
            if not state.exited:
                raise subprocess.TimeoutExpired("browser", timeout)

        def terminate(self):
            state.exited = True

        def kill(self):
            pytest.fail("graceful termination should be sufficient")

    def popen(command, **kwargs):
        state.command = command
        assert kwargs["shell"] is False
        state.profile = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--user-data-dir=")))
        assert state.profile.exists()
        return Process()

    def close_command(method):
        assert method == "Browser.close"
        state.exited = True

    def close():
        state.closed = True

    browser = SimpleNamespace(contexts=[context], close=close,
                              new_browser_cdp_session=lambda: SimpleNamespace(send=close_command))

    def connect(endpoint, **kwargs):
        assert endpoint == "ws://127.0.0.1:12345/devtools/browser/test"
        assert kwargs["no_defaults"] is True
        if fail_connect:
            raise RuntimeError("connection failed")
        return browser

    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    monkeypatch.setattr(launcher, "available_debug_port", lambda: 12345)
    monkeypatch.setattr(launcher, "wait_debugger", lambda *args: "ws://127.0.0.1:12345/devtools/browser/test")
    playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    if fail_connect:
        with pytest.raises(RuntimeError):
            with launcher.login_browser(playwright, "chrome", executable=str(executable)):
                pytest.fail("must not yield on connection failure")
    else:
        with launcher.login_browser(playwright, "chrome", executable=str(executable)) as connected:
            assert connected == (browser, context)
        assert state.closed
    assert state.exited
    assert not state.profile.exists()
