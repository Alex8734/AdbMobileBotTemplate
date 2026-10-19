"""EventBridge – die Thread-Grenze zwischen Bot und GUI.

Der EventBus ruft Handler im Thread des Publishers (meist dem Bot-Thread) auf. Die Bridge legt dort
nur in eine Queue; Frames werden koalesziert (nur der neueste zählt). Ein QTimer leert die Queue im
GUI-Thread und emittiert dort die Qt-Signals.

Warum nicht ein Signal pro Event direkt aus dem Bot-Thread: Frames müssen koalesziert werden, Log-Bursts
dürfen die GUI nicht fluten, und die Thread-Grenze liegt an genau einer, testbaren Stelle.
"""

from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, QTimer, Signal

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
from adbbot.core.types import Frame


class EventBridge(QObject):
    frame_ready = Signal(object)  # Frame
    debug_image_ready = Signal(object)  # DebugImageUpdated (neuestes pro Name)
    detections_ready = Signal(object)  # DetectionsUpdated
    log_ready = Signal(object)  # LogEmitted
    state_changed = Signal(object)  # StateChanged
    flow_event = Signal(object)  # FlowStarted | StepStarted | StepFinished | FlowFinished
    device_status = Signal(object)  # DeviceStatus
    watcher_triggered = Signal(object)  # WatcherTriggered
    registries_changed = Signal(object)  # RegistriesChanged

    def __init__(
        self,
        bus: EventBus,
        parent: QObject | None = None,
        *,
        interval_ms: int = 33,
        max_events_per_tick: int = 500,
        autostart: bool = True,
    ) -> None:
        super().__init__(parent)
        self.max_events_per_tick = max_events_per_tick
        self.dropped_frames = 0
        self._queue: queue.SimpleQueue[Event] = queue.SimpleQueue()
        self._frame_lock = threading.Lock()
        self._latest_frame: Frame | None = None
        self._latest_debug_images: dict[str, DebugImageUpdated] = {}
        self._unsubscribe = bus.subscribe_all(self._enqueue)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.drain)
        if autostart:
            self._timer.start()

    def _enqueue(self, event: Event) -> None:
        """Läuft im Thread des Publishers – nur ablegen, nie Qt anfassen."""
        if isinstance(event, FrameCaptured):
            with self._frame_lock:
                if self._latest_frame is not None:
                    self.dropped_frames += 1
                self._latest_frame = event.frame
            return
        if isinstance(event, DebugImageUpdated):
            with self._frame_lock:
                self._latest_debug_images[event.name] = event
            return
        self._queue.put(event)

    def drain(self) -> int:
        """GUI-Thread: neuesten Frame, neueste Debug-Bilder + bis zu `max_events_per_tick` Events emittieren.

        Der Rest bleibt für den nächsten Tick in der Queue.
        """
        with self._frame_lock:
            frame, self._latest_frame = self._latest_frame, None
            debug_images, self._latest_debug_images = self._latest_debug_images, {}
        emitted = 0
        if frame is not None:
            self.frame_ready.emit(frame)
            emitted += 1
        for debug_image in debug_images.values():
            self.debug_image_ready.emit(debug_image)
            emitted += 1
        for _ in range(self.max_events_per_tick):
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            self._dispatch(event)
            emitted += 1
        return emitted

    def close(self) -> None:
        self._timer.stop()
        self._unsubscribe()

    def _dispatch(self, event: Event) -> None:
        match event:
            case DetectionsUpdated():
                self.detections_ready.emit(event)
            case LogEmitted():
                self.log_ready.emit(event)
            case StateChanged():
                self.state_changed.emit(event)
            case FlowStarted() | StepStarted() | StepFinished() | FlowFinished():
                self.flow_event.emit(event)
            case DeviceStatus():
                self.device_status.emit(event)
            case WatcherTriggered():
                self.watcher_triggered.emit(event)
            case RegistriesChanged():
                self.registries_changed.emit(event)
