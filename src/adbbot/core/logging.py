"""structlog-Setup.

Jeder Log-Eintrag geht formatiert auf Konsole (und optional Datei) und zusätzlich als `LogEmitted`
auf den EventBus. Kontext über `log.bind(source="flow", flow=..., step=...)` – danach filtert die GUI.

Bewusst ohne `structlog.configure()`: der Logger wird pro Context gebaut, kein globaler Zustand.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import TextIO

import structlog
from structlog.typing import EventDict, FilteringBoundLogger, Processor, WrappedLogger

from adbbot.core.events import EventBus, LogEmitted

_RESERVED_KEYS = frozenset({"event", "level", "timestamp", "source"})


class _TeeLogger:
    """Schreibt jede gerenderte Zeile in mehrere Streams."""

    def __init__(self, streams: list[TextIO]) -> None:
        self._streams = streams
        self._lock = threading.Lock()

    def msg(self, message: str) -> None:
        with self._lock:
            for stream in self._streams:
                stream.write(message + "\n")
                stream.flush()

    log = debug = info = warn = warning = err = error = critical = exception = fatal = failure = msg


def bus_processor(bus: EventBus) -> Processor:
    guard = threading.local()

    def publish(_logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        # Reentrancy-Schutz: loggt ein Bus-Handler selbst, entsteht keine Endlosschleife.
        if getattr(guard, "active", False):
            return event_dict
        guard.active = True
        try:
            bus.publish(
                LogEmitted(
                    ts=time.time(),
                    level=str(event_dict.get("level", method_name)),
                    source=str(event_dict.get("source", "app")),
                    message=str(event_dict.get("event", "")),
                    context={k: v for k, v in event_dict.items() if k not in _RESERVED_KEYS},
                )
            )
        finally:
            guard.active = False
        return event_dict

    return publish


def create_logger(
    *,
    level: str = "INFO",
    bus: EventBus | None = None,
    log_file: Path | None = None,
    stream: TextIO | None = None,
) -> FilteringBoundLogger:
    streams: list[TextIO] = [stream if stream is not None else sys.stderr]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        streams.append(log_file.open("a", encoding="utf-8"))

    processors: list[Processor] = [
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="%H:%M:%S"),
    ]
    if bus is not None:
        processors.append(bus_processor(bus))
    processors.append(structlog.dev.ConsoleRenderer(colors=False))

    logger: FilteringBoundLogger = structlog.wrap_logger(
        _TeeLogger(streams),
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=False,
    )
    return logger


def get_null_logger() -> FilteringBoundLogger:
    """Logger, der nichts ausgibt (Default für Komponenten ohne übergebenen Logger)."""

    def drop(_logger: WrappedLogger, _method_name: str, _event_dict: EventDict) -> EventDict:
        raise structlog.DropEvent

    logger: FilteringBoundLogger = structlog.wrap_logger(
        _TeeLogger([]),
        processors=[drop],
        wrapper_class=structlog.make_filtering_bound_logger("CRITICAL"),
    )
    return logger


__all__ = ["bus_processor", "create_logger", "get_null_logger"]
