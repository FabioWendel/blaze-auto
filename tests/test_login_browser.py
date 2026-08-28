"""Teste opcional com Chrome real, mas toda a rede substituída por fixtures."""
import json
import os
import time
from contextlib import ExitStack

import pytest
from dotenv import dotenv_values

from blaze_auto.login_capture import SessionCapture, observe_response, save_env
from blaze_auto.login_browser import login_browser


@pytest.mark.skipif(os.getenv("BLAZE_RUN_BROWSER_TEST") != "1", reason="teste opcional com Chrome instalado")
@pytest.mark.parametrize("direct", [False, True])
def test_chrome_capture_with_fully_mocked_network(tmp_path, direct):
    from playwright.sync_api import sync_playwright

    auth = "Bearer fake.browser.test"
    capture = SessionCapture()
    html = """<!doctype html><title>Teste local de captura</title><script>
    async function test() {
      const headers = {Authorization: 'Bearer fake.browser.test'};
      await fetch('/api/bootstrap/me', {headers});
      window.finished = true;
    }
    test();
    </script>"""

    def route_response(route):
        url = route.request.url
        if url == "https://blaze.bet.br/pt/games/crash":
            route.fulfill(status=200, content_type="text/html", body=html)
        elif url == "https://blaze.bet.br/api/bootstrap/me":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({
                              "user": {"username": "browser_test", "xp": {"rank": "gold"}},
                              "wallets": [{"id": 123, "currency": {"type": "BRL", "name": "Brazilian Real"}}],
                          }))
        else:
            route.abort()  # Nenhuma requisição pode sair para a rede.

    with sync_playwright() as playwright, ExitStack() as resources:
        if direct:
            browser, context = resources.enter_context(login_browser(playwright, "chrome", headless_test=True))
        else:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(service_workers="block")
        try:
            context.route("**/*", route_response)
            context.on("response", lambda response: observe_response(capture, response))
            page = context.new_page()
            page.goto("https://blaze.bet.br/pt/games/crash")
            page.wait_for_function("window.finished === true")
            # fetch() resolve ao receber headers; os callbacks ainda podem estar
            # lendo o corpo. Aguarda a captura, como o loop do comando real.
            deadline = time.monotonic() + 5
            while capture.missing() and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            assert not capture.missing()
            path = tmp_path / ".env"
            save_env(path, capture.env_values())
            assert dotenv_values(path)["BLAZE_AUTHORIZATION"] == auth
            assert dotenv_values(path)["BLAZE_WALLET_ID"] == "123"
        finally:
            if not direct:
                browser.close()
