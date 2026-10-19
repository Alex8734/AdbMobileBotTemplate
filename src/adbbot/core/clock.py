"""Zeitquelle. Alle Warte- und Timeout-Logik benutzt ausschließlich eine `Clock`, nie `time` direkt."""

from __future__ import annotations

import threading
import time
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock:
    """Für Tests: `sleep` rückt nur die Zeit vor und kehrt sofort zurück."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._lock = threading.Lock()
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self._now += max(0.0, seconds)

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds
