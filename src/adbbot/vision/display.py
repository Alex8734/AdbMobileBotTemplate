"""OpenCV-Zwischenbilder (Graustufen, Masken, Match-Heatmaps, ...) in anzeigbare BGR-Bilder umwandeln."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from adbbot.core.types import Image


def to_display_image(image: npt.NDArray[Any]) -> Image:
    """Beliebiges Zwischenbild → neues BGR-Bild (uint8).

    - Graustufen (H x W oder H x W x 1) und BGRA werden nach BGR konvertiert.
    - bool-Masken: `True` = weiß.
    - Andere Datentypen (float, int16, ...) werden auf 0–255 normiert, z. B. `cv2.matchTemplate`-Ergebnisse.
    """
    array = np.asarray(image)
    if array.ndim == 3 and array.shape[2] == 1:
        array = array[:, :, 0]
    if array.size == 0 or array.ndim not in (2, 3) or (array.ndim == 3 and array.shape[2] not in (3, 4)):
        raise ValueError(f"Kein darstellbares Bild: shape={array.shape}, dtype={array.dtype}")

    if array.dtype == np.bool_:
        array = array.astype(np.uint8) * 255
    elif array.dtype != np.uint8:
        values = np.nan_to_num(array.astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)
        low, high = float(values.min()), float(values.max())
        scaled = (values - low) * (255.0 / (high - low)) if high > low else np.zeros_like(values)
        array = scaled.round().astype(np.uint8)

    if array.ndim == 2:
        converted = cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
    elif array.shape[2] == 4:
        converted = cv2.cvtColor(array, cv2.COLOR_BGRA2BGR)
    else:
        converted = array.copy()
    return np.ascontiguousarray(converted, dtype=np.uint8)
