import random

from adbbot.core.clock import FakeClock
from adbbot.core.config import BotConfig
from adbbot.core.types import Point
from adbbot.device.fake_device import FakeDevice
from adbbot.device.humanize import HumanizedDevice, humanize


def make(fake_device: FakeDevice, fake_clock: FakeClock, **kwargs: object) -> HumanizedDevice:
    return HumanizedDevice(fake_device, sleep=fake_clock.sleep, rng=random.Random(42), **kwargs)  # type: ignore[arg-type]


def test_tap_jitter_and_post_delay(fake_device: FakeDevice, fake_clock: FakeClock) -> None:
    device = make(fake_device, fake_clock, tap_jitter_px=3, post_input_delay_ms=(150, 400))
    for _ in range(200):
        device.tap(Point(100, 100))

    points = [r.args[0] for r in fake_device.inputs]
    assert all(97 <= p.x <= 103 and 97 <= p.y <= 103 for p in points)
    assert len(set(points)) > 1
    assert all(0.15 <= s <= 0.4 for s in fake_clock.sleeps)
    assert len(fake_clock.sleeps) == 200


def test_jitter_never_negative(fake_device: FakeDevice, fake_clock: FakeClock) -> None:
    device = make(fake_device, fake_clock, tap_jitter_px=10)
    for _ in range(50):
        device.tap(Point(0, 0))
    assert all(r.args[0].x >= 0 and r.args[0].y >= 0 for r in fake_device.inputs)


def test_swipe_duration_varies(fake_device: FakeDevice, fake_clock: FakeClock) -> None:
    device = make(fake_device, fake_clock, duration_jitter=0.2)
    for _ in range(50):
        device.swipe(Point(100, 100), Point(100, 500), 1000)
    durations = {r.args[2] for r in fake_device.inputs}
    assert all(800 <= d <= 1200 for d in durations)
    assert len(durations) > 1


def test_zero_jitter_and_delay_is_exact(fake_device: FakeDevice, fake_clock: FakeClock) -> None:
    device = make(fake_device, fake_clock, tap_jitter_px=0, post_input_delay_ms=(0, 0))
    device.tap(Point(7, 8))
    assert fake_device.inputs[0].args == (Point(7, 8),)
    assert fake_clock.sleeps == []


def test_passthrough(fake_device: FakeDevice, fake_clock: FakeClock) -> None:
    device = make(fake_device, fake_clock)
    assert device.serial == "fake"
    assert device.screenshot().size == fake_device.screen_size()
    device.start_app("com.x")
    assert device.current_app() == "com.x"


def test_humanize_respects_config(fake_device: FakeDevice, fake_clock: FakeClock) -> None:
    assert humanize(fake_device, BotConfig(humanize_input=False), sleep=fake_clock.sleep) is fake_device
    wrapped = humanize(fake_device, BotConfig(humanize_input=True), sleep=fake_clock.sleep)
    assert isinstance(wrapped, HumanizedDevice)
