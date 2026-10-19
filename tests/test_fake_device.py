from pathlib import Path

import numpy as np
import pytest

from adbbot.core.clock import FakeClock
from adbbot.core.exceptions import DeviceError
from adbbot.core.types import Image, Point, Size
from adbbot.device.fake_device import FakeDevice, InputRecord
from adbbot.device.protocol import KEY_BACK, DeviceProtocol
from adbbot.utils.image import save_image


def test_implements_protocol(fake_device: FakeDevice) -> None:
    assert isinstance(fake_device, DeviceProtocol)


def test_sequential_holds_last_screen(fake_device: FakeDevice) -> None:
    shown = [fake_device.current_screen for _ in range(4) if fake_device.screenshot()]
    assert shown == ["shop", "popup", "popup", "popup"]  # current_screen nach dem jeweiligen Screenshot
    assert fake_device.screenshot_count == 4


def test_sequential_loop(screens: dict[str, Image], fake_clock: FakeClock) -> None:
    device = FakeDevice(screens, clock=fake_clock, loop=True)
    device.connect()
    frames = [device.screenshot() for _ in range(4)]
    assert np.array_equal(frames[3].image, screens["home"])


def test_frame_timestamp_comes_from_clock(fake_device: FakeDevice, fake_clock: FakeClock) -> None:
    fake_clock.advance(2.5)
    assert fake_device.screenshot().ts == 102.5


def test_manual_mode_switches_on_input(fake_device: FakeDevice, screens: dict[str, Image]) -> None:
    fake_device.show("home")

    def on_input(device: FakeDevice, record: InputRecord) -> None:
        if record.kind == "tap" and record.args[0] == Point(10, 10):
            device.show("shop")

    fake_device.on_input = on_input
    assert np.array_equal(fake_device.screenshot().image, screens["home"])
    assert np.array_equal(fake_device.screenshot().image, screens["home"])  # manual: kein Weiterschalten
    fake_device.tap(Point(10, 10))
    assert np.array_equal(fake_device.screenshot().image, screens["shop"])


def test_inputs_are_recorded(fake_device: FakeDevice) -> None:
    fake_device.tap(Point(1, 2))
    fake_device.swipe(Point(0, 0), Point(5, 5), 300)
    fake_device.long_press(Point(3, 3), 800)
    fake_device.key(KEY_BACK)
    fake_device.input_text("hallo")
    fake_device.start_app("com.example", ".Main")
    assert fake_device.current_app() == "com.example"
    fake_device.stop_app("com.example")
    assert fake_device.current_app() is None

    assert [r.kind for r in fake_device.inputs] == [
        "tap",
        "swipe",
        "long_press",
        "key",
        "text",
        "start_app",
        "stop_app",
    ]
    assert fake_device.inputs[1].args == (Point(0, 0), Point(5, 5), 300)


def test_disconnected_raises(screens: dict[str, Image]) -> None:
    device = FakeDevice(screens)
    with pytest.raises(DeviceError):
        device.screenshot()
    with pytest.raises(DeviceError):
        device.tap(Point(0, 0))
    assert device.inputs == []


def test_fail_next(fake_device: FakeDevice) -> None:
    fake_device.fail_next(DeviceError("adb weg"))
    with pytest.raises(DeviceError, match="adb weg"):
        fake_device.screenshot()
    fake_device.screenshot()  # nur einmal


def test_screen_size(fake_device: FakeDevice) -> None:
    assert fake_device.screen_size() == Size(40, 60)


def test_unknown_screen(fake_device: FakeDevice) -> None:
    with pytest.raises(KeyError, match="home, shop, popup"):
        fake_device.show("nope")


def test_from_dir(tmp_path: Path, screens: dict[str, Image]) -> None:
    for name, image in screens.items():
        save_image(tmp_path / f"{name}.png", image)
    device = FakeDevice.from_dir(tmp_path, serial="dir")
    assert device.names == ["home", "popup", "shop"]
    assert device.serial == "dir"
    device.connect()
    assert np.array_equal(device.screenshot().image, screens["home"])


def test_from_empty_dir(tmp_path: Path) -> None:
    with pytest.raises(DeviceError):
        FakeDevice.from_dir(tmp_path)


def test_requires_screens() -> None:
    with pytest.raises(ValueError):
        FakeDevice([])
