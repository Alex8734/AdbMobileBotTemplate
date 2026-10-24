from pathlib import Path

import pytest

from adbbot.bot.context import BotContext
from adbbot.bot.foreground import TITLE, ForegroundGuard
from adbbot.core.clock import FakeClock
from adbbot.core.events import StepStarted, WatcherTriggered
from adbbot.core.exceptions import StepFailed, WatcherLoop
from adbbot.core.types import Point
from tests.botkit import EventLog, fake, make_context
from tests.imagegen import textured

GAME = "com.example.test"
STORE = "com.android.vending"


@pytest.fixture
def ctx(tmp_path: Path, fake_clock: FakeClock) -> BotContext:
    c = make_context(
        tmp_path,
        clock=fake_clock,
        screens={"game": textured(360, 640, seed=1), "store": textured(360, 640, seed=2)},
        scenario={
            "start": "game",
            "start_app": "game",
            "transitions": {"game": [{"tap": [0, 0, 360, 640], "goto": "store", "app": STORE}]},
        },
        config={"app": {"foreground_allowlist": ["com.google.android.gms"]}},
    )
    fake(c).current_package = GAME
    return c


def guard(ctx: BotContext, *, interval_s: float = 0.0, max_triggers: int = 3) -> ForegroundGuard:
    return ForegroundGuard(ctx, interval_s=interval_s, max_triggers_per_step=max_triggers)


def leave_app(ctx: BotContext) -> None:
    fake(ctx).show("game")
    ctx.device.tap(Point(10, 10))
    assert ctx.device.current_app() == STORE


def test_brings_app_back(ctx: BotContext) -> None:
    events = EventLog(ctx.events)
    g = guard(ctx)
    assert not g.run()  # App vorne: nichts tun

    leave_app(ctx)
    assert g.run()
    assert (ctx.device.current_app(), fake(ctx).current_screen) == (GAME, "game")
    assert fake(ctx).inputs[-1].kind == "bring_to_front"
    assert [e.watcher for e in events.of(WatcherTriggered)] == [TITLE]


def test_allowlist_lockscreen_and_suspend(ctx: BotContext) -> None:
    g = guard(ctx)
    fake(ctx).current_package = "com.google.android.gms"
    assert not g.run()
    fake(ctx).current_package = None
    assert not g.run()

    leave_app(ctx)
    with ctx.suspend_watchers("foreground"):
        assert not g.run()
    assert g.run()


def test_interval(ctx: BotContext, fake_clock: FakeClock) -> None:
    g = guard(ctx, interval_s=2.0)
    assert not g.run()
    leave_app(ctx)
    assert not g.run()  # zu früh
    fake_clock.advance(2.0)
    assert g.run()


def test_loop_limit_resets_per_step(ctx: BotContext) -> None:
    g = guard(ctx, max_triggers=2)
    for _ in range(2):
        leave_app(ctx)
        assert g.run()
    ctx.events.publish(StepStarted("x", "Schritt", 0))
    for _ in range(2):
        leave_app(ctx)
        assert g.run()
    leave_app(ctx)
    with pytest.raises(WatcherLoop):
        g.run()


def test_fails_when_app_does_not_come_back(ctx: BotContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fake(ctx), "bring_to_front", lambda package: None)
    leave_app(ctx)
    with pytest.raises(StepFailed, match="nicht wieder im Vordergrund"):
        guard(ctx).run()
