import pytest
import requests

from blaze_auto.api_client import CASHOUT_URL, ENTER_URL, BlazeApiError, BlazeEntryNotSent, BlazeUncertainOutcome, CrashAccount, CrashApiClient


class FakeResponse:
    ok = True
    status_code = 200
    text = ""

    def json(self):
        return {"ok": True}


class FakeSession:
    def __init__(self):
        self.calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


def make_client():
    session = FakeSession()
    account = CrashAccount("secret", 123, "user", "gold", room_id=4)
    return CrashApiClient(account, session_factory=lambda: session), session


def test_manual_entry_payload():
    client, session = make_client()
    assert client.enter("0.10", "round-1") == {"ok": True}
    url, request = session.calls[0]
    assert url == ENTER_URL
    assert request["json"] == {
        "amount": "0.10",
        "type": "BRL",
        "auto_cashout_at": None,
        "room_id": 4,
        "username": "user",
        "rank": "gold",
        "client_round_id": "round-1",
        "wallet_id": 123,
    }
    assert request["headers"]["authorization"] == "Bearer secret"
    assert request["allow_redirects"] is False
    assert session.closed


def test_auto_cashout_entry_payload():
    client, session = make_client()
    client.enter("0.10", "round-2", "5")
    assert session.calls[0][1]["json"]["auto_cashout_at"] == "5.00"


def test_cashout_payload():
    client, session = make_client()
    client.cashout()
    url, request = session.calls[0]
    assert url == CASHOUT_URL
    assert request["json"] == {"room_id": 4, "wallet_id": 123}


def test_new_session_for_each_transaction():
    sessions = []
    def factory():
        session = FakeSession()
        sessions.append(session)
        return session
    client = CrashApiClient(CrashAccount("secret", 123, "user", "gold"), session_factory=factory)
    client.enter("1", "a", "5")
    client.enter("1", "b", "5")
    assert len(sessions) == 2
    assert all(session.closed and len(session.calls) == 1 for session in sessions)


@pytest.mark.parametrize("error", [requests.ConnectionError, requests.Timeout, requests.ReadTimeout])
def test_network_error_is_uncertain_and_never_retried(error):
    class BrokenSession(FakeSession):
        def post(self, url, **kwargs):
            self.calls.append(url)
            raise error("sensitive response should not be logged")
    session = BrokenSession()
    client = CrashApiClient(CrashAccount("secret", 123, "user", "gold"), session_factory=lambda: session)
    with pytest.raises(BlazeUncertainOutcome) as raised:
        client.enter("1", "a", "5")
    assert "sensitive" not in str(raised.value)
    assert len(session.calls) == 1
    assert session.closed


def test_connect_timeout_is_not_sent_and_client_itself_never_retries():
    class BrokenSession(FakeSession):
        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            raise requests.ConnectTimeout("sensitive")
    session = BrokenSession()
    client = CrashApiClient(CrashAccount("secret", 123, "user", "gold"), session_factory=lambda: session)
    with pytest.raises(BlazeEntryNotSent) as raised:
        client.enter("1", "a", "5", timeout=0.25)
    assert "sensitive" not in str(raised.value)
    assert len(session.calls) == 1
    assert session.calls[0][1]["timeout"] == 0.25
    assert session.closed


@pytest.mark.parametrize("status", [302, 307, 408, 500, 502, 503])
def test_ambiguous_http_status_does_not_retry(status):
    class AmbiguousSession(FakeSession):
        def post(self, url, **kwargs):
            response = super().post(url, **kwargs)
            response.status_code = status
            return response
    session = AmbiguousSession()
    client = CrashApiClient(CrashAccount("secret", 123, "user", "gold"), session_factory=lambda: session)
    with pytest.raises(BlazeUncertainOutcome):
        client.cashout()
    assert len(session.calls) == 1


def test_explicit_rejection_is_not_uncertain():
    class RejectedSession(FakeSession):
        def post(self, url, **kwargs):
            response = super().post(url, **kwargs)
            response.status_code = 401
            return response
    client = CrashApiClient(CrashAccount("secret", 123, "user", "gold"), session_factory=RejectedSession)
    with pytest.raises(BlazeApiError) as raised:
        client.enter("1", "a", "5")
    assert not isinstance(raised.value, BlazeUncertainOutcome)


@pytest.mark.parametrize("body", [None, {}, [], "html"])
def test_unexpected_success_body_requires_review(body):
    class UnexpectedResponse(FakeResponse):
        def json(self):
            if body == "html":
                raise ValueError("not JSON")
            return body
    class UnexpectedSession(FakeSession):
        def post(self, *args, **kwargs):
            return UnexpectedResponse()
    client = CrashApiClient(CrashAccount("secret", 123, "user", "gold"), session_factory=UnexpectedSession)
    with pytest.raises(BlazeUncertainOutcome):
        client.enter("1", "a", "5")
