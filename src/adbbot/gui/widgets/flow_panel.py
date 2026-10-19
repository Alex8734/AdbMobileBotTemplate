"""FlowPanel: Flow-Auswahl, Parameter (JSON) und Step-Timeline mit Dauer und Ergebnis.

Bewusst kein „Schritt x/y“: die Gesamtzahl ist vorab nicht bekannt (Steps sind bedingte Methodenaufrufe).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from adbbot.core.events import FlowFinished, FlowStarted, StepFinished, StepStarted
from adbbot.core.state import BotState
from adbbot.core.types import StepResult

RUNNING_ICON = "▶"
ABORTED_ICON = "■"
RESULT_ICONS = {StepResult.OK: "✔", StepResult.SKIPPED: "↷", StepResult.FAILED: "✗"}
RESULT_COLORS = {StepResult.OK: "#a3d977", StepResult.SKIPPED: "#9aa0aa", StepResult.FAILED: "#ff6b6b"}


class FlowPanel(QWidget):
    start_requested = Signal(str, dict)  # Flow-Name, Parameter

    def __init__(self, flows: Sequence[tuple[str, str]], parent: QWidget | None = None) -> None:
        """`flows`: (Name, Beschreibung)."""
        super().__init__(parent)
        self.flow_combo = QComboBox()
        for name, description in flows:
            self.flow_combo.addItem(name, name)
            self.flow_combo.setItemData(self.flow_combo.count() - 1, description, Qt.ItemDataRole.ToolTipRole)
        self.params_edit = QLineEdit()
        self.params_edit.setPlaceholderText('Parameter (JSON, optional), z. B. {"anzahl": 3}')
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.request_start)

        self.header = QLabel("Kein Flow aktiv")
        self.header.setObjectName("flowHeader")
        self.timeline = QTreeWidget()
        self.timeline.setObjectName("timeline")
        self.timeline.setHeaderLabels(["", "#", "Schritt", "Dauer"])
        self.timeline.setRootIsDecorated(False)
        self.timeline.setUniformRowHeights(True)
        self.timeline.setAlternatingRowColors(True)
        header = self.timeline.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.flow_combo, 1)
        row.addWidget(self.start_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(row)
        layout.addWidget(self.params_edit)
        layout.addWidget(self.header)
        layout.addWidget(self.timeline, 1)

        self._rows: dict[int, QTreeWidgetItem] = {}
        self._flow: str | None = None
        self._state = BotState.DISCONNECTED
        self._result: FlowFinished | None = None
        self._elapsed = QElapsedTimer()
        self._final_ms: int | None = None
        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._update_header)

    # --- Auswahl und Start -------------------------------------------------------------------------

    def current_flow(self) -> str | None:
        data = self.flow_combo.currentData()
        return data if isinstance(data, str) else None

    def params(self) -> dict[str, Any]:
        """Wirft `ValueError` bei ungültigem JSON."""
        text = self.params_edit.text().strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Parameter sind kein gültiges JSON: {e.msg}") from e
        if not isinstance(value, dict):
            raise ValueError("Parameter müssen ein JSON-Objekt sein")
        return value

    def request_start(self) -> bool:
        name = self.current_flow()
        if name is None:
            return False
        try:
            params = self.params()
        except ValueError as e:
            self.params_edit.setStyleSheet("border: 1px solid #d05050;")
            self.params_edit.setToolTip(str(e))
            return False
        self.params_edit.setStyleSheet("")
        self.params_edit.setToolTip("")
        self.start_requested.emit(name, params)
        return True

    def set_busy(self, busy: bool) -> None:
        self.flow_combo.setEnabled(not busy)
        self.start_button.setEnabled(not busy)

    def set_state(self, state: BotState) -> None:
        self._state = state
        self._update_header()

    # --- Timeline ----------------------------------------------------------------------------------

    @property
    def finished_result(self) -> FlowFinished | None:
        return self._result

    def step_rows(self) -> list[tuple[str, str, str, str]]:
        return [
            (item.text(0), item.text(1), item.text(2), item.text(3))
            for item in (self.timeline.topLevelItem(i) for i in range(self.timeline.topLevelItemCount()))
            if item is not None
        ]

    def handle_event(self, event: object) -> None:
        match event:
            case FlowStarted(flow=name):
                self.timeline.clear()
                self._rows.clear()
                self._flow = name
                self._result = None
                self._final_ms = None
                self._elapsed.start()
                self._tick.start()
            case StepStarted(step=title, index=index):
                item = QTreeWidgetItem([RUNNING_ICON, str(index), title, "…"])
                self._set_bold(item, True)
                self.timeline.addTopLevelItem(item)
                self._rows[index] = item
                self.timeline.scrollToItem(item)
            case StepFinished(step=title, index=index, result=result, duration_s=duration, error=error):
                row = self._rows.get(index)
                if row is None:
                    row = QTreeWidgetItem(["", str(index), title, ""])
                    self.timeline.addTopLevelItem(row)
                    self._rows[index] = row
                row.setText(0, RESULT_ICONS[result])
                row.setText(3, f"{duration:.1f} s")
                row.setForeground(0, QBrush(QColor(RESULT_COLORS[result])))
                self._set_bold(row, False)
                if error:
                    row.setToolTip(2, error)
            case FlowFinished():
                self._tick.stop()
                self._result = event
                self._final_ms = self._elapsed.elapsed() if self._elapsed.isValid() else 0
                for item in self._rows.values():
                    if item.text(0) == RUNNING_ICON:
                        item.setText(0, ABORTED_ICON)
                        item.setText(3, "–")
                        self._set_bold(item, False)
        self._update_header()

    def _update_header(self) -> None:
        if self._flow is None:
            self.header.setText("Kein Flow aktiv")
            return
        if self._final_ms is not None:
            ms = self._final_ms
        else:
            ms = self._elapsed.elapsed() if self._elapsed.isValid() else 0
        seconds = ms // 1000
        if self._result is not None:
            status = "abgebrochen" if self._result.cancelled else self._result.result.name
            if self._result.error:
                status += f": {self._result.error}"
        else:
            status = self._state.name
        self.header.setText(f"{self._flow} · {status} · {seconds // 60:02d}:{seconds % 60:02d}")
        self.header.setToolTip(self.header.text())

    @staticmethod
    def _set_bold(item: QTreeWidgetItem, bold: bool) -> None:
        font = QFont(item.font(2))
        font.setBold(bold)
        item.setFont(2, font)
