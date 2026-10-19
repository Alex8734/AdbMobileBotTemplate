"""Hauptfenster: ScreenView in der Mitte, Panels als verschiebbare Docks, Toolbar, Menü, Statusleiste.

Das Fenster kennt den Bot nur über die EventBridge (Events rein) und `submit` (Commands raus).
Kein direkter Gerätezugriff. Layout und Fenstergröße werden in QSettings gespeichert.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QStyle,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from adbbot.bot.registry import Registries
from adbbot.core.commands import (
    CaptureFrame,
    Command,
    ConnectDevice,
    Pause,
    ReloadTemplates,
    Resume,
    RunAction,
    StartFlow,
    Stop,
    TapAt,
)
from adbbot.core.config import AppConfig
from adbbot.core.events import DebugImageUpdated, DeviceStatus, FlowFinished, StateChanged
from adbbot.core.state import BotState
from adbbot.core.types import Frame, Point, Rect
from adbbot.gui.bridge import EventBridge
from adbbot.gui.widgets.action_panel import ActionPanel
from adbbot.gui.widgets.debug_panel import DebugPanel
from adbbot.gui.widgets.device_panel import DeviceLister, DevicePanel
from adbbot.gui.widgets.flow_panel import FlowPanel
from adbbot.gui.widgets.screen_view import ScreenView
from adbbot.gui.widgets.status_bar import BotStatusBar
from adbbot.gui.widgets.template_capture import TemplateCaptureDialog
from adbbot.vision.template import TemplateStore

SP = QStyle.StandardPixmap
LIVE_VIEW = "Livebild"


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: AppConfig,
        registries: Registries,
        submit: Callable[[Command], None],
        bridge: EventBridge,
        *,
        device_lister: DeviceLister,
        settings: QSettings | None = None,
        templates: TemplateStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """`templates`: Ziel für die Template-Aufnahme (ohne Store ist die Aufnahme deaktiviert)."""
        super().__init__(parent)
        self.config = config
        self._submit = submit
        self._bridge = bridge
        self._settings = settings if settings is not None else QSettings()
        self._templates = templates
        self._page_names = registries.pages.names()
        self.capture_dialog: TemplateCaptureDialog | None = None
        self._state = BotState.DISCONNECTED
        self._cursor: Point | None = None
        self._live_frame: Frame | None = None
        self._debug_images: dict[str, Frame] = {}  # neuestes Zwischenbild pro Ansicht
        self._wanted_view: str | None = None  # gespeicherte Auswahl, bis der Bot diese Ansicht schickt
        self.setObjectName("MainWindow")
        self.setWindowTitle(f"AdbBot – {config.profile} · {config.app.package}")
        self.resize(1400, 900)
        self.setDockOptions(
            QMainWindow.DockOption.AnimatedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AllowNestedDocks
        )

        # Zentrum: Ansicht-Auswahl (Livebild oder Zwischenbild aus dem Bot) + Bild
        self.screen_view = ScreenView()
        self.view_combo = QComboBox()
        self.view_combo.setObjectName("viewCombo")
        self.view_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.view_combo.setToolTip("Livebild oder ein Zwischenbild aus dem Bot (vision.debug_image)")
        self.view_combo.addItem(LIVE_VIEW, None)
        view_bar = QHBoxLayout()
        view_bar.setContentsMargins(6, 3, 6, 3)
        view_bar.addWidget(QLabel("Ansicht"))
        view_bar.addWidget(self.view_combo)
        view_bar.addStretch(1)
        self.view_info = QLabel()
        self.view_info.setObjectName("viewInfo")
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addLayout(view_bar)
        central_layout.addWidget(self.screen_view, 1)
        central_layout.addWidget(self.view_info)
        self.setCentralWidget(central)

        # Panels als Docks
        self.debug_panel = DebugPanel(max_lines=config.gui.max_log_lines)
        self.flow_panel = FlowPanel([(cls.name, cls.description) for cls in registries.flows.classes()])
        self.action_panel = ActionPanel(registries.actions.groups())
        self.device_panel = DevicePanel(config.device.backend, device_lister)
        self.status = BotStatusBar()
        self.setStatusBar(self.status)

        self.docks: dict[str, QDockWidget] = {}
        for key, title, widget in (
            ("log", "Debug-Log", self.debug_panel),
            ("flow", "Flow", self.flow_panel),
            ("actions", "Actions", self.action_panel),
            ("device", "Gerät", self.device_panel),
        ):
            dock = QDockWidget(title, self)
            dock.setObjectName(f"dock_{key}")
            dock.setWidget(widget)
            self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
            self.docks[key] = dock
        self.tabifyDockWidget(self.docks["actions"], self.docks["device"])
        self.docks["actions"].raise_()

        self._create_actions()
        self._create_toolbar()
        self._create_menus()
        self._connect_signals()

        self._default_layout = self.saveState()
        self._restore_settings()
        self.act_click_tap.setChecked(config.gui.click_to_tap)
        self._apply_state(BotState.DISCONNECTED)
        self._update_view_info()

    @property
    def state(self) -> BotState:
        return self._state

    # --- Aufbau ------------------------------------------------------------------------------------

    def _icon(self, pixmap: QStyle.StandardPixmap) -> QIcon:
        return self.style().standardIcon(pixmap)

    def _action(
        self,
        text: str,
        slot: Callable[[], object] | None = None,
        *,
        icon: QStyle.StandardPixmap | None = None,
        shortcut: str | None = None,
        tip: str | None = None,
        checkable: bool = False,
    ) -> QAction:
        action = QAction(text, self)
        if icon is not None:
            action.setIcon(self._icon(icon))
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        action.setCheckable(checkable)
        if slot is not None:
            action.triggered.connect(lambda _checked=False: slot())
        return action

    def _create_actions(self) -> None:
        self.act_connect = self._action(
            "Verbinden", self._connect_device, icon=SP.SP_BrowserReload, tip="Gerät (neu) verbinden"
        )
        self.act_start = self._action(
            "Start", self.flow_panel.request_start, icon=SP.SP_MediaPlay, shortcut="F5", tip="Flow starten"
        )
        self.act_pause = self._action("Pause", self._toggle_pause, icon=SP.SP_MediaPause, shortcut="F6")
        self.act_stop = self._action(
            "Stop", lambda: self._submit(Stop()), icon=SP.SP_MediaStop, shortcut="Shift+F5"
        )
        self.act_capture = self._action(
            "Frame",
            lambda: self._submit(CaptureFrame()),
            icon=SP.SP_DialogOpenButton,
            tip="Frischen Screenshot holen",
        )
        self.act_template = self._action(
            "Template aufnehmen",
            icon=SP.SP_FileDialogNewFolder,
            checkable=True,
            tip="Livebild einfrieren, Bereich aufziehen und als Template speichern",
        )
        self.act_template.setEnabled(self._templates is not None)
        self.act_template.toggled.connect(self._set_capture_mode)
        self.act_click_tap = self._action(
            "Klick = Tap", checkable=True, tip="Klick ins Livebild tippt an dieser Stelle aufs Gerät"
        )
        self.act_click_tap.toggled.connect(self._set_click_to_tap)
        self.act_hold = self._action(
            "Overlays halten", checkable=True, tip="Overlays älterer Frames weiter zeigen"
        )
        self.act_hold.toggled.connect(self._set_hold_overlays)
        self.act_fit = self._action("Einpassen", self.screen_view.fit_to_window, shortcut="Ctrl+0")
        self.act_reload = self._action(
            "Templates neu laden", lambda: self._submit(ReloadTemplates()), shortcut="Ctrl+R"
        )
        self.act_reset_layout = self._action("Layout zurücksetzen", self._reset_layout)
        self.act_quit = self._action("Beenden", self.close, shortcut="Ctrl+Q")

    def _create_toolbar(self) -> None:
        bar = QToolBar("Steuerung", self)
        bar.setObjectName("mainToolbar")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        bar.addAction(self.act_connect)
        bar.addSeparator()
        for action in (self.act_start, self.act_pause, self.act_stop):
            bar.addAction(action)
        bar.addSeparator()
        for action in (self.act_capture, self.act_template, self.act_click_tap, self.act_hold, self.act_fit):
            bar.addAction(action)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        self.device_label = QLabel()
        bar.addWidget(self.device_label)
        self.addToolBar(bar)
        self._on_device_status(DeviceStatus(connected=False, serial=None, size=None))

    def _create_menus(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("&Datei")
        file_menu.addAction(self.act_quit)
        bot_menu = menu.addMenu("&Bot")
        for action in (self.act_connect, self.act_start, self.act_pause, self.act_stop):
            bot_menu.addAction(action)
        bot_menu.addSeparator()
        bot_menu.addAction(self.act_capture)
        bot_menu.addAction(self.act_reload)
        view_menu = menu.addMenu("&Ansicht")
        for action in (self.act_fit, self.act_hold, self.act_click_tap):
            view_menu.addAction(action)
        view_menu.addSeparator()
        for dock in self.docks.values():
            view_menu.addAction(dock.toggleViewAction())
        view_menu.addSeparator()
        view_menu.addAction(self.act_reset_layout)

    def _connect_signals(self) -> None:
        bridge = self._bridge
        bridge.frame_ready.connect(self._on_frame)
        bridge.debug_image_ready.connect(self._on_debug_image)
        bridge.detections_ready.connect(self.screen_view.set_detections)
        bridge.log_ready.connect(self.debug_panel.append)
        bridge.state_changed.connect(self._on_state_changed)
        bridge.flow_event.connect(self._on_flow_event)
        bridge.device_status.connect(self._on_device_status)
        bridge.watcher_triggered.connect(lambda _event: self.status.watcher_triggered())
        bridge.registries_changed.connect(
            lambda _event: self.status.showMessage("Templates neu geladen", 3000)
        )

        self.screen_view.cursor_moved.connect(self._on_cursor_moved)
        self.screen_view.zoom_changed.connect(lambda _zoom: self._update_view_info())
        self.screen_view.tap_requested.connect(self._on_tap_requested)
        self.view_combo.currentIndexChanged.connect(self._on_view_selected)
        self.screen_view.region_selected.connect(self._on_region_selected)
        self.flow_panel.start_requested.connect(lambda name, params: self._submit(StartFlow(name, params)))
        self.action_panel.action_requested.connect(lambda name: self._submit(RunAction(name)))
        self.device_panel.connect_requested.connect(lambda serial: self._submit(ConnectDevice(serial)))

    # --- Reaktionen --------------------------------------------------------------------------------

    def _on_frame(self, frame: Frame) -> None:
        self._live_frame = frame
        self.status.frame_received()
        if self.current_view is None:
            self.screen_view.set_frame(frame)
            self._update_view_info()

    # --- Ansichten ---------------------------------------------------------------------------------

    @property
    def current_view(self) -> str | None:
        """Name des angezeigten Zwischenbilds, `None` = Livebild."""
        data = self.view_combo.currentData()
        return data if isinstance(data, str) else None

    def view_names(self) -> list[str]:
        return [self.view_combo.itemText(i) for i in range(self.view_combo.count())]

    def select_view(self, name: str | None) -> bool:
        """Ansicht wählen (`None` = Livebild). `False`, wenn der Bot sie noch nicht geschickt hat."""
        index = 0 if name is None else self.view_combo.findData(name)
        if index < 0:
            return False
        self.view_combo.setCurrentIndex(index)
        return True

    def _on_debug_image(self, event: DebugImageUpdated) -> None:
        is_new = event.name not in self._debug_images
        self._debug_images[event.name] = event.frame
        if is_new:
            self.view_combo.addItem(event.name, event.name)
        if self.current_view == event.name:
            self.screen_view.set_frame(event.frame)
            self._update_view_info()
        elif (
            event.name == self._wanted_view
            and self.current_view is None
            and not self.screen_view.capture_mode
        ):
            self.select_view(event.name)

    def _on_view_selected(self, _index: int) -> None:
        name = self.current_view
        self._wanted_view = name
        frame = self._live_frame if name is None else self._debug_images.get(name)
        if frame is not None:
            self.screen_view.set_frame(frame)
        self._update_view_info()

    def _on_tap_requested(self, point: Point) -> None:
        live = self._live_frame
        if self.current_view is not None and (live is None or live.size != self.screen_view.frame_size):
            self.status.showMessage(
                "Klick = Tap: Ansicht ist nicht so groß wie das Livebild – Livebild wählen", 5000
            )
            return
        self._submit(TapAt(point))

    def _on_state_changed(self, event: StateChanged) -> None:
        self._apply_state(event.state)

    def _on_flow_event(self, event: object) -> None:
        self.flow_panel.handle_event(event)
        if isinstance(event, FlowFinished) and event.error:
            self.status.showMessage(f"{event.flow}: {event.error}", 8000)

    def _on_device_status(self, status: DeviceStatus) -> None:
        self.device_panel.set_status(status)
        if status.connected:
            self.device_label.setText(f"<b style='color:#a3d977'>●</b> {status.serial or ''}")
        else:
            self.device_label.setText("<b style='color:#ff6b6b'>●</b> getrennt")
        self.device_label.setToolTip(status.error or "")

    def _on_cursor_moved(self, point: Point | None) -> None:
        self._cursor = point
        self._update_view_info()

    def _update_view_info(self) -> None:
        size = self.screen_view.frame_size
        parts = [f"Zoom {self.screen_view.zoom:.0%}"]
        parts.append(f"Cursor ({self._cursor.x}, {self._cursor.y})" if self._cursor else "Cursor –")
        if size is not None:
            parts.append(f"Frame {size.w} × {size.h}")
        self.view_info.setText("   ".join(parts))

    def _apply_state(self, state: BotState) -> None:
        self._state = state
        idle = state is BotState.IDLE
        paused = state is BotState.PAUSED
        self.act_start.setEnabled(idle)
        self.act_pause.setEnabled(state in (BotState.RUNNING, BotState.PAUSED))
        self.act_pause.setText("Fortsetzen" if paused else "Pause")
        self.act_pause.setIcon(self._icon(SP.SP_MediaPlay if paused else SP.SP_MediaPause))
        self.act_stop.setEnabled(state in (BotState.RUNNING, BotState.PAUSED))
        self.act_capture.setEnabled(idle)
        self.act_connect.setEnabled(state in (BotState.IDLE, BotState.DISCONNECTED))
        self.flow_panel.set_busy(not idle)
        self.flow_panel.set_state(state)
        self.action_panel.set_busy(not idle)
        self.status.set_state(state)

    def _toggle_pause(self) -> None:
        self._submit(Resume() if self._state is BotState.PAUSED else Pause())

    def _connect_device(self) -> None:
        self._submit(ConnectDevice(self.device_panel.selected_serial()))

    def _set_click_to_tap(self, enabled: bool) -> None:
        if enabled:
            self.act_template.setChecked(False)
        self.screen_view.click_to_tap = enabled

    def _set_capture_mode(self, enabled: bool) -> None:
        if enabled:
            self.act_click_tap.setChecked(False)
            self.select_view(None)  # Templates immer aus dem echten Livebild
            self.status.showMessage("Template aufnehmen: Rechteck im eingefrorenen Livebild aufziehen", 6000)
        self.view_combo.setEnabled(not enabled)
        self.screen_view.capture_mode = enabled

    def _on_region_selected(self, rect: Rect) -> None:
        frame = self.screen_view.frame
        if frame is None or self._templates is None:
            return
        crop = frame.image[rect.y : rect.bottom, rect.x : rect.right].copy()
        dialog = TemplateCaptureDialog(crop, rect, frame.size, self._templates, self._page_names, self)
        dialog.saved.connect(self._on_template_saved)
        dialog.finished.connect(lambda _result: self.screen_view.clear_selection())
        self.capture_dialog = dialog
        dialog.open()

    def _on_template_saved(self, name: str, path: object) -> None:
        self._submit(ReloadTemplates())
        self.status.showMessage(f"Template {name} gespeichert: {path}", 8000)
        self.act_template.setChecked(False)

    def _set_hold_overlays(self, enabled: bool) -> None:
        self.screen_view.hold_overlays = enabled

    # --- Layout speichern ----------------------------------------------------------------------------

    def _reset_layout(self) -> None:
        self.restoreState(self._default_layout)
        for dock in self.docks.values():
            dock.show()

    def _restore_settings(self) -> None:
        geometry = self._settings.value("main/geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        state = self._settings.value("main/state")
        if isinstance(state, QByteArray):
            self.restoreState(state)
        self.act_hold.setChecked(self._settings.value("view/hold_overlays", False) in (True, "true"))
        view = self._settings.value("view/screen", "")
        self._wanted_view = view if isinstance(view, str) and view else None

    def save_settings(self) -> None:
        self._settings.setValue("main/geometry", self.saveGeometry())
        self._settings.setValue("main/state", self.saveState())
        self._settings.setValue("view/hold_overlays", self.act_hold.isChecked())
        self._settings.setValue("view/screen", self._wanted_view or "")
        self._settings.sync()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.save_settings()
        super().closeEvent(event)
