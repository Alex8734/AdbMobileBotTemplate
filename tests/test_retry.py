import pytest

from adbbot.core.clock import FakeClock
from adbbot.core.events import DeviceStatus, EventBus
from adbbot.core.exceptions import BotStopped, DeviceError
from adbbot.core.types import Size
from adbbot.device.discovery import connect_with_retry
from adbbot.device.fake_device import FakeDevice
from adbbot.utils.retry import retry


def flaky(failures: int, exc: Exception) -> tuple[list[int], object]:
    calls: list[int] = []

    def fn() -> str:
        calls.append(1)
        if len(calls) <= failures:
            raise exc
        return "ok"

    return calls, fn


def test_retry_succeeds_with_backoff(fake_clock: FakeClock) -> None:
    calls, fn = flaky(3, DeviceError("x"))
    result = retry(fn, clock=fake_clock, attempts=5, base_delay=1, max_delay=3, factor=2)  # type: ignore[arg-type]
    assert result == "ok"
    assert len(calls) == 4
    assert fake_clock.sleeps == [1, 2, 3]  # auf max_delay gedeckelt


def test_retry_gives_up_with_last_error(fake_clock: FakeClock) -> None:
    calls, fn = flaky(10, DeviceError("immer"))
    with pytest.raises(DeviceError, match="immer"):
        retry(fn, clock=fake_clock, attempts=3)  # type: ignore[arg-type]
    assert len(calls) == 3


def test_retry_ignores_other_exceptions(fake_clock: FakeClock) -> None:
    calls, fn = flaky(1, KeyError("k"))
    with pytest.raises(KeyError):
        retry(fn, clock=fake_clock, attempts=3, exceptions=(DeviceError,))  # type: ignore[arg-type]
    assert len(calls) == 1


def test_retry_never_catches_bot_stopped(fake_clock: FakeClock) -> None:
    def fn() -> None:
        raise BotStopped

    with pytest.raises(BotStopped):
        retry(fn, clock=fake_clock, attempts=3)
    assert fake_clock.sleeps == []


def test_connect_with_retry_publishes_status(
    fake_device: FakeDevice, fake_clock: FakeClock, bus: EventBus
) -> None:
    fake_device.disconnect()
    statuses: list[DeviceStatus] = []
    bus.subscribe(DeviceStatus, statuses.append)
    fake_device.fail_next(DeviceError("offline"))

    size = connect_with_retry(fake_device, clock=fake_clock, attempts=3, base_delay_s=1, bus=bus)

    assert size == Size(40, 60)
    assert fake_device.is_connected()
    assert [s.connected for s in statuses] == [False, True]
    assert statuses[0].error == "offline"
    assert fake_clock.sleeps == [1]


def test_connect_with_retry_final_failure(
    fake_device: FakeDevice, fake_clock: FakeClock, bus: EventBus
) -> None:
    statuses: list[DeviceStatus] = []
    bus.subscribe(DeviceStatus, statuses.append)
    original_connect = fake_device.connect

    def always_fail() -> None:
        fake_device.fail_next(DeviceError("weg"))
        original_connect()

    fake_device.connect = always_fail  # type: ignore[method-assign]
    with pytest.raises(DeviceError):
        connect_with_retry(fake_device, clock=fake_clock, attempts=2, bus=bus)
    assert [s.connected for s in statuses] == [False, False]
