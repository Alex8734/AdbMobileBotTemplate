"""Fehler-Snapshots: Frame mit Overlays + Log-Auszug nach `data/failures/`.

Ohne Bild vom Moment des Fehlers ist Debugging von Bots Raten.
"""

from __future__ import annotations

import json
import threading
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from adbbot.core.events import EventBus, LogEmitted
from adbbot.core.exceptions import BotError
from adbbot.utils.image import save_image
from adbbot.utils.text import slugify
from adbbot.vision.vision import Vision

_REPORTED_ATTR = "_adbbot_reported"


def mark_reported(error: BaseException) -> None:
    """Fehler wurde geloggt und gesnapshottet – äußere Steps wiederholen ihn nicht erneut."""
    setattr(error, _REPORTED_ATTR, True)


def was_reported(error: BaseException) -> bool:
    return bool(getattr(error, _REPORTED_ATTR, False))


class RecentLogs:
    """Ringpuffer der letzten Log-Einträge vom EventBus."""

    def __init__(self, bus: EventBus, maxlen: int = 200) -> None:
        self._items: deque[LogEmitted] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._unsubscribe = bus.subscribe(LogEmitted, self._append)

    def _append(self, event: LogEmitted) -> None:
        with self._lock:
            self._items.append(event)

    def lines(self, count: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._items)[-count:]
        return [
            {
                "time": datetime.fromtimestamp(e.ts).strftime("%H:%M:%S.%f")[:-3],
                "level": e.level,
                "source": e.source,
                "message": e.message,
                "context": e.context,
            }
            for e in items
        ]

    def close(self) -> None:
        self._unsubscribe()


class FailureRecorder:
    def __init__(self, vision: Vision, bus: EventBus, directory: Path, *, log_lines: int = 50) -> None:
        self.directory = directory
        self._vision = vision
        self._recent = RecentLogs(bus)
        self._log_lines = log_lines

    def record(self, *, flow: str, step: str | None, error: BaseException) -> Path:
        """Speichert `<zeit>_<flow>_<step>.png` (falls ein Frame verfügbar ist) und `.json`."""
        frame = self._vision.last_frame
        if frame is None:
            try:
                frame = self._vision.capture()
            except BotError:
                frame = None

        now = datetime.now()
        base = (
            f"{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}_{slugify(flow, 30)}_{slugify(step or 'flow')}"
        )
        self.directory.mkdir(parents=True, exist_ok=True)

        image_name: str | None = None
        if frame is not None:
            detections = [d for d in self._vision.overlay.detections if d.frame_ts == frame.ts]
            image_name = f"{base}.png"
            save_image(self.directory / image_name, self._vision.overlay.render(frame.image, detections))

        report = {
            "time": now.isoformat(timespec="seconds"),
            "flow": flow,
            "step": step,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exception(error),
            "screenshot": image_name,
            "logs": self._recent.lines(self._log_lines),
        }
        path = self.directory / f"{base}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path
