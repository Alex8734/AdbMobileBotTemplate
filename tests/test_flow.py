import json
from pathlib import Path

import pytest

from adbbot.bot.context import BotContext
from adbbot.bot.flow import Flow, step
from adbbot.core.clock import FakeClock
from adbbot.core.events import StepFinished, StepStarted
from adbbot.core.exceptions import (
    BotStopped,
    ElementNotFound,
    OcrUnavailable,
    StepFailed,
    StepSkipped,
    StepTimeout,
    WrongPage,
)
from adbbot.core.types import Frame, Point, StepResult
from tests.botkit import EventLog, fake, make_context
from tests.imagegen import paste, textured


@pytest.fixture
def ctx(tmp_path: Path, fake_clock: FakeClock) -> BotContext:
    button = textured(60, 40, seed=7)
    background = textured(360, 640, seed=1)
    return make_context(
        tmp_path,
        clock=fake_clock,
        screens={"empty": background, "home": paste(background, button, Point(100, 300))},
        templates={"home/button": button},
    )


@pytest.fixture
def events(ctx: BotContext) -> EventLog:
    return EventLog(ctx.events)


def failure_reports(ctx: BotContext) -> list[dict[str, object]]:
    directory = ctx.root / "data" / "failures"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


class SimpleFlow(Flow):
    def run(self) -> None:
        self.first()
        self.skipped()
        self.last()

    @step("Erster")
    def first(self) -> None:
        self.params.setdefault("calls", []).append("first")

    @step("Übersprungen")
    def skipped(self) -> None:
        raise StepSkipped("nichts zu tun")

    @step("Letzter")
    def last(self) -> None:
        self.params["calls"].append("last")


def test_steps_publish_events(ctx: BotContext, events: EventLog) -> None:
    assert SimpleFlow.name == "simple"
    flow = SimpleFlow(ctx)
    flow.run()
    assert flow.params["calls"] == ["first", "last"]
    assert [(e.flow, e.step, e.index) for e in events.of(StepStarted)] == [
        ("simple", "Erster", 1),
        ("simple", "Übersprungen", 2),
        ("simple", "Letzter", 3),
    ]
    assert [e.result for e in events.of(StepFinished)] == [StepResult.OK, StepResult.SKIPPED, StepResult.OK]
    assert flow.skipped() is StepResult.SKIPPED
    assert ctx.control.deadline is None


class RetryFlow(Flow):
    def __init__(self, ctx: BotContext, failures: int, retries: int | None) -> None:
        super().__init__(ctx)
        self.failures = failures
        self.attempts = 0
        self.recovered: list[str] = []
        self.flaky = step("Wackelig", retries=retries)(RetryFlow._flaky).__get__(self)

    def _flaky(self) -> None:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise ElementNotFound(f"Versuch {self.attempts}")

    def recover(self, error: StepFailed) -> None:
        self.recovered.append(str(error))

    def run(self) -> None:
        self.flaky()


def test_retry_with_recover(ctx: BotContext, events: EventLog) -> None:
    flow = RetryFlow(ctx, failures=2, retries=2)
    flow.run()
    assert flow.attempts == 3
    assert flow.recovered == ["Versuch 1", "Versuch 2"]
    assert events.of(StepFinished)[-1].result is StepResult.OK
    assert failure_reports(ctx) == []


def test_retries_exhausted(ctx: BotContext, events: EventLog) -> None:
    flow = RetryFlow(ctx, failures=10, retries=1)
    with pytest.raises(ElementNotFound, match="Versuch 2"):
        flow.run()
    assert flow.attempts == 2
    finished = events.of(StepFinished)[-1]
    assert finished.result is StepResult.FAILED
    assert finished.error == "ElementNotFound: Versuch 2"

    reports = failure_reports(ctx)
    assert len(reports) == 1
    assert reports[0]["step"] == "Wackelig"
    assert reports[0]["error_type"] == "ElementNotFound"
    assert reports[0]["logs"]
    assert (ctx.root / "data" / "failures" / str(reports[0]["screenshot"])).is_file()


def test_default_retries_from_config(ctx: BotContext) -> None:
    flow = RetryFlow(ctx, failures=10, retries=None)  # bot.default_retries = 1
    with pytest.raises(ElementNotFound):
        flow.run()
    assert flow.attempts == 2


class OptionalFlow(Flow):
    def run(self) -> None:
        self.broken()
        self.no_ocr()
        self.after()

    @step("Kaputt, aber optional", optional=True, retries=0)
    def broken(self) -> None:
        raise StepFailed("egal")

    @step("Ohne OCR", optional=True)
    def no_ocr(self) -> None:
        self.params["ocr_calls"] = self.params.get("ocr_calls", 0) + 1
        raise OcrUnavailable("keine Engine")

    @step("Danach")
    def after(self) -> None:
        self.params["after"] = True


def test_optional_steps_continue(ctx: BotContext, events: EventLog) -> None:
    flow = OptionalFlow(ctx)
    flow.run()
    assert flow.params["after"] is True
    assert flow.params["ocr_calls"] == 1  # kein StepFailed → kein Retry
    assert [e.result for e in events.of(StepFinished)] == [
        StepResult.FAILED,
        StepResult.FAILED,
        StepResult.OK,
    ]
    assert len(failure_reports(ctx)) == 2


class TimeoutFlow(Flow):
    def run(self) -> None:
        self.params["step"](self)

    @step("Schläft zu lange", timeout=5, retries=0)
    def sleeper(self) -> None:
        while True:
            self.sleep(1)

    @step("Wartet zu lange", timeout=1, retries=0)
    def waiter(self) -> None:
        self.vision.wait_for("home/button", timeout=60)

    @step("Timeout pro Versuch", timeout=2, retries=1)
    def per_attempt(self) -> None:
        while True:
            self.sleep(0.5)


def test_step_timeout_in_sleep(ctx: BotContext, fake_clock: FakeClock) -> None:
    start = fake_clock.monotonic()
    with pytest.raises(StepTimeout):
        TimeoutFlow(ctx, {"step": TimeoutFlow.sleeper}).run()
    assert 5 <= fake_clock.monotonic() - start <= 6
    assert ctx.control.deadline is None


def test_step_timeout_in_wait_for(ctx: BotContext, fake_clock: FakeClock) -> None:
    fake(ctx).show("empty")
    ctx.vision.poll_hook = lambda _frame: ctx.checkpoint()
    start = fake_clock.monotonic()
    with pytest.raises(StepTimeout):
        TimeoutFlow(ctx, {"step": TimeoutFlow.waiter}).run()
    assert fake_clock.monotonic() - start < 2


def test_timeout_applies_per_attempt(ctx: BotContext, fake_clock: FakeClock) -> None:
    start = fake_clock.monotonic()
    with pytest.raises(StepTimeout):
        TimeoutFlow(ctx, {"step": TimeoutFlow.per_attempt}).run()
    assert 4 <= fake_clock.monotonic() - start <= 5


class StopFlow(Flow):
    def run(self) -> None:
        self.wait_step()

    @step("Wartet")
    def wait_step(self) -> None:
        self.vision.wait_for("home/button", timeout=60)

    @step("Schluckt nichts")
    def swallow(self) -> None:
        try:
            self.sleep(1)
        except Exception:
            self.params["swallowed"] = True


def test_stop_during_wait_for(ctx: BotContext, events: EventLog) -> None:
    fake(ctx).show("empty")
    frames: list[Frame] = []

    def hook(frame: Frame) -> None:
        frames.append(frame)
        if len(frames) == 3:
            ctx.control.request_stop()
        ctx.checkpoint()

    ctx.vision.poll_hook = hook
    with pytest.raises(BotStopped):
        StopFlow(ctx).run()
    assert len(frames) == 3
    assert events.of(StepStarted) and not events.of(StepFinished)
    assert failure_reports(ctx) == []
    assert ctx.control.deadline is None


def test_bot_stopped_is_not_swallowed(ctx: BotContext) -> None:
    flow = StopFlow(ctx)
    ctx.control.request_stop()
    with pytest.raises(BotStopped):
        flow.swallow()
    assert "swallowed" not in flow.params


class SubFlow(Flow):
    name = "sub"

    def run(self) -> None:
        self.inner()

    @step("Innen")
    def inner(self) -> None:
        self.params["seen"].append(self.params["x"])


class ParentFlow(Flow):
    name = "parent"

    def run(self) -> None:
        self.outer()
        self.run_flow(SubFlow, x=1, seen=self.params["seen"])

    @step("Außen")
    def outer(self) -> None:
        self.nested()

    @step("Verschachtelt")
    def nested(self) -> None:
        pass


def test_nested_steps_and_subflows(ctx: BotContext, events: EventLog) -> None:
    seen: list[int] = []
    ParentFlow(ctx, {"seen": seen}).run()
    assert seen == [1]
    assert [(e.flow, e.step, e.index) for e in events.of(StepStarted)] == [
        ("parent", "Außen", 1),
        ("parent", "Verschachtelt", 2),
        ("parent", "sub › Innen", 3),
    ]


class NestedFailFlow(Flow):
    def run(self) -> None:
        self.outer()

    @step("Außen", retries=3)
    def outer(self) -> None:
        self.params["outer"] = self.params.get("outer", 0) + 1
        self.inner()

    @step("Innen", retries=1)
    def inner(self) -> None:
        self.params["inner"] = self.params.get("inner", 0) + 1
        raise StepFailed("innen kaputt")


def test_failed_inner_step_is_not_retried_by_outer(ctx: BotContext, events: EventLog) -> None:
    flow = NestedFailFlow(ctx)
    with pytest.raises(StepFailed):
        flow.run()
    assert flow.params == {"outer": 1, "inner": 2}
    assert len(failure_reports(ctx)) == 1
    assert [e.result for e in events.of(StepFinished)] == [StepResult.FAILED, StepResult.FAILED]


class AppFlow(Flow):
    def run(self) -> None:
        self.ensure_app_running()


def test_ensure_app_running_starts_app(ctx: BotContext) -> None:
    AppFlow(ctx).run()
    device = fake(ctx)
    assert device.current_app() == "com.example.test"
    assert [r.kind for r in device.inputs] == ["start_app"]
    AppFlow(ctx).run()  # läuft schon → kein zweiter Start
    assert [r.kind for r in device.inputs] == ["start_app"]


def test_ensure_app_running_without_restart(tmp_path: Path, fake_clock: FakeClock) -> None:
    ctx = make_context(
        tmp_path,
        clock=fake_clock,
        screens={"s": textured(360, 640, seed=1)},
        config={"app": {"restart_if_not_running": False}, "bot": {"default_retries": 0}},
    )
    with pytest.raises(WrongPage, match="nicht im Vordergrund"):
        AppFlow(ctx).run()
