"""DebugPanel: Log mit Level-/Quellen-Filter, Textsuche, Auto-Scroll und Ringpuffer."""

from __future__ import annotations

import html
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from adbbot.core.events import LogEmitted

LEVELS = ("debug", "info", "warning", "error", "critical")
LEVEL_COLORS = {
    "debug": "#8a8f98",
    "info": "#d8dee9",
    "warning": "#ebcb8b",
    "error": "#ff6b6b",
    "critical": "#ff4d6d",
}
ALL_SOURCES = "Alle Quellen"

_ALIASES = {"warn": "warning", "exception": "error", "err": "error", "fatal": "critical", "failure": "error"}
_HIDDEN_CONTEXT_KEYS = frozenset({"exc_info"})


def normalize_level(level: str) -> str:
    name = _ALIASES.get(level.lower(), level.lower())
    return name if name in LEVELS else "info"


def level_rank(level: str) -> int:
    return LEVELS.index(normalize_level(level))


def format_context(context: dict[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in context.items() if k not in _HIDDEN_CONTEXT_KEYS)


def format_line(event: LogEmitted) -> str:
    time = datetime.fromtimestamp(event.ts).strftime("%H:%M:%S")
    line = f"{time} {normalize_level(event.level).upper():<8} {event.source:<8} {event.message}"
    context = format_context(event.context)
    return f"{line}  {context}" if context else line


@dataclass(frozen=True, slots=True)
class LogFilter:
    min_level: str = "info"
    source: str | None = None  # None = alle
    text: str = ""  # Teilstring in Nachricht oder Kontext, ohne Groß-/Kleinschreibung

    def accepts(self, event: LogEmitted) -> bool:
        if level_rank(event.level) < level_rank(self.min_level):
            return False
        if self.source is not None and event.source != self.source:
            return False
        if self.text:
            haystack = f"{event.message} {format_context(event.context)}".lower()
            return self.text.lower() in haystack
        return True


class DebugPanel(QWidget):
    def __init__(self, max_lines: int = 5000, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: deque[LogEmitted] = deque(maxlen=max_lines)
        self._filter = LogFilter()
        self._sources: set[str] = set()

        self.level_combo = QComboBox()
        for level in LEVELS[:-1]:
            self.level_combo.addItem(level.upper(), level)
        self.level_combo.setCurrentIndex(LEVELS.index("info"))
        self.source_combo = QComboBox()
        self.source_combo.addItem(ALL_SOURCES, None)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Suchen …")
        self.search_edit.setClearButtonEnabled(True)
        self.auto_scroll = QCheckBox("Auto-Scroll")
        self.auto_scroll.setChecked(True)
        clear_button = QToolButton()
        clear_button.setText("Leeren")
        clear_button.clicked.connect(self.clear)

        self.view = QPlainTextEdit()
        self.view.setObjectName("debugLog")
        self.view.setReadOnly(True)
        self.view.setUndoRedoEnabled(False)
        self.view.setMaximumBlockCount(max_lines)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(self.level_combo)
        controls.addWidget(self.source_combo)
        controls.addWidget(self.search_edit, 1)
        controls.addWidget(self.auto_scroll)
        controls.addWidget(clear_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(controls)
        layout.addWidget(self.view)

        self.level_combo.currentIndexChanged.connect(self._update_filter)
        self.source_combo.currentIndexChanged.connect(self._update_filter)
        self.search_edit.textChanged.connect(self._update_filter)

    @property
    def filter(self) -> LogFilter:
        return self._filter

    def append(self, event: LogEmitted) -> None:
        self._entries.append(event)
        if event.source not in self._sources:
            self._add_source(event.source)
        if self._filter.accepts(event):
            self._write(event)
            self._scroll_to_end()

    def visible_entries(self) -> list[LogEmitted]:
        return [e for e in self._entries if self._filter.accepts(e)]

    def text(self) -> str:
        return self.view.toPlainText()

    def clear(self) -> None:
        self._entries.clear()
        self.view.clear()

    # --- intern ------------------------------------------------------------------------------------

    def _add_source(self, source: str) -> None:
        self._sources.add(source)
        index = 1
        while index < self.source_combo.count() and self.source_combo.itemText(index) < source:
            index += 1
        self.source_combo.insertItem(index, source, source)

    def _update_filter(self, *_args: object) -> None:
        level = self.level_combo.currentData()
        source = self.source_combo.currentData()
        self._filter = LogFilter(
            min_level=level if isinstance(level, str) else "info",
            source=source if isinstance(source, str) else None,
            text=self.search_edit.text().strip(),
        )
        self._rebuild()

    def _rebuild(self) -> None:
        self.view.setUpdatesEnabled(False)
        try:
            self.view.clear()
            for event in self._entries:
                if self._filter.accepts(event):
                    self._write(event)
        finally:
            self.view.setUpdatesEnabled(True)
        self._scroll_to_end()

    def _write(self, event: LogEmitted) -> None:
        color = LEVEL_COLORS[normalize_level(event.level)]
        self.view.appendHtml(
            f'<span style="white-space:pre; color:{color}">{html.escape(format_line(event))}</span>'
        )

    def _scroll_to_end(self) -> None:
        if self.auto_scroll.isChecked():
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())
