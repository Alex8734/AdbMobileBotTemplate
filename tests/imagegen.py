"""Synthetische Test-Screens: glatte Zufallstexturen überstehen Skalierung und sind eindeutig matchbar."""

from __future__ import annotations

import cv2
import numpy as np

from adbbot.core.types import Image, Point


def textured(w: int, h: int, *, seed: int, cell: int = 8) -> Image:
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, (max(2, h // cell), max(2, w // cell), 3), dtype=np.uint8)
    return np.asarray(cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC), dtype=np.uint8)


def solid_patch(w: int, h: int, bgr: tuple[int, int, int]) -> Image:
    image = np.zeros((h, w, 3), dtype=np.uint8)
    image[:, :] = bgr
    return image


def paste(screen: Image, patch: Image, at: Point) -> Image:
    out = screen.copy()
    h, w = patch.shape[:2]
    out[at.y : at.y + h, at.x : at.x + w] = patch
    return out
