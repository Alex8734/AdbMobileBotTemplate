import importlib.util

import cv2
import numpy as np
import pytest

from adbbot.core.exceptions import OcrUnavailable
from adbbot.core.types import Rect
from adbbot.vision.ocr import NoOcr, OcrResult, create_ocr_engine, join_text, parse_number


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("12,345", 12345),
        ("1.234.567", 1234567),
        ("Gold: 500", 500),
        ("1.2K", 1200),
        ("1,5k", 1500),
        ("3M", 3_000_000),
        ("2.25B", 2_250_000_000),
        ("5 Minuten", 5),
        ("5Min", 5),
        ("7'500", 7500),
        ("keine Zahl", None),
        ("", None),
    ],
)
def test_parse_number(text: str, number: int | None) -> None:
    assert parse_number(text) == number


def test_join_text_reading_order() -> None:
    results = [
        OcrResult("Welt", Rect(60, 2, 40, 20), 0.9),
        OcrResult("zwei", Rect(0, 40, 40, 20), 0.9),
        OcrResult("Hallo", Rect(0, 0, 50, 20), 0.9),
    ]
    assert join_text(results) == "Hallo Welt\nzwei"
    assert join_text([]) == ""


def test_no_ocr() -> None:
    assert isinstance(create_ocr_engine("none"), NoOcr)
    with pytest.raises(OcrUnavailable):
        NoOcr().read(np.zeros((10, 10, 3), dtype=np.uint8))


@pytest.mark.skipif(importlib.util.find_spec("pytesseract") is not None, reason="pytesseract installiert")
def test_tesseract_unavailable() -> None:
    with pytest.raises(OcrUnavailable):
        create_ocr_engine("tesseract")


def test_rapidocr_reads_number() -> None:
    pytest.importorskip("rapidocr_onnxruntime")
    image = np.full((80, 400, 3), 255, dtype=np.uint8)
    cv2.putText(image, "Gold 12,345", (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)

    results = create_ocr_engine("rapidocr").read(image)
    assert results
    assert all(0 <= r.confidence <= 1 for r in results)
    assert all(r.rect.w > 0 and r.rect.h > 0 for r in results)
    assert parse_number(join_text(results)) == 12345
