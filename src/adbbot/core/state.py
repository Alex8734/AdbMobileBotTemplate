"""Bot-Zustand und kooperative Steuerung (Pause/Stop/Deadline)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from enum import Enum, auto

from adbbot.core.clock import Clock
from adbbot.core.exceptions import BotStopped, StepTimeout


class BotState(Enum):
    DISCONNECTED = auto()
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()


class RunControl:
    """Thread-sicher. Wird vom GUI-/CLI-Thread gesetzt und vom Bot-Thread in `checkpoint()` gelesen.

    Pause blockiert über eine Condition (wacht bei Resume/Stop sofort auf, sonst alle
    `pause_poll_s` Sekunden echter Zeit). Die Pausendauer wird auf eine gesetzte Deadline
    aufgeschlagen, damit ein pausierter Schritt nach dem Fortsetzen nicht sofort in den Timeout läuft.
    """

    def __init__(
        self,
        clock: Clock,
        *,
        on_pause_changed: Callable[[bool], None] | None = None,
        pause_poll_s: float = 0.1,
    ) -> None:
        self._clock = clock
        self._cond = threading.Condition()
        self._stop = False
        self._pause = False
        self._deadline: float | None = None
        self.on_pause_changed = on_pause_changed
        self._pause_poll_s = pause_poll_s

    # --- Anforderungen (beliebiger Thread) ---------------------------------------------------------

    def request_pause(self) -> None:
        with self._cond:
            self._pause = True

    def request_resume(self) -> None:
        with self._cond:
            self._pause = False
            self._cond.notify_all()

    def request_stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()

    def reset(self) -> None:
        """Vor jedem neuen Flow."""
        with self._cond:
            self._stop = False
            self._pause = False
            self._deadline = None
            self._cond.notify_all()

    def set_deadline(self, monotonic_ts: float | None) -> None:
        with self._cond:
            self._deadline = monotonic_ts

    @property
    def stop_requested(self) -> bool:
        with self._cond:
            return self._stop

    @property
    def pause_requested(self) -> bool:
        with self._cond:
            return self._pause

    @property
    def deadline(self) -> float | None:
        with self._cond:
            return self._deadline

    # --- Bot-Thread --------------------------------------------------------------------------------

    def checkpoint(self) -> None:
        """1. Stop → `BotStopped`. 2. Pause → blockiert (Stop bleibt möglich). 3. Deadline → `StepTimeout`."""
        with self._cond:
            if self._stop:
                raise BotStopped
            paused = self._pause

        if paused:
            self._wait_while_paused()

        with self._cond:
            if self._deadline is not None and self._clock.monotonic() > self._deadline:
                raise StepTimeout("Zeitlimit des Schritts überschritten")

    def sleep(self, seconds: float, *, chunk_s: float = 0.1) -> None:
        """Unterbrechbares Schlafen: in kleinen Schritten, jeder mit `checkpoint()`."""
        end = self._clock.monotonic() + seconds
        self.checkpoint()
        while (remaining := end - self._clock.monotonic()) > 0:
            self._clock.sleep(min(chunk_s, remaining))
            self.checkpoint()

    def _wait_while_paused(self) -> None:
        started = self._clock.monotonic()
        self._notify_pause(True)
        try:
            with self._cond:
                while self._pause and not self._stop:
                    self._cond.wait(self._pause_poll_s)
                if self._stop:
                    raise BotStopped
                if self._deadline is not None:
                    self._deadline += self._clock.monotonic() - started
        finally:
            self._notify_pause(False)

    def _notify_pause(self, paused: bool) -> None:
        if self.on_pause_changed is not None:
            self.on_pause_changed(paused)
