import numpy as np
from PySide6.QtCore import QPointF, Qt
from pytestqt.qtbot import QtBot

from adbbot.bot.actions import ActionSpec
from adbbot.core.events import (
    DetectionsUpdated,
    DeviceStatus,
    FlowFinished,
    FlowStarted,
    StepFinished,
    StepStarted,
)
from adbbot.core.state import BotState
from adbbot.core.types import Detection, Frame, Point, Rect, Size, StepResult
from adbbot.device.discovery import DeviceInfo
from adbbot.gui.widgets.action_panel import ActionPanel
from adbbot.gui.widgets.device_panel import DevicePanel
from adbbot.gui.widgets.flow_panel import FlowPanel
from adbbot.gui.widgets.overlay_item import caption
from adbbot.gui.widgets.screen_view import ScreenView, frame_to_qimage
from adbbot.gui.widgets.status_bar import BotStatusBar


def frame(ts: float, w: int = 100, h: int = 200) -> Frame:
    image = np.zeros((h, w, 3), dtype=np.uint8)
    image[:, :, 2] = 255  # BGR: rot
    return Frame(image, ts)


def detections(ts: float) -> DetectionsUpdated:
    return DetectionsUpdated(
        ts,
        [
            Detection(Rect(10, 10, 30, 20), "home/a", "found", ts, confidence=0.93),
            Detection(Rect(40, 50, 30, 20), "home/b", "missing", ts, confidence=0.41),
        ],
    )


def test_frame_to_qimage_converts_bgr() -> None:
    image = frame_to_qimage(frame(1.0).image)
    assert (image.width(), image.height()) == (100, 200)
    color = image.pixelColor(5, 5)
    assert (color.red(), color.green(), color.blue()) == (255, 0, 0)


def test_screen_view_overlays_follow_frame(qtbot: QtBot) -> None:
    view = ScreenView()
    qtbot.addWidget(view)
    view.set_frame(frame(1.0))
    assert view.frame_size == Size(100, 200)

    view.set_detections(detections(1.0))
    assert [item.detection.label for item in view.overlay_items] == ["home/a", "home/b"]
    assert view.overlay_items[1].label.text == "home/b ✗ 0.41"

    view.set_frame(frame(2.0))  # neuer Frame → alte Overlays verschwinden
    assert view.overlay_items == []
    view.hold_overlays = True
    assert len(view.overlay_items) == 2
    view.hold_overlays = False
    view.set_detections(detections(2.0))
    assert len(view.overlay_items) == 2


def test_screen_view_zoom_and_click_to_tap(qtbot: QtBot) -> None:
    view = ScreenView()
    qtbot.addWidget(view)
    view.resize(400, 400)
    view.show()
    qtbot.waitExposed(view)
    view.set_frame(frame(1.0))
    view.fit_to_window()
    assert 1.0 < view.zoom < 2.0  # 200 px hoch in ~400 px

    view.set_zoom(3.0)
    assert view.zoom == 3.0
    view.fit_to_window()

    taps: list[Point] = []
    view.tap_requested.connect(taps.append)
    target = view.mapFromScene(QPointF(50.5, 100.5))
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=target)
    assert taps == []  # ohne Klick = Tap passiert nichts

    view.click_to_tap = True
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=target)
    assert len(taps) == 1
    assert abs(taps[0].x - 50) <= 1
    assert abs(taps[0].y - 100) <= 1


def test_caption() -> None:
    assert (
        caption(Detection(Rect(0, 0, 1, 1), "text", "text", 0.0, confidence=0.8, text="1,250"))
        == "1,250 0.80"
    )
    assert caption(Detection(Rect(0, 0, 1, 1), "x", "mark", 0.0)) == "x"


def test_flow_panel_timeline(qtbot: QtBot) -> None:
    panel = FlowPanel([("daily", "Tägliches"), ("farm", "")])
    qtbot.addWidget(panel)
    assert panel.current_flow() == "daily"
    panel.set_state(BotState.RUNNING)

    for event in (
        FlowStarted("daily"),
        StepStarted("daily", "Zur Startseite", 1),
        StepFinished("daily", "Zur Startseite", 1, StepResult.OK, 1.24),
        StepStarted("daily", "Geschenk", 2),
        StepFinished("daily", "Geschenk", 2, StepResult.SKIPPED, 0.1),
        StepStarted("daily", "Shop", 3),
    ):
        panel.handle_event(event)
    assert panel.step_rows() == [
        ("✔", "1", "Zur Startseite", "1.2 s"),
        ("↷", "2", "Geschenk", "0.1 s"),
        ("▶", "3", "Shop", "…"),
    ]
    assert panel.header.text().startswith("daily · RUNNING")

    panel.handle_event(FlowFinished("daily", StepResult.FAILED, error="ElementNotFound: x"))
    assert panel.step_rows()[-1][0] == "■"
    assert "FAILED: ElementNotFound: x" in panel.header.text()
    assert panel.finished_result is not None

    panel.handle_event(FlowStarted("farm"))
    assert panel.step_rows() == []
    assert panel.finished_result is None


def test_flow_panel_start_and_params(qtbot: QtBot) -> None:
    panel = FlowPanel([("daily", "")])
    qtbot.addWidget(panel)
    requests: list[tuple[str, dict[str, object]]] = []
    panel.start_requested.connect(lambda name, params: requests.append((name, params)))

    panel.params_edit.setText('{"anzahl": 3}')
    qtbot.mouseClick(panel.start_button, Qt.MouseButton.LeftButton)
    panel.params_edit.setText("[1, 2]")
    assert not panel.request_start()
    panel.params_edit.setText("{kaputt")
    assert not panel.request_start()
    assert "JSON" in panel.params_edit.toolTip()
    assert requests == [("daily", {"anzahl": 3})]

    panel.set_busy(True)
    assert not panel.start_button.isEnabled()


def test_action_panel(qtbot: QtBot) -> None:
    def screenshot(ctx: object) -> None: ...

    def shop(ctx: object) -> None: ...

    groups = {
        "Debug": [ActionSpec("Screenshot", screenshot, "Debug", "Bild speichern")],
        "Test": [ActionSpec("Shop", shop, "Test", shortcut="F8")],
    }
    panel = ActionPanel(groups)  # type: ignore[arg-type]
    qtbot.addWidget(panel)
    requested: list[str] = []
    panel.action_requested.connect(requested.append)

    qtbot.mouseClick(panel.button("shop"), Qt.MouseButton.LeftButton)
    qtbot.mouseClick(panel.button("screenshot"), Qt.MouseButton.LeftButton)
    assert requested == ["shop", "screenshot"]
    assert panel.button("screenshot").toolTip() == "Bild speichern"
    assert panel.button("shop").toolTip() == "shop [F8]"
    assert panel.button("shop").text() == "Shop  (F8)"
    assert [a.shortcut().toString() for a in panel.shortcut_actions] == ["F8"]
    panel.shortcut_actions[0].trigger()
    assert requested[-1] == "shop"

    section = panel.sections["Debug"]
    section.set_expanded(False)
    assert not section.expanded
    assert not section.header.isChecked()

    panel.set_busy(True)
    assert not panel.button("screenshot").isEnabled()
    assert not panel.button("shop").isEnabled()
    assert not panel.shortcut_actions[0].isEnabled()


def test_device_panel(qtbot: QtBot) -> None:
    devices = [DeviceInfo("emulator-5554", "device", "sdk_gphone64"), DeviceInfo("R58M", "unauthorized")]
    panel = DevicePanel("adb", lambda: devices)
    qtbot.addWidget(panel)
    requested: list[str | None] = []
    panel.connect_requested.connect(requested.append)

    panel.refresh()
    qtbot.waitUntil(lambda: panel.device_list.count() == 2)
    assert panel.selected_serial() == "emulator-5554"
    qtbot.mouseClick(panel.connect_button, Qt.MouseButton.LeftButton)
    assert requested == ["emulator-5554"]

    panel.set_status(DeviceStatus(True, "emulator-5554", Size(1080, 1920)))
    assert "verbunden" in panel.status_label.text()
    assert "1080 × 1920" in panel.info_label.text()

    def broken() -> list[DeviceInfo]:
        raise RuntimeError("adb weg")

    failing = DevicePanel("adb", broken)
    qtbot.addWidget(failing)
    failing.refresh()
    qtbot.waitUntil(lambda: failing.device_list.count() == 1)
    item = failing.device_list.item(0)
    assert item is not None
    assert "adb weg" in item.text()


def test_status_bar(qtbot: QtBot) -> None:
    bar = BotStatusBar()
    qtbot.addWidget(bar)
    assert bar.summary().startswith("DISCONNECTED")
    bar.set_state(BotState.RUNNING)
    for _ in range(5):
        bar.frame_received()
        qtbot.wait(20)
    bar.watcher_triggered()
    bar.refresh()
    summary = bar.summary()
    assert summary.startswith("RUNNING")
    assert "Laufzeit 00:00:00" in summary
    assert "Watcher 1" in summary
    assert bar.fps() > 5
    bar.set_state(BotState.IDLE)
    assert "Laufzeit –" in bar.summary()
