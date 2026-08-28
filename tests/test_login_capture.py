import io
import os
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values

from blaze_auto import login_capture
from blaze_auto.login_capture import SessionCapture, api_path, merge_env, observe_response, save_env


PROFILE_URL = "https://blaze.bet.br/api/users/me"
WALLET_URL = "https://blaze.bet.br/api/wallets"
FAKE_AUTH = "Bearer fictitious.test.token"
HEADERS = {"authorization": FAKE_AUTH}
PROFILE = {"username": "test_user", "rank": {"name": "gold"}}
WALLETS = [{"id": 123, "currency": {"code": "BRL"}}, {"id": 456, "currency": "USD"}]


def complete_capture():
    capture = SessionCapture()
    capture.observe(PROFILE_URL, 200, "GET", HEADERS, PROFILE)
    capture.observe(WALLET_URL, 200, "GET", HEADERS, WALLETS)
    return capture


class Response:
    def __init__(self, url=PROFILE_URL, status=200, method="GET", headers=None, body=None):
        self.url = url
        self.status = status
        self.request = SimpleNamespace(method=method, all_headers=lambda: headers or HEADERS)
        self.body = body
        self.json_calls = 0

    def json(self):
        self.json_calls += 1
        return self.body


@pytest.mark.parametrize("url", [
    "https://evilblaze.bet.br/api/users/me", "https://blaze.bet.br.evil.test/api/users/me",
    "http://blaze.bet.br/api/users/me", "https://blaze.bet.br:444/api/users/me",
    "https://blaze.bet.br@evil.test/api/users/me", "https://user@blaze.bet.br/api/users/me",
    "https://blaze.bet.br/pt/games/crash", "not-a-url", "https://[bad/api/users/me",
])
def test_untrusted_urls_are_ignored(url):
    capture = SessionCapture()
    capture.observe(url, 200, "GET", HEADERS, PROFILE)
    assert api_path(url) == ""
    assert "BLAZE_AUTHORIZATION" in capture.missing()


def test_complete_capture_maps_only_required_fields():
    capture = complete_capture()
    assert capture.missing() == []
    assert capture.env_values() == {
        "BLAZE_AUTHORIZATION": FAKE_AUTH, "BLAZE_WALLET_ID": "123",
        "BLAZE_USERNAME": "test_user", "BLAZE_RANK": "gold",
        "BLAZE_SESSION_ID": "", "BLAZE_CLIENT_VERSION": "",
    }
    assert FAKE_AUTH not in repr(capture)


def test_nested_profile_embedded_wallet_and_optional_headers():
    capture = SessionCapture()
    capture.observe(PROFILE_URL, 200, "GET", {
        "Authorization": FAKE_AUTH, "X-Session-Id": "fake-session", "X-Client-Version": "1.2",
    }, {"data": {"user": {**PROFILE, "wallets": WALLETS}}})
    assert not capture.missing()
    assert capture.env_values()["BLAZE_SESSION_ID"] == "fake-session"


@pytest.mark.parametrize("envelope", [False, True])
def test_bootstrap_current_site_format_is_captured(envelope):
    body = {
        "user": {"id": 987, "username": "test_user", "xp": {"rank": "gold", "level": 10}},
        "wallets": [{"id": 123, "currency": {"type": "BRL", "name": "Brazilian Real"}}],
        "transactions": {"latestDeposit": {"id": 999, "currency": {"type": "BRL"}}},
    }
    capture = SessionCapture()
    response = Response("https://api.blaze.bet.br/api/bootstrap/me",
                        body={"data": body} if envelope else body)
    observe_response(capture, response)
    assert response.json_calls == 1
    assert not capture.missing()
    values = capture.env_values()
    assert values["BLAZE_USERNAME"] == "test_user"
    assert values["BLAZE_RANK"] == "gold"
    assert values["BLAZE_WALLET_ID"] == "123"


def test_wallet_currency_type_takes_precedence_over_display_name():
    capture = SessionCapture()
    capture.observe(PROFILE_URL, 200, "GET", HEADERS, PROFILE)
    capture.observe(WALLET_URL, 200, "GET", HEADERS, [
        {"id": 123, "currency": {"type": "BRL", "name": "Brazilian Real"}},
        {"id": 456, "currency": {"type": "USD", "name": "BRL"}},
    ])
    assert capture.env_values()["BLAZE_WALLET_ID"] == "123"


@pytest.mark.parametrize("xp", [None, [], "gold", {}, {"rank": 10}])
def test_bootstrap_does_not_invent_missing_rank(xp):
    capture = SessionCapture()
    capture.observe("https://blaze.bet.br/api/bootstrap/me", 200, "GET", HEADERS, {
        "user": {"username": "test_user", "xp": xp}, "wallets": WALLETS,
    })
    assert capture.missing() == ["BLAZE_RANK"]


def test_other_users_profile_cannot_supply_nested_rank():
    capture = SessionCapture()
    capture.observe("https://blaze.bet.br/api/user_profiles/other-player", 200, "GET", HEADERS,
                    {"user": {"username": "other", "xp": {"rank": "gold"}}, "wallets": WALLETS})
    assert "BLAZE_USERNAME" in capture.missing()
    assert "BLAZE_RANK" in capture.missing()


@pytest.mark.parametrize("status", [301, 401, 403, 500])
def test_failed_auth_is_not_captured(status):
    capture = SessionCapture()
    capture.observe(PROFILE_URL, status, "GET", HEADERS, PROFILE)
    assert "BLAZE_AUTHORIZATION" in capture.missing()


def test_changing_token_clears_previous_user_and_wallet():
    capture = complete_capture()
    capture.observe(WALLET_URL, 200, "GET", {"authorization": "Bearer other.fake.token"}, WALLETS)
    assert "BLAZE_USERNAME" in capture.missing()
    assert "BLAZE_RANK" in capture.missing()
    with pytest.raises(ValueError):
        capture.env_values()


def test_public_player_profile_is_not_used():
    capture = SessionCapture()
    capture.observe("https://blaze.bet.br/api/users/other-player", 200, "GET", HEADERS, PROFILE)
    assert "BLAZE_USERNAME" in capture.missing()


@pytest.mark.parametrize("preferred,ready", [("", False), ("123", True), ("999", False)])
def test_multiple_brl_wallets_require_explicit_observed_choice(preferred, ready):
    capture = SessionCapture(preferred)
    capture.observe(PROFILE_URL, 200, "GET", HEADERS, PROFILE)
    capture.observe(WALLET_URL, 200, "GET", HEADERS, WALLETS + [{"id": 789, "type": "BRL"}])
    assert (not capture.missing()) == ready


def test_wallet_update_can_invalidate_earlier_selection():
    capture = complete_capture()
    capture.observe(WALLET_URL, 200, "GET", HEADERS, WALLETS + [{"id": 789, "type": "BRL"}])
    assert "BLAZE_WALLET_ID" in capture.missing()


@pytest.mark.parametrize("bad", ["name\nINJECTED=1", "name\r", "${SECRET}", 123, None])
def test_invalid_profile_values_are_not_written(bad):
    capture = SessionCapture()
    capture.observe(PROFILE_URL, 200, "GET", HEADERS, {"username": bad, "rank": "gold"})
    assert "BLAZE_USERNAME" in capture.missing()


def test_callback_does_not_read_login_or_unrelated_bodies():
    capture = SessionCapture()
    login_response = Response("https://blaze.bet.br/api/auth/password", method="POST", body={"password": "fake-password"})
    observe_response(capture, login_response)
    assert login_response.json_calls == 0
    other = Response("https://outside.test/api/users/me", body=PROFILE)
    observe_response(capture, other)
    assert other.json_calls == 0
    assert "BLAZE_USERNAME" in capture.missing()


def test_callback_ignores_unreadable_response_without_logging(capsys):
    response = Response()
    def fail():
        raise ValueError("private-body")
    response.json = fail
    observe_response(SessionCapture(), response)
    assert capsys.readouterr().out == ""


def test_env_merge_preserves_unrelated_settings_and_removes_duplicate_credentials():
    original = "# keep comment\nOTHER='value'\nBLAZE_ROOM_ID=7\nexport BLAZE_USERNAME='old'\nBLAZE_USERNAME=duplicate\nBLAZE_SESSION_ID=old-session\nMULTILINE='a\nb'"
    merged = merge_env(original, complete_capture().env_values())
    parsed = dotenv_values(stream=io.StringIO(merged), interpolate=False)
    assert "# keep comment\n" in merged
    assert parsed["OTHER"] == "value"
    assert parsed["MULTILINE"] == "a\nb"
    assert parsed["BLAZE_ROOM_ID"] == "7"
    assert parsed["BLAZE_SESSION_ID"] == ""
    assert parsed["BLAZE_USERNAME"] == "test_user"
    assert merged.count("BLAZE_USERNAME=") == 1


def test_env_quote_round_trip():
    values = complete_capture().env_values()
    values["BLAZE_USERNAME"] = "test'user\\name"
    parsed = dotenv_values(stream=io.StringIO(merge_env("", values)), interpolate=False)
    assert parsed["BLAZE_USERNAME"] == values["BLAZE_USERNAME"]


def test_save_creates_env_atomically_without_artifacts(tmp_path):
    path = tmp_path / ".env"
    save_env(path, complete_capture().env_values())
    assert dotenv_values(path)["BLAZE_WALLET_ID"] == "123"
    assert dotenv_values(path)["BLAZE_ROOM_ID"] == "4"
    assert list(tmp_path.iterdir()) == [path]
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_invalid_existing_env_is_untouched(tmp_path):
    path = tmp_path / ".env"
    original = b"BLAZE_AUTHORIZATION='unterminated"
    path.write_bytes(original)
    with pytest.raises(ValueError, match="sintaxe"):
        save_env(path, complete_capture().env_values())
    assert path.read_bytes() == original


def test_replace_failure_leaves_original_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("OTHER=original\n")
    def fail(*args):
        raise PermissionError
    monkeypatch.setattr(login_capture.os, "replace", fail)
    with pytest.raises(PermissionError):
        save_env(path, complete_capture().env_values())
    assert path.read_text() == "OTHER=original\n"
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_edit_is_preserved(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("OTHER=original\n")
    original_fsync = login_capture.os.fsync
    def edit_during_flush(descriptor):
        original_fsync(descriptor)
        path.write_text("OTHER=edited\n")
    monkeypatch.setattr(login_capture.os, "fsync", edit_during_flush)
    with pytest.raises(ValueError, match="mudou"):
        save_env(path, complete_capture().env_values())
    assert path.read_text() == "OTHER=edited\n"
    assert list(tmp_path.iterdir()) == [path]


def test_partial_values_cannot_be_written(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OTHER=original\n")
    with pytest.raises(ValueError):
        save_env(path, {"BLAZE_AUTHORIZATION": FAKE_AUTH})
    assert path.read_text() == "OTHER=original\n"
