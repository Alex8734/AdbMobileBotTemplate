"""Test-Bausteine für die Bot-Schicht: Context mit FakeDevice, Event-Sammler."""

from __future__ import annotations

import io
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from adbbot.app import build_context
from adbbot.bot.context import BotContext
from adbbot.core.clock import Clock
from adbbot.core.config import AppConfig, deep_merge
from adbbot.core.events import Event, EventBus
from adbbot.core.logging import create_logger
from adbbot.core.types import Image
from adbbot.device.fake_device import FakeDevice, FakeScenario
from adbbot.utils.image import save_image

BASE_CONFIG: dict[str, Any] = {
    "profile": "test",
    "app": {"package": "com.example.test", "start_timeout_s": 2},
    "device": {"backend": "fake", "reference_resolution": [360, 640], "connect_attempts": 1},
    "vision": {"templates_dir": "templates", "poll_interval_ms": 100},
    "bot": {
        "humanize_input": False,
        "step_timeout_s": 30,
        "element_timeout_s": 2,
        "default_retries": 1,
        "watcher_interval_ms": 0,
        "watcher_max_triggers_per_step": 3,
        "idle_frame_interval_ms": 0,
    },
}


def make_context(
    root: Path,
    *,
    clock: Clock,
    screens: Mapping[str, Image],
    templates: Mapping[str, Image] | None = None,
    scenario: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
    connect: bool = True,
) -> BotContext:
    (root / "templates").mkdir(parents=True, exist_ok=True)
    for name, image in (templates or {}).items():
        save_image(root / "templates" / f"{name}.png", image)
    app_config = AppConfig.model_validate(deep_merge(BASE_CONFIG, config or {}))
    device = FakeDevice(
        screens,
        clock=clock,
        mode="manual",
        scenario=FakeScenario.from_dict(scenario) if scenario else None,
    )
    if connect:
        device.connect()
    bus = EventBus()
    log = create_logger(level="DEBUG", bus=bus, stream=io.StringIO())
    return build_context(app_config, root=root, clock=clock, bus=bus, log=log, device=device)


def fake(ctx: BotContext) -> FakeDevice:
    assert isinstance(ctx.device, FakeDevice)
    return ctx.device


class EventLog:
    """Sammelt Events thread-sicher; `wait_for` wartet (echte Zeit) auf eine Bedingung."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        self._cond = threading.Condition()
        bus.subscribe_all(self._add)

    def _add(self, event: Event) -> None:
        with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    def of[E: Event](self, kind: type[E]) -> list[E]:
        with self._cond:
            return [e for e in self.events if isinstance(e, kind)]

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 5.0) -> None:
        with self._cond:
            ok = self._cond.wait_for(predicate, timeout)
        assert ok, "Bedingung nicht rechtzeitig eingetreten"
