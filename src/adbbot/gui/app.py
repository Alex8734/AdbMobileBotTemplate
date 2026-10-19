"""GUI starten: QApplication, Theme, Bridge, Engine, Hauptfenster."""

from __future__ import annotations

import signal
import sys
from collections.abc import Sequence
from importlib import resources

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from adbbot.bot.context import BotContext
from adbbot.bot.engine import BotEngine
from adbbot.bot.registry import Registries
from adbbot.core.types import Size
from adbbot.device.discovery import DeviceInfo, list_devices
from adbbot.gui.bridge import EventBridge
from adbbot.gui.main_window import MainWindow
from adbbot.gui.widgets.device_panel import DeviceLister
from adbbot.utils.paths import resolve
from adbbot.vision.template import TemplateStore

ORGANIZATION = "AdbBotTemplate"


def dark_palette() -> QPalette:
    palette = QPalette()
    window, base, text = QColor(40, 42, 48), QColor(28, 29, 34), QColor(220, 222, 228)
    role = QPalette.ColorRole
    for r, color in (
        (role.Window, window),
        (role.WindowText, text),
        (role.Base, base),
        (role.AlternateBase, QColor(34, 35, 41)),
        (role.ToolTipBase, window),
        (role.ToolTipText, text),
        (role.Text, text),
        (role.Button, window),
        (role.ButtonText, text),
        (role.BrightText, QColor(255, 90, 90)),
        (role.Highlight, QColor(64, 120, 200)),
        (role.HighlightedText, QColor(255, 255, 255)),
        (role.Link, QColor(110, 165, 240)),
        (role.PlaceholderText, QColor(135, 139, 148)),
    ):
        palette.setColor(r, color)
    disabled = QColor(115, 118, 126)
    for r in (role.Text, role.ButtonText, role.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, r, disabled)
    return palette


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(list(argv or sys.argv[:1]))
    app.setOrganizationName(ORGANIZATION)
    app.setApplicationName("AdbBot")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(
        resources.files("adbbot.gui.resources").joinpath("style.qss").read_text(encoding="utf-8")
    )
    return app


def device_lister_for(ctx: BotContext) -> DeviceLister:
    """Echte adb-Geräteliste; beim Fake-Backend nur das Fake-Gerät."""
    device_config = ctx.config.device
    if device_config.backend == "fake":
        return lambda: [DeviceInfo(ctx.device.serial, "device", "FakeDevice")]
    return lambda: list_devices(device_config.adb_host, device_config.adb_port)


def run_gui(ctx: BotContext, registries: Registries, *, argv: Sequence[str] | None = None) -> int:
    app = create_application(argv)
    bridge = EventBridge(ctx.events)
    engine = BotEngine(ctx, registries, idle_frames=True)
    window = MainWindow(
        ctx.config,
        registries,
        engine.submit,
        bridge,
        device_lister=device_lister_for(ctx),
        settings=QSettings(ORGANIZATION, f"AdbBot-{ctx.config.profile}"),
        templates=TemplateStore(
            resolve(ctx.config.vision.templates_dir, ctx.root),
            reference_resolution=Size(*ctx.config.device.reference_resolution),
        ),
    )
    window.show()
    engine.start()
    window.device_panel.refresh()

    # Ctrl-C im Terminal: Fenster sauber schließen. Der Timer gibt Python regelmäßig die Kontrolle,
    # damit das Signal überhaupt verarbeitet wird, während Qt die Event-Loop hält.
    signal.signal(signal.SIGINT, lambda *_: window.close())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(200)

    try:
        return app.exec()
    finally:
        keepalive.stop()
        engine.shutdown()
        bridge.close()
