"""Flow + @step. `run()` ist die Inhaltsangabe, die Steps sind die Kapitel.

    class DailyFlow(ProjectFlow):
        name = "daily"

        def run(self) -> None:
            self.ensure_app_running()
            self.collect_daily_gift()

        @step("Tagesgeschenk abholen", retries=2)
        def collect_daily_gift(self) -> None:
            if not self.home.daily_gift.exists():
                raise StepSkipped("Kein Geschenk verfügbar")
            self.home.daily_gift.tap()

Was `@step` macht: checkpoint → `StepStarted` → Log-Kontext → kooperative Deadline pro Versuch →
`StepSkipped` = SKIPPED → `StepFailed` = `recover()` + neuer Versuch → endgültiger Fehler: Snapshot,
`optional` → weiter, sonst Flow-Abbruch → `StepFinished`. `BotStopped` wird nie gefangen.

Achtung: ein Retry führt die ganze Step-Methode erneut aus. Steps daher so schreiben, dass ein zweiter
Durchlauf harmlos ist (Zustand prüfen statt blind tappen).
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Concatenate

from structlog.typing import FilteringBoundLogger

from adbbot.bot.element import ElementAccess
from adbbot.bot.failure import mark_reported, was_reported
from adbbot.core.events import StepFinished, StepStarted
from adbbot.core.exceptions import BotError, StepFailed, StepSkipped, WrongPage
from adbbot.core.types import StepResult
from adbbot.utils.text import default_name

if TYPE_CHECKING:
    from adbbot.bot.context import BotContext
    from adbbot.bot.page import Page
    from adbbot.bot.storage import Storage
    from adbbot.device.protocol import DeviceProtocol
    from adbbot.vision.vision import Vision

STEP_ATTR = "__adbbot_step__"


@dataclass(frozen=True, slots=True)
class StepInfo:
    title: str
    retries: int | None = None  # None = bot.default_retries
    optional: bool = False
    timeout: float | None = None  # None = bot.step_timeout_s, gilt pro Versuch


def step[F: Flow, **P](
    title: str, *, retries: int | None = None, optional: bool = False, timeout: float | None = None
) -> Callable[[Callable[Concatenate[F, P], None]], Callable[Concatenate[F, P], StepResult]]:
    """Macht aus einer Flow-Methode einen benannten, wiederholbaren Schritt."""
    info = StepInfo(title, retries, optional, timeout)

    def decorate(func: Callable[Concatenate[F, P], None]) -> Callable[Concatenate[F, P], StepResult]:
        @functools.wraps(func)
        def wrapper(self: F, /, *args: P.args, **kwargs: P.kwargs) -> StepResult:
            return self._execute_step(info, lambda: func(self, *args, **kwargs))

        setattr(wrapper, STEP_ATTR, info)
        return wrapper

    return decorate


@dataclass(slots=True)
class _RunState:
    """Geteilt zwischen einem Flow und seinen Unterflows (`run_flow`)."""

    root_name: str
    counter: int = 0


class Flow(ElementAccess):
    name: ClassVar[str] = "flow"
    description: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "name" not in cls.__dict__:
            cls.name = default_name(cls.__name__, "Flow")

    def __init__(
        self, ctx: BotContext, params: Mapping[str, Any] | None = None, *, parent: Flow | None = None
    ) -> None:
        self.ctx = ctx
        self.params: dict[str, Any] = dict(params or {})
        self.parent = parent
        self._run: _RunState = parent._run if parent else _RunState(self.name)
        self._prefix: str = f"{parent._prefix}{self.name} › " if parent else ""
        self.log: FilteringBoundLogger = ctx.log.bind(source="flow", flow=self._run.root_name)

    @property
    def device(self) -> DeviceProtocol:
        return self.ctx.device

    @property
    def vision(self) -> Vision:
        return self.ctx.vision

    @property
    def store(self) -> Storage:
        return self.ctx.store

    def sleep(self, seconds: float) -> None:
        self.ctx.sleep(seconds)

    def go_to[P: Page](self, page: type[P]) -> P:
        """Per Navigator zur Seite wechseln (Weg aus `Page.transitions`)."""
        return self.ctx.navigator.go_to(page)

    def run(self) -> None:
        raise NotImplementedError(f"{type(self).__name__}.run() fehlt")

    def recover(self, error: StepFailed) -> None:
        """Hook zwischen zwei Versuchen eines Steps – z. B. Popups schließen. Default: nichts."""

    def run_flow(self, flow: type[Flow], **params: Any) -> None:
        """Unterflow ausführen; seine Steps erscheinen als `<unterflow> › <titel>`."""
        flow(self.ctx, params, parent=self).run()

    # --- generische Steps --------------------------------------------------------------------------

    @step("App im Vordergrund sicherstellen")
    def ensure_app_running(self) -> None:
        app = self.ctx.config.app
        current = self.device.current_app()
        if current == app.package:
            return
        if not app.restart_if_not_running:
            raise WrongPage(f"App {app.package} ist nicht im Vordergrund (aktuell: {current or '-'})")
        self.log.info("App wird gestartet", package=app.package, current=current)
        self.device.start_app(app.package, app.activity)
        self._wait_for_app(app.package, app.start_timeout_s)

    @step("App neu starten")
    def restart_app(self) -> None:
        app = self.ctx.config.app
        self.log.info("App wird neu gestartet", package=app.package)
        self.device.stop_app(app.package)
        self.sleep(1.0)
        self.device.start_app(app.package, app.activity)
        self._wait_for_app(app.package, app.start_timeout_s)

    def _wait_for_app(self, package: str, timeout: float) -> None:
        deadline = self.ctx.clock.monotonic() + timeout
        while self.device.current_app() != package:
            if self.ctx.clock.monotonic() >= deadline:
                raise StepFailed(f"App {package} ist nach {timeout:g} s nicht im Vordergrund")
            self.sleep(0.5)

    # --- Step-Ausführung ---------------------------------------------------------------------------

    def _execute_step(self, info: StepInfo, body: Callable[[], None]) -> StepResult:
        ctx, control, clock = self.ctx, self.ctx.control, self.ctx.clock
        control.checkpoint()
        self._run.counter += 1
        index = self._run.counter
        title = f"{self._prefix}{info.title}"
        flow_name = self._run.root_name

        outer_log, outer_deadline = self.log, control.deadline
        step_log = outer_log.bind(step=title)
        self.log = step_log
        ctx.events.publish(StepStarted(flow_name, title, index))
        step_log.info("Schritt gestartet", index=index)
        started = clock.monotonic()

        result = StepResult.OK
        error_text: str | None = None
        failure: Exception | None = None
        try:
            result = self._attempts(info, body, outer_deadline)
        except Exception as e:
            result = StepResult.FAILED
            error_text = f"{type(e).__name__}: {e}"
            if not was_reported(e):
                if isinstance(e, BotError):
                    step_log.error("Schritt fehlgeschlagen", error=error_text)
                else:
                    step_log.exception("Unerwarteter Fehler im Schritt", error=error_text)
                self._record_failure(title, e)
                mark_reported(e)
            if info.optional:
                step_log.warning("Optionaler Schritt fehlgeschlagen – Flow läuft weiter")
            else:
                failure = e
        finally:
            self.log = outer_log
            control.set_deadline(outer_deadline)

        duration = clock.monotonic() - started
        ctx.events.publish(StepFinished(flow_name, title, index, result, duration, error_text))
        step_log.info("Schritt beendet", result=result.name, duration_s=round(duration, 2))
        if failure is not None:
            raise failure
        return result

    def _attempts(self, info: StepInfo, body: Callable[[], None], outer_deadline: float | None) -> StepResult:
        bot = self.ctx.config.bot
        max_retries = bot.default_retries if info.retries is None else info.retries
        timeout = bot.step_timeout_s if info.timeout is None else info.timeout
        attempt = 0
        while True:
            attempt += 1
            self._set_deadline(timeout, outer_deadline)
            try:
                body()
                return StepResult.OK
            except StepSkipped as e:
                self.log.info("Schritt übersprungen", reason=str(e))
                return StepResult.SKIPPED
            except StepFailed as e:
                if was_reported(e) or attempt > max_retries:
                    raise
                self.log.warning(
                    "Versuch fehlgeschlagen – neuer Versuch",
                    attempt=attempt,
                    attempts=max_retries + 1,
                    error=f"{type(e).__name__}: {e}",
                )
                self._set_deadline(timeout, outer_deadline)
                try:
                    self.recover(e)
                except StepFailed as recover_error:
                    self.log.warning("recover() fehlgeschlagen", error=str(recover_error))

    def _set_deadline(self, timeout: float, outer_deadline: float | None) -> None:
        deadline = self.ctx.clock.monotonic() + timeout
        self.ctx.control.set_deadline(deadline if outer_deadline is None else min(deadline, outer_deadline))

    def _record_failure(self, step_title: str, error: Exception) -> None:
        if self.ctx.failures is None:
            return
        try:
            path = self.ctx.failures.record(flow=self._run.root_name, step=step_title, error=error)
        except Exception as e:
            self.log.warning("Fehler-Snapshot konnte nicht gespeichert werden", error=str(e))
            return
        self.log.info("Fehler-Snapshot gespeichert", path=str(path))
