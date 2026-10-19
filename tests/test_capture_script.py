import importlib.util
import os
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from adbbot.utils.image import load_image, save_image
from adbbot.utils.paths import ROOT_ENV
from tests.imagegen import textured

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "capture_screenshots.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_screenshots_script", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    profiles = tmp_path / "config" / "profiles"
    profiles.mkdir(parents=True)
    (tmp_path / "config" / "default.yaml").write_text("log_level: INFO\n", encoding="utf-8")
    (profiles / "test.yaml").write_text(
        "app:\n  package: com.example.test\ndevice:\n  backend: fake\n  fake_screens_dir: screens\n",
        encoding="utf-8",
    )
    for i in range(3):
        save_image(tmp_path / "screens" / f"{i}.png", textured(40, 60, seed=i))
    monkeypatch.setenv(ROOT_ENV, str(tmp_path))
    for key in [k for k in os.environ if k.startswith("ADBBOT__")]:
        monkeypatch.delenv(key)
    return tmp_path


def test_fixed_count(script: ModuleType, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = project / "out"
    assert script.main(["--profile", "test", "--count", "5", "--interval", "0", "--out", str(out)]) == 0
    files = sorted(out.glob("*.png"))
    assert [f.name for f in files] == [f"screen_{i:04d}.png" for i in range(1, 6)]
    assert np.array_equal(load_image(files[4]), load_image(project / "screens" / "2.png"))  # letztes bleibt
    assert "5 Screenshot(s)" in capsys.readouterr().out


def test_only_changes_until_duration(script: ModuleType, project: Path) -> None:
    out = project / "changes"
    argv = ["--profile", "test", "--interval", "0", "--on-change", "--duration", "0.3", "--prefix", "shop"]
    assert script.main([*argv, "--out", str(out)]) == 0
    assert len(list(out.glob("shop_*.png"))) == 3


def test_frame_difference(script: ModuleType) -> None:
    a = textured(10, 10, seed=1)
    assert script.frame_difference(a, a.copy()) == 0.0
    assert script.frame_difference(a, textured(10, 12, seed=1)) == 255.0
    assert 0 < script.frame_difference(a, textured(10, 10, seed=2)) < 255


def test_device_error(script: ModuleType, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert script.main(["--profile", "test", "--set", "device.fake_screens_dir=fehlt", "--count", "1"]) == 1
    assert "Fehler" in capsys.readouterr().out
