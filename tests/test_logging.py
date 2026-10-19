import io
from pathlib import Path

from adbbot.core.events import EventBus, LogEmitted
from adbbot.core.logging import create_logger, get_null_logger


def test_log_is_published_with_source_and_context(bus: EventBus) -> None:
    events: list[LogEmitted] = []
    bus.subscribe(LogEmitted, events.append)
    stream = io.StringIO()

    log = create_logger(level="INFO", bus=bus, stream=stream).bind(source="flow", flow="daily")
    log.info("Step gestartet", step="Zur Startseite")

    assert len(events) == 1
    e = events[0]
    assert (e.level, e.source, e.message) == ("info", "flow", "Step gestartet")
    assert e.context == {"flow": "daily", "step": "Zur Startseite"}
    assert "Step gestartet" in stream.getvalue()


def test_level_filter(bus: EventBus) -> None:
    events: list[LogEmitted] = []
    bus.subscribe(LogEmitted, events.append)
    log = create_logger(level="WARNING", bus=bus, stream=io.StringIO())
    log.info("leise")
    log.warning("laut")
    assert [e.message for e in events] == ["laut"]


def test_handler_that_logs_does_not_recurse(bus: EventBus) -> None:
    log = create_logger(bus=bus, stream=io.StringIO())
    events: list[LogEmitted] = []

    def handler(e: LogEmitted) -> None:
        events.append(e)
        log.info("aus dem Handler")

    bus.subscribe(LogEmitted, handler)
    log.info("start")
    assert [e.message for e in events] == ["start"]


def test_log_file(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "bot.log"
    create_logger(log_file=path, stream=io.StringIO()).error("in Datei")
    assert "in Datei" in path.read_text(encoding="utf-8")


def test_null_logger_is_silent() -> None:
    get_null_logger().critical("nichts passiert")
