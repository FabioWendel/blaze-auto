"""Abre somente um navegador próprio e observa sua sessão via depuração local."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit


class LoginBrowserError(RuntimeError):
    """Mensagem segura para exibição, sem URLs de sessão ou credenciais."""


def browser_executable(playwright: Any, name: str, explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    elif name == "chromium":
        candidates.append(Path(playwright.chromium.executable_path))
    elif sys.platform == "win32":
        relative = "Google/Chrome/Application/chrome.exe" if name == "chrome" else "Microsoft/Edge/Application/msedge.exe"
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(key):
                candidates.append(Path(os.environ[key]) / relative)
    elif sys.platform == "darwin":
        app = "Google Chrome" if name == "chrome" else "Microsoft Edge"
        candidates.append(Path(f"/Applications/{app}.app/Contents/MacOS/{app}"))
    else:
        names = ("google-chrome", "google-chrome-stable") if name == "chrome" else ("microsoft-edge", "microsoft-edge-stable")
        for command in names:
            executable = shutil.which(command)
            if executable:
                candidates.append(Path(executable))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise LoginBrowserError("Navegador não encontrado. Use --browser edge ou informe --browser-path com o executável instalado.")


def available_debug_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def browser_command(executable: Path, profile: Path, port: int, *, headless: bool = False) -> list[str]:
    command = [
        str(executable), f"--user-data-dir={profile}",
        "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}",
        "--no-first-run", "--no-default-browser-check", "--new-window",
    ]
    if headless:  # Somente para testes locais; não exposto no comando de login.
        command.append("--headless=new")
    return command + ["about:blank"]


def wait_debugger(process: Any, port: int, timeout: float = 20.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LoginBrowserError("O navegador encerrou antes de abrir a captura local.")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            connection.request("GET", "/json/version")
            response = connection.getresponse()
            if response.status == 200:
                endpoint = json.loads(response.read(65536)).get("webSocketDebuggerUrl", "")
                parsed = urlsplit(endpoint)
                if (parsed.scheme == "ws" and parsed.hostname in {"127.0.0.1", "localhost"}
                        and parsed.port == port and parsed.path.startswith("/devtools/browser/")
                        and not parsed.username and not parsed.password):
                    return endpoint
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.1)
    raise LoginBrowserError("A depuração local não ficou disponível. Verifique se o navegador permite depuração nesse perfil separado.")


def stop_owned_process(process: Any) -> None:
    if process.poll() is not None:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


@contextmanager
def login_browser(playwright: Any, name: str, *, mode: str = "normal",
                  executable: str | None = None, headless_test: bool = False) -> Iterator[tuple[Any, Any]]:
    if mode == "playwright":
        options: dict[str, Any] = {"headless": False}
        if name != "chromium":
            options["channel"] = "chrome" if name == "chrome" else "msedge"
        if executable:
            options["executable_path"] = str(browser_executable(playwright, name, executable))
        browser = playwright.chromium.launch(**options)
        try:
            yield browser, browser.new_context(viewport={"width": 1366, "height": 768})
        finally:
            browser.close()
        return

    executable_path = browser_executable(playwright, name, executable)
    # Não aponta para perfis existentes e nunca encerra Chrome por nome de processo.
    temporary_profile = tempfile.TemporaryDirectory(prefix="blaze-login-")
    process = None
    browser = None
    try:
        port = available_debug_port()
        command = browser_command(executable_path, Path(temporary_profile.name), port, headless=headless_test)
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, shell=False)
        endpoint = wait_debugger(process, port)
        browser = playwright.chromium.connect_over_cdp(endpoint, timeout=20000, no_defaults=True)
        context = browser.contexts[0]
        yield browser, context
    finally:
        if browser is not None:
            try:
                browser.new_browser_cdp_session().send("Browser.close")
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
        if process is not None:
            stop_owned_process(process)
        try:
            temporary_profile.cleanup()
        except OSError:
            print(f"AVISO: o perfil temporário não pôde ser removido: {temporary_profile.name}", flush=True)
