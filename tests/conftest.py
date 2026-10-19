from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from adbbot.core.clock import FakeClock
from adbbot.core.events import EventBus
from adbbot.core.types import Image
from adbbot.device.fake_device import FakeDevice


def solid(color: tuple[int, int, int], w: int = 40, h: int = 60) -> Image:
    image = np.zeros((h, w, 3), dtype=np.uint8)
    image[:, :] = color
    return image


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(start=100.0)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def screens() -> dict[str, Image]:
    return {"home": solid((255, 0, 0)), "shop": solid((0, 255, 0)), "popup": solid((0, 0, 255))}


@pytest.fixture
def fake_device(screens: dict[str, Image], fake_clock: FakeClock) -> FakeDevice:
    device = FakeDevice(screens, clock=fake_clock)
    device.connect()
    return device


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Minimales config/-Verzeichnis mit default.yaml und Profil `test`."""
    root = tmp_path / "config"
    (root / "profiles").mkdir(parents=True)
    (root / "default.yaml").write_text(
        "log_level: INFO\n"
        "device:\n  command_timeout_s: 10\n"
        "bot:\n  step_timeout_s: 30\n  default_retries: 1\n",
        encoding="utf-8",
    )
    (root / "profiles" / "test.yaml").write_text(
        "app:\n  package: com.example.test\nbot:\n  step_timeout_s: 45\n", encoding="utf-8"
    )
    return root
