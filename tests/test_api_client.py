from blaze_auto.api_client import CASHOUT_URL, ENTER_URL, CrashAccount, CrashApiClient


class FakeResponse:
    ok = True
    status_code = 200
    text = ""

    def json(self):
        return {"ok": True}


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


def make_client():
    session = FakeSession()
    account = CrashAccount("secret", 123, "user", "gold", room_id=4)
    return CrashApiClient(account, session=session), session


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
