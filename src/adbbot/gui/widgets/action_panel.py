"""ActionPanel: Buttons aus der ActionRegistry, gruppiert in aufklappbaren Abschnitten."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from adbbot.bot.actions import ActionSpec


class CollapsibleSection(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.header = QToolButton()
        self.header.setObjectName("sectionHeader")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.ArrowType.DownArrow)
        self.header.toggled.connect(self.set_expanded)
        self.content = QWidget()
        self._content_layout = QVBoxLayout(self.content)
        self._content_layout.setContentsMargins(12, 0, 0, 4)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.content)

    @property
    def expanded(self) -> bool:
        return not self.content.isHidden()

    def set_expanded(self, expanded: bool) -> None:
        self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.content.setVisible(expanded)
        if self.header.isChecked() != expanded:
            self.header.setChecked(expanded)

    def add_widget(self, widget: QWidget) -> None:
        self._content_layout.addWidget(widget)


class ActionPanel(QWidget):
    action_requested = Signal(str)  # ActionSpec.name

    def __init__(self, groups: Mapping[str, Sequence[ActionSpec]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sections: dict[str, CollapsibleSection] = {}
        self._buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        if not groups:
            layout.addWidget(QLabel("Keine Actions registriert"))
        for group, specs in groups.items():
            section = CollapsibleSection(group)
            for spec in specs:
                button = QPushButton(spec.title)
                button.setToolTip(spec.description or spec.name)
                button.clicked.connect(
                    lambda _checked=False, name=spec.name: self.action_requested.emit(name)
                )
                section.add_widget(button)
                self._buttons[spec.name] = button
            layout.addWidget(section)
            self.sections[group] = section
        layout.addStretch(1)

    def button(self, name: str) -> QPushButton:
        return self._buttons[name]

    def set_busy(self, busy: bool) -> None:
        for button in self._buttons.values():
            button.setEnabled(not busy)
