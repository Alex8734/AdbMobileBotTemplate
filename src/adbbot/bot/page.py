"""Page-Object-Pattern: eine Page = ein Screen der App.

class HomePage(Page):
    anchor = Element("home/logo", region=Rect(0, 0, 1080, 200))   # Page ist aktiv, wenn sichtbar
    shop_button = Element("home/shop_button")

    def open_shop(self) -> ShopPage:
        self.shop_button.tap()
        return self.ctx.page(ShopPage).wait_until_active()
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

from structlog.typing import FilteringBoundLogger

from adbbot.bot.element import BoundElement, Descriptor, Element, ElementAccess
from adbbot.core.exceptions import BotError, ElementNotFound, WrongPage
from adbbot.core.types import Frame
from adbbot.utils.text import default_name

if TYPE_CHECKING:
    from adbbot.bot.context import BotContext


class Page(ElementAccess):
    name: ClassVar[str] = "page"
    priority: ClassVar[int] = 0
    """Höher = wird bei der Seitenerkennung zuerst geprüft (z. B. Popups über einer anderen Seite)."""
    transitions: ClassVar[Mapping[type[Page] | str, Callable[[Any], None]]] = MappingProxyType({})
    """Für den Navigator: Ziel (Page-Klasse oder -Name) → Aktion, bekommt die aktuelle Page-Instanz."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "name" not in cls.__dict__:
            cls.name = default_name(cls.__name__, "Page")

    def __init__(self, ctx: BotContext) -> None:
        self.ctx = ctx

    @property
    def log(self) -> FilteringBoundLogger:
        return self.ctx.log.bind(source="page", page=self.name)

    # --- Deklarationen -----------------------------------------------------------------------------

    @classmethod
    def descriptors(cls) -> dict[str, Descriptor[Any]]:
        found: dict[str, Descriptor[Any]] = {}
        for klass in reversed(cls.__mro__):
            for key, value in vars(klass).items():
                if isinstance(value, Descriptor):
                    found[key] = value
        return found

    @classmethod
    def has_anchor(cls) -> bool:
        return isinstance(cls.descriptors().get("anchor"), Element)

    @classmethod
    def template_names(cls) -> set[str]:
        return {t for d in cls.descriptors().values() for t in d.template_names()}

    # --- Zustand -----------------------------------------------------------------------------------

    def is_active(self, *, frame: Frame | None = None) -> bool:
        return self._anchor().exists(frame=frame)

    def wait_until_active(self, timeout: float | None = None) -> Self:
        """Wirft `WrongPage`, wenn der Anchor nicht rechtzeitig erscheint."""
        try:
            self._anchor().wait(timeout=timeout)
        except ElementNotFound as e:
            raise WrongPage(f"Seite {self.name!r} nicht erkannt: {e}") from e
        return self

    def ensure_active(self, *, frame: Frame | None = None) -> Self:
        if not self.is_active(frame=frame):
            raise WrongPage(f"Erwartet Seite {self.name!r}, sie ist nicht aktiv")
        return self

    def _anchor(self) -> BoundElement:
        anchor = type(self).descriptors().get("anchor")
        if not isinstance(anchor, Element):
            raise TypeError(f"{type(self).__name__} deklariert keinen anchor = Element(...)")
        return anchor.bind(self.ctx)


class PageRegistry:
    """Bekannte Page-Klassen (für Validierung und später den Navigator) + Instanz-Cache."""

    def __init__(self) -> None:
        self._classes: dict[str, type[Page]] = {}
        self._instances: dict[type[Page], Page] = {}
        self._lock = threading.Lock()

    def register(self, cls: type[Page]) -> None:
        if not cls.has_anchor():
            raise BotError(f"Page {cls.__qualname__} hat keinen anchor = Element(...)")
        with self._lock:
            existing = self._classes.get(cls.name)
            if existing is not None and existing is not cls:
                raise BotError(
                    f"Page-Name {cls.name!r} doppelt: {existing.__qualname__} und {cls.__qualname__}"
                )
            self._classes[cls.name] = cls

    @property
    def classes(self) -> list[type[Page]]:
        with self._lock:
            return list(self._classes.values())

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._classes)

    def get_class(self, name: str) -> type[Page]:
        with self._lock:
            try:
                return self._classes[name]
            except KeyError:
                known = ", ".join(sorted(self._classes)) or "keine"
                raise BotError(f"Unbekannte Page {name!r}. Vorhanden: {known}") from None

    def instance[P: Page](self, cls: type[P], ctx: BotContext) -> P:
        """Pages sind zustandslos – eine Instanz pro Klasse und Context genügt."""
        with self._lock:
            page = self._instances.get(cls)
            if page is None or page.ctx is not ctx:
                page = cls(ctx)
                self._instances[cls] = page
        return cast(P, page)

    def template_names(self) -> set[str]:
        return {t for cls in self.classes for t in cls.template_names()}
