from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from .protocol import (
    build_subscribe_message,
    extract_crash_bets,
    extract_crash_ticks,
    normalize_completed_round,
)


DEFAULT_WS_URL = "wss://api-gaming.blaze.bet.br/replication/?EIO=3&transport=websocket"
DEFAULT_CRASH_ROOM = "crash_room_4"
DEFAULT_CRASH_BETS_ROOM = "crash_room_4:bets"
DEFAULT_ORIGIN = "https://blaze.bet.br"


@dataclass(frozen=True)
class SocketConnectionStatus:
    state: str
    attempts: int
    message_age: float | None
    tick_age: float | None
    round_id: str
    round_status: str


@dataclass(frozen=True)
class CrashSnapshot:
    status: str = ""
    round_id: str = ""
    updated_at: str = ""
    crash_point: float | None = None
    is_bonus_round: bool | None = None
    total_bets_placed: int | None = None
    received_at: float = 0.0
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class CrashBetsSnapshot:
    round_id: str = ""
    total_bets: int = 0
    total_amount: float = 0.0
    created_bets: int = 0
    won_bets: int = 0
    lost_bets: int = 0
    total_bets_placed: int | None = None
    received_at: float = 0.0


class BlazeCrashWatcher:
    """Cliente em background para o evento público `crash.tick`."""

    def __init__(
        self,
        url: str = DEFAULT_WS_URL,
        room: str = DEFAULT_CRASH_ROOM,
        reconnect_seconds: float = 3.0,
        bets_room: str | None = DEFAULT_CRASH_BETS_ROOM,
    ) -> None:
        self.url = url
        self.room = room
        self.reconnect_seconds = reconnect_seconds
        self.bets_room = bets_room
        self._lock = threading.Lock()
        self._snapshot = CrashSnapshot()
        self._bets_snapshot = CrashBetsSnapshot()
        self._rounds: deque[dict[str, Any]] = deque(maxlen=1_000)
        self._emitted_ids: set[str] = set()
        self._thread: threading.Thread | None = None
        self._ws_app: Any | None = None
        self._stop = threading.Event()
        self._last_error = ""
        self._connection_state = "CONECTANDO"
        self._connection_attempts = 0
        self._last_message_monotonic: float | None = None
        self._last_tick_monotonic: float | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="blaze-crash-ws", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._lock:
            self._connection_state = "PARADO"
            app = self._ws_app
        if app is not None:
            app.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def snapshot(self) -> CrashSnapshot:
        with self._lock:
            return self._snapshot

    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def connection_status(self) -> SocketConnectionStatus:
        with self._lock:
            now = time.monotonic()
            state = self._connection_state
            if self._thread is not None and not self._thread.is_alive() and not self._stop.is_set():
                state = "DESCONECTADO"
            return SocketConnectionStatus(
                state=state,
                attempts=self._connection_attempts,
                message_age=None if self._last_message_monotonic is None else max(0, now - self._last_message_monotonic),
                tick_age=None if self._last_tick_monotonic is None else max(0, now - self._last_tick_monotonic),
                round_id=self._snapshot.round_id if self._last_tick_monotonic is not None else "",
                round_status=self._snapshot.status if self._last_tick_monotonic is not None else "",
            )

    def bets_snapshot(self) -> CrashBetsSnapshot:
        with self._lock:
            return self._bets_snapshot

    def pop_completed_rounds(self) -> list[dict[str, Any]]:
        with self._lock:
            rounds = list(self._rounds)
            self._rounds.clear()
        rounds.sort(key=lambda row: row.get("updated_at") or "")
        return rounds

    def _run(self) -> None:
        try:
            import websocket
        except ImportError:
            with self._lock:
                self._last_error = "dependência websocket-client não instalada"
                self._connection_state = "DESCONECTADO"
            return

        while not self._stop.is_set():
            with self._lock:
                self._connection_attempts += 1
                self._connection_state = "CONECTANDO" if self._connection_attempts == 1 else "RECONECTANDO"
            app = websocket.WebSocketApp(
                self.url,
                header=[
                    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149 Safari/537.36",
                    "Accept-Language: pt-BR,pt;q=0.9",
                    "Cache-Control: no-cache",
                ],
                on_message=self._on_message,
                on_open=self._on_open,
                on_pong=self._on_pong,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            with self._lock:
                self._ws_app = app
            app.run_forever(origin=DEFAULT_ORIGIN, ping_interval=20, ping_timeout=10)
            with self._lock:
                if self._ws_app is app:
                    self._ws_app = None
                self._connection_state = "PARADO" if self._stop.is_set() else "RECONECTANDO"
            if not self._stop.wait(self.reconnect_seconds):
                continue

    def _on_open(self, _ws: Any) -> None:
        with self._lock:
            self._connection_state = "PARADO" if self._stop.is_set() else "CONECTADO"
            # Uma conexão nova não herda indicadores de atividade da anterior.
            self._last_message_monotonic = None
            self._last_tick_monotonic = None

    def _on_pong(self, _ws: Any, _message: Any) -> None:
        with self._lock:
            self._last_message_monotonic = time.monotonic()

    def _on_error(self, _ws: Any, error: Any) -> None:
        with self._lock:
            self._last_error = str(error)
            self._connection_state = "PARADO" if self._stop.is_set() else "RECONECTANDO"

    def _on_close(self, _ws: Any, _code: Any, _message: Any) -> None:
        with self._lock:
            self._connection_state = "PARADO" if self._stop.is_set() else "RECONECTANDO"
            if not self._last_error and not self._stop.is_set():
                self._last_error = "websocket fechado"

    def _on_message(self, ws: Any, message: str) -> None:
        with self._lock:
            self._last_message_monotonic = time.monotonic()
        if message.startswith("0"):
            ws.send("40")
            return
        if message == "40":
            ws.send(build_subscribe_message(self.room))
            if self.bets_room and self.bets_room != self.room:
                ws.send(build_subscribe_message(self.bets_room))
            return
        if message == "2":
            ws.send("3")
            return

        for payload in extract_crash_ticks(message):
            snapshot = _snapshot_from_payload(payload)
            completed = normalize_completed_round(payload)
            with self._lock:
                self._snapshot = snapshot
                self._last_tick_monotonic = time.monotonic()
                if completed and completed["id"] not in self._emitted_ids:
                    self._rounds.append(completed)
                    self._emitted_ids.add(completed["id"])
                self._last_error = ""

        for payload in extract_crash_bets(message):
            bets_snapshot = _bets_snapshot_from_payload(payload)
            with self._lock:
                self._bets_snapshot = bets_snapshot
                self._last_error = ""


def _snapshot_from_payload(payload: dict[str, Any]) -> CrashSnapshot:
    point = payload.get("crash_point")
    try:
        crash_point = float(point) if point is not None else None
    except (TypeError, ValueError):
        crash_point = None
    total = payload.get("total_bets_placed")
    try:
        total_bets = int(total) if total is not None else None
    except (TypeError, ValueError):
        total_bets = None
    bonus = payload.get("is_bonus_round")
    return CrashSnapshot(
        status=str(payload.get("status") or ""),
        round_id=str(payload.get("id") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        crash_point=crash_point,
        is_bonus_round=bool(bonus) if bonus is not None else None,
        total_bets_placed=total_bets,
        received_at=time.time(),
        raw=dict(payload),
    )


def _bets_snapshot_from_payload(payload: dict[str, Any]) -> CrashBetsSnapshot:
    bets = payload.get("bets")
    if not isinstance(bets, list):
        bets = []
    valid_bets = [bet for bet in bets if isinstance(bet, dict)]
    total_amount = 0.0
    status_counts = {"created": 0, "win": 0, "lost": 0}
    for bet in valid_bets:
        try:
            total_amount += float(bet.get("amount") or 0)
        except (TypeError, ValueError):
            pass
        status = str(bet.get("status") or "")
        if status in status_counts:
            status_counts[status] += 1
    total_placed = payload.get("total_bets_placed")
    try:
        total_bets_placed = int(total_placed) if total_placed is not None else None
    except (TypeError, ValueError):
        total_bets_placed = None
    return CrashBetsSnapshot(
        round_id=str(payload.get("id") or ""),
        total_bets=len(valid_bets),
        total_amount=total_amount,
        created_bets=status_counts["created"],
        won_bets=status_counts["win"],
        lost_bets=status_counts["lost"],
        total_bets_placed=total_bets_placed,
        received_at=time.time(),
    )
