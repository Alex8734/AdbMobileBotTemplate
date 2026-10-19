"""BotContext – alles, was Flows, Pages, Watcher und Actions zur Laufzeit brauchen. Kein globaler Zustand."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from structlog.typing import FilteringBoundLogger

from adbbot.bot.element import BoundElement, Element
from adbbot.bot.navigator import Navigator
from adbbot.bot.page import Page, PageRegistry
from adbbot.bot.storage import Storage
from adbbot.core.clock import Clock
from adbbot.core.config import AppConfig
from adbbot.core.events import EventBus
from adbbot.core.state import RunControl
from adbbot.core.types import Rect
from adbbot.device.protocol import DeviceProtocol
from adbbot.vision.vision import Vision

if TYPE_CHECKING:
    from adbbot.bot.failure import FailureRecorder


@dataclass(eq=False)
class BotContext:
    config: AppConfig
    device: DeviceProtocol
    vision: Vision
    events: EventBus
    control: RunControl
    clock: Clock
    log: FilteringBoundLogger
    pages: PageRegistry
    root: Path
    rng: random.Random = field(default_factory=random.Random)
    failures: FailureRecorder | None = None
    store: Storage = field(default_factory=Storage.in_memory)  # persistenter Key-Value-Store pro Profil

    @cached_property
    def navigator(self) -> Navigator:
        """Seiten-Navigation über `Page.transitions` (einmal pro Context)."""
        return Navigator(self)

    def checkpoint(self) -> None:
        """Pause/Stop/Step-Timeout respektieren. Läuft in allen Vision-Warteschleifen automatisch."""
        self.control.checkpoint()

    def sleep(self, seconds: float) -> None:
        """Schlafen in 100-ms-Schritten, jeder mit `checkpoint()` – Stop wirkt sofort."""
        self.control.sleep(seconds)

    def page[P: Page](self, cls: type[P]) -> P:
        """Typsicher: `ctx.page(HomePage)` → `HomePage`."""
        return self.pages.instance(cls, self)

    def element(
        self,
        template: str | Sequence[str],
        *,
        region: Rect | None = None,
        threshold: float | None = None,
        timeout: float | None = None,
    ) -> BoundElement:
        """Ad-hoc-Element ohne Page."""
        return Element(template, region=region, threshold=threshold, timeout=timeout).bind(self)
