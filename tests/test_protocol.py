import json

from blaze_auto.protocol import (
    build_subscribe_message,
    extract_crash_bets,
    extract_crash_ticks,
    normalize_completed_round,
    parse_engineio_message,
)


def socket_message(payload):
    return "42" + json.dumps(["data", {"id": "crash.tick", "payload": payload}])


def test_builds_crash_room_subscription():
    assert build_subscribe_message("crash_room_1") == (
        '42["cmd",{"id":"subscribe","payload":{"room":"crash_room_1"}}]'
    )


def test_ignores_handshake_ping_and_unknown_event():
    assert parse_engineio_message('0{"sid":"abc"}') is None
    assert parse_engineio_message("2") is None
    unknown = "42" + json.dumps(["other", {}])
    assert parse_engineio_message(unknown) is None


def test_extracts_crash_tick():
    payload = {"id": "round-1", "status": "waiting", "crash_point": None}
    assert extract_crash_ticks(socket_message(payload)) == [payload]


def test_extracts_crash_tick_bets():
    payload = {"id": "round-1", "bets": [{"id": "bet-1", "status": "created"}]}
    message = "42" + json.dumps(["data", {"id": "crash.tick-bets", "payload": payload}])
    assert extract_crash_bets(message) == [payload]
    assert extract_crash_ticks(message) == []


def test_only_normalizes_completed_round():
    waiting = {"id": "round-1", "status": "waiting", "crash_point": None}
    assert normalize_completed_round(waiting) is None
    complete = {
        "id": "round-1",
        "updated_at": "2026-08-26T21:15:58.370Z",
        "status": "complete",
        "crash_point": "1.15",
        "is_bonus_round": False,
        "total_bets_placed": 1,
    }
    assert normalize_completed_round(complete) == {
        "id": "round-1",
        "updated_at": "2026-08-26T21:15:58.370Z",
        "crash_point": 1.15,
        "is_bonus_round": False,
        "total_bets_placed": 1,
    }
