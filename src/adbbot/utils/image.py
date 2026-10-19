"""Bild-Dateien lesen/schreiben/dekodieren.

Einzige Stelle außerhalb von `vision/`, die `cv2` benutzt – und nur für Datei-I/O, nie für Erkennung.
Über `np.fromfile`/`tofile`, damit auch Pfade mit Umlauten funktionieren.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from adbbot.core.types import Image


def decode_image(data: bytes) -> Image:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Bilddaten konnten nicht dekodiert werden")
    return np.asarray(image, dtype=np.uint8)


def load_image(path: Path) -> Image:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        return decode_image(np.fromfile(path, dtype=np.uint8).tobytes())
    except ValueError as e:
        raise ValueError(f"{path}: kein lesbares Bild") from e


def save_image(path: Path, image: Image) -> None:
    ok, buffer = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise ValueError(f"{path}: Bild konnte nicht kodiert werden")
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer.tofile(path)
