from pytestqt.qtbot import QtBot

from adbbot.core.events import LogEmitted
from adbbot.gui.widgets.debug_panel import DebugPanel, LogFilter, format_line, level_rank


def log(level: str, source: str, message: str, **context: object) -> LogEmitted:
    return LogEmitted(0.0, level, source, message, dict(context))


def test_log_filter() -> None:
    assert level_rank("exception") == level_rank("error")
    assert level_rank("unbekannt") == level_rank("info")

    f = LogFilter(min_level="warning")
    assert not f.accepts(log("info", "flow", "a"))
    assert f.accepts(log("warn", "flow", "a"))
    assert f.accepts(log("error", "flow", "a"))

    by_source = LogFilter(min_level="debug", source="vision")
    assert by_source.accepts(log("debug", "vision", "find"))
    assert not by_source.accepts(log("error", "flow", "x"))

    search = LogFilter(min_level="debug", text="SHOP")
    assert search.accepts(log("info", "page", "tap", element="home.shop_button"))
    assert not search.accepts(log("info", "page", "tap", element="home.logo"))


def test_format_line() -> None:
    line = format_line(log("warning", "watcher", "Watcher ausgelöst", watcher="Popup"))
    assert "WARNING" in line
    assert "watcher" in line
    assert line.endswith("Watcher ausgelöst  watcher=Popup")


def test_panel_filters_by_level_source_and_text(qtbot: QtBot) -> None:
    panel = DebugPanel(max_lines=100)
    qtbot.addWidget(panel)
    for event in (
        log("debug", "vision", "find", template="home/logo"),
        log("info", "flow", "Schritt gestartet", step="Shop besuchen"),
        log("warning", "watcher", "Watcher ausgelöst"),
        log("error", "engine", "Fehlgeschlagen"),
    ):
        panel.append(event)

    assert [e.message for e in panel.visible_entries()] == [
        "Schritt gestartet",
        "Watcher ausgelöst",
        "Fehlgeschlagen",
    ]
    assert "find" not in panel.text()
    assert [panel.source_combo.itemText(i) for i in range(panel.source_combo.count())] == [
        "Alle Quellen",
        "engine",
        "flow",
        "vision",
        "watcher",
    ]

    panel.level_combo.setCurrentIndex(0)  # DEBUG
    assert len(panel.visible_entries()) == 4
    assert "home/logo" in panel.text()

    panel.source_combo.setCurrentText("vision")
    assert [e.message for e in panel.visible_entries()] == ["find"]

    panel.source_combo.setCurrentIndex(0)
    panel.search_edit.setText("shop")
    assert [e.message for e in panel.visible_entries()] == ["Schritt gestartet"]
    assert panel.text().count("\n") == 0


def test_ring_buffer_and_clear(qtbot: QtBot) -> None:
    panel = DebugPanel(max_lines=3)
    qtbot.addWidget(panel)
    for i in range(10):
        panel.append(log("info", "flow", f"zeile {i}"))
    assert [e.message for e in panel.visible_entries()] == ["zeile 7", "zeile 8", "zeile 9"]
    assert panel.view.document().blockCount() == 3
    panel.clear()
    assert panel.visible_entries() == []
    assert panel.text() == ""
