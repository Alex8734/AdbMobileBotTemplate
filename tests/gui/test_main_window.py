"""Hauptfenster: offline (Commands mitschneiden, Events von Hand) und live mit Engine + Demo-Projekt."""

import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPalette
from pytestqt.qtbot import QtBot

from adbbot.app import build_app
from adbbot.bot.context import BotContext
from adbbot.bot.engine import BotEngine
from adbbot.bot.registry import Registries
from adbbot.core.commands import Command, Pause, Resume, RunAction, StartFlow, TapAt
from adbbot.core.config import load_config
from adbbot.core.events import (
    DebugImageUpdated,
    DetectionsUpdated,
    DeviceStatus,
    EventBus,
    FrameCaptured,
    LogEmitted,
    StateChanged,
)
from adbbot.core.logging import create_logger
from adbbot.core.state import BotState
from adbbot.core.types import Detection, Frame, Point, Rect, Size, StepResult
from adbbot.gui.app import create_application, device_lister_for
from adbbot.gui.bridge import EventBridge
from adbbot.gui.main_window import MainWindow
from mybot.bootstrap import register

ROOT = Path(__file__).resolve().parents[2]


def demo_app() -> tuple[BotContext, Registries]:
    config = load_config(
        "demo",
        config_dir=ROOT / "config",
        environ={},
        overrides={
            "bot": {
                "humanize_input": False,
                "watcher_interval_ms": 0,
                "failure_snapshots": False,
                "persist_state": False,
                "idle_frame_interval_ms": 50,
            },
            "vision": {"poll_interval_ms": 30},
        },
    )
    bus = EventBus()
    log = create_logger(level="DEBUG", bus=bus, stream=io.StringIO())
    return build_app(config, register, root=ROOT, bus=bus, log=log)


@dataclass
class Gui:
    ctx: BotContext
    window: MainWindow
    bridge: EventBridge
    engine: BotEngine | None = None
    submitted: list[Command] = field(default_factory=list)

    def publish(self, *events: object) -> None:
        for event in events:
            self.ctx.events.publish(event)  # type: ignore[arg-type]
        self.bridge.drain()


def settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def offline(qtbot: QtBot, tmp_path: Path) -> Iterator[Gui]:
    ctx, registries = demo_app()
    bridge = EventBridge(ctx.events, autostart=False)
    gui = Gui(ctx, None, bridge)  # type: ignore[arg-type]
    gui.window = MainWindow(
        ctx.config,
        registries,
        gui.submitted.append,
        bridge,
        device_lister=device_lister_for(ctx),
        settings=settings(tmp_path),
    )
    qtbot.addWidget(gui.window)
    yield gui
    bridge.close()


@pytest.fixture
def live(qtbot: QtBot, tmp_path: Path) -> Iterator[Gui]:
    ctx, registries = demo_app()
    bridge = EventBridge(ctx.events, interval_ms=10)
    engine = BotEngine(ctx, registries, idle_frames=True)
    window = MainWindow(
        ctx.config,
        registries,
        engine.submit,
        bridge,
        device_lister=device_lister_for(ctx),
        settings=settings(tmp_path),
    )
    qtbot.addWidget(window)
    window.show()
    engine.start()
    yield Gui(ctx, window, bridge, engine)
    assert engine.shutdown()
    bridge.close()


def test_create_application_applies_theme(qtbot: QtBot) -> None:
    app = create_application()
    # Mit Stylesheet wickelt Qt den Fusion-Style in einen QStyleSheetStyle – daher Palette + QSS prüfen
    window = app.palette().color(QPalette.ColorRole.Window)
    assert (window.red(), window.green(), window.blue()) == (40, 42, 48)
    assert "QToolBar" in app.styleSheet()


def test_controls_follow_state(offline: Gui) -> None:
    w = offline.window
    assert w.state is BotState.DISCONNECTED
    assert not w.act_start.isEnabled()

    offline.publish(StateChanged(BotState.IDLE))
    assert w.act_start.isEnabled()
    assert not w.act_stop.isEnabled()
    assert w.action_panel.button("save_screenshot").isEnabled()

    offline.publish(StateChanged(BotState.RUNNING))
    assert not w.act_start.isEnabled()
    assert w.act_pause.isEnabled()
    assert w.act_stop.isEnabled()
    assert not w.action_panel.button("save_screenshot").isEnabled()
    assert not w.flow_panel.start_button.isEnabled()
    assert w.status.summary().startswith("RUNNING")
    w.act_pause.trigger()

    offline.publish(StateChanged(BotState.PAUSED))
    assert w.act_pause.text() == "Fortsetzen"
    w.act_pause.trigger()
    assert offline.submitted == [Pause(), Resume()]


def test_commands_from_gui(offline: Gui, qtbot: QtBot) -> None:
    w = offline.window
    offline.publish(StateChanged(BotState.IDLE))

    w.flow_panel.params_edit.setText('{"runden": 2}')
    w.act_start.trigger()
    qtbot.mouseClick(w.action_panel.button("test_shop"), Qt.MouseButton.LeftButton)
    # Signal direkt auslösen: testet die Verdrahtung View → TapAt-Command
    w.screen_view.tap_requested.emit(Point(3, 4))
    w.act_click_tap.setChecked(True)
    assert w.screen_view.click_to_tap

    assert offline.submitted == [
        StartFlow("daily", {"runden": 2}),
        RunAction("test_shop"),
        TapAt(Point(3, 4)),
    ]


def test_events_reach_widgets(offline: Gui) -> None:
    w = offline.window
    offline.publish(
        FrameCaptured(Frame(np.zeros((960, 540, 3), dtype=np.uint8), 5.0)),
        DetectionsUpdated(5.0, [Detection(Rect(10, 10, 50, 20), "home/logo", "found", 5.0, confidence=0.99)]),
        LogEmitted(0.0, "info", "flow", "Hallo aus dem Bot"),
        DeviceStatus(True, "fake", Size(540, 960)),
    )
    assert w.screen_view.frame_size == Size(540, 960)
    assert len(w.screen_view.overlay_items) == 1
    assert "Hallo aus dem Bot" in w.debug_panel.text()
    assert "fake" in w.device_label.text()
    assert "Frame 540 × 960" in w.view_info.text()


def test_debug_views_can_be_selected(offline: Gui) -> None:
    w = offline.window
    live = Frame(np.zeros((960, 540, 3), dtype=np.uint8), 5.0)
    gray = Frame(np.full((960, 540, 3), 128, dtype=np.uint8), 5.0)
    heatmap = Frame(np.full((10, 20, 3), 255, dtype=np.uint8), -1.0)
    offline.publish(
        FrameCaptured(live), DebugImageUpdated("grau", gray), DebugImageUpdated("heatmap", heatmap)
    )
    assert w.view_names() == ["Livebild", "grau", "heatmap"]
    assert w.current_view is None
    assert w.screen_view.frame is live

    assert w.select_view("grau")
    assert w.screen_view.frame is gray
    newer = Frame(live.image, 6.0)
    offline.publish(FrameCaptured(newer))
    assert w.screen_view.frame is gray  # neue Livebilder überschreiben die gewählte Ansicht nicht
    w.screen_view.tap_requested.emit(Point(1, 2))  # gleiche Größe wie das Livebild → Tap erlaubt

    assert w.select_view("heatmap")
    assert w.screen_view.frame_size == Size(20, 10)
    w.screen_view.tap_requested.emit(Point(3, 4))  # andere Größe → Koordinaten passen nicht, kein Tap
    assert offline.submitted == [TapAt(Point(1, 2))]
    assert not w.select_view("gibt es nicht")

    w.act_template.setChecked(True)  # Template-Aufnahme immer aus dem Livebild
    assert w.current_view is None
    assert w.screen_view.frame is newer
    assert not w.view_combo.isEnabled()


def test_selected_view_is_restored_when_bot_sends_it(offline: Gui, tmp_path: Path, qtbot: QtBot) -> None:
    image = Frame(np.zeros((4, 4, 3), dtype=np.uint8), -1.0)
    offline.publish(DebugImageUpdated("grau", image))
    assert offline.window.select_view("grau")
    offline.window.close()

    ctx, registries = demo_app()
    bridge = EventBridge(ctx.events, autostart=False)
    again = MainWindow(
        ctx.config,
        registries,
        lambda _command: None,
        bridge,
        device_lister=device_lister_for(ctx),
        settings=settings(tmp_path),
    )
    qtbot.addWidget(again)
    assert again.current_view is None  # die Ansicht gibt es erst, wenn der Bot sie schickt
    ctx.events.publish(DebugImageUpdated("grau", image))
    bridge.drain()
    assert again.current_view == "grau"
    bridge.close()


def test_layout_is_saved_and_restored(offline: Gui, tmp_path: Path, qtbot: QtBot) -> None:
    offline.window.act_hold.setChecked(True)
    offline.window.close()
    stored = settings(tmp_path)
    assert stored.value("main/state") is not None

    ctx, registries = demo_app()
    again = MainWindow(
        ctx.config,
        registries,
        lambda _command: None,
        offline.bridge,
        device_lister=device_lister_for(ctx),
        settings=settings(tmp_path),
    )
    qtbot.addWidget(again)
    assert again.act_hold.isChecked()
    assert again.screen_view.hold_overlays


def test_demo_flow_runs_from_gui(live: Gui, qtbot: QtBot) -> None:
    w = live.window
    qtbot.waitUntil(lambda: w.state is BotState.IDLE, timeout=5000)
    qtbot.waitUntil(lambda: w.screen_view.frame_size == Size(540, 960), timeout=5000)
    assert "verbunden" in w.device_panel.status_label.text()

    w.act_start.trigger()
    qtbot.waitUntil(lambda: w.flow_panel.finished_result is not None, timeout=15000)
    result = w.flow_panel.finished_result
    assert result is not None
    assert result.result is StepResult.OK, result.error

    rows = w.flow_panel.step_rows()
    assert [row[2] for row in rows] == [
        "App im Vordergrund sicherstellen",
        "Zur Startseite navigieren",
        "Gold lesen",
        "Tagesgeschenk abholen",
        "Shop besuchen",
    ]
    assert {row[0] for row in rows} <= {"✔", "↷"}
    qtbot.waitUntil(lambda: w.state is BotState.IDLE, timeout=5000)
    assert w.act_start.isEnabled()
    assert "Watcher ausgelöst" in w.debug_panel.text()
    assert "Watcher 1" in w.status.summary()
