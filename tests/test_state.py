import threading
import time

import pytest

from adbbot.core.clock import FakeClock
from adbbot.core.exceptions import BotStopped, StepTimeout
from adbbot.core.state import RunControl


def run_in_thread(fn: object) -> tuple[threading.Thread, list[BaseException | None]]:
    outcome: list[BaseException | None] = []

    def target() -> None:
        try:
            fn()  # type: ignore[operator]
            outcome.append(None)
        except BaseException as e:
            outcome.append(e)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, outcome


def wait_until(predicate: object, timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while not predicate():  # type: ignore[operator]
        assert time.monotonic() < end, "Bedingung nicht eingetreten"
        time.sleep(0.005)


def test_checkpoint_passes_when_nothing_requested(fake_clock: FakeClock) -> None:
    RunControl(fake_clock).checkpoint()


def test_stop_raises_bot_stopped(fake_clock: FakeClock) -> None:
    control = RunControl(fake_clock)
    control.request_stop()
    with pytest.raises(BotStopped):
        control.checkpoint()


def test_bot_stopped_is_not_swallowed_by_except_exception(fake_clock: FakeClock) -> None:
    control = RunControl(fake_clock)
    control.request_stop()
    with pytest.raises(BotStopped):
        try:
            control.checkpoint()
        except Exception:
            pytest.fail("BotStopped darf nicht als Exception gefangen werden")


def test_deadline_raises_step_timeout(fake_clock: FakeClock) -> None:
    control = RunControl(fake_clock)
    control.set_deadline(fake_clock.monotonic() + 5)
    fake_clock.advance(5)
    control.checkpoint()  # genau auf der Deadline ist noch ok
    fake_clock.advance(0.1)
    with pytest.raises(StepTimeout):
        control.checkpoint()
    control.set_deadline(None)
    control.checkpoint()


def test_reset_clears_everything(fake_clock: FakeClock) -> None:
    control = RunControl(fake_clock)
    control.request_stop()
    control.request_pause()
    control.set_deadline(0)
    control.reset()
    assert not control.stop_requested
    assert not control.pause_requested
    assert control.deadline is None
    control.checkpoint()


def test_pause_blocks_until_resume(fake_clock: FakeClock) -> None:
    changes: list[bool] = []
    control = RunControl(fake_clock, on_pause_changed=changes.append, pause_poll_s=0.01)
    control.request_pause()

    thread, outcome = run_in_thread(control.checkpoint)
    wait_until(lambda: changes == [True])
    assert thread.is_alive()

    control.request_resume()
    thread.join(timeout=2)
    assert outcome == [None]
    assert changes == [True, False]


def test_stop_during_pause_raises(fake_clock: FakeClock) -> None:
    changes: list[bool] = []
    control = RunControl(fake_clock, on_pause_changed=changes.append, pause_poll_s=0.01)
    control.request_pause()

    thread, outcome = run_in_thread(control.checkpoint)
    wait_until(lambda: changes == [True])
    control.request_stop()
    thread.join(timeout=2)
    assert len(outcome) == 1
    assert isinstance(outcome[0], BotStopped)
    assert changes == [True, False]


def test_pause_duration_extends_deadline(fake_clock: FakeClock) -> None:
    changes: list[bool] = []
    control = RunControl(fake_clock, on_pause_changed=changes.append, pause_poll_s=0.01)
    control.set_deadline(fake_clock.monotonic() + 10)
    control.request_pause()

    thread, outcome = run_in_thread(control.checkpoint)
    wait_until(lambda: changes == [True])
    fake_clock.advance(60)  # eine Minute Pause
    control.request_resume()
    thread.join(timeout=2)

    assert outcome == [None]  # kein StepTimeout nach dem Fortsetzen
    assert control.deadline == pytest.approx(100 + 10 + 60)
