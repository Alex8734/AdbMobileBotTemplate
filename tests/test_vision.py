from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from adbbot.core.clock import FakeClock
from adbbot.core.events import DebugImageUpdated, DetectionsUpdated, EventBus, FrameCaptured
from adbbot.core.exceptions import BotStopped, ElementNotFound, OcrUnavailable, StepTimeout
from adbbot.core.types import Frame, Image, Point, Rect, Size
from adbbot.device.fake_device import FakeDevice
from adbbot.utils.image import save_image
from adbbot.vision.ocr import OcrResult
from adbbot.vision.template import TemplateStore
from adbbot.vision.vision import Vision
from tests.imagegen import paste, solid_patch, textured


@dataclass
class World:
    vision: Vision
    device: FakeDevice
    clock: FakeClock
    screens: dict[str, Image]
    frames: list[Frame] = field(default_factory=list)


@pytest.fixture
def world(tmp_path: Path, fake_clock: FakeClock, bus: EventBus) -> World:
    background = textured(360, 640, seed=1)
    button = textured(60, 40, seed=7)
    popup = textured(50, 30, seed=8)
    save_image(tmp_path / "home" / "button.png", button)
    save_image(tmp_path / "common" / "popup.png", popup)

    screens = {
        "a_empty": background,
        "b_empty": background,
        "c_button": paste(background, button, Point(100, 300)),
        "d_both": paste(paste(background, button, Point(100, 300)), popup, Point(200, 50)),
    }
    device = FakeDevice(screens, clock=fake_clock)
    device.connect()
    store = TemplateStore(tmp_path, reference_resolution=Size(360, 640))
    vision = Vision(device, store, clock=fake_clock, bus=bus, poll_interval_s=0.3)
    return World(vision, device, fake_clock, screens)


def test_debug_image_is_published_as_bgr_copy(world: World, bus: EventBus) -> None:
    images: list[DebugImageUpdated] = []
    bus.subscribe(DebugImageUpdated, images.append)
    frame = world.vision.capture()
    gray = frame.image[:, :, 0].copy()

    world.vision.debug_image("grau", gray)
    world.vision.debug_image("heatmap", np.array([[0.0, 0.5], [1.0, np.nan]], dtype=np.float32))
    world.vision.debug_image("maske", np.array([[True, False]]))
    gray[:] = 0  # der Bot arbeitet weiter auf seinem Array – die GUI hat eine Kopie

    grau, heatmap, maske = images
    assert grau.name == "grau"
    assert grau.frame.ts == frame.ts  # gleiche Größe wie der Frame → dessen Overlays passen
    assert grau.frame.image.shape == (640, 360, 3)
    assert np.array_equal(grau.frame.image[:, :, 2], frame.image[:, :, 0])
    assert heatmap.frame.ts == -1.0
    assert heatmap.frame.image[:, :, 0].tolist() == [[0, 128], [255, 0]]
    assert maske.frame.image[:, :, 1].tolist() == [[255, 0]]

    with pytest.raises(ValueError, match="Kein darstellbares Bild"):
        world.vision.debug_image("kaputt", np.zeros((4, 4, 2), dtype=np.uint8))


def test_capture_publishes_and_resets_overlay(world: World, bus: EventBus) -> None:
    frames: list[FrameCaptured] = []
    bus.subscribe(FrameCaptured, frames.append)
    world.vision.overlay.mark(Rect(0, 0, 1, 1), "alt", frame_ts=0.0)

    frame = world.vision.capture()
    assert frames == [FrameCaptured(frame)]
    assert world.vision.last_frame is frame
    assert world.vision.overlay.detections == []


def test_frame_rule(world: World) -> None:
    world.device.show("c_button")
    frame = world.vision.capture()
    assert world.vision.find("home/button", frame=frame) is not None
    assert world.vision.exists("common/popup", frame=frame) is False
    assert world.device.screenshot_count == 1  # frame= → kein neuer Screenshot

    assert world.vision.find("home/button") is not None
    assert world.device.screenshot_count == 2


def test_find_overlays(world: World, bus: EventBus) -> None:
    events: list[DetectionsUpdated] = []
    bus.subscribe(DetectionsUpdated, events.append)
    world.device.show("c_button")
    frame = world.vision.capture()

    match = world.vision.find("home/button", frame=frame)
    assert match is not None
    world.vision.find("common/popup", frame=frame)
    world.vision.find("common/popup", frame=frame, region=Rect(0, 0, 200, 100))

    kinds = [(d.kind, d.label) for d in world.vision.overlay.detections]
    assert kinds == [("found", "home/button"), ("missing", "common/popup"), ("missing", "common/popup")]
    assert world.vision.overlay.detections[2].rect == Rect(0, 0, 200, 100)
    assert events[-1].frame_ts == frame.ts


def test_wait_for_polls_until_found(world: World) -> None:
    polled: list[Frame] = []
    world.vision.poll_hook = polled.append

    match = world.vision.wait_for("home/button", timeout=10)

    assert match.rect == Rect(100, 300, 60, 40)
    assert len(polled) == 3  # a_empty, b_empty, c_button
    assert world.clock.sleeps == [pytest.approx(0.3), pytest.approx(0.3)]


def test_wait_for_timeout(world: World) -> None:
    world.device.show("a_empty")
    start = world.clock.monotonic()
    with pytest.raises(ElementNotFound, match="home/button"):
        world.vision.wait_for("home/button", timeout=1.0)
    assert world.clock.monotonic() == pytest.approx(start + 1.0)
    assert world.device.screenshot_count == 5  # 0.0, 0.3, 0.6, 0.9, 1.0


def test_wait_with_zero_timeout_checks_once(world: World) -> None:
    world.device.show("a_empty")
    with pytest.raises(ElementNotFound):
        world.vision.wait_for("home/button", timeout=0)
    assert world.device.screenshot_count == 1


def test_stop_during_wait(world: World) -> None:
    world.device.show("a_empty")
    calls = 0

    def hook(_: Frame) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BotStopped

    world.vision.poll_hook = hook
    with pytest.raises(BotStopped):
        world.vision.wait_for("home/button", timeout=30)
    assert world.device.screenshot_count == 2


def test_wait_for_any_uses_order(world: World) -> None:
    world.device.show("d_both")
    assert world.vision.wait_for_any(["common/popup", "home/button"], timeout=1).label == "common/popup"
    assert world.vision.wait_for_any(["home/button", "common/popup"], timeout=1).label == "home/button"


def test_wait_until_gone(world: World) -> None:
    world.device.show("c_button")
    with pytest.raises(StepTimeout, match="noch sichtbar"):
        world.vision.wait_until_gone("home/button", timeout=0.5)

    world.device.on_input = lambda device, record: device.show("a_empty")
    world.device.tap(Point(0, 0))
    world.vision.wait_until_gone("home/button", timeout=0.5)


def test_find_all_and_find_any(world: World) -> None:
    world.device.show("d_both")
    frame = world.vision.capture()
    assert len(world.vision.find_all("home/button", frame=frame)) == 1
    match = world.vision.find_any(["home/button", "common/popup"], frame=frame)
    assert match is not None
    assert match.label == "home/button"
    assert world.vision.find_all("common/popup", frame=frame, region=Rect(0, 400, 360, 240)) == []


@dataclass
class FakeOcr:
    results: list[OcrResult]
    images: list[Image] = field(default_factory=list)

    def read(self, image: Image) -> list[OcrResult]:
        self.images.append(image)
        return self.results


def test_read_text_and_number(world: World) -> None:
    ocr = FakeOcr(
        [OcrResult("Gold", Rect(0, 0, 30, 20), 0.95), OcrResult("12,345", Rect(40, 0, 50, 20), 0.8)]
    )
    world.vision.ocr = ocr
    frame = world.vision.capture()

    assert world.vision.read_text(Rect(10, 20, 120, 30), frame=frame) == "Gold 12,345"
    assert ocr.images[0].shape == (30, 120, 3)
    detection = world.vision.overlay.detections[-1]
    assert (detection.kind, detection.rect, detection.text) == ("text", Rect(10, 20, 120, 30), "Gold 12,345")
    assert detection.confidence == 0.8

    assert world.vision.read_number(Rect(10, 20, 120, 30), frame=frame) == 12345
    assert world.vision.read_text(Rect(10, 20, 120, 30), frame=frame, min_confidence=0.9) == "Gold"
    with pytest.raises(ValueError):
        world.vision.read_text(Rect(1000, 1000, 10, 10), frame=frame)


def test_read_text_without_engine(world: World) -> None:
    with pytest.raises(OcrUnavailable):
        world.vision.read_text(Rect(0, 0, 10, 10))


def test_colors(world: World) -> None:
    screen = paste(textured(360, 640, seed=1), solid_patch(40, 40, (0, 0, 255)), Point(20, 20))
    frame = Frame(screen, ts=world.clock.monotonic())
    assert world.vision.pixel(Point(30, 30), frame=frame) == (255, 0, 0)
    assert world.vision.color_matches(Point(30, 30), (250, 0, 0), frame=frame)
    assert not world.vision.color_matches(Point(30, 30), (0, 0, 255), frame=frame)
    assert world.vision.color_ratio(Rect(20, 20, 80, 40), (255, 0, 0), frame=frame) == pytest.approx(0.5)
    assert [d.kind for d in world.vision.overlay.detections] == ["found", "missing", "mark"]


def test_reference_coordinates_are_scaled(tmp_path: Path, fake_clock: FakeClock) -> None:
    """Gerät 360x640, Referenz 720x1280: Regionen und Punkte werden halbiert, Templates verkleinert."""
    reference_screen = textured(720, 1280, seed=3, cell=16)
    save_image(tmp_path / "home" / "icon.png", reference_screen[600:680, 200:320])
    small = reference_screen[::2, ::2].copy()
    device = FakeDevice([small], clock=fake_clock)
    device.connect()
    vision = Vision(device, TemplateStore(tmp_path, reference_resolution=Size(720, 1280)), clock=fake_clock)

    frame = vision.capture()
    assert vision.scale == 0.5
    match = vision.find("home/icon", frame=frame, region=Rect(150, 550, 250, 200))
    assert match is not None
    assert abs(match.rect.x - 100) <= 1
    assert vision.find("home/icon", frame=frame, region=Rect(0, 0, 720, 400)) is None
    assert vision.pixel(Point(200, 600), frame=frame) == vision.pixel(Point(201, 601), frame=frame)
    assert vision.to_device(Rect(200, 600, 120, 80)) == Rect(100, 300, 60, 40)
