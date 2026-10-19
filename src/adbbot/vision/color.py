"""Pixel- und Farbbereichs-Checks.

Farben in der API sind **RGB** (so, wie Farbpicker und Grafikprogramme sie anzeigen).
Frames sind intern BGR; die Umrechnung passiert ausschließlich hier.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from adbbot.core.types import Image, Point, Rect


class Rgb(NamedTuple):
    r: int
    g: int
    b: int

    @classmethod
    def from_hex(cls, value: str) -> Rgb:
        """`"#ff8800"` oder `"ff8800"`."""
        v = value.removeprefix("#")
        if len(v) != 6:
            raise ValueError(f"Ungültige Farbe {value!r}, erwartet #rrggbb")
        return cls(int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))

    def hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"


ColorLike = Rgb | tuple[int, int, int]


def pixel_at(image: Image, p: Point) -> Rgb:
    h, w = image.shape[:2]
    if not (0 <= p.x < w and 0 <= p.y < h):
        raise ValueError(f"Punkt {p} liegt außerhalb des Bildes ({w}x{h})")
    b, g, r = (int(c) for c in image[p.y, p.x, :3])
    return Rgb(r, g, b)


def color_distance(a: ColorLike, b: ColorLike) -> int:
    """Größte Abweichung eines einzelnen Kanals."""
    return max(abs(int(x) - int(y)) for x, y in zip(a, b, strict=True))


def color_matches(image: Image, p: Point, expected: ColorLike, tolerance: int = 20) -> bool:
    return color_distance(pixel_at(image, p), expected) <= tolerance


def _area(image: Image, rect: Rect) -> Image:
    area = image[max(0, rect.y) : max(0, rect.bottom), max(0, rect.x) : max(0, rect.right)]
    if area.size == 0:
        raise ValueError(f"Region {rect} liegt außerhalb des Bildes")
    return area


def color_ratio(image: Image, rect: Rect, expected: ColorLike, tolerance: int = 20) -> float:
    """Anteil der Pixel in `rect`, die innerhalb der Toleranz liegen (0..1)."""
    area = _area(image, rect).astype(np.int16)
    r, g, b = expected
    target = np.array([b, g, r], dtype=np.int16)
    within = np.all(np.abs(area[:, :, :3] - target) <= tolerance, axis=2)
    return float(within.mean())


def mean_color(image: Image, rect: Rect) -> Rgb:
    b, g, r = (round(float(c)) for c in _area(image, rect)[:, :, :3].reshape(-1, 3).mean(axis=0))
    return Rgb(r, g, b)
