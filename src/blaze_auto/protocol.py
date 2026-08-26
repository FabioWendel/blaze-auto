from __future__ import annotations

import json
from typing import Any


CRASH_EVENT = "crash.tick"
CRASH_BETS_EVENT = "crash.tick-bets"


def build_subscribe_message(room: str) -> str:
    payload = ["cmd", {"id": "subscribe", "payload": {"room": room}}]
    return "42" + json.dumps(payload, separators=(",", ":"))


def parse_engineio_message(message: str) -> list[Any] | None:
    """Extrai os itens do evento Socket.IO `data`.

    Mensagens de handshake, ping/pong e eventos desconhecidos retornam None.
    """
    if not message.startswith("42"):
        return None
    try:
        parsed = json.loads(message[2:])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, list) or len(parsed) < 2 or parsed[0] != "data":
        return None
    payload = parsed[1]
    return payload if isinstance(payload, list) else [payload]


def extract_crash_ticks(message: str) -> list[dict[str, Any]]:
    items = parse_engineio_message(message) or []
    ticks: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("id") != CRASH_EVENT:
            continue
        payload = item.get("payload")
        if isinstance(payload, dict):
            ticks.append(payload)
    return ticks


def extract_crash_bets(message: str) -> list[dict[str, Any]]:
    items = parse_engineio_message(message) or []
    events: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("id") != CRASH_BETS_EVENT:
            continue
        payload = item.get("payload")
        if isinstance(payload, dict):
            events.append(payload)
    return events


def normalize_completed_round(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normaliza somente resultados completos e válidos."""
    if payload.get("status") != "complete":
        return None
    round_id = payload.get("id")
    crash_point = payload.get("crash_point")
    if round_id is None or crash_point is None:
        return None
    try:
        point = float(crash_point)
    except (TypeError, ValueError):
        return None
    return {
        "id": str(round_id),
        "updated_at": str(payload.get("updated_at") or ""),
        "crash_point": point,
        "is_bonus_round": bool(payload.get("is_bonus_round")),
        "total_bets_placed": _optional_int(payload.get("total_bets_placed")),
    }


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
