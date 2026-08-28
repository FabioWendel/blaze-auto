from types import SimpleNamespace

import pytest

from blaze_auto.game_menu import GAMES, choose_game


def test_game_menu_opens_both_games_in_same_context_and_stays_open(capsys):
    urls = []
    page = SimpleNamespace(goto=lambda url, **kwargs: urls.append(url))
    context = SimpleNamespace(pages=[page])
    browser = SimpleNamespace(is_connected=lambda: True)
    choices = iter(["1", "3", "double", "3", "crash", "3", "0"])
    choose_game(browser, context, read_choice=lambda prompt: next(choices))
    assert urls == [GAMES["1"][1], GAMES["2"][1], GAMES["1"][1]]
    output = capsys.readouterr().out
    assert output.count("ESCOLHA O JOGO") == 4
    assert "Nenhuma aposta automática foi iniciada" in output


def test_invalid_option_does_not_navigate():
    urls = []
    page = SimpleNamespace(goto=lambda url, **kwargs: urls.append(url))
    choices = iter(["", "3", "--live_dc", "https://evil.test", "0"])
    choose_game(SimpleNamespace(is_connected=lambda: True), SimpleNamespace(pages=[page]),
                read_choice=lambda prompt: next(choices))
    assert urls == []


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_interrupt_exits_without_error(error, capsys):
    def read(prompt):
        raise error
    choose_game(SimpleNamespace(is_connected=lambda: True), SimpleNamespace(pages=[]), read_choice=read)
    assert "Encerrando o menu" in capsys.readouterr().out


def test_navigation_error_returns_to_menu_without_leaking_details(capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("private-session-info")
    choices = iter(["2", "0"])
    choose_game(SimpleNamespace(is_connected=lambda: True),
                SimpleNamespace(pages=[SimpleNamespace(goto=fail)]),
                read_choice=lambda prompt: next(choices))
    output = capsys.readouterr().out
    assert "private-session-info" not in output
    assert "Não foi possível confirmar" in output
    assert output.count("ESCOLHA O JOGO") == 2


def test_closed_browser_does_not_wait_for_input(capsys):
    def read(prompt):
        pytest.fail("must not prompt with disconnected browser")
    choose_game(SimpleNamespace(is_connected=lambda: False), SimpleNamespace(pages=[]), read_choice=read)
    assert "navegador foi fechado" in capsys.readouterr().out


@pytest.mark.parametrize("game", ["1", "2"])
def test_menu_runs_selected_bot_and_returns_to_menu(game, monkeypatch, capsys):
    from blaze_auto import game_menu
    calls = []
    monkeypatch.setattr(game_menu, "run_automation", lambda choice, args: calls.append((choice, args)) or 0)
    # mode, stake, (pattern/cashout or gales), loss, gain, daily limit, session limit
    config = [""] * (8 if game == "1" else 7)
    choices = iter([game, *config, "0"])
    choose_game(SimpleNamespace(is_connected=lambda: True),
                SimpleNamespace(pages=[SimpleNamespace(goto=lambda *a, **k: None)]),
                read_choice=lambda prompt: next(choices))
    assert len(calls) == 1 and calls[0][0] == game
    args = calls[0][1]
    assert "--live" not in args
    assert args[args.index("--max-session-entries") + 1] == "0"
    if game == "1":
        assert args[args.index("--pattern") + 1] == "MABBM"
    else:
        assert args[args.index("--max-gales") + 1] == "3"
    assert "voltou ao menu" in capsys.readouterr().out


@pytest.mark.parametrize("confirmation,expected", [("REAL", True), ("sim", False), ("", False)])
def test_live_requires_explicit_confirmation(confirmation, expected):
    from blaze_auto.game_menu import configure_automation
    choices = iter(["2", "", "", "", "", "", "", confirmation])
    args = configure_automation("2", lambda prompt: next(choices))
    assert (args is not None and "--live" in args) == expected


@pytest.mark.parametrize("game,module_name", [("1", "auto_bot"), ("2", "double_bot")])
def test_runner_uses_existing_module_and_current_captured_env(game, module_name, monkeypatch, tmp_path):
    import os
    from blaze_auto import game_menu
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('BLAZE_AUTHORIZATION="fresh-test-token"\n', encoding="utf-8")
    monkeypatch.setenv("BLAZE_AUTHORIZATION", "old-test-token")
    monkeypatch.setenv("BLAZE_SESSION_ID", "old-session")
    def run(args):
        assert os.environ["BLAZE_AUTHORIZATION"] == "fresh-test-token"
        assert os.environ["BLAZE_SESSION_ID"] == ""
        assert not args.live
        return 3
    monkeypatch.setattr(getattr(game_menu, module_name), "run", run)
    assert game_menu.run_automation(game, []) == 3
    assert os.environ["BLAZE_AUTHORIZATION"] == "old-test-token"
    assert os.environ["BLAZE_SESSION_ID"] == "old-session"


def test_invalid_numeric_options_are_reprompted():
    from decimal import Decimal
    from blaze_auto.game_menu import ask_number
    choices = iter(["NaN", "Infinity", "-1", "11", "2.5", "3"])
    assert ask_number(lambda p: next(choices), "Gales", "3", integer=True,
                      minimum=Decimal(0), maximum=Decimal(10)) == "3"


@pytest.mark.parametrize("mode,confirmation,expected_live", [("1", None, False), ("2", "REAL", True), ("2", "não", False)])
def test_crash_experimental_menu_still_requires_real_confirmation(mode, confirmation, expected_live, capsys):
    from blaze_auto.game_menu import configure_automation
    choices = iter([mode, "", "2", "", "", "", "", ""] + ([] if confirmation is None else [confirmation]))
    args = configure_automation("1", lambda prompt: next(choices))
    if mode == "2" and not expected_live:
        assert args is None
    else:
        assert ("--live" in args) == expected_live
        assert args[args.index("--preset") + 1] == "baixas-media"
        assert args[args.index("--pattern") + 1] == "BBBBM"
        assert args[args.index("--auto-cashout-at") + 1] == "1.50"
    assert "EXPERIMENTAL" in capsys.readouterr().out
