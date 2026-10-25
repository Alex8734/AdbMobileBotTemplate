"""Template Matching mit OpenCV.

- Normalfall: `TM_CCOEFF_NORMED` (robust gegen Helligkeitsunterschiede), optional mit Maske.
- Einfarbige Templates haben keine Varianz, dort liefert CCOEFF nur NaN. Für sie wird die
  quadratische Abweichung in eine Ähnlichkeit 0..1 umgerechnet.
- Templates mit `grayscale` (Template-JSON) werden in Graustufen gematcht – etwa 3x schneller.
- Einfarbige Bildbereiche (z. B. schwarzer Rand) haben ebenfalls keine Varianz. Mit Maske liefert OpenCV
  dort keine NaN, sondern Rechenmüll (auch Werte > 1) – solche Positionen zählen als 0.

Ein Template mit mehreren Varianten liefert das Ergebnis der besten Variante.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from adbbot.core.types import Image, Match, Rect, Size
from adbbot.utils.geometry import clip, full_rect, iou
from adbbot.vision.template import Template, TemplateVariant

ScoreMap = npt.NDArray[np.float32]

_MAX_CANDIDATES = 2000  # Obergrenze vor der Non-Maximum-Suppression


@dataclass(frozen=True, slots=True)
class VariantScore:
    variant: TemplateVariant
    match: Match | None  # beste Position unabhängig vom Threshold; None = Suchbereich zu klein


class TemplateMatcher:
    def __init__(self, default_threshold: float = 0.85) -> None:
        self.default_threshold = default_threshold

    def threshold_for(self, template: Template, threshold: float | None = None) -> float:
        """Vorrang: Aufruf → Template-Metadaten → Default."""
        if threshold is not None:
            return threshold
        if template.threshold is not None:
            return template.threshold
        return self.default_threshold

    def search_rect(self, frame_size: Size, template: Template, region: Rect | None = None) -> Rect | None:
        """Vorrang: Aufruf → Template-Metadaten → ganzes Bild. Auf das Bild beschnitten."""
        return clip(region or template.region or full_rect(frame_size), frame_size)

    def score_variants(
        self, image: Image, template: Template, *, region: Rect | None = None
    ) -> list[VariantScore]:
        search = self._search_area(image, template, region)
        results: list[VariantScore] = []
        for variant in template.variants:
            scores = self._scores(image, search, variant)
            if scores is None:
                results.append(VariantScore(variant, None))
                continue
            _, max_val, _, (x, y) = cv2.minMaxLoc(scores)
            rect = Rect(search.x + x, search.y + y, variant.size.w, variant.size.h) if search else None
            results.append(
                VariantScore(variant, Match(rect, float(max_val), template.name) if rect else None)
            )
        return results

    def best(self, image: Image, template: Template, *, region: Rect | None = None) -> Match | None:
        """Beste Position über alle Varianten, ohne Threshold."""
        matches = [s.match for s in self.score_variants(image, template, region=region) if s.match]
        return max(matches, key=lambda m: m.confidence, default=None)

    def find(
        self, image: Image, template: Template, *, region: Rect | None = None, threshold: float | None = None
    ) -> Match | None:
        best = self.best(image, template, region=region)
        if best is not None and best.confidence >= self.threshold_for(template, threshold):
            return best
        return None

    def find_all(
        self,
        image: Image,
        template: Template,
        *,
        region: Rect | None = None,
        threshold: float | None = None,
        max_results: int = 50,
        overlap: float = 0.1,
    ) -> list[Match]:
        """Alle Vorkommen, absteigend nach Confidence.

        Treffer, die einen besseren um mehr als `overlap` (IoU) überlappen, werden verworfen.
        Bei gleicher Confidence gewinnt der obere, dann der linke – das Ergebnis ist deterministisch.
        """
        thr = self.threshold_for(template, threshold)
        search = self._search_area(image, template, region)
        candidates: list[Match] = []
        for variant in template.variants:
            scores = self._scores(image, search, variant)
            if scores is None or search is None:
                continue
            candidates.extend(_peaks(scores, thr, search, variant.size, template.name))

        candidates.sort(key=lambda m: (-m.confidence, m.rect.y, m.rect.x))
        kept: list[Match] = []
        for candidate in candidates[:_MAX_CANDIDATES]:
            if all(iou(candidate.rect, k.rect) <= overlap for k in kept):
                kept.append(candidate)
                if len(kept) >= max_results:
                    break
        return kept

    # --- intern ------------------------------------------------------------------------------------

    def _search_area(self, image: Image, template: Template, region: Rect | None) -> Rect | None:
        h, w = image.shape[:2]
        return self.search_rect(Size(w, h), template, region)

    @staticmethod
    def _scores(image: Image, search: Rect | None, variant: TemplateVariant) -> ScoreMap | None:
        if search is None or variant.size.w > search.w or variant.size.h > search.h:
            return None
        area = image[search.y : search.bottom, search.x : search.right]
        tpl, mask = variant.image, variant.mask

        if variant.flat:
            if mask is None:
                diff = cv2.matchTemplate(area, tpl, cv2.TM_SQDIFF)
                n = tpl.shape[0] * tpl.shape[1]
            else:
                diff = cv2.matchTemplate(area, tpl, cv2.TM_SQDIFF, mask=mask)
                n = int(np.count_nonzero(mask))
            scores = 1.0 - np.sqrt(np.maximum(diff, 0.0) / (n * 3 * 255.0**2))
        else:
            if variant.gray is not None:
                area, tpl = np.asarray(cv2.cvtColor(area, cv2.COLOR_BGR2GRAY), dtype=np.uint8), variant.gray
            scores = cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)

        result = np.nan_to_num(np.asarray(scores, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
        if not variant.flat:
            result[_flat_windows(area, variant.size) | (result > 1.0 + 1e-3)] = 0.0
        return np.clip(result, -1.0, 1.0, out=result)


def _flat_windows(area: Image, size: Size, min_std: float = 1.0) -> npt.NDArray[np.bool_]:
    """Pro Template-Position: ist das Bildfenster praktisch einfarbig? (Graustufen, über Integralbilder)"""
    gray = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY) if area.ndim == 3 else area
    sums, sums_sq = cv2.integral2(gray, sdepth=cv2.CV_64F)
    total = np.asarray(sums, dtype=np.float64)
    total_sq = np.asarray(sums_sq, dtype=np.float64)
    h, w = size.h, size.w

    def window(integral: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        return integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]

    n = float(w * h)
    mean = window(total) / n
    variance = window(total_sq) / n - mean * mean
    return np.asarray(variance < min_std * min_std)


def _peaks(scores: ScoreMap, threshold: float, search: Rect, size: Size, label: str) -> list[Match]:
    """Lokale Maxima über dem Threshold (Umgebung ≈ halbe Template-Größe)."""
    above = scores >= threshold
    if not above.any():
        return []
    kernel = np.ones((max(1, size.h // 2), max(1, size.w // 2)), dtype=np.uint8)
    local_max = scores >= cv2.dilate(scores, kernel)
    ys, xs = np.nonzero(above & local_max)
    if len(ys) > _MAX_CANDIDATES:
        top = np.argpartition(scores[ys, xs], -_MAX_CANDIDATES)[-_MAX_CANDIDATES:]
        ys, xs = ys[top], xs[top]
    return [
        Match(Rect(search.x + int(x), search.y + int(y), size.w, size.h), float(scores[y, x]), label)
        for y, x in zip(ys, xs, strict=True)
    ]
