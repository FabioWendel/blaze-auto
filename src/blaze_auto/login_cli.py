from __future__ import annotations

import argparse
import math
import time
from pathlib import Path
from typing import Any, Callable

from .game_menu import choose_game
from .login_browser import LoginBrowserError, login_browser
from .login_capture import REQUIRED_KEYS, SessionCapture, observe_response, positive_id, save_env


LOGIN_URL = "https://blaze.bet.br/pt/games/crash"


def capture_login(playwright: Any, browser_name: str, timeout: float,
                  capture: SessionCapture, env_path: Path, *, launch_mode: str = "normal",
                  browser_path: str | None = None,
                  after_login: Callable[[Any, Any], None] | None = None) -> bool:
    with login_browser(playwright, browser_name, mode=launch_mode, executable=browser_path) as (browser, context):
        listener = lambda response: observe_response(capture, response)
        context.on("response", listener)
        page = context.pages[0] if context.pages else context.new_page()
        print("Faça login na janela aberta e permaneça no Crash. Não é necessário apostar.", flush=True)
        print("Se faltarem dados, abra seu perfil e a carteira BRL na mesma janela.", flush=True)
        deadline = time.monotonic() + timeout
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=min(timeout * 1000, 30000))
        except Exception:
            print("A página não terminou de carregar. Se a janela estiver aberta, conclua o login nela.", flush=True)
        last_missing: tuple[str, ...] | None = None
        while time.monotonic() < deadline:
            missing = tuple(capture.missing())
            if not missing:
                save_env(env_path, capture.env_values())
                context.remove_listener("response", listener)
                if after_login is not None:
                    print("Configuração salva no .env. A janela continuará aberta para escolher o jogo.", flush=True)
                    after_login(browser, context)
                else:
                    print("Configuração salva no .env. Fechando a janela de captura.", flush=True)
                return True
            if missing != last_missing:
                captured = [key for key in REQUIRED_KEYS if key not in missing]
                if captured:
                    print("Capturados (valores ocultos): " + ", ".join(captured), flush=True)
                print("Aguardando: " + ", ".join(missing), flush=True)
                last_missing = missing
            if not browser.is_connected() or not context.pages:
                break
            try:
                context.pages[0].wait_for_timeout(250)
            except Exception:
                break  # A janela pode ter sido fechada durante a espera.
        print("Captura incompleta; .env preservado. Faltam: " + ", ".join(capture.missing()), flush=True)
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Login, captura local de configuração e escolha entre Crash e Double")
    parser.add_argument("--login-only", action="store_true", help="salva o .env e encerra sem abrir o menu de jogos")
    parser.add_argument("--browser", choices=("chrome", "edge", "chromium"), default="chrome")
    parser.add_argument("--launch-mode", choices=("normal", "playwright"), default="normal",
                        help="normal abre o executável diretamente e observa via depuração local")
    parser.add_argument("--browser-path", help="caminho do executável, se não for encontrado automaticamente")
    parser.add_argument("--timeout-seconds", type=float, default=300, help="tempo para concluir o login (padrão: 300)")
    parser.add_argument("--wallet-id", default="", help="somente para escolher entre várias carteiras BRL observadas")
    return parser


def run(args: argparse.Namespace) -> int:
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        print("ERRO: --timeout-seconds deve ser positivo e finito.")
        return 1
    if args.wallet_id and not positive_id(args.wallet_id):
        print("ERRO: --wallet-id deve ser um inteiro positivo.")
        return 1
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('Instale o suporte ao navegador: python -m pip install -e ".[browser]"')
        return 1
    print("LOGIN LOCAL | dados somente no .env desta pasta; a captura não aposta. "
          "Após o login, o menu permite iniciar o bot com confirmação do modo real.", flush=True)
    if args.launch_mode == "normal":
        print("NAVEGADOR DIRETO | perfil temporário separado | depuração somente em 127.0.0.1", flush=True)
    try:
        with sync_playwright() as playwright:
            return 0 if capture_login(playwright, args.browser, args.timeout_seconds,
                                      SessionCapture(args.wallet_id), Path(".env"),
                                      launch_mode=args.launch_mode, browser_path=args.browser_path,
                                      after_login=None if args.login_only else choose_game) else 2
    except KeyboardInterrupt:
        print("Captura interrompida. Confira se houve confirmação de gravação antes de reiniciar o bot.")
        return 2
    except LoginBrowserError as exc:
        print(f"ERRO: {exc}")
        return 1
    except Exception:
        # Erros de Playwright podem conter URLs/headers. Não imprimir traceback.
        print("Não foi possível concluir a captura. Confira o navegador escolhido e a permissão de escrita no .env.")
        print("Para Chromium: python -m playwright install chromium; depois use --browser chromium.")
        return 1


def main() -> None:
    raise SystemExit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
