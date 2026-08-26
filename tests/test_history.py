from datetime import datetime, timezone

from blaze_auto.history import extract_records, filter_interval


def test_extracts_paginated_response():
    records, pages = extract_records({"records": [{"id": "a"}], "total_pages": 12})
    assert records == [{"id": "a"}]
    assert pages == 12


def test_filters_deduplicates_and_sorts_interval():
    rows = [
        {
            "id": "new",
            "status": "complete",
            "created_at": "2026-08-20T00:00:00.000Z",
            "crash_point": "2.50",
            "server_seed": "b",
            "is_bonus_round": False,
        },
        {
            "id": "old",
            "status": "complete",
            "created_at": "2026-08-10T00:00:00.000Z",
            "crash_point": "1.10",
            "server_seed": "a",
            "is_bonus_round": False,
        },
        {
            "id": "new",
            "status": "complete",
            "created_at": "2026-08-20T00:00:00.000Z",
            "crash_point": "2.50",
            "server_seed": "b",
            "is_bonus_round": False,
        },
        {
            "id": "outside",
            "status": "complete",
            "created_at": "2026-07-01T00:00:00.000Z",
            "crash_point": "10.00",
        },
    ]
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 26, tzinfo=timezone.utc)
    result = filter_interval(rows, start, end)
    assert [row["id"] for row in result] == ["old", "new"]
    assert result[1]["crash_point"] == 2.5
