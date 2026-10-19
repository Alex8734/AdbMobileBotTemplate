"""StatusBar: Zustand, FPS, Laufzeit, Alter des letzten Frames, Watcher-Auslösungen."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QElapsedTimer, QTimer
from PySide6.QtWidgets import QLabel, QStatusBar, QWidget

from adbbot.core.state import BotState

STATE_COLORS = {
    BotState.DISCONNECTED: "#ff6b6b",
    BotState.IDLE: "#9aa0aa",
    BotState.RUNNING: "#a3d977",
    BotState.PAUSED: "#ebcb8b",
    BotState.STOPPING: "#ebcb8b",
}
_ACTIVE = (BotState.RUNNING, BotState.PAUSED, BotState.STOPPING)
_FPS_WINDOW_MS = 2000


class BotStatusBar(QStatusBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state_label = QLabel()
        self.fps_label = QLabel()
        self.runtime_label = QLabel()
        self.frame_label = QLabel()
        self.watcher_label = QLabel()
        for label in (
            self.state_label,
            self.fps_label,
            self.runtime_label,
            self.frame_label,
            self.watcher_label,
        ):
            self.addPermanentWidget(label)

        self._clock = QElapsedTimer()
        self._clock.start()
        self._frames: deque[int] = deque(maxlen=240)
        self._last_frame_ms: int | None = None
        self._run_started_ms: int | None = None
        self._watchers = 0
        self._state = BotState.DISCONNECTED
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def set_state(self, state: BotState) -> None:
        if state in _ACTIVE:
            if self._run_started_ms is None:
                self._run_started_ms = self._clock.elapsed()
        else:
            self._run_started_ms = None
        self._state = state
        self.refresh()

    def frame_received(self) -> None:
        now = self._clock.elapsed()
        self._frames.append(now)
        self._last_frame_ms = now

    def watcher_triggered(self) -> None:
        self._watchers += 1
        self.refresh()

    def fps(self) -> float:
        now = self._clock.elapsed()
        while self._frames and now - self._frames[0] > _FPS_WINDOW_MS:
            self._frames.popleft()
        if len(self._frames) < 2:
            return 0.0
        span = self._frames[-1] - self._frames[0]
        return (len(self._frames) - 1) * 1000 / span if span > 0 else 0.0

    def refresh(self) -> None:
        now = self._clock.elapsed()
        self.state_label.setText(f"<b style='color:{STATE_COLORS[self._state]}'>{self._state.name}</b>")
        self.fps_label.setText(f"{self.fps():.1f} fps")
        if self._run_started_ms is None:
            self.runtime_label.setText("Laufzeit –")
        else:
            seconds = (now - self._run_started_ms) // 1000
            self.runtime_label.setText(
                f"Laufzeit {seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
            )
        if self._last_frame_ms is None:
            self.frame_label.setText("kein Frame")
        else:
            self.frame_label.setText(f"letzter Frame vor {(now - self._last_frame_ms) / 1000:.1f} s")
        self.watcher_label.setText(f"Watcher {self._watchers}")

    def summary(self) -> str:
        labels = (self.state_label, self.fps_label, self.runtime_label, self.frame_label, self.watcher_label)
        return " · ".join(_plain(label.text()) for label in labels)


def _plain(text: str) -> str:
    """Einfache HTML-Tags entfernen (für Tests und Tooltips)."""
    out, inside = [], False
    for char in text:
        if char == "<":
            inside = True
        elif char == ">":
            inside = False
        elif not inside:
            out.append(char)
    return "".join(out)
