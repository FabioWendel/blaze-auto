from __future__ import annotations

import math
import time
from datetime import datetime

from .crash_watcher import BlazeCrashWatcher, SocketConnectionStatus


def format_socket_status(status: SocketConnectionStatus, event_name: str = "crash.tick") -> str:
    message_age = "nenhuma" if status.message_age is None else f"{status.message_age:.1f}s atrás"
    if status.tick_age is None:
        tick = "AGUARDANDO PRIMEIRO TICK"
    else:
        freshness = "RECENTE" if status.tick_age <= 2 else "SEM TICK RECENTE"
        tick = f"{status.tick_age:.1f}s atrás ({freshness})"
    return (
        f"SOCKET | {status.state} | tentativa de conexão={status.attempts}"
        f" | última mensagem={message_age} | {event_name}={tick}"
        f" | rodada={status.round_id or '-'} | estado={status.round_status or '-'}"
    )


class SocketStatusLogger:
    """Indicador informativo; não altera decisões de entrada ou reconexão."""

    def __init__(self, interval_seconds: float = 0.0, event_name: str = "crash.tick") -> None:
        if not math.isfinite(interval_seconds) or interval_seconds < 0:
            raise ValueError("--socket-log-interval deve ser finito e >= 0 (0 desliga)")
        self.interval_seconds = interval_seconds
        self.event_name = event_name
        self._last_print_at: float | None = None
        self._last_state: tuple[str, int] | None = None

    def log_if_due(self, watcher: BlazeCrashWatcher) -> None:
        if self.interval_seconds == 0:
            return
        status = watcher.connection_status()
        state = (status.state, status.attempts)
        now = time.monotonic()
        if (self._last_print_at is None or state != self._last_state
                or now - self._last_print_at >= self.interval_seconds):
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] {format_socket_status(status, self.event_name)}", flush=True)
            self._last_print_at = now
            self._last_state = state
