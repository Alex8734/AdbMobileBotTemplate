"""EventBus – der einzige Kanal vom Bot zur GUI.

Thread-sicher, typisiert, ohne Qt-Abhängigkeit. `publish` ruft die Handler synchron im Thread des
Publishers auf; Handler müssen daher billig sein (die GUI-Bridge legt nur in eine Queue).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from adbbot.core.state import BotState
from adbbot.core.types import Detection, Frame, Size, StepResult

_stdlib_log = logging.getLogger(__name__)


class Event:
    __slots__ = ()


@dataclass(slots=True)
class FrameCaptured(Event):
    frame: Frame


@dataclass(slots=True)
class DebugImageUpdated(Event):
    """Zwischenbild aus dem Bot (`vision.debug_image`), in der GUI unter „Ansicht“ wählbar.

    `frame.image` ist bereits BGR/uint8. `frame.ts` ist der Zeitstempel des zugehörigen Frames (dann passen
    dessen Overlays) oder -1.0, wenn das Bild zu keinem Frame gehört.
    """

    name: str
    frame: Frame


@dataclass(slots=True)
class DetectionsUpdated(Event):
    frame_ts: float
    detections: list[Detection]


@dataclass(slots=True)
class LogEmitted(Event):
    ts: float
    level: str
    source: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StateChanged(Event):
    state: BotState


@dataclass(slots=True)
class DeviceStatus(Event):
    connected: bool
    serial: str | None
    size: Size | None
    error: str | None = None
    retry_in_s: float | None = None  # nächster automatischer Verbindungsversuch


@dataclass(slots=True)
class FlowStarted(Event):
    flow: str


@dataclass(slots=True)
class StepStarted(Event):
    flow: str
    step: str
    index: int


@dataclass(slots=True)
class StepFinished(Event):
    flow: str
    step: str
    index: int
    result: StepResult
    duration_s: float
    error: str | None = None


@dataclass(slots=True)
class FlowFinished(Event):
    flow: str
    result: StepResult
    cancelled: bool = False
    error: str | None = None


@dataclass(slots=True)
class WatcherTriggered(Event):
    watcher: str


@dataclass(slots=True)
class RegistriesChanged(Event):
    pass


Handler = Callable[[Any], None]


class EventBus:
    """Handler für einen Typ erhalten auch alle Unterklassen-Events (`subscribe(Event, ...)` = alles).

    Eine Exception in einem Handler wird geloggt und beeinträchtigt weder den Publisher noch
    andere Handler. Das Logging dafür läuft bewusst über stdlib-`logging`, nicht über den Bus,
    damit ein fehlerhafter Log-Handler keine Endlosschleife erzeugt.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: list[tuple[type[Event], Handler]] = []

    def subscribe[E: Event](self, event_type: type[E], handler: Callable[[E], None]) -> Callable[[], None]:
        entry: tuple[type[Event], Handler] = (event_type, handler)
        with self._lock:
            self._handlers.append(entry)

        def unsubscribe() -> None:
            with self._lock:
                if entry in self._handlers:
                    self._handlers.remove(entry)

        return unsubscribe

    def subscribe_all(self, handler: Callable[[Event], None]) -> Callable[[], None]:
        return self.subscribe(Event, handler)

    def publish(self, event: Event) -> None:
        with self._lock:
            handlers = [h for t, h in self._handlers if isinstance(event, t)]
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                _stdlib_log.exception("Event-Handler %r fehlgeschlagen für %s", handler, type(event).__name__)
