"""DetectionStore: sammelt die Erkennungen zum aktuellen Frame und rendert sie in ein Bild.

Die GUI zeichnet aus den `DetectionsUpdated`-Events, Fehler-Snapshots nutzen `render()`.
"""

from __future__ import annotations

import itertools
import threading

import cv2

from adbbot.core.events import DetectionsUpdated, EventBus
from adbbot.core.types import Detection, Image, Match, Rect

# BGR
KIND_COLORS: dict[str, tuple[int, int, int]] = {
    "found": (80, 200, 80),
    "missing": (60, 60, 230),
    "text": (40, 200, 230),
    "mark": (230, 160, 40),
}


class DetectionStore:
    def __init__(self, bus: EventBus | None = None, *, max_items: int = 500) -> None:
        self._bus = bus
        self._max_items = max_items
        self._lock = threading.Lock()
        self._frame_ts: float | None = None
        self._items: list[Detection] = []

    @property
    def frame_ts(self) -> float | None:
        with self._lock:
            return self._frame_ts

    @property
    def detections(self) -> list[Detection]:
        with self._lock:
            return list(self._items)

    def new_frame(self, frame_ts: float) -> None:
        """Wird von `Vision.capture()` aufgerufen – alte Detections verfallen."""
        with self._lock:
            self._frame_ts = frame_ts
            self._items = []

    def add(self, detection: Detection) -> None:
        """Detections zu einem älteren Frame als dem aktuellen werden verworfen."""
        with self._lock:
            if self._frame_ts is not None and detection.frame_ts < self._frame_ts:
                return
            if self._frame_ts is None or detection.frame_ts > self._frame_ts:
                self._frame_ts = detection.frame_ts
                self._items = []
            self._items.append(detection)
            if len(self._items) > self._max_items:
                del self._items[: len(self._items) - self._max_items]
            event = DetectionsUpdated(self._frame_ts, list(self._items))
        if self._bus is not None:
            self._bus.publish(event)

    # --- Kurzformen --------------------------------------------------------------------------------

    def found(self, match: Match, frame_ts: float) -> None:
        self.add(Detection(match.rect, match.label, "found", frame_ts, confidence=match.confidence))

    def missing(self, rect: Rect, label: str, frame_ts: float, confidence: float | None = None) -> None:
        self.add(Detection(rect, label, "missing", frame_ts, confidence=confidence))

    def text(self, rect: Rect, text: str, frame_ts: float, confidence: float | None = None) -> None:
        self.add(Detection(rect, "text", "text", frame_ts, confidence=confidence, text=text))

    def mark(self, rect: Rect, label: str, frame_ts: float | None = None) -> None:
        """Manuelle Markierung, standardmäßig zum aktuellen Frame."""
        ts = frame_ts if frame_ts is not None else (self.frame_ts or 0.0)
        self.add(Detection(rect, label, "mark", ts))

    # --- Rendern -----------------------------------------------------------------------------------

    def render(self, image: Image, detections: list[Detection] | None = None) -> Image:
        """Kopie von `image` mit eingezeichneten Detections (Default: die aktuellen)."""
        canvas = image.copy()
        items = self.detections if detections is None else detections
        scale = max(1.0, min(canvas.shape[:2]) / 720)
        thickness = max(1, round(2 * scale))
        font_scale = 0.5 * scale
        for d in items:
            color = KIND_COLORS[d.kind]
            p1, p2 = (d.rect.x, d.rect.y), (d.rect.right - 1, d.rect.bottom - 1)
            if d.kind == "missing":
                _dashed_rect(canvas, p1, p2, color, thickness, dash=max(4, round(8 * scale)))
            else:
                cv2.rectangle(canvas, p1, p2, color, thickness)
            _label(canvas, _caption(d), p1, color, font_scale, thickness)
        return canvas


def _caption(d: Detection) -> str:
    parts = [d.text if d.kind == "text" and d.text is not None else d.label]
    if d.kind == "missing":
        parts.append("x")
    if d.confidence is not None:
        parts.append(f"{d.confidence:.2f}")
    # OpenCV-Fonts können nur ASCII
    return " ".join(parts).encode("ascii", "replace").decode()


def _label(
    canvas: Image,
    text: str,
    anchor: tuple[int, int],
    color: tuple[int, int, int],
    scale: float,
    thickness: int,
) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    text_thickness = max(1, thickness // 2)
    (tw, th), baseline = cv2.getTextSize(text, font, scale, text_thickness)
    x = anchor[0]
    y = anchor[1] - 4 if anchor[1] - th - baseline - 4 >= 0 else anchor[1] + th + 4
    cv2.rectangle(canvas, (x, y - th - baseline // 2 - 2), (x + tw + 4, y + baseline // 2 + 2), color, -1)
    cv2.putText(canvas, text, (x + 2, y), font, scale, (0, 0, 0), text_thickness, cv2.LINE_AA)


def _dashed_rect(
    canvas: Image,
    p1: tuple[int, int],
    p2: tuple[int, int],
    color: tuple[int, int, int],
    thickness: int,
    dash: int,
) -> None:
    (x1, y1), (x2, y2) = p1, p2
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    for (ax, ay), (bx, by) in itertools.pairwise(corners):
        length = max(abs(bx - ax), abs(by - ay))
        for start in range(0, max(1, length), dash * 2):
            end = min(start + dash, length)
            t0, t1 = start / max(1, length), end / max(1, length)
            a = (round(ax + (bx - ax) * t0), round(ay + (by - ay) * t0))
            b = (round(ax + (bx - ax) * t1), round(ay + (by - ay) * t1))
            cv2.line(canvas, a, b, color, thickness)
