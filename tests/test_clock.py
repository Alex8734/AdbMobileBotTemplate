from adbbot.core.clock import FakeClock, RealClock


def test_fake_clock_sleep_advances_without_waiting() -> None:
    clock = FakeClock(start=5.0)
    clock.sleep(30)
    clock.sleep(-1)  # negative Werte verändern die Zeit nicht
    clock.advance(0.5)
    assert clock.monotonic() == 35.5
    assert clock.sleeps == [30, -1]


def test_real_clock_is_monotonic() -> None:
    clock = RealClock()
    a = clock.monotonic()
    clock.sleep(0)
    assert clock.monotonic() >= a
