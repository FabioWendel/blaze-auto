import json
from types import SimpleNamespace

import pytest

from blaze_auto import account_history_cli as history


START, END = "2026-07-28T16:13:06.480Z", "2026-08-28T16:13:06.480Z"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(history.time, "sleep", lambda *a: None)


def row(identity, slug="double", created="2026-08-20T12:00:00Z"):
    return dict(id=identity, slug=slug, created_at=created, amount="1.00")


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def response(body, status=200):
    return SimpleNamespace(status_code=status, headers={}, json=lambda: body)


def test_download_paginates_keeps_raw_filters_and_splits_double(tmp_path):
    session = Session([
        response(dict(total_pages=2, records=[row("a"), row("b", "crash")], games_played=[])),
        response(dict(total_pages=2, records=[row("a"), row("c", created="2026-07-01T00:00:00Z")]))])
    output = tmp_path / "new"
    meta = history.download(session, START, END, output)
    assert meta["pages_downloaded"] == 2 and meta["raw_record_count"] == 4
    assert meta["record_count"] == 2 and meta["duplicates"] == 1 and meta["outside_interval"] == 1
    assert len(list((output / "pages").glob("*.json"))) == 2
    assert len(json.loads((output / "double_account_history.json").read_text(encoding="utf-8"))["records"]) == 1
    assert [call[1]["params"]["page"] for call in session.calls] == [1, 2]
    assert all(call[1]["allow_redirects"] is False for call in session.calls)


@pytest.mark.parametrize("status", [301, 401, 403])
def test_no_redirect_or_auth_retry(tmp_path, status):
    session = Session([response({}, status)])
    with pytest.raises(history.DownloadError):
        history.download(session, START, END, tmp_path / "new")
    assert len(session.calls) == 1


def test_rate_limit_read_retry_is_bounded():
    session = Session([response({}, 429)] * 4)
    with pytest.raises(history.DownloadError):
        history.fetch_page(session, 1, START, END)
    assert len(session.calls) == 4


def test_changing_pagination_is_partial_not_complete(tmp_path):
    session = Session([response(dict(total_pages=2, records=[row("a")])),
                       response(dict(total_pages=3, records=[row("b")]))])
    output = tmp_path / "new"
    with pytest.raises(history.DownloadError):
        history.download(session, START, END, output)
    assert not (output / "summary.json").exists()
    assert len(list((output / "pages").glob("*.json"))) == 2


def test_fixed_page_ceiling_empty_next_and_last_are_recorded(tmp_path):
    session = Session([response(dict(total_pages=250, records=[row("a", "double_room_1")])),
                       response(dict(total_pages=250, records=[])),
                       response(dict(total_pages=250, records=[])),
                       response(dict(total_pages=250, records=[]))])
    output = tmp_path / "new"
    meta = history.download(session, START, END, output)
    assert meta["record_count"] == 1
    assert meta["terminal_empty_page"] == 2
    assert meta["empty_probe_pages"] == [3, 250]
    assert not meta["all_reported_pages_downloaded"]
    assert meta["pages_downloaded"] == 4
    assert (output / "double_account_history.json").exists()


def test_nonempty_page_after_empty_page_requires_review(tmp_path):
    session = Session([response(dict(total_pages=250, records=[])),
                       response(dict(total_pages=250, records=[row("a")]))])
    output = tmp_path / "new"
    with pytest.raises(history.DownloadError):
        history.download(session, START, END, output)
    assert not (output / "account_history.json").exists()
