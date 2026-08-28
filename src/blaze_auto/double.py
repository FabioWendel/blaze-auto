"""Protocolo público e executor do Double (sala 1, separado do Crash)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .api_client import CrashApiClient, BlazeUncertainOutcome, decimal_text
from .crash_watcher import BlazeCrashWatcher, DEFAULT_WS_URL
from .protocol import parse_engineio_message


DOUBLE_ENTER_URL = "https://blaze.bet.br/api/singleplayer-originals/originals/roulette_bets"
COLOR_NAMES = {0: "branco", 1: "vermelho", 2: "preto"}


def response_shape(body: dict[str, Any]) -> str:
    """Only known field names and types; never response values or user data."""
    fields = ("id", "color", "amount", "currency_type", "status", "error", "success",
              "roulette_id", "roulette_game_id", "round_id")
    def describe(value: Any) -> str:
        if not isinstance(value, dict):
            return type(value).__name__
        return ",".join(f"{key}:{type(value[key]).__name__}" for key in fields if key in value) or "sem_campos_conhecidos"
    return f"raiz[{describe(body)}]; bet[{describe(body.get('bet'))}]"


def validate_entry_response(body: dict[str, Any], amount_text: str, color: int,
                            expected_round_id: str) -> None:
    """Validate the Double envelope, not a supposed bet.id/bet.color schema.

    The official DOUBLE_V2/OWN_BET reducer reads response.color; the POST
    handler reads response.bet.amount and response.bet.currency_type.
    A nested bet ID is not part of that acceptance contract.
    """
    def uncertain(reason: str) -> None:
        raise BlazeUncertainOutcome(f"Double: {reason}; formato={response_shape(body)}; confira a conta")

    bet = body.get("bet")
    if not isinstance(bet, dict) or not bet:
        uncertain("objeto bet ausente ou inválido")
    for obj in (body, bet):
        if obj.get("error") or obj.get("success") is False:
            uncertain("resposta contém indicação de erro")
        if obj.get("status") in ("rejected", "failed", "cancelled", "canceled", "error"):
            uncertain("resposta não indica aceitação")

    # Accept the current root color and the older all-nested representation.
    # If both are supplied, both must match; never hide a contradiction.
    colors = [obj["color"] for obj in (body, bet) if "color" in obj]
    if not colors or any(type(value) is not int or value != color for value in colors):
        uncertain("cor ausente ou diferente da entrada")
    try:
        amount = Decimal(str(bet.get("amount")))
        valid_amount = amount.is_finite() and amount == Decimal(amount_text)
    except (InvalidOperation, ValueError):
        valid_amount = False
    if not valid_amount:
        uncertain("valor ausente ou diferente da entrada")
    if bet.get("currency_type") != "BRL":
        uncertain("moeda ausente ou diferente de BRL")
    for obj in (body, bet):
        # An envelope's plain id can identify the bet, not the round.
        # Only explicitly named round identifiers are compared here.
        for key in ("roulette_id", "roulette_game_id", "round_id"):
            if obj.get(key) is not None and str(obj[key]) != expected_round_id:
                uncertain("rodada da resposta diverge da entrada")


def extract_double_ticks(message: str) -> list[dict[str, Any]]:
    return [item["payload"] for item in parse_engineio_message(message) or []
            if isinstance(item, dict) and item.get("id") == "double.tick"
            and isinstance(item.get("payload"), dict)]


def normalize_double_result(payload: dict[str, Any]) -> dict[str, Any] | None:
    # rolling already contains the color, but only complete settles a round.
    if payload.get("status") != "complete" or not payload.get("id"):
        return None
    color, roll = payload.get("color"), payload.get("roll")
    if type(color) is not int or type(roll) is not int or not 0 <= roll <= 14:
        return None
    expected = 0 if roll == 0 else 1 if roll <= 7 else 2
    if color != expected:
        return None
    return {"id": str(payload["id"]), "color": color, "roll": roll,
            "updated_at": str(payload.get("updated_at") or "")}


class BlazeDoubleWatcher(BlazeCrashWatcher):
    def __init__(self, url: str = DEFAULT_WS_URL, reconnect_seconds: float = 3.0) -> None:
        super().__init__(url=url, room="double_room_1", bets_room=None,
                         reconnect_seconds=reconnect_seconds)

    _extract_ticks = staticmethod(extract_double_ticks)
    _normalize_result = staticmethod(normalize_double_result)


class DoubleApiClient(CrashApiClient):
    def enter(self, amount: str | Decimal, color: int, expected_round_id: str,
              *, timeout: float | None = None) -> dict[str, Any]:
        if type(color) is not int or color not in (1, 2):
            raise ValueError("Double aceita somente vermelho (1) ou preto (2)")
        amount_text = decimal_text(amount, "amount", Decimal("0.01"))
        body = self._post(DOUBLE_ENTER_URL, {
            "amount": amount_text, "currency_type": "BRL", "color": color,
            "free_bet": False, "room_id": 1, "username": self.account.username,
            "rank": self.account.rank, "wallet_id": self.account.wallet_id,
        }, timeout=timeout)
        validate_entry_response(body, amount_text, color, expected_round_id)
        return body

    def _headers(self) -> dict[str, str]:
        return {**super()._headers(), "referer": "https://blaze.bet.br/pt/games/double"}
