import threading

import numpy as np
from pytestqt.qtbot import QtBot

from adbbot.core.events import (
    DebugImageUpdated,
    DetectionsUpdated,
    DeviceStatus,
    Event,
    EventBus,
    FlowFinished,
    FlowStarted,
    FrameCaptured,
    LogEmitted,
    RegistriesChanged,
    StateChanged,
    StepFinished,
    StepStarted,
    WatcherTriggered,
)
from adbbot.core.state import BotState
from adbbot.core.types import Frame, StepResult
from adbbot.gui.bridge import EventBridge


def frame(ts: float) -> Frame:
    return Frame(np.zeros((4, 4, 3), dtype=np.uint8), ts)


def test_frames_are_coalesced_and_order_is_kept(qtbot: QtBot) -> None:
    bus = EventBus()
    bridge = EventBridge(bus, autostart=False)
    frames: list[Frame] = []
    flow: list[Event] = []
    logs: list[LogEmitted] = []
    bridge.frame_ready.connect(frames.append)
    bridge.flow_event.connect(flow.append)
    bridge.log_ready.connect(logs.append)

    def publish() -> None:  # aus einem fremden Thread, wie die Engine
        for i in range(3):
            bus.publish(FrameCaptured(frame(float(i))))
        bus.publish(StepStarted("f", "a", 1))
        bus.publish(LogEmitted(0.0, "info", "flow", "hallo"))
        bus.publish(StepFinished("f", "a", 1, StepResult.OK, 0.1))

    thread = threading.Thread(target=publish)
    thread.start()
    thread.join()

    assert frames == []  # nichts passiert ohne drain im GUI-Thread
    assert bridge.drain() == 4
    assert [f.ts for f in frames] == [2.0]
    assert bridge.dropped_frames == 2
    assert [type(e) for e in flow] == [StepStarted, StepFinished]
    assert [e.message for e in logs] == ["hallo"]
    assert bridge.drain() == 0
    bridge.close()


def test_routing_of_all_event_types(qtbot: QtBot) -> None:
    bus = EventBus()
    bridge = EventBridge(bus, autostart=False)
    received: dict[str, list[object]] = {}
    for name in (
        "frame_ready",
        "debug_image_ready",
        "detections_ready",
        "log_ready",
        "state_changed",
        "flow_event",
        "device_status",
        "watcher_triggered",
        "registries_changed",
    ):
        received[name] = []
        getattr(bridge, name).connect(received[name].append)

    for event in (
        FrameCaptured(frame(1.0)),
        DebugImageUpdated("grau", frame(1.0)),
        DetectionsUpdated(1.0, []),
        LogEmitted(0.0, "info", "x", "y"),
        StateChanged(BotState.IDLE),
        FlowStarted("f"),
        FlowFinished("f", StepResult.OK),
        DeviceStatus(True, "s", None),
        WatcherTriggered("w"),
        RegistriesChanged(),
    ):
        bus.publish(event)
    bridge.drain()

    assert {name: len(items) for name, items in received.items()} == {
        "frame_ready": 1,
        "debug_image_ready": 1,
        "detections_ready": 1,
        "log_ready": 1,
        "state_changed": 1,
        "flow_event": 2,
        "device_status": 1,
        "watcher_triggered": 1,
        "registries_changed": 1,
    }
    bridge.close()


def test_debug_images_are_coalesced_per_name(qtbot: QtBot) -> None:
    bus = EventBus()
    bridge = EventBridge(bus, autostart=False)
    received: list[DebugImageUpdated] = []
    bridge.debug_image_ready.connect(received.append)
    for ts in (1.0, 2.0):
        bus.publish(DebugImageUpdated("grau", frame(ts)))
        bus.publish(DebugImageUpdated("maske", frame(ts)))

    assert bridge.drain() == 2
    assert [(e.name, e.frame.ts) for e in received] == [("grau", 2.0), ("maske", 2.0)]
    assert bridge.drain() == 0
    bridge.close()


def test_log_bursts_are_spread_over_ticks(qtbot: QtBot) -> None:
    bus = EventBus()
    bridge = EventBridge(bus, autostart=False, max_events_per_tick=10)
    for i in range(25):
        bus.publish(LogEmitted(0.0, "info", "x", str(i)))
    assert [bridge.drain(), bridge.drain(), bridge.drain(), bridge.drain()] == [10, 10, 5, 0]
    bridge.close()


def test_timer_drains_in_gui_thread(qtbot: QtBot) -> None:
    bus = EventBus()
    bridge = EventBridge(bus, interval_ms=5)
    with qtbot.waitSignal(bridge.state_changed, timeout=2000) as blocker:
        threading.Thread(target=lambda: bus.publish(StateChanged(BotState.RUNNING))).start()
    assert blocker.args == [StateChanged(BotState.RUNNING)]
    bridge.close()


def test_close_unsubscribes(qtbot: QtBot) -> None:
    bus = EventBus()
    bridge = EventBridge(bus, autostart=False)
    bridge.close()
    bus.publish(StateChanged(BotState.IDLE))
    assert bridge.drain() == 0
