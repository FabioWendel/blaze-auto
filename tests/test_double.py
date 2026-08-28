import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from blaze_auto.api_client import CrashAccount, BlazeUncertainOutcome
from blaze_auto.double import (BlazeDoubleWatcher, DoubleApiClient, DOUBLE_ENTER_URL,
                               extract_double_ticks, normalize_double_result)


def tick(status="complete", color=1, roll=4):
    return dict(id="round-a", status=status, color=color, roll=roll, updated_at="2026-08-28T12:00:00Z")


def packet(payload, event="double.tick"):
    return '42' + json.dumps(["data", {"id": event, "payload": payload}])


@pytest.mark.parametrize("payload", [tick("waiting", None, None), tick("rolling"),
                                     tick(color=1, roll=0), tick(color=True),
                                     tick(roll=15), tick(roll=4.5), tick(color="1")])
def test_double_result_rejects_nonfinal_or_invalid_data(payload):
    assert normalize_double_result(payload) is None


@pytest.mark.parametrize("color,roll", [(0, 0), (1, 1), (1, 7), (2, 8), (2, 14)])
def test_double_result_colors(color, roll):
    assert normalize_double_result(tick(color=color, roll=roll))["color"] == color


def test_double_watcher_handshake_heartbeat_health_and_dedup():
    sent = []
    ws = SimpleNamespace(send=sent.append)
    watcher = BlazeDoubleWatcher()
    watcher._on_open(ws)
    watcher._on_message(ws, '0{}')
    watcher._on_message(ws, '40')
    watcher._on_message(ws, '2')
    assert sent[0] == "40" and sent[-1] == "3"
    assert "double_room_1" in sent[1] and "crash_room" not in sent[1]
    watcher._on_message(ws, packet(tick("rolling")))
    assert watcher.pop_completed_rounds() == []
    watcher._on_message(ws, packet(tick()))
    watcher._on_message(ws, packet(tick()))
    assert len(watcher.pop_completed_rounds()) == 1
    assert watcher.connection_status().tick_age is not None
    assert watcher.snapshot().round_id == "round-a"
    assert extract_double_ticks(packet(tick(), "crash.tick")) == []


def client():
    return DoubleApiClient(CrashAccount("test-token", 123, "test-user", "test-rank", room_id=4))


def valid_reply():
    return {"bet": {"id": "bet-a", "amount": "0.10", "color": 1, "currency_type": "BRL", "roulette_id": "round-a"}}


def test_double_request_uses_own_room_and_endpoint_without_crash_payload(monkeypatch):
    api = client()
    calls = []
    monkeypatch.setattr(api, "_post", lambda url, payload, **kw: calls.append((url, payload, kw)) or valid_reply())
    api.enter(Decimal("0.10"), 1, "round-a", timeout=2)
    url, payload, kwargs = calls[0]
    assert url == DOUBLE_ENTER_URL
    assert payload == dict(amount="0.10", color=1, currency_type="BRL", free_bet=False,
                           room_id=1, username="test-user", rank="test-rank", wallet_id=123)
    assert kwargs == {"timeout": 2}
    assert api._headers()["referer"].endswith("/double")


@pytest.mark.parametrize("reply", [{"ok": True}, {"bet": {}},
                                    {"bet": {"id": "id", "amount": "bad"}},
                                    {"bet": {**valid_reply()["bet"], "color": 2}},
                                    {"bet": {**valid_reply()["bet"], "roulette_id": "wrong"}}])
def test_double_unconfirmed_response_is_never_accepted(monkeypatch, reply):
    api = client()
    monkeypatch.setattr(api, "_post", lambda *a, **k: reply)
    with pytest.raises(BlazeUncertainOutcome):
        api.enter("0.10", 1, "round-a")


@pytest.mark.parametrize("color", [1, 2])
@pytest.mark.parametrize("identifier", [{}, {"id": "envelope-bet-id"}])
def test_current_double_envelope_does_not_require_nested_bet_id(monkeypatch, color, identifier):
    api = client()
    reply = {**identifier, "color": color, "bet": {"amount": "0.10", "currency_type": "BRL"}}
    calls = []
    monkeypatch.setattr(api, "_post", lambda *a, **k: calls.append(a) or reply)
    assert api.enter("0.10", color, "round-a") == reply
    assert len(calls) == 1


@pytest.mark.parametrize("change", [
    {"color": 2}, {"color": True}, {"color": "1"}, {"color": None},
    {"bet": {"amount": "0.10", "currency_type": "BRL", "color": 2}},
    {"bet": {"amount": "0.20", "currency_type": "BRL"}},
    {"bet": {"amount": "nan", "currency_type": "BRL"}},
    {"bet": {"amount": "0.10", "currency_type": "USD"}},
    {"error": {"message": "sensitive-error"}}, {"success": False}, {"status": "rejected"},
    {"round_id": "other-round"}, {"roulette_game_id": "other-round"},
    {"bet": {"amount": "0.10", "currency_type": "BRL", "error": "private"}},
])
def test_current_envelope_still_rejects_inconsistent_data(monkeypatch, change):
    api = client()
    reply = {"color": 1, "bet": {"amount": "0.10", "currency_type": "BRL"}, **change}
    monkeypatch.setattr(api, "_post", lambda *a, **k: reply)
    with pytest.raises(BlazeUncertainOutcome):
        api.enter("0.10", 1, "round-a")


def test_schema_diagnostics_do_not_expose_response_values(monkeypatch):
    api = client()
    reply = {"color": "private-color", "id": "private-id", "authorization": "private-token",
             "user": {"email": "private-email"}, "private-key": "private-value",
             "bet": {"amount": "private-amount", "currency_type": "private-currency"}}
    monkeypatch.setattr(api, "_post", lambda *a, **k: reply)
    with pytest.raises(BlazeUncertainOutcome) as error:
        api.enter("0.10", 1, "round-a")
    assert "private" not in str(error.value)
    assert "color:str" in str(error.value) and "amount:str" in str(error.value)
