from pathlib import Path
from typing import Any

import pytest

from adbbot.core.clock import FakeClock
from adbbot.core.exceptions import ConfigError
from adbbot.core.types import Image, Point
from adbbot.device.fake_device import FakeDevice, FakeScenario, InputRecord
from adbbot.device.protocol import KEY_BACK
from adbbot.utils.image import save_image

SCENARIO: dict[str, Any] = {
    "start": "shop",
    "start_app": "home",
    "transitions": {
        "home": [{"tap": [0, 0, 20, 20], "goto": "shop"}],
        "shop": [{"tap": [0, 0, 20, 20], "goto": "popup"}],
        "popup": [{"tap": [0, 0, 20, 20], "goto": "@back"}],
        "*": [{"key": KEY_BACK, "goto": "@back"}],
    },
}


@pytest.fixture
def device(screens: dict[str, Image], fake_clock: FakeClock) -> FakeDevice:
    d = FakeDevice(screens, clock=fake_clock, scenario=FakeScenario.from_dict(SCENARIO))
    d.connect()
    return d


def test_start_screen_and_manual_mode(device: FakeDevice) -> None:
    assert device.current_screen == "shop"
    device.screenshot()
    device.screenshot()
    assert device.current_screen == "shop"


def test_taps_follow_transitions(device: FakeDevice) -> None:
    device.start_app("com.x")
    assert device.current_screen == "home"
    device.tap(Point(30, 30))  # außerhalb
    assert device.current_screen == "home"
    device.tap(Point(5, 5))
    device.tap(Point(5, 5))
    assert device.current_screen == "popup"
    assert device.history == ["home", "shop"]


def test_back_via_tap_and_key(device: FakeDevice) -> None:
    device.start_app("com.x")
    device.tap(Point(1, 1))  # → shop
    device.tap(Point(1, 1))  # → popup
    device.tap(Point(1, 1))  # @back → shop
    assert device.current_screen == "shop"
    device.key(KEY_BACK)  # "*" → @back → home
    assert device.current_screen == "home"
    device.key(KEY_BACK)  # leere History → bleibt
    assert device.current_screen == "home"


def test_start_app_resets_history(device: FakeDevice) -> None:
    device.tap(Point(1, 1))
    assert device.history == ["shop"]
    device.start_app("com.x")
    assert device.history == []
    assert device.current_screen == "home"


def test_on_input_still_called(device: FakeDevice) -> None:
    seen: list[InputRecord] = []
    device.on_input = lambda _device, record: seen.append(record)
    device.tap(Point(1, 1))
    assert [r.kind for r in seen] == ["tap"]


def test_unknown_screens_rejected(screens: dict[str, Image]) -> None:
    scenario = FakeScenario.from_dict(
        {"start": "nope", "transitions": {"home": [{"key": 4, "goto": "gibts_nicht"}]}}
    )
    with pytest.raises(ConfigError, match="gibts_nicht, nope"):
        FakeDevice(screens, scenario=scenario)


@pytest.mark.parametrize(
    "data",
    [
        {"transitions": {"home": [{"goto": "shop"}]}},
        {"transitions": {"home": [{"tap": [0, 0, 1, 1], "key": 4, "goto": "shop"}]}},
        {"transitions": {"home": [{"tap": [0, 0], "goto": "shop"}]}},
        {"starts": "home"},
    ],
)
def test_invalid_scenario(data: dict[str, Any]) -> None:
    with pytest.raises(ConfigError, match="ungültiges Szenario"):
        FakeScenario.from_dict(data)


def test_from_dir_loads_scenario(tmp_path: Path, screens: dict[str, Image]) -> None:
    for name, image in screens.items():
        save_image(tmp_path / f"{name}.png", image)
    (tmp_path / "scenario.yaml").write_text(
        "start: popup\ntransitions:\n  popup:\n    - {tap: [0, 0, 10, 10], goto: home}\n", encoding="utf-8"
    )
    device = FakeDevice.from_dir(tmp_path)
    device.connect()
    assert device.current_screen == "popup"
    device.tap(Point(2, 2))
    assert device.current_screen == "home"

    (tmp_path / "scenario.yaml").write_text("start: [kaputt", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        FakeDevice.from_dir(tmp_path)


def test_transition_switches_app_and_bring_to_front_returns(
    fake_clock: FakeClock, screens: dict[str, Image]
) -> None:
    scenario = FakeScenario.from_dict(
        {
            "start": "home",
            "transitions": {
                "home": [{"tap": [0, 0, 10, 10], "goto": "shop"}],
                "shop": [{"tap": [0, 0, 10, 10], "goto": "popup", "app": "com.android.vending"}],
                "popup": [{"tap": [0, 0, 10, 10], "goto": "home", "activity": "com.vending.Details"}],
            },
        }
    )
    d = FakeDevice(screens, clock=fake_clock, scenario=scenario, current_package="com.game")
    d.connect()
    d.tap(Point(5, 5))
    d.tap(Point(5, 5))  # Werbung → Play Store
    assert (d.current_screen, d.current_app(), d.current_activity()) == ("popup", "com.android.vending", None)
    d.tap(Point(5, 5))  # innerhalb des Stores weiter, App bleibt
    assert (d.current_app(), d.current_activity()) == ("com.android.vending", "com.vending.Details")

    d.bring_to_front("com.game")
    assert (d.current_screen, d.current_app(), d.current_activity()) == ("shop", "com.game", None)
    d.bring_to_front("com.game")  # schon vorne: nichts passiert
    assert d.current_screen == "shop"
    assert [r.kind for r in d.inputs][-2:] == ["bring_to_front", "bring_to_front"]


def test_bring_to_front_starts_app_when_not_running(fake_clock: FakeClock, screens: dict[str, Image]) -> None:
    d = FakeDevice(
        screens, clock=fake_clock, scenario=FakeScenario.from_dict({"start": "shop", "start_app": "home"})
    )
    d.connect()
    d.bring_to_front("com.game")
    assert (d.current_screen, d.current_app()) == ("home", "com.game")
