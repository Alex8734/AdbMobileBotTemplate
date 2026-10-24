"""Vordergrund-Wächter: holt die App zurück, wenn sie während eines Flows den Vordergrund verliert.

Typischer Fall: Ein Tap landet auf einer Werbung und öffnet den Play Store oder den Browser. Template-Watcher
helfen dann nicht – auf dem fremden Screen gibt es nichts, das sie kennen.

Die Engine ruft `run()` im Poll-Hook auf. `current_app()` ist ein eigener adb-Aufruf, deshalb höchstens alle
`bot.foreground_check_interval_ms`. Abschaltbar pro Block über `ctx.suspend_watchers("foreground")`, etwa
wenn ein Flow absichtlich die Systemeinstellungen öffnet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adbbot.core.events import StepStarted, WatcherTriggered
from adbbot.core.exceptions import StepFailed, WatcherLoop

if TYPE_CHECKING:
    from adbbot.bot.context import BotContext

FOREGROUND_GROUP = "foreground"
TITLE = "App im Vordergrund"


class ForegroundGuard:
    """- nicht reentrant, zählt Auslösungen pro Step wie der `WatcherRunner`
    - holt die App per `bring_to_front()` (bestehender Task, kein Neustart) und wartet, bis sie vorne ist
    """

    def __init__(self, ctx: BotContext, *, interval_s: float, max_triggers_per_step: int) -> None:
        self.ctx = ctx
        self.interval_s = interval_s
        self.max_triggers_per_step = max_triggers_per_step
        self._last_check: float | None = None
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

    def run(self) -> bool:
        """`True`, wenn die App zurückgeholt wurde (der aktuelle Frame ist danach veraltet)."""
        if self._active or self.ctx.watchers_suspended(FOREGROUND_GROUP):
            return False
        now = self.ctx.clock.monotonic()
        if self._last_check is not None and now - self._last_check < self.interval_s:
            return False
        self._last_check = now

        app = self.ctx.config.app
        current = self.ctx.device.current_app()
        # None: keine fokussierte App (z. B. Sperrbildschirm) – zurückholen hilft da nicht
        if current is None or current == app.package or current in app.foreground_allowlist:
            return False

        self._step_triggers += 1
        if self._step_triggers > self.max_triggers_per_step:
            raise WatcherLoop(
                f"App verliert dauerhaft den Vordergrund (mehr als {self.max_triggers_per_step} Mal "
                f"in diesem Schritt, zuletzt an {current})"
            )
        self.ctx.events.publish(WatcherTriggered(TITLE))
        self._log.warning("App verlassen – hole sie zurück", watcher=TITLE, current=current)
        self._active = True
        try:
            self._bring_back(app.package, app.start_timeout_s)
        finally:
            self._active = False
        return True

    def _bring_back(self, package: str, timeout: float) -> None:
        self.ctx.device.bring_to_front(package)
        deadline = self.ctx.clock.monotonic() + timeout
        while self.ctx.device.current_app() != package:
            if self.ctx.clock.monotonic() >= deadline:
                raise StepFailed(f"App {package} ist nach {timeout:g} s nicht wieder im Vordergrund")
            self.ctx.sleep(0.5)
        self._log.info("App wieder im Vordergrund", watcher=TITLE)
