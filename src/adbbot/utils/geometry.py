"""Rechteck-Operationen und Skalierung zwischen Auflösungen."""

from __future__ import annotations

from adbbot.core.types import Point, Rect, Size


def full_rect(size: Size) -> Rect:
    return Rect(0, 0, size.w, size.h)


def intersection(a: Rect, b: Rect) -> Rect | None:
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1, y1 = min(a.right, b.right), min(a.bottom, b.bottom)
    if x1 <= x0 or y1 <= y0:
        return None
    return Rect(x0, y0, x1 - x0, y1 - y0)


def clip(rect: Rect, size: Size) -> Rect | None:
    """Rechteck auf die Bildfläche beschneiden; `None`, wenn es komplett außerhalb liegt."""
    return intersection(rect, full_rect(size))


def iou(a: Rect, b: Rect) -> float:
    inter = intersection(a, b)
    if inter is None:
        return 0.0
    inter_area = inter.w * inter.h
    return inter_area / (a.w * a.h + b.w * b.h - inter_area)


def oriented(reference: Size, actual: Size) -> Size:
    """Referenzgröße in die Ausrichtung (Hoch-/Querformat) von `actual` drehen."""
    if (reference.w > reference.h) != (actual.w > actual.h):
        return Size(reference.h, reference.w)
    return reference


def scale_factor(reference: Size, actual: Size) -> float:
    """Faktor Referenz → Gerät, bezogen auf die kürzere Seite.

    Android-UIs skalieren mit der kürzeren Seite (dp); ein längeres Display (z. B. 1080x2400 statt
    1080x1920) ergibt daher Faktor 1.0 – dort ist nur mehr Platz, nichts größer.
    """
    ref = oriented(reference, actual)
    return min(actual.w, actual.h) / min(ref.w, ref.h)


def scale_point(p: Point, factor: float) -> Point:
    return Point(round(p.x * factor), round(p.y * factor))
