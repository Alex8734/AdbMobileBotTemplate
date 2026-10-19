"""Vision-Fassade – das Einzige aus dieser Schicht, was die Bot-Schicht benutzt.

- **Vision sieht nur**, sie tappt nie.
- **Frame-Regel**: jeder Aufruf ohne `frame=` macht einen frischen Screenshot. Für mehrere Prüfungen auf
  demselben Bild: `frame = vision.capture()` und weiterreichen.
- **Koordinaten**: `region`/Punkte in Aufrufen sind in der Referenzauflösung angegeben und werden auf das
  Gerät umgerechnet. Zurückgegebene `Match.rect` sind Geräte-Koordinaten (direkt tappbar).
- **Automatisches Overlay**: jeder `find*`/`read_*`/`wait_*`/Farb-Aufruf legt eine Detection an.
- **Warteschleifen**: capture → `poll_hook(frame)` → prüfen → `clock.sleep(poll_interval)`.
  Über den Hook laufen `checkpoint()` (Pause/Stop/Timeout) und die Watcher der Bot-Schicht.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy.typing as npt
from structlog.typing import FilteringBoundLogger

from adbbot.core.clock import Clock
from adbbot.core.config import AppConfig
from adbbot.core.events import DebugImageUpdated, EventBus, FrameCaptured
from adbbot.core.exceptions import ElementNotFound, StepFailed, StepTimeout
from adbbot.core.logging import get_null_logger
from adbbot.core.types import Frame, Match, Point, Rect, Size
from adbbot.device.protocol import DeviceProtocol
from adbbot.utils.geometry import clip, full_rect, scale_factor, scale_point
from adbbot.utils.paths import resolve
from adbbot.vision import color
from adbbot.vision.color import ColorLike, Rgb
from adbbot.vision.display import to_display_image
from adbbot.vision.matcher import TemplateMatcher
from adbbot.vision.ocr import NoOcr, OcrEngine, create_ocr_engine, join_text, parse_number
from adbbot.vision.overlay import DetectionStore
from adbbot.vision.template import Template, TemplateStore

PollHook = Callable[[Frame], None]

_MIN_SLEEP_S = 0.001


class Vision:
    def __init__(
        self,
        device: DeviceProtocol,
        templates: TemplateStore,
        *,
        clock: Clock,
        bus: EventBus | None = None,
        matcher: TemplateMatcher | None = None,
        ocr: OcrEngine | None = None,
        poll_interval_s: float = 0.3,
        log: FilteringBoundLogger | None = None,
    ) -> None:
        """Die Referenzauflösung kommt aus dem `TemplateStore` – eine Quelle für Templates und Regionen."""
        self.device = device
        self.templates = templates
        self.clock = clock
        self.matcher = matcher or TemplateMatcher()
        self.ocr: OcrEngine = ocr or NoOcr()
        self.poll_interval_s = poll_interval_s
        self.overlay = DetectionStore(bus)
        self.poll_hook: PollHook | None = None  # wird von der Engine gesetzt
        self.log = (log or get_null_logger()).bind(source="vision")
        self._bus = bus
        self._last_frame: Frame | None = None
        self._screen_size: Size | None = None
        self._scale = 1.0

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        device: DeviceProtocol,
        *,
        clock: Clock,
        root: Path,
        bus: EventBus | None = None,
        log: FilteringBoundLogger | None = None,
    ) -> Vision:
        reference = Size(*config.device.reference_resolution)
        return cls(
            device,
            TemplateStore(resolve(config.vision.templates_dir, root), reference_resolution=reference),
            clock=clock,
            bus=bus,
            matcher=TemplateMatcher(config.vision.default_threshold),
            ocr=create_ocr_engine(config.vision.ocr_engine),
            poll_interval_s=config.vision.poll_interval_ms / 1000,
            log=log,
        )

    # --- Frames und Koordinaten --------------------------------------------------------------------

    @property
    def last_frame(self) -> Frame | None:
        return self._last_frame

    @property
    def scale(self) -> float:
        """Faktor Referenz → Gerät (1.0, solange Gerät und Referenz gleich groß sind)."""
        return self._scale

    def to_device(self, rect: Rect) -> Rect:
        return rect if self._scale == 1.0 else rect.scaled(self._scale)

    def to_device_point(self, p: Point) -> Point:
        return p if self._scale == 1.0 else scale_point(p, self._scale)

    def capture(self) -> Frame:
        frame = self.device.screenshot()
        self._sync_size(frame.size)
        self._last_frame = frame
        self.overlay.new_frame(frame.ts)
        if self._bus is not None:
            self._bus.publish(FrameCaptured(frame))
        return frame

    def debug_image(self, name: str, image: npt.NDArray[Any]) -> None:
        """Zwischenbild in der GUI zeigen (Dropdown „Ansicht“ über dem Livebild), z. B.

            gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
            self.vision.debug_image("Graustufen", gray)

        Graustufen, bool-Masken, BGR/BGRA und Float-Bilder (werden auf 0–255 normiert) sind erlaubt; das Bild
        wird kopiert. Pro Name zeigt die GUI das neueste Bild. Hat es die Größe des letzten Frames, werden
        dessen Overlays darauf gezeichnet. Ohne GUI (kein Bus) passiert nichts.
        """
        if self._bus is None:
            return
        display = to_display_image(image)
        last = self._last_frame
        same_size = last is not None and last.image.shape[:2] == display.shape[:2]
        ts = last.ts if last is not None and same_size else -1.0
        self._bus.publish(DebugImageUpdated(name, Frame(display, ts)))

    # --- Einzelbild-Operationen --------------------------------------------------------------------

    def find(
        self,
        template: str,
        *,
        frame: Frame | None = None,
        region: Rect | None = None,
        threshold: float | None = None,
    ) -> Match | None:
        f = self._frame(frame)
        tpl = self.templates.get(template)
        search = self.to_device(region) if region else None
        started = time.perf_counter()
        best = self.matcher.best(f.image, tpl, region=search)
        thr = self.matcher.threshold_for(tpl, threshold)
        found = best is not None and best.confidence >= thr
        self.log.debug(
            "find",
            template=template,
            found=found,
            confidence=round(best.confidence, 3) if best else None,
            threshold=thr,
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
        if best is not None and found:
            self.overlay.found(best, f.ts)
            return best
        self._add_missing(f, tpl, search, best)
        return None

    def find_all(
        self,
        template: str,
        *,
        frame: Frame | None = None,
        region: Rect | None = None,
        threshold: float | None = None,
        max_results: int = 50,
    ) -> list[Match]:
        f = self._frame(frame)
        tpl = self.templates.get(template)
        search = self.to_device(region) if region else None
        matches = self.matcher.find_all(
            f.image, tpl, region=search, threshold=threshold, max_results=max_results
        )
        self.log.debug("find_all", template=template, count=len(matches))
        for m in matches:
            self.overlay.found(m, f.ts)
        if not matches:
            self._add_missing(f, tpl, search, None)
        return matches

    def exists(
        self,
        template: str,
        *,
        frame: Frame | None = None,
        region: Rect | None = None,
        threshold: float | None = None,
    ) -> bool:
        return self.find(template, frame=frame, region=region, threshold=threshold) is not None

    def find_any(
        self,
        templates: Sequence[str],
        *,
        frame: Frame | None = None,
        region: Rect | None = None,
        threshold: float | None = None,
    ) -> Match | None:
        """Erstes Template (in der angegebenen Reihenfolge), das auf demselben Frame gefunden wird."""
        f = self._frame(frame)
        for name in templates:
            match = self.find(name, frame=f, region=region, threshold=threshold)
            if match is not None:
                return match
        return None

    def read_text(self, region: Rect, *, frame: Frame | None = None, min_confidence: float = 0.0) -> str:
        f = self._frame(frame)
        area = clip(self.to_device(region), f.size)
        if area is None:
            raise ValueError(f"Region {region} liegt außerhalb des Bildes")
        results = [r for r in self.ocr.read(f.crop(area).image.copy()) if r.confidence >= min_confidence]
        text = join_text(results)
        confidence = min((r.confidence for r in results), default=None)
        self.overlay.text(area, text, f.ts, confidence)
        self.log.debug("read_text", region=area, text=text, confidence=confidence)
        return text

    def read_number(self, region: Rect, *, frame: Frame | None = None) -> int | None:
        return parse_number(self.read_text(region, frame=frame))

    def pixel(self, p: Point, *, frame: Frame | None = None) -> Rgb:
        f = self._frame(frame)
        return color.pixel_at(f.image, self.to_device_point(p))

    def color_matches(
        self, p: Point, expected: ColorLike, tolerance: int = 20, *, frame: Frame | None = None
    ) -> bool:
        f = self._frame(frame)
        point = self.to_device_point(p)
        actual = color.pixel_at(f.image, point)
        ok = color.color_distance(actual, expected) <= tolerance
        rect = clip(Rect(point.x - 5, point.y - 5, 11, 11), f.size) or Rect(point.x, point.y, 1, 1)
        label = f"{Rgb(*expected).hex()} ~ {actual.hex()}"
        if ok:
            self.overlay.found(Match(rect, 1.0, label), f.ts)
        else:
            self.overlay.missing(rect, label, f.ts)
        return ok

    def color_ratio(
        self, region: Rect, expected: ColorLike, tolerance: int = 20, *, frame: Frame | None = None
    ) -> float:
        f = self._frame(frame)
        area = clip(self.to_device(region), f.size)
        if area is None:
            raise ValueError(f"Region {region} liegt außerhalb des Bildes")
        ratio = color.color_ratio(f.image, area, expected, tolerance)
        self.overlay.mark(area, f"{Rgb(*expected).hex()} {ratio:.0%}", f.ts)
        return ratio

    # --- Warteschleifen ----------------------------------------------------------------------------

    def wait_until[T](
        self,
        check: Callable[[Frame], T | None],
        *,
        timeout: float,
        description: str = "Bedingung",
        error: type[StepFailed] = StepTimeout,
    ) -> T:
        """Allgemeine Warteschleife. `check` gilt als erfüllt, sobald es weder `None` noch `False` liefert.

        Mindestens eine Prüfung findet immer statt, auch bei `timeout=0`.
        """
        deadline = self.clock.monotonic() + timeout
        while True:
            loop_start = self.clock.monotonic()
            frame = self.capture()
            if self.poll_hook is not None:
                self.poll_hook(frame)
            result = check(frame)
            if result is not None and result is not False:
                return result
            now = self.clock.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                raise error(f"{description}: nicht erfüllt nach {timeout:g} s")
            pause = max(_MIN_SLEEP_S, self.poll_interval_s - (now - loop_start))
            self.clock.sleep(min(pause, remaining))

    def wait_for(
        self,
        template: str,
        *,
        timeout: float,
        region: Rect | None = None,
        threshold: float | None = None,
    ) -> Match:
        return self.wait_until(
            lambda f: self.find(template, frame=f, region=region, threshold=threshold),
            timeout=timeout,
            description=f"{template} nicht gefunden",
            error=ElementNotFound,
        )

    def wait_for_any(
        self,
        templates: Sequence[str],
        *,
        timeout: float,
        region: Rect | None = None,
        threshold: float | None = None,
    ) -> Match:
        return self.wait_until(
            lambda f: self.find_any(templates, frame=f, region=region, threshold=threshold),
            timeout=timeout,
            description=f"keins von {', '.join(templates)} gefunden",
            error=ElementNotFound,
        )

    def wait_until_gone(
        self,
        template: str,
        *,
        timeout: float,
        region: Rect | None = None,
        threshold: float | None = None,
    ) -> None:
        self.wait_until(
            lambda f: self.find(template, frame=f, region=region, threshold=threshold) is None,
            timeout=timeout,
            description=f"{template} noch sichtbar",
            error=StepTimeout,
        )

    # --- intern ------------------------------------------------------------------------------------

    def _frame(self, frame: Frame | None) -> Frame:
        if frame is None:
            return self.capture()
        self._sync_size(frame.size)
        return frame

    def _sync_size(self, size: Size) -> None:
        if size != self._screen_size:
            self._screen_size = size
            self._scale = scale_factor(self.templates.reference_resolution, size)
            self.templates.set_screen_size(size)

    def _add_missing(self, frame: Frame, template: Template, search: Rect | None, best: Match | None) -> None:
        """Rot gestrichelt: in der Suchregion, sonst an der besten (zu schwachen) Position."""
        region = search or template.region
        rect = clip(region, frame.size) if region else (best.rect if best else full_rect(frame.size))
        self.overlay.missing(
            rect or full_rect(frame.size), template.name, frame.ts, best.confidence if best else None
        )
