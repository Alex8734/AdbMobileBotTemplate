import io
import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QSettings, Qt
from pytestqt.qtbot import QtBot

from adbbot.app import build_app
from adbbot.core.commands import Command, ReloadTemplates
from adbbot.core.config import load_config
from adbbot.core.events import EventBus, FrameCaptured
from adbbot.core.logging import create_logger
from adbbot.core.types import Frame, Point, Rect, Size
from adbbot.gui.app import device_lister_for
from adbbot.gui.bridge import EventBridge
from adbbot.gui.main_window import MainWindow
from adbbot.gui.widgets.screen_view import ScreenView
from adbbot.gui.widgets.template_capture import TemplateCaptureDialog
from adbbot.vision.template import TemplateStore
from mybot.bootstrap import register
from tests.imagegen import textured

ROOT = Path(__file__).resolve().parents[2]


def test_screen_view_selection_and_freeze(qtbot: QtBot) -> None:
    view = ScreenView()
    qtbot.addWidget(view)
    view.resize(400, 400)
    view.show()
    qtbot.waitExposed(view)
    view.set_frame(Frame(textured(100, 200, seed=1), 1.0))
    view.fit_to_window()

    regions: list[Rect] = []
    taps: list[Point] = []
    view.region_selected.connect(regions.append)
    view.tap_requested.connect(taps.append)
    view.click_to_tap = True
    view.capture_mode = True

    start = view.mapFromScene(QPointF(10.5, 20.5))
    end = view.mapFromScene(QPointF(60.5, 80.5))
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(view.viewport(), pos=end)
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)

    assert taps == []  # Capture hat Vorrang vor Klick = Tap
    assert len(regions) == 1
    r = regions[0]
    assert abs(r.x - 10) <= 1 and abs(r.y - 20) <= 1
    assert abs(r.right - 60) <= 1 and abs(r.bottom - 80) <= 1

    view.set_frame(Frame(textured(100, 200, seed=2), 2.0))
    assert view.frame_ts == 1.0  # eingefroren, solange Capture aktiv ist
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert len(regions) == 1  # Klick ohne Aufziehen ist keine Auswahl

    view.capture_mode = False
    view.set_frame(Frame(textured(100, 200, seed=2), 3.0))
    assert view.frame_ts == 3.0


def test_dialog_saves_template_variant_and_errors(qtbot: QtBot, tmp_path: Path) -> None:
    store = TemplateStore(tmp_path / "templates", reference_resolution=Size(100, 200))
    image = textured(30, 20, seed=1)

    dialog = TemplateCaptureDialog(image, Rect(10, 20, 30, 20), Size(100, 200), store, ["home", "shop"])
    qtbot.addWidget(dialog)
    saved: list[tuple[str, Path]] = []
    dialog.saved.connect(lambda name, path: saved.append((name, path)))
    assert [dialog.page_combo.itemText(i) for i in range(dialog.page_combo.count())] == ["home", "shop"]

    dialog.page_combo.setCurrentText("home")
    dialog.name_edit.setText("play_button")
    dialog.threshold_check.setChecked(True)
    dialog.threshold_spin.setValue(0.93)
    dialog.region_check.setChecked(True)
    dialog.margin_spin.setValue(5)
    dialog.save_button.click()

    png = tmp_path / "templates" / "home" / "play_button.png"
    assert saved == [("home/play_button", png)]
    assert json.loads(png.with_suffix(".json").read_text(encoding="utf-8")) == {
        "threshold": 0.93,
        "region": [5, 15, 40, 30],
    }
    assert np.array_equal(store.get("home/play_button").variants[0].image, image)

    again = TemplateCaptureDialog(image, Rect(0, 0, 30, 20), Size(100, 200), store, ["home"])
    qtbot.addWidget(again)
    again.page_combo.setCurrentText("home")
    again.name_edit.setText("play_button")
    assert "existiert bereits" in again.target_label.text()
    again.save_button.click()
    assert "existiert bereits" in again.error_label.text()
    again.variant_check.setChecked(True)
    again.save_button.click()
    assert again.saved_path == png.with_name("play_button@2.png")

    invalid = TemplateCaptureDialog(image, Rect(0, 0, 30, 20), Size(100, 200), store, ["home"])
    qtbot.addWidget(invalid)
    invalid.name_edit.setText("Play Button")
    invalid.save_button.click()
    assert "Ungültiger Template-Name" in invalid.error_label.text()
    assert invalid.saved_path is None


def test_main_window_capture_flow(qtbot: QtBot, tmp_path: Path) -> None:
    config = load_config(
        "demo", config_dir=ROOT / "config", environ={}, overrides={"bot": {"persist_state": False}}
    )
    bus = EventBus()
    ctx, registries = build_app(
        config, register, root=ROOT, bus=bus, log=create_logger(bus=bus, stream=io.StringIO())
    )
    bridge = EventBridge(ctx.events, autostart=False)
    submitted: list[Command] = []
    store = TemplateStore(tmp_path / "templates", reference_resolution=Size(540, 960))
    window = MainWindow(
        config,
        registries,
        submitted.append,
        bridge,
        device_lister=device_lister_for(ctx),
        settings=QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat),
        templates=store,
    )
    qtbot.addWidget(window)
    screen = textured(540, 960, seed=5)
    ctx.events.publish(FrameCaptured(Frame(screen, 1.0)))
    bridge.drain()

    assert window.act_template.isEnabled()
    window.act_click_tap.setChecked(True)
    window.act_template.setChecked(True)
    assert not window.act_click_tap.isChecked()
    assert window.screen_view.capture_mode

    window.screen_view.region_selected.emit(Rect(100, 200, 50, 40))
    dialog = window.capture_dialog
    assert dialog is not None
    assert dialog.page_combo.findText("home") >= 0  # registrierte Pages werden angeboten
    dialog.page_combo.setCurrentText("home")
    dialog.name_edit.setText("captured")
    dialog.save_button.click()

    saved = store.get("home/captured").variants[0].image
    assert np.array_equal(saved, screen[200:240, 100:150])
    assert submitted == [ReloadTemplates()]
    assert not window.act_template.isChecked()
    bridge.close()
