"""Elemente: benannte, wiederfindbare Bestandteile eines Screens.

Deklariert als Klassenattribute einer Page. Elemente sind Descriptoren: beim Zugriff über eine
Page-Instanz entsteht ein gebundenes Objekt, das den Context kennt.

    class HomePage(Page):
        shop_button = Element("home/shop_button")   # HomePage.shop_button → Element (Deklaration)

    ctx.page(HomePage).shop_button.tap()            # page.shop_button     → BoundElement (Aktionen)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, Self, overload

from adbbot.core.exceptions import ElementNotFound, StepTimeout
from adbbot.core.types import Frame, Match, Point, Rect
from adbbot.vision.color import ColorLike, Rgb
from adbbot.vision.ocr import parse_number

if TYPE_CHECKING:
    from adbbot.bot.context import BotContext


class HasContext(Protocol):
    @property
    def ctx(self) -> BotContext: ...


def _timeout(ctx: BotContext, *candidates: float | None) -> float:
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return ctx.config.bot.element_timeout_s


class Descriptor[B]:
    """Basis aller Element-Deklarationen. `B` ist der gebundene Typ."""

    def __init__(self, *, name: str | None = None) -> None:
        self._explicit_name = name
        self._attr_name: str | None = None
        self._owner: type | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        self._attr_name = name
        self._owner = owner

    @property
    def name(self) -> str:
        return self._explicit_name or self._attr_name or self._fallback_name()

    @property
    def label(self) -> str:
        """`home.shop_button` – für Logs und Fehlermeldungen."""
        if self._owner is None:
            return self.name
        return f"{getattr(self._owner, 'name', self._owner.__name__)}.{self.name}"

    def template_names(self) -> tuple[str, ...]:
        return ()

    @overload
    def __get__(self, instance: None, owner: type) -> Self: ...
    @overload
    def __get__(self, instance: HasContext, owner: type) -> B: ...
    def __get__(self, instance: HasContext | None, owner: type) -> Self | B:
        if instance is None:
            return self
        return self.bind(instance.ctx)

    def bind(self, ctx: BotContext) -> B:
        raise NotImplementedError

    def _fallback_name(self) -> str:
        return "element"


# --- Template-Element ----------------------------------------------------------------------------


class Element(Descriptor["BoundElement"]):
    """Über ein oder mehrere Templates erkennbares Element. Mehrere Templates = Alternativen."""

    def __init__(
        self,
        template: str | Sequence[str],
        *,
        region: Rect | None = None,
        threshold: float | None = None,
        timeout: float | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self.templates: tuple[str, ...] = (template,) if isinstance(template, str) else tuple(template)
        if not self.templates:
            raise ValueError("Element braucht mindestens ein Template")
        self.region = region
        self.threshold = threshold
        self.timeout = timeout

    def template_names(self) -> tuple[str, ...]:
        return self.templates

    def bind(self, ctx: BotContext) -> BoundElement:
        return BoundElement(self, ctx)

    def _fallback_name(self) -> str:
        return self.templates[0].rsplit("/", 1)[-1]


class BoundElement:
    def __init__(self, element: Element, ctx: BotContext) -> None:
        self.element = element
        self.ctx = ctx

    @property
    def name(self) -> str:
        return self.element.label

    def find(self, *, frame: Frame | None = None) -> Match | None:
        e = self.element
        return self.ctx.vision.find_any(e.templates, frame=frame, region=e.region, threshold=e.threshold)

    def exists(self, *, frame: Frame | None = None) -> bool:
        return self.find(frame=frame) is not None

    def wait(self, *, timeout: float | None = None) -> Match:
        """Wartet, bis das Element sichtbar ist. Wirft `ElementNotFound`."""
        e = self.element
        seconds = _timeout(self.ctx, timeout, e.timeout)
        try:
            return self.ctx.vision.wait_for_any(
                e.templates, timeout=seconds, region=e.region, threshold=e.threshold
            )
        except ElementNotFound as error:
            raise ElementNotFound(f"{self.name} nicht gefunden nach {seconds:g} s") from error

    def wait_gone(self, *, timeout: float | None = None) -> None:
        """Wartet, bis das Element verschwunden ist. Wirft `StepTimeout`."""
        seconds = _timeout(self.ctx, timeout, self.element.timeout)
        self.ctx.vision.wait_until(
            lambda f: self.find(frame=f) is None,
            timeout=seconds,
            description=f"{self.name} noch sichtbar",
            error=StepTimeout,
        )

    def tap(self, *, timeout: float | None = None) -> Match:
        """wait + Tap (bei `humanize_input` auf einen zufälligen Punkt im Element)."""
        match = self.wait(timeout=timeout)
        self._tap(match)
        return match

    def tap_if_present(self, *, frame: Frame | None = None) -> bool:
        match = self.find(frame=frame)
        if match is None:
            return False
        self._tap(match)
        return True

    def long_press(self, *, duration_ms: int = 800, timeout: float | None = None) -> Match:
        match = self.wait(timeout=timeout)
        point = self._point(match)
        self.ctx.log.debug("long_press", source="page", element=self.name, x=point.x, y=point.y)
        self.ctx.device.long_press(point, duration_ms)
        return match

    def _point(self, match: Match) -> Point:
        if self.ctx.config.bot.humanize_input:
            return match.rect.random_point(margin=0.25, rng=self.ctx.rng)
        return match.rect.center

    def _tap(self, match: Match) -> None:
        point = self._point(match)
        self.ctx.log.debug("tap", source="page", element=self.name, x=point.x, y=point.y)
        self.ctx.device.tap(point)


# --- Text-Element --------------------------------------------------------------------------------


class TextElement(Descriptor["BoundTextElement"]):
    """OCR-Region (Referenz-Koordinaten)."""

    def __init__(self, region: Rect, *, min_confidence: float = 0.0, name: str | None = None) -> None:
        super().__init__(name=name)
        self.region = region
        self.min_confidence = min_confidence

    def bind(self, ctx: BotContext) -> BoundTextElement:
        return BoundTextElement(self, ctx)

    def _fallback_name(self) -> str:
        return "text"


class BoundTextElement:
    def __init__(self, element: TextElement, ctx: BotContext) -> None:
        self.element = element
        self.ctx = ctx

    @property
    def name(self) -> str:
        return self.element.label

    def read_text(self, *, frame: Frame | None = None) -> str:
        e = self.element
        return self.ctx.vision.read_text(e.region, frame=frame, min_confidence=e.min_confidence)

    def read_number(self, *, frame: Frame | None = None) -> int | None:
        return parse_number(self.read_text(frame=frame))

    def tap(self) -> None:
        self.ctx.device.tap(self.ctx.vision.to_device_point(self.element.region.center))


# --- Farb-Element --------------------------------------------------------------------------------


class ColorElement(Descriptor["BoundColorElement"]):
    """Zustand an einer Farbe erkennen – an einem Punkt oder als Mindestanteil in einer Region."""

    def __init__(
        self,
        color: ColorLike | str,
        *,
        point: Point | None = None,
        region: Rect | None = None,
        tolerance: int = 20,
        min_ratio: float = 0.5,
        timeout: float | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        if (point is None) == (region is None):
            raise ValueError("ColorElement braucht genau eins von point oder region")
        self.color = Rgb.from_hex(color) if isinstance(color, str) else Rgb(*color)
        self.point = point
        self.region = region
        self.tolerance = tolerance
        self.min_ratio = min_ratio
        self.timeout = timeout

    def bind(self, ctx: BotContext) -> BoundColorElement:
        return BoundColorElement(self, ctx)

    def _fallback_name(self) -> str:
        return f"color_{self.color.hex().lstrip('#')}"


class BoundColorElement:
    def __init__(self, element: ColorElement, ctx: BotContext) -> None:
        self.element = element
        self.ctx = ctx

    @property
    def name(self) -> str:
        return self.element.label

    def exists(self, *, frame: Frame | None = None) -> bool:
        e, vision = self.element, self.ctx.vision
        if e.point is not None:
            return vision.color_matches(e.point, e.color, e.tolerance, frame=frame)
        assert e.region is not None
        return vision.color_ratio(e.region, e.color, e.tolerance, frame=frame) >= e.min_ratio

    def wait(self, *, timeout: float | None = None) -> None:
        self.ctx.vision.wait_until(
            lambda f: self.exists(frame=f),
            timeout=_timeout(self.ctx, timeout, self.element.timeout),
            description=f"{self.name}: Farbe {self.element.color.hex()} nicht erreicht",
            error=ElementNotFound,
        )

    def wait_gone(self, *, timeout: float | None = None) -> None:
        self.ctx.vision.wait_until(
            lambda f: not self.exists(frame=f),
            timeout=_timeout(self.ctx, timeout, self.element.timeout),
            description=f"{self.name}: Farbe {self.element.color.hex()} noch vorhanden",
            error=StepTimeout,
        )

    def tap(self) -> None:
        e = self.element
        target = e.point if e.point is not None else e.region.center if e.region else Point(0, 0)
        self.ctx.device.tap(self.ctx.vision.to_device_point(target))


# --- Kurzformen für Pages und Flows --------------------------------------------------------------


class ElementAccess:
    """Kurzformen für Ad-hoc-Templates: `self.tap("common/close_x")`."""

    ctx: BotContext

    def el(
        self,
        template: str | Sequence[str],
        *,
        region: Rect | None = None,
        threshold: float | None = None,
        timeout: float | None = None,
    ) -> BoundElement:
        return self.ctx.element(template, region=region, threshold=threshold, timeout=timeout)

    def tap(
        self,
        template: str | Sequence[str],
        *,
        region: Rect | None = None,
        threshold: float | None = None,
        timeout: float | None = None,
    ) -> Match:
        return self.el(template, region=region, threshold=threshold, timeout=timeout).tap()

    def tap_if_present(
        self,
        template: str | Sequence[str],
        *,
        region: Rect | None = None,
        threshold: float | None = None,
        frame: Frame | None = None,
    ) -> bool:
        return self.el(template, region=region, threshold=threshold).tap_if_present(frame=frame)

    def exists(
        self,
        template: str | Sequence[str],
        *,
        region: Rect | None = None,
        threshold: float | None = None,
        frame: Frame | None = None,
    ) -> bool:
        return self.el(template, region=region, threshold=threshold).exists(frame=frame)

    def wait_for(
        self,
        template: str | Sequence[str],
        *,
        timeout: float | None = None,
        region: Rect | None = None,
        threshold: float | None = None,
    ) -> Match:
        return self.el(template, region=region, threshold=threshold, timeout=timeout).wait()
