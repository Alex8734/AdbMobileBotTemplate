"""OCR: `OcrEngine`-Protocol + Adapter für RapidOCR (empfohlen) und Tesseract.

Beide Pakete sind optional: `uv sync --extra ocr` installiert RapidOCR.
"""

from __future__ import annotations

import importlib.util
import math
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from adbbot.core.exceptions import OcrUnavailable
from adbbot.core.types import Image, Rect


@dataclass(frozen=True, slots=True)
class OcrResult:
    text: str
    rect: Rect  # relativ zum übergebenen Bild
    confidence: float  # 0..1


class OcrEngine(Protocol):
    def read(self, image: Image) -> list[OcrResult]: ...


class NoOcr:
    def read(self, image: Image) -> list[OcrResult]:
        raise OcrUnavailable("Keine OCR-Engine konfiguriert (vision.ocr_engine: none)")


class RapidOcrEngine:
    """Das Modell wird beim ersten `read()` geladen (dauert ca. 1 s)."""

    def __init__(self) -> None:
        if importlib.util.find_spec("rapidocr_onnxruntime") is None:
            raise OcrUnavailable("rapidocr-onnxruntime ist nicht installiert: uv sync --extra ocr")
        self._engine: Any = None
        self._lock = threading.Lock()

    def read(self, image: Image) -> list[OcrResult]:
        with self._lock:
            if self._engine is None:
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR()
            result, _ = self._engine(image)
        out: list[OcrResult] = []
        for box, text, score in result or []:
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            x0, y0 = math.floor(min(xs)), math.floor(min(ys))
            rect = Rect(x0, y0, math.ceil(max(xs)) - x0, math.ceil(max(ys)) - y0)
            out.append(OcrResult(str(text), rect, float(score)))
        return out


class TesseractOcrEngine:
    """Braucht `pytesseract` und das `tesseract`-Programm im PATH."""

    def __init__(self, lang: str = "eng", config: str = "--psm 6") -> None:
        try:
            import pytesseract
        except ImportError as e:
            raise OcrUnavailable("pytesseract ist nicht installiert") from e
        try:
            pytesseract.get_tesseract_version()
        except pytesseract.TesseractNotFoundError as e:
            raise OcrUnavailable("tesseract-Programm nicht gefunden") from e
        self._tesseract: Any = pytesseract
        self._lang = lang
        self._config = config

    def read(self, image: Image) -> list[OcrResult]:
        data = self._tesseract.image_to_data(
            image[:, :, ::-1],  # BGR → RGB
            lang=self._lang,
            config=self._config,
            output_type=self._tesseract.Output.DICT,
        )
        out: list[OcrResult] = []
        for i, text in enumerate(data["text"]):
            confidence = float(data["conf"][i])
            if text.strip() and confidence >= 0:
                rect = Rect(data["left"][i], data["top"][i], data["width"][i], data["height"][i])
                out.append(OcrResult(text, rect, confidence / 100))
        return out


def create_ocr_engine(name: Literal["rapidocr", "tesseract", "none"]) -> OcrEngine:
    match name:
        case "rapidocr":
            return RapidOcrEngine()
        case "tesseract":
            return TesseractOcrEngine()
        case "none":
            return NoOcr()


def join_text(results: Sequence[OcrResult]) -> str:
    """Ergebnisse in Lesereihenfolge: Zeilen von oben nach unten, Wörter von links nach rechts."""
    lines: list[list[OcrResult]] = []
    for r in sorted(results, key=lambda r: r.rect.center.y):
        if lines:
            line = lines[-1]
            line_center = sum(x.rect.center.y for x in line) / len(line)
            if abs(r.rect.center.y - line_center) <= max(r.rect.h, line[0].rect.h) / 2:
                line.append(r)
                continue
        lines.append([r])
    return "\n".join(" ".join(x.text for x in sorted(line, key=lambda r: r.rect.x)) for line in lines)


_NUMBER_RE = re.compile(r"(\d[\d.,']*)(?:([kKmMbB])(?![A-Za-z]))?")
_SUFFIXES = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_number(text: str) -> int | None:
    """Erste Zahl im Text. `"12,345"` → 12345, `"1.2K"` → 1200, `"3M"` → 3000000, `"Gold: 50"` → 50.

    Ohne Suffix sind `.`, `,` und `'` Tausendertrenner; mit Suffix darf die Zahl Nachkommastellen haben.
    """
    m = _NUMBER_RE.search(text)
    if m is None:
        return None
    digits, suffix = m.group(1), m.group(2)
    if suffix is None:
        return int(re.sub(r"\D", "", digits))

    digits = digits.rstrip(".,'")
    last = max(digits.rfind("."), digits.rfind(","))
    if last != -1 and 0 < len(digits) - last - 1 <= 2:
        value = float(f"{re.sub(r'\D', '', digits[:last]) or 0}.{digits[last + 1 :]}")
    else:
        value = float(re.sub(r"\D", "", digits))
    return round(value * _SUFFIXES[suffix.lower()])
