"""Werttypen, die von allen Schichten geteilt werden."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt

Image = npt.NDArray[np.uint8]
"""BGR-Bild (H x W x 3), wie OpenCV es erwartet."""


@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int

    def offset(self, dx: int, dy: int) -> Point:
        return Point(self.x + dx, self.y + dy)


@dataclass(frozen=True, slots=True)
class Size:
    w: int
    h: int


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> Point:
        return Point(self.x + self.w // 2, self.y + self.h // 2)

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def scaled(self, factor: float) -> Rect:
        return Rect(
            round(self.x * factor), round(self.y * factor), round(self.w * factor), round(self.h * factor)
        )

    def contains(self, p: Point) -> bool:
        return self.x <= p.x < self.right and self.y <= p.y < self.bottom

    def random_point(self, margin: float = 0.2, rng: random.Random | None = None) -> Point:
        """Zufälliger Punkt im Rechteck, `margin` (Anteil je Seite) vom Rand entfernt."""
        r = rng or random
        mx, my = int(self.w * margin), int(self.h * margin)
        x0, x1 = self.x + mx, max(self.x + mx, self.right - 1 - mx)
        y0, y1 = self.y + my, max(self.y + my, self.bottom - 1 - my)
        return Point(r.randint(x0, x1), r.randint(y0, y1))


@dataclass(frozen=True, slots=True)
class Match:
    rect: Rect
    confidence: float
    label: str


@dataclass(frozen=True, slots=True)
class Detection:
    """Das, was die GUI als Overlay zeichnet."""

    rect: Rect
    label: str
    kind: Literal["found", "missing", "text", "mark"]
    frame_ts: float
    confidence: float | None = None
    text: str | None = None


@dataclass(slots=True)
class Frame:
    """Ein Screenshot. Per Konvention unveränderlich: nie in `image` schreiben."""

    image: Image
    ts: float

    @property
    def size(self) -> Size:
        h, w = self.image.shape[:2]
        return Size(w, h)

    def crop(self, rect: Rect) -> Frame:
        return Frame(self.image[rect.y : rect.bottom, rect.x : rect.right], self.ts)

    def save(self, path: Path) -> None:
        from adbbot.utils.image import save_image

        save_image(path, self.image)


class StepResult(Enum):
    OK = auto()
    SKIPPED = auto()
    FAILED = auto()
