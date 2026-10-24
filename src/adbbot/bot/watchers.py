"""Watcher: global registrierte Handler für Störungen, die überall auftauchen können.

    @watcher("Bewertungs-Popup", template="common/rate_later", priority=10)
    def dismiss_rating(ctx: BotContext, match: Match) -> None:
        ctx.device.tap(match.rect.center)

Watcher mit `group` lassen sich zeitweise abschalten, z. B. während der Bot selbst eine Werbung schaut:

    with ctx.suspend_watchers("ads"):
        ...

Die Engine prüft sie über `vision.poll_hook` auf jedem Frame der Warteschleifen – ohne Extra-Screenshot.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from adbbot.core.events import StepStarted, WatcherTriggered
from adbbot.core.exceptions import WatcherLoop
from adbbot.core.types import Frame, Match, Rect

if TYPE_CHECKING:
    from adbbot.bot.context import BotContext

WATCHER_ATTR = "__adbbot_watcher__"

type WatcherFunc = Callable[[BotContext, Match], None]


@dataclass(frozen=True, slots=True)
class WatcherSpec:
    title: str
    func: WatcherFunc
    templates: tuple[str, ...]
    region: Rect | None = None  # Referenz-Koordinaten
    threshold: float | None = None
    priority: int = 0  # höher = zuerst
    cooldown_s: float = 2.0  # frühestens nach dieser Zeit erneut auslösen
    idle: bool = False  # auch ohne laufenden Flow prüfen (braucht Idle-Livebild)
    group: str | None = None  # über `ctx.suspend_watchers(group)` abschaltbar

    @property
    def key(self) -> str:
        return f"{getattr(self.func, '__module__', '?')}.{getattr(self.func, '__qualname__', self.title)}"


def watcher(
    title: str,
    *,
    template: str | Sequence[str],
    region: Rect | None = None,
    threshold: float | None = None,
    priority: int = 0,
    cooldown_s: float = 2.0,
    idle: bool = False,
    group: str | None = None,
) -> Callable[[WatcherFunc], WatcherFunc]:
    templates = (template,) if isinstance(template, str) else tuple(template)
    if not templates:
        raise ValueError("Watcher braucht mindestens ein Template")

    def decorate(func: WatcherFunc) -> WatcherFunc:
        spec = WatcherSpec(title, func, templates, region, threshold, priority, cooldown_s, idle, group)
        setattr(func, WATCHER_ATTR, spec)
        return func

    return decorate


def get_watcher_spec(obj: object) -> WatcherSpec | None:
    spec = getattr(obj, WATCHER_ATTR, None)
    return spec if isinstance(spec, WatcherSpec) else None


class WatcherRunner:
    """Prüft Watcher auf bereits geholten Frames.

    - höchstens alle `interval_s`, nach Priorität, pro Aufruf löst höchstens einer aus
    - nicht reentrant: während ein Handler läuft (evtl. selbst mit `wait_for`), wird nichts geprüft
    - mehr als `max_triggers_per_step` Auslösungen in einem Step → `WatcherLoop`
    """

    def __init__(
        self,
        ctx: BotContext,
        specs: Sequence[WatcherSpec],
        *,
        interval_s: float = 1.0,
        max_triggers_per_step: int = 5,
    ) -> None:
        self.ctx = ctx
        self.specs = sorted(specs, key=lambda s: -s.priority)
        self.interval_s = interval_s
        self.max_triggers_per_step = max_triggers_per_step
        self._last_check: float | None = None
        self._last_trigger: dict[str, float] = {}
        self._step_triggers = 0
        self._active = False
        self._log = ctx.log.bind(source="watcher")
        self._unsubscribe = ctx.events.subscribe(StepStarted, lambda _event: self.reset_step())

    def reset_step(self) -> None:
        self._step_triggers = 0

    def reset(self) -> None:
        """Vor jedem Flow."""
        self._step_triggers = 0
        self._last_check = None

    def close(self) -> None:
        self._unsubscribe()

    def run(self, frame: Frame, *, flow_running: bool) -> bool:
        """`True`, wenn ein Watcher ausgelöst hat (der Frame ist danach veraltet)."""
        if self._active or not self.specs:
            return False
        now = self.ctx.clock.monotonic()
        if self._last_check is not None and now - self._last_check < self.interval_s:
            return False
        self._last_check = now

        for spec in self.specs:
            if not (flow_running or spec.idle):
                continue
            if spec.group is not None and self.ctx.watchers_suspended(spec.group):
                continue
            last = self._last_trigger.get(spec.key)
            if last is not None and now - last < spec.cooldown_s:
                continue
            match = self._find(spec, frame)
            if match is None:
                continue

            self._step_triggers += 1
            if flow_running and self._step_triggers > self.max_triggers_per_step:
                raise WatcherLoop(
                    f"Watcher „{spec.title}“ feuert dauerhaft "
                    f"(mehr als {self.max_triggers_per_step} Auslösungen in diesem Schritt)"
                )
            self._last_trigger[spec.key] = now
            self.ctx.vision.overlay.found(
                Match(match.rect, match.confidence, f"watcher: {spec.title}"), frame.ts
            )
            self.ctx.events.publish(WatcherTriggered(spec.title))
            self._log.info("Watcher ausgelöst", watcher=spec.title, confidence=round(match.confidence, 3))
            self._active = True
            try:
                spec.func(self.ctx, match)
            finally:
                self._active = False
            return True
        return False

    def _find(self, spec: WatcherSpec, frame: Frame) -> Match | None:
        """Direkt über den Matcher – ohne Overlay-Einträge für jede erfolglose Prüfung."""
        vision = self.ctx.vision
        region = vision.to_device(spec.region) if spec.region else None
        for name in spec.templates:
            match = vision.matcher.find(
                frame.image, vision.templates.get(name), region=region, threshold=spec.threshold
            )
            if match is not None:
                return match
        return None
