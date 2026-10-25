from pathlib import Path

import pytest

from adbbot.bot.context import BotContext
from adbbot.bot.flow import Flow, step
from adbbot.bot.watchers import WatcherRunner, WatcherSpec, get_watcher_spec, watcher
from adbbot.core.clock import FakeClock
from adbbot.core.events import StepStarted, WatcherTriggered
from adbbot.core.exceptions import WatcherLoop
from adbbot.core.types import Frame, Match, Point, Rect
from tests.botkit import EventLog, fake, make_context
from tests.imagegen import paste, textured


@pytest.fixture
def ctx(tmp_path: Path, fake_clock: FakeClock) -> BotContext:
    background = textured(360, 640, seed=1)
    popup, button = textured(50, 30, seed=8), textured(60, 40, seed=7)
    return make_context(
        tmp_path,
        clock=fake_clock,
        screens={
            "popup": paste(background, popup, Point(200, 50)),
            "clean": paste(background, button, Point(100, 300)),
        },
        templates={"common/popup": popup, "home/button": button},
        scenario={"transitions": {"popup": [{"tap": [200, 50, 50, 30], "goto": "clean"}]}},
    )


def make_spec(title: str = "Popup", calls: list[str] | None = None, **kwargs: object) -> WatcherSpec:
    record = calls if calls is not None else []

    @watcher(
        title,
        template=kwargs.pop("template", "common/popup"),
        cooldown_s=kwargs.pop("cooldown_s", 0),
        **kwargs,
    )  # type: ignore[arg-type]
    def handler(ctx: BotContext, match: Match) -> None:
        record.append(title)

    handler.__qualname__ = f"handler_{title}"
    spec = get_watcher_spec(handler)
    assert spec is not None
    return spec


def runner(
    ctx: BotContext, *specs: WatcherSpec, interval_s: float = 0.0, max_triggers: int = 3
) -> WatcherRunner:
    return WatcherRunner(ctx, list(specs), interval_s=interval_s, max_triggers_per_step=max_triggers)


def test_triggers_on_matching_frame(ctx: BotContext) -> None:
    events = EventLog(ctx.events)
    calls: list[str] = []
    r = runner(ctx, make_spec(calls=calls))

    frame = ctx.vision.capture()
    assert r.run(frame, flow_running=True)
    assert calls == ["Popup"]
    assert [e.watcher for e in events.of(WatcherTriggered)] == ["Popup"]
    detection = ctx.vision.overlay.detections[-1]
    assert (detection.label, detection.rect) == ("watcher: Popup", Rect(200, 50, 50, 30))

    fake(ctx).show("clean")
    assert not r.run(ctx.vision.capture(), flow_running=True)


def test_only_while_flow_runs_unless_idle(ctx: BotContext) -> None:
    calls: list[str] = []
    r = runner(ctx, make_spec("normal", calls), make_spec("idle", calls, idle=True))
    frame = ctx.vision.capture()
    assert r.run(frame, flow_running=False)
    assert calls == ["idle"]


def test_priority_and_one_per_frame(ctx: BotContext) -> None:
    calls: list[str] = []
    r = runner(ctx, make_spec("niedrig", calls, priority=0), make_spec("hoch", calls, priority=10))
    assert r.run(ctx.vision.capture(), flow_running=True)
    assert calls == ["hoch"]


def test_cooldown_and_interval(ctx: BotContext, fake_clock: FakeClock) -> None:
    calls: list[str] = []
    r = runner(ctx, make_spec(calls=calls, cooldown_s=5), interval_s=1.0)
    frame = ctx.vision.capture()
    assert r.run(frame, flow_running=True)
    assert not r.run(frame, flow_running=True)  # Intervall
    fake_clock.advance(1)
    assert not r.run(frame, flow_running=True)  # Cooldown
    fake_clock.advance(4)
    assert r.run(frame, flow_running=True)
    assert calls == ["Popup", "Popup"]


def test_not_reentrant(ctx: BotContext) -> None:
    inner_results: list[bool] = []
    frame = ctx.vision.capture()

    @watcher("Rekursiv", template="common/popup", cooldown_s=0)
    def recursive(c: BotContext, match: Match) -> None:
        inner_results.append(r.run(frame, flow_running=True))

    spec = get_watcher_spec(recursive)
    assert spec is not None
    r = runner(ctx, spec)
    assert r.run(frame, flow_running=True)
    assert inner_results == [False]


def test_trigger_limit_per_step(ctx: BotContext) -> None:
    r = runner(ctx, make_spec(), max_triggers=3)
    frame = ctx.vision.capture()
    for _ in range(3):
        assert r.run(frame, flow_running=True)
    with pytest.raises(WatcherLoop, match="Popup"):
        r.run(frame, flow_running=True)

    ctx.events.publish(StepStarted("flow", "nächster Schritt", 2))
    assert r.run(frame, flow_running=True)
    r.close()


class WaitForButton(Flow):
    def run(self) -> None:
        self.wait_button()

    @step("Auf Button warten")
    def wait_button(self) -> None:
        self.params["match"] = self.wait_for("home/button", timeout=5)


def test_watcher_clears_popup_during_wait(ctx: BotContext) -> None:
    @watcher("Popup schließen", template="common/popup", cooldown_s=0)
    def close_popup(c: BotContext, match: Match) -> None:
        c.device.tap(match.rect.center)

    spec = get_watcher_spec(close_popup)
    assert spec is not None
    r = runner(ctx, spec)

    def poll(frame: Frame) -> None:
        ctx.checkpoint()
        r.run(frame, flow_running=True)

    ctx.vision.poll_hook = poll
    flow = WaitForButton(ctx)
    flow.run()
    assert flow.params["match"].rect == Rect(100, 300, 60, 40)
    assert fake(ctx).current_screen == "clean"
    assert [rec.kind for rec in fake(ctx).inputs] == ["tap"]


def test_group_can_be_suspended(ctx: BotContext) -> None:
    calls: list[str] = []
    r = runner(ctx, make_spec("ad", calls, group="ads"))
    frame = ctx.vision.capture()
    with ctx.suspend_watchers("ads"):
        with ctx.suspend_watchers("ads"):  # verschachtelt
            assert not r.run(frame, flow_running=True)
        assert not r.run(frame, flow_running=True)
    assert calls == []
    assert r.run(frame, flow_running=True)
    assert calls == ["ad"]


def test_confirm_can_reject_a_match(ctx: BotContext) -> None:
    calls: list[str] = []
    answers = [False, True]
    seen: list[Match] = []

    def confirm(c: BotContext, match: Match) -> bool:
        seen.append(match)
        return answers.pop(0)

    r = runner(ctx, make_spec("ad", calls, confirm=confirm))
    frame = ctx.vision.capture()
    assert not r.run(frame, flow_running=True)
    assert r.run(frame, flow_running=True)
    assert calls == ["ad"]
    assert seen[0].label == "common/popup"


def test_handler_time_does_not_count_for_step_timeout(ctx: BotContext, fake_clock: FakeClock) -> None:
    @watcher("langsam", template="common/popup", cooldown_s=0)
    def slow(c: BotContext, match: Match) -> None:
        c.sleep(60)

    spec = get_watcher_spec(slow)
    assert spec is not None
    ctx.control.set_deadline(fake_clock.monotonic() + 5)
    assert runner(ctx, spec).run(ctx.vision.capture(), flow_running=True)
    ctx.checkpoint()  # ohne Pause wäre die Deadline längst überschritten
    deadline = ctx.control.deadline
    assert deadline is not None
    assert deadline - fake_clock.monotonic() == pytest.approx(5, abs=0.2)
