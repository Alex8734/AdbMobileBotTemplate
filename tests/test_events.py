import threading

import pytest

from adbbot.core.events import Event, EventBus, FlowStarted, StateChanged
from adbbot.core.state import BotState


def test_subscribe_by_type(bus: EventBus) -> None:
    flows: list[FlowStarted] = []
    bus.subscribe(FlowStarted, flows.append)
    bus.publish(FlowStarted("daily"))
    bus.publish(StateChanged(BotState.RUNNING))
    assert flows == [FlowStarted("daily")]


def test_subscribe_all_receives_everything(bus: EventBus) -> None:
    seen: list[Event] = []
    bus.subscribe_all(seen.append)
    bus.publish(FlowStarted("daily"))
    bus.publish(StateChanged(BotState.IDLE))
    assert [type(e) for e in seen] == [FlowStarted, StateChanged]


def test_unsubscribe(bus: EventBus) -> None:
    seen: list[Event] = []
    unsubscribe = bus.subscribe_all(seen.append)
    unsubscribe()
    unsubscribe()  # idempotent
    bus.publish(FlowStarted("daily"))
    assert seen == []


def test_failing_handler_does_not_break_others(bus: EventBus, caplog: pytest.LogCaptureFixture) -> None:
    seen: list[Event] = []

    def broken(_: Event) -> None:
        raise RuntimeError("kaputt")

    bus.subscribe_all(broken)
    bus.subscribe_all(seen.append)
    bus.publish(FlowStarted("daily"))
    assert len(seen) == 1
    assert "kaputt" in caplog.text


def test_handler_may_unsubscribe_during_publish(bus: EventBus) -> None:
    calls: list[str] = []
    unsubscribe = bus.subscribe_all(lambda _: (calls.append("a"), unsubscribe()))
    bus.publish(FlowStarted("x"))
    bus.publish(FlowStarted("y"))
    assert calls == ["a"]


def test_publish_from_many_threads(bus: EventBus) -> None:
    count = 0
    lock = threading.Lock()

    def handler(_: Event) -> None:
        nonlocal count
        with lock:
            count += 1

    bus.subscribe_all(handler)
    threads = [
        threading.Thread(target=lambda: [bus.publish(FlowStarted("f")) for _ in range(200)]) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert count == 1600
