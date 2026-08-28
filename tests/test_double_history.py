from datetime import datetime, timedelta, timezone

import pytest

from blaze_auto import double_history_cli as history


START, END = "2026-07-30T04:00:00.000Z", "2026-07-31T03:59:59.999Z"


@pytest.mark.parametrize("count", [84730, "84730"])
def test_record_count_accepts_integer_or_api_decimal_string(count):
    assert history.validate_page({"records": [], "total_pages": count})["total_pages"] == 84730


def rows(n):
    start = datetime(2026, 7, 30, 4, tzinfo=timezone.utc)
    return [dict(id=str(i), created_at=history.iso_z(start + timedelta(seconds=i * 30)), color=1, roll=5) for i in range(n)]


def test_record_count_not_number_of_pages_and_resume(tmp_path, monkeypatch):
    data = rows(201)
    calls = []
    class Client:
        def __init__(self, *args):
            pass
        def fetch(self, page):
            calls.append(page)
            return {"records": data[(page - 1) * 100:page * 100], "total_pages": len(data)}
    monkeypatch.setattr(history, "PageClient", Client)
    out = tmp_path / "history"
    meta = history.download(out, START, END)
    assert sorted(calls) == [1, 2, 3, 4]
    assert meta["record_count"] == 201 and meta["pages_with_records"] == 3
    assert meta["complete_for_requested_interval"]
    with pytest.raises(history.DownloadError):
        history.download(out, START, "2026-08-01T03:59:59.999Z")


@pytest.mark.parametrize("change", [{"color": 2}, {"roll": 15}, {"color": True},
                                    {"created_at": "2026-01-01T00:00:00Z"}, {"id": ""}])
def test_invalid_round_never_silently_dropped(change):
    row = {**rows(1)[0], **change}
    with pytest.raises(history.DownloadError):
        history.validate_round(row, history.parse_iso(START), history.parse_iso(END))


def test_aggregation_uses_manaus_and_keeps_white():
    data = [dict(id="a", created_at="2026-07-30T04:00:00Z", color=0, roll=0),
            dict(id="b", created_at="2026-07-31T03:59:00Z", color=2, roll=10)]
    result = history.aggregate(data)
    assert result["hours"][0]["white"] == 1
    assert result["hours"][23]["black"] == 1
    assert result["rounds_by_local_day"] == {"2026-07-30": 2}


def test_duplicate_page_blocks_consolidation(tmp_path, monkeypatch):
    data = rows(100)
    class Client:
        def __init__(self, *args):
            pass
        def fetch(self, page):
            return {"records": data if page <= 2 else [], "total_pages": 200}
    monkeypatch.setattr(history, "PageClient", Client)
    with pytest.raises(history.DownloadError, match="duplicado"):
        history.download(tmp_path / "history", START, END)
    assert not (tmp_path / "history" / "summary.json").exists()


def test_future_end_is_clipped_and_resume_preserves_cutoff(tmp_path, monkeypatch):
    frozen = datetime(2026, 8, 28, 16, 19, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen
    ends = []
    class Client:
        def __init__(self, output, start, end):
            ends.append(end)
        def fetch(self, page):
            return {"records": rows(1) if page == 1 else [], "total_pages": 1}
    monkeypatch.setattr(history, "datetime", Clock)
    monkeypatch.setattr(history, "PageClient", Client)
    out = tmp_path / "history"
    requested = "2026-08-29T03:59:59.999Z"
    first = history.download(out, START, requested)
    frozen += timedelta(hours=1)
    second = history.download(out, START, requested)
    assert first["effective_end_utc"] == second["effective_end_utc"] == "2026-08-28T16:17:00.000Z"
    assert not first["complete_for_requested_interval"]
    assert first["complete_for_effective_interval"]
    assert len(set(ends)) == 1


def test_page_cache_does_not_repeat_http_or_require_credentials(tmp_path, monkeypatch):
    class Session:
        headers = {}
        calls = []
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return type("Response", (), {"status_code": 200, "json": lambda self: {"records": rows(1), "total_pages": "1"}})()
    session = Session()
    monkeypatch.setattr(history.requests, "Session", lambda: session)
    monkeypatch.setattr(history.RateGate, "wait", lambda *a: None)
    client = history.PageClient(tmp_path, START, END)
    assert client.fetch(1)["total_pages"] == 1
    assert client.fetch(1)["total_pages"] == 1
    assert len(session.calls) == 1
    assert "authorization" not in session.headers
    assert session.calls[0][1]["allow_redirects"] is False


def test_extra_nonempty_page_blocks_complete_marker(tmp_path, monkeypatch):
    class Client:
        def __init__(self, *args):
            pass
        def fetch(self, page):
            return {"records": rows(1), "total_pages": 1}
    monkeypatch.setattr(history, "PageClient", Client)
    with pytest.raises(history.DownloadError, match="paginação"):
        history.download(tmp_path / "history", START, END)
    assert not (tmp_path / "history" / "summary.json").exists()
