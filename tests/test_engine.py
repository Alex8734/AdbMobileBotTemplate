"""Engine-Tests mit echtem Thread und RealClock (kurze Wartezeiten)."""

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from adbbot.bot.actions import action, get_action_spec
from adbbot.bot.context import BotContext
from adbbot.bot.engine import BotEngine
from adbbot.bot.flow import Flow
from adbbot.bot.registry import Registries
from adbbot.bot.watchers import get_watcher_spec, watcher
from adbbot.core.clock import RealClock
from adbbot.core.commands import KeyEvent, Pause, ReloadTemplates, Resume, RunAction, StartFlow, Stop, TapAt
from adbbot.core.events import FlowFinished, FlowStarted, RegistriesChanged, StateChanged, WatcherTriggered
from adbbot.core.exceptions import DeviceError
from adbbot.core.state import BotState
from adbbot.core.types import Match, Point, StepResult
from adbbot.device.fake_device import FakeScenario
from tests.botkit import EventLog, fake, make_context
from tests.imagegen import paste, textured


class Record(Flow):
    def run(self) -> None:
        self.params["order"].append(self.params["label"])


class Loop(Flow):
    """Läuft, bis `release` gesetzt ist oder Stop kommt."""

    def run(self) -> None:
        self.params["started"].set()
        release = self.params.get("release") or threading.Event()
        while not release.is_set():
            self.sleep(0.01)
        self.params["order"].append("loop")


class Boom(Flow):
    def run(self) -> None:
        raise KeyError("kaputt")


class WaitButton(Flow):
    def run(self) -> None:
        self.wait_for("home/button", timeout=2)


@dataclass
class World:
    ctx: BotContext
    reg: Registries
    events: EventLog
    order: list[str] = field(default_factory=list)
    engine: BotEngine | None = None

    def start(self) -> BotEngine:
        self.engine = BotEngine(self.ctx, self.reg, idle_frames=False)
        self.engine.start()
        return self.engine

    def finished(self, count: int = 1) -> list[FlowFinished]:
        self.events.wait_for(lambda: len(self.events.of(FlowFinished)) >= count)
        return self.events.of(FlowFinished)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    button, background = textured(60, 40, seed=7), textured(360, 640, seed=1)
    ctx = make_context(
        tmp_path,
        clock=RealClock(),
        screens={"home": paste(background, button, Point(100, 300)), "empty": background},
        templates={"home/button": button},
        config={"vision": {"poll_interval_ms": 10}},
        connect=False,
    )
    reg = Registries(ctx.pages)
    for cls in (Record, Loop, Boom, WaitButton):
        reg.flows.register(cls)
    w = World(ctx, reg, EventLog(ctx.events))

    @action("Aufnehmen", group="Test")
    def record_action(c: BotContext) -> None:
        w.order.append("action")

    spec = get_action_spec(record_action)
    assert spec is not None
    reg.actions.register(spec)
    yield w
    if w.engine is not None:
        assert w.engine.shutdown()


def test_flow_lifecycle(world: World) -> None:
    engine = world.start()
    engine.submit(StartFlow("record", {"order": world.order, "label": "a"}))
    result = world.finished()[0]
    assert (result.flow, result.result, result.cancelled) == ("record", StepResult.OK, False)
    assert world.order == ["a"]
    assert [e.state for e in world.events.of(StateChanged)] == [
        BotState.IDLE,
        BotState.RUNNING,
        BotState.IDLE,
    ]
    assert engine.state is BotState.IDLE


def test_work_commands_are_sequential(world: World) -> None:
    engine = world.start()
    started, release = threading.Event(), threading.Event()
    engine.submit(StartFlow("loop", {"order": world.order, "started": started, "release": release}))
    engine.submit(StartFlow("record", {"order": world.order, "label": "a"}))
    engine.submit(RunAction("record_action"))
    engine.submit(StartFlow("record", {"order": world.order, "label": "b"}))
    assert started.wait(2)
    assert world.order == []  # nichts läuft parallel zum ersten Flow
    release.set()

    finished = world.finished(4)
    assert world.order == ["loop", "a", "action", "b"]
    assert [e.flow for e in finished] == ["loop", "record", "Aufnehmen", "record"]
    lifecycle = [type(e) for e in world.events.events if isinstance(e, FlowStarted | FlowFinished)]
    assert lifecycle == [FlowStarted, FlowFinished] * 4


def test_pause_resume_stop_act_immediately(world: World) -> None:
    engine = world.start()
    started = threading.Event()
    engine.submit(StartFlow("loop", {"order": world.order, "started": started}))
    assert started.wait(2)

    engine.submit(Pause())
    world.events.wait_for(lambda: engine.state is BotState.PAUSED)
    engine.submit(Resume())
    world.events.wait_for(lambda: engine.state is BotState.RUNNING)
    engine.submit(Stop())

    result = world.finished()[0]
    assert result.cancelled
    assert world.order == []
    world.events.wait_for(lambda: engine.state is BotState.IDLE)
    states = [e.state for e in world.events.of(StateChanged)]
    assert states[:4] == [BotState.IDLE, BotState.RUNNING, BotState.PAUSED, BotState.RUNNING]
    assert BotState.STOPPING in states


def test_stop_discards_queued_work(world: World) -> None:
    engine = world.start()
    started = threading.Event()
    engine.submit(StartFlow("loop", {"order": world.order, "started": started}))
    engine.submit(StartFlow("record", {"order": world.order, "label": "verworfen"}))
    assert started.wait(2)
    engine.submit(Stop())
    world.finished()
    engine.submit(StartFlow("record", {"order": world.order, "label": "danach"}))
    finished = world.finished(2)
    assert [e.flow for e in finished] == ["loop", "record"]
    assert world.order == ["danach"]


def test_unknown_flow_and_action(world: World) -> None:
    engine = world.start()
    engine.submit(StartFlow("gibts_nicht"))
    engine.submit(RunAction("gibts_auch_nicht"))
    finished = world.finished(2)
    assert all(e.result is StepResult.FAILED for e in finished)
    assert "Unbekannter Flow" in (finished[0].error or "")
    assert "Unbekannte Action" in (finished[1].error or "")


def test_unexpected_exception_keeps_engine_alive(world: World) -> None:
    engine = world.start()
    engine.submit(StartFlow("boom"))
    engine.submit(StartFlow("record", {"order": world.order, "label": "weiter"}))
    finished = world.finished(2)
    assert finished[0].result is StepResult.FAILED
    assert finished[0].error == "KeyError: 'kaputt'"
    assert finished[1].result is StepResult.OK
    assert list((world.ctx.root / "data" / "failures").glob("*_boom_flow.json"))


def test_poll_hook_runs_watchers(world: World) -> None:
    calls: list[Match] = []

    @watcher("Button gesehen", template="home/button", cooldown_s=60)
    def seen(c: BotContext, match: Match) -> None:
        calls.append(match)

    spec = get_watcher_spec(seen)
    assert spec is not None
    world.reg.add_watcher(spec)
    engine = world.start()
    engine.submit(StartFlow("wait_button"))
    assert world.finished()[0].result is StepResult.OK
    assert len(calls) == 1
    assert [e.watcher for e in world.events.of(WatcherTriggered)] == ["Button gesehen"]


def test_device_and_template_commands(world: World) -> None:
    engine = world.start()
    engine.submit(TapAt(Point(1, 2)))
    engine.submit(KeyEvent(4))
    engine.submit(ReloadTemplates())
    world.events.wait_for(lambda: bool(world.events.of(RegistriesChanged)))
    assert [(r.kind, r.args) for r in fake(world.ctx).inputs] == [("tap", (Point(1, 2),)), ("key", (4,))]


def test_without_device(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> None:
        raise DeviceError("kein adb")

    monkeypatch.setattr(fake(world.ctx), "connect", refuse)
    engine = world.start()
    engine.submit(StartFlow("record", {"order": world.order, "label": "x"}))
    result = world.finished()[0]
    assert result.result is StepResult.FAILED
    assert result.error == "Kein Gerät verbunden"
    assert engine.state is BotState.DISCONNECTED
    assert world.order == []


def test_shutdown(world: World) -> None:
    engine = world.start()
    assert engine.is_alive
    assert engine.shutdown()
    assert not engine.is_alive
    world.engine = None


class TapIntoStore(Flow):
    """Tippt versehentlich auf eine Werbung, die den Play Store öffnet, und wartet danach auf den Button."""

    def run(self) -> None:
        self.device.tap(Point(5, 5))
        self.wait_for("home/button", timeout=2)


def test_foreground_guard_brings_app_back(world: World) -> None:
    device = fake(world.ctx)
    device.scenario = FakeScenario.from_dict(
        {"transitions": {"home": [{"tap": [0, 0, 10, 10], "goto": "empty", "app": "com.android.vending"}]}}
    )
    device.current_package = "com.example.test"
    world.reg.flows.register(TapIntoStore)
    world.start().submit(StartFlow("tap_into_store"))

    assert world.finished()[0].result is StepResult.OK
    assert device.current_app() == "com.example.test"
    assert "App im Vordergrund" in [e.watcher for e in world.events.of(WatcherTriggered)]
