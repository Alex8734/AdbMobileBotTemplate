"""DevicePanel: Geräteliste, Verbinden, Verbindungsstatus.

Die Geräteliste kommt vom adb-Server (kein Gerätezugriff) und wird in einem Hilfsthread geladen,
damit ein langsames adb die GUI nicht blockiert. Verbinden läuft als `ConnectDevice`-Command.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from adbbot.core.events import DeviceStatus
from adbbot.device.discovery import DeviceInfo

DeviceLister = Callable[[], list[DeviceInfo]]


class DevicePanel(QWidget):
    connect_requested = Signal(object)  # Serial | None
    devices_loaded = Signal(object)  # list[DeviceInfo] | Exception – intern, aus dem Hilfsthread

    def __init__(self, backend: str, lister: DeviceLister, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lister = lister
        self.status_label = QLabel()
        self.info_label = QLabel(f"Backend: {backend}")
        self.info_label.setWordWrap(True)
        self.device_list = QListWidget()
        self.refresh_button = QPushButton("Aktualisieren")
        self.connect_button = QPushButton("Verbinden")
        self.refresh_button.clicked.connect(self.refresh)
        self.connect_button.clicked.connect(lambda: self.connect_requested.emit(self.selected_serial()))
        self.devices_loaded.connect(self._show_devices)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.connect_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.status_label)
        layout.addWidget(self.info_label)
        layout.addWidget(self.device_list, 1)
        layout.addLayout(buttons)

        self._backend = backend
        self.set_status(DeviceStatus(connected=False, serial=None, size=None))

    def refresh(self) -> None:
        self.refresh_button.setEnabled(False)
        threading.Thread(target=self._load, name="device-list", daemon=True).start()

    def selected_serial(self) -> str | None:
        item = self.device_list.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return data if isinstance(data, str) else None

    def set_status(self, status: DeviceStatus) -> None:
        if status.connected:
            self.status_label.setText(f"<b style='color:#a3d977'>● verbunden</b> {status.serial or ''}")
        else:
            self.status_label.setText("<b style='color:#ff6b6b'>● getrennt</b>")
        details = [f"Backend: {self._backend}"]
        if status.size is not None:
            details.append(f"Auflösung: {status.size.w} × {status.size.h}")
        if status.error:
            details.append(f"Fehler: {status.error}")
        if status.retry_in_s:
            details.append(f"nächster Versuch in {status.retry_in_s:.1f} s")
        self.info_label.setText(" · ".join(details))

    def _load(self) -> None:
        try:
            result: list[DeviceInfo] | Exception = self._lister()
        except Exception as e:
            result = e
        self.devices_loaded.emit(result)

    def _show_devices(self, result: object) -> None:
        self.refresh_button.setEnabled(True)
        self.device_list.clear()
        if isinstance(result, Exception):
            self.device_list.addItem(f"Fehler: {result}")
            return
        if not isinstance(result, list) or not result:
            self.device_list.addItem("Keine Geräte gefunden")
            return
        for device in result:
            item = QListWidgetItem(f"{device.serial}  ·  {device.state}  ·  {device.model or '?'}")
            item.setData(Qt.ItemDataRole.UserRole, device.serial)
            self.device_list.addItem(item)
        self.device_list.setCurrentRow(0)
