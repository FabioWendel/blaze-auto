"""Escolha de jogo e execução de um bot por vez após o login local."""
from __future__ import annotations

import os
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from dotenv import dotenv_values

from . import auto_bot, double_bot
from .crash_presets import EXPERIMENTAL_WARNING, PRESETS


GAMES = {
    "1": ("Crash", "https://blaze.bet.br/pt/games/crash"),
    "2": ("Double", "https://blaze.bet.br/pt/games/double"),
}


@contextmanager
def captured_environment(env_path: Path):
    """Use this capture even when the terminal has stale BLAZE credentials."""
    keys = ("BLAZE_AUTHORIZATION", "BLAZE_WALLET_ID", "BLAZE_USERNAME", "BLAZE_RANK",
            "BLAZE_ROOM_ID", "BLAZE_SESSION_ID", "BLAZE_CLIENT_VERSION")
    if not env_path.is_file():
        raise ValueError(".env não encontrado; conclua a captura antes de iniciar o bot")
    values = dotenv_values(env_path, interpolate=False)
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ[key] = values.get(key) or ("4" if key == "BLAZE_ROOM_ID" else "")
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_automation(game: str, arguments: list[str]) -> int:
    module = auto_bot if game == "1" else double_bot
    with captured_environment(Path(".env")):
        return module.run(module.build_parser().parse_args(arguments))


def ask_number(read: Callable[[str], str], prompt: str, default: str, *,
               integer: bool = False, minimum: Decimal = Decimal("0.01"),
               maximum: Decimal | None = None) -> str:
    while True:
        text = read(f"{prompt} [{default}]: ").strip().replace(",", ".") or default
        try:
            value = Decimal(text)
            if (not value.is_finite() or value < minimum or (maximum is not None and value > maximum)
                    or value != value.quantize(Decimal("1") if integer else Decimal("0.01"))):
                raise ValueError
            return str(int(value)) if integer else f"{value:.2f}"
        except (InvalidOperation, ValueError):
            print("Valor inválido; confira os limites e as casas decimais.", flush=True)


def configure_automation(game: str, read: Callable[[str], str]) -> list[str] | None:
    print("\n1 - Simulação (sem dinheiro real) [padrão]\n2 - Apostas reais\n3 - Só abrir o site\n0 - Voltar", flush=True)
    while True:
        mode = read("Modo [1]: ").strip() or "1"
        if mode in {"0", "3"}:
            return None
        if mode in {"1", "2"}:
            break
        print("Modo inválido. Digite 0, 1, 2 ou 3.", flush=True)
    stake = ask_number(read, "Valor inicial em R$", "0.10")
    arguments = ["--stake", stake]
    if game == "1":
        print("Crash: Enter = MABBM/5x | 2 = BBBBM/1.50x experimental | ou digite sua sequência B/M/A.", flush=True)
        preset_name = "original"
        while True:
            pattern = read("Padrão Crash [MABBM]: ").strip().upper() or "MABBM"
            if pattern in {"2", "BAIXAS-MEDIA"}:
                preset_name = "baixas-media"
                pattern = PRESETS[preset_name].pattern
                break
            if pattern and all(char in "BMA" for char in pattern):
                break
            print("Use somente B, M e A.", flush=True)
        if PRESETS[preset_name].experimental:
            print(EXPERIMENTAL_WARNING, flush=True)
            print("Quatro baixas (<2x), depois média (2x a <5x) concluída; entrada só na rodada SEGUINTE.", flush=True)
        cashout = ask_number(read, "Autoretirada (x)", PRESETS[preset_name].cashout, minimum=Decimal("1.01"))
        arguments += ["--preset", preset_name, "--pattern", pattern, "--auto-cashout-at", cashout]
        detail = f"padrão {pattern} | autoretirada {cashout}x"
    else:
        gales = ask_number(read, "Máximo de dobragens (0 a 10)", "3", integer=True,
                           minimum=Decimal(0), maximum=Decimal(10))
        arguments += ["--max-gales", gales]
        exposure = Decimal(stake) * (2 ** (int(gales) + 1) - 1)
        detail = f"última cor concluída (vermelho/preto) → dobra e alterna na perda | até {gales} dobragens | sem proteção no branco"
        print(f"Se perder todas as entradas da sequência: R$ {exposure:.2f}. "
              "Dobrar não garante recuperar perdas. Os limites podem parar antes.", flush=True)
    loss = ask_number(read, "Stop-loss diário em R$", "5.00")
    gain = ask_number(read, "Stop-gain diário em R$", "5.00")
    daily = ask_number(read, "Máximo de entradas por dia", "20", integer=True, minimum=Decimal(1))
    session = ask_number(read, "Entradas nesta sessão (0 = contínuo; 1 = uma entrada e resultado)",
                         "0", integer=True, minimum=Decimal(0))
    arguments += ["--daily-stop-loss", loss, "--daily-take-profit", gain,
                  "--max-daily-entries", daily, "--max-session-entries", session]
    print(f"{GAMES[game][0]} | {'REAL' if mode == '2' else 'SIMULAÇÃO'} | base R$ {stake} | {detail}", flush=True)
    print(f"Limites diários (UTC): perda R$ {loss}, ganho R$ {gain}, {daily} entradas. "
          f"Sessão: {session if session != '0' else 'contínua'}. Ctrl+C interrompe o bot e volta ao menu.", flush=True)
    if mode == "2":
        if read("Para autorizar apostas com dinheiro real, digite REAL: ").strip().upper() != "REAL":
            print("Cancelado. Nenhuma aposta foi enviada.", flush=True)
            return None
        arguments.append("--live")
    return arguments


def choose_game(browser: Any, context: Any, *, read_choice: Callable[[str], str] | None = None) -> None:
    read_choice = read_choice or input
    while browser.is_connected():
        print("\nESCOLHA O JOGO\n1 - Crash\n2 - Double\n0 - Sair e fechar a janela de captura", flush=True)
        try:
            choice = read_choice("Opção: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nEncerrando o menu. O .env salvo será mantido.", flush=True)
            return
        choice = {"crash": "1", "double": "2", "sair": "0"}.get(choice, choice)
        if choice == "0":
            print("Encerrando. O .env salvo será mantido.", flush=True)
            return
        if choice not in GAMES:
            print("Opção inválida. Digite 1, 2 ou 0.", flush=True)
            continue
        if not browser.is_connected():
            break
        name, url = GAMES[choice]
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            # Erros de navegação podem incluir dados da sessão; não imprimi-los.
            if not browser.is_connected():
                break
            print("Não foi possível confirmar o carregamento. Confira a janela ou escolha novamente.", flush=True)
            continue
        print(f"{name} aberto na janela já logada.", flush=True)
        try:
            arguments = configure_automation(choice, read_choice)
            if arguments is not None:
                code = run_automation(choice, arguments)
                print(f"Automação encerrada (código {code}). Você voltou ao menu.", flush=True)
            else:
                print("Nenhuma aposta automática foi iniciada. Voltando ao menu.", flush=True)
        except EOFError:
            return
        except KeyboardInterrupt:
            print("\nAutomação/configuração interrompida. Voltando ao menu.", flush=True)
        except Exception:
            # Never include credential-bearing exception details.
            print("Não foi possível executar a automação. Confira o .env e o ledger de pendências antes de tentar novamente.", flush=True)
    print("O navegador foi fechado. O .env salvo será mantido.", flush=True)
