"""Auto-Reconnect im Idle-Tick: Verbindungsverlust erkennen, mit Backoff neu verbinden, Status melden."""

from pathlib import Path

import pytest

from adbbot.bot.engine import BotEngine
from adbbot.bot.registry import Registries
from adbbot.core.clock import RealClock
from adbbot.core.events import DeviceStatus
from adbbot.core.exceptions import DeviceError
from adbbot.core.state import BotState
from tests.botkit import EventLog, fake, make_context
from tests.imagegen import textured

FAST = {"reconnect_delay_s": 0.05, "reconnect_max_delay_s": 0.1, "health_check_interval_s": 0.05}


def test_reconnects_after_device_loss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_context(
        tmp_path,
        clock=RealClock(),
        screens={"s": textured(360, 640, seed=1)},
        config={"device": FAST},
        connect=False,
    )
    events = EventLog(ctx.events)
    engine = BotEngine(ctx, Registries(ctx.pages), idle_frames=False)
    engine.start()
    try:
        events.wait_for(lambda: engine.state is BotState.IDLE)

        device = fake(ctx)
        real_connect = device.connect
        failures = {"left": 2}

        def flaky_connect() -> None:
            if failures["left"] > 0:
                failures["left"] -= 1
                raise DeviceError("USB getrennt")
            real_connect()

        monkeypatch.setattr(device, "connect", flaky_connect)
        device.disconnect()  # Gerät geht verloren, während kein Flow läuft

        events.wait_for(lambda: engine.state is BotState.DISCONNECTED)
        events.wait_for(lambda: any(s.retry_in_s for s in events.of(DeviceStatus)))
        events.wait_for(lambda: engine.state is BotState.IDLE)

        assert failures["left"] == 0
        statuses = events.of(DeviceStatus)
        assert statuses[-1].connected
        assert any(s.error == "USB getrennt" for s in statuses)
    finally:
        assert engine.shutdown()


def test_idle_frames_detect_loss(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        clock=RealClock(),
        screens={"s": textured(360, 640, seed=1)},
        config={"device": FAST, "bot": {"idle_frame_interval_ms": 20}},
        connect=False,
    )
    events = EventLog(ctx.events)
    engine = BotEngine(ctx, Registries(ctx.pages), idle_frames=True)
    engine.start()
    try:
        events.wait_for(lambda: engine.state is BotState.IDLE)
        fake(ctx).fail_next(DeviceError("screencap kaputt"))
        events.wait_for(lambda: any(s.error == "screencap kaputt" for s in events.of(DeviceStatus)))
        events.wait_for(lambda: engine.state is BotState.IDLE)  # nächster Tick verbindet wieder
    finally:
        assert engine.shutdown()
