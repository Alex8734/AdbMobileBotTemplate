import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from adbbot.core.types import Point
from adbbot.utils.image import load_image, save_image
from tests.imagegen import paste, textured

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "test_template.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("test_template_script_module", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def files(tmp_path: Path) -> Path:
    button = textured(60, 40, seed=7)
    save_image(tmp_path / "templates" / "home" / "button.png", button)
    save_image(tmp_path / "shot.png", paste(textured(360, 640, seed=1), button, Point(100, 300)))
    return tmp_path


def run(script: ModuleType, files: Path, *extra: str) -> int:
    code: int = script.main(
        [
            str(files / "shot.png"),
            *extra,
            "--templates-dir",
            str(files / "templates"),
            "--reference",
            "360x640",
        ]
    )
    return code


def test_found_writes_result_image(
    script: ModuleType, files: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(script, files, "home/button") == 0
    out = capsys.readouterr().out
    assert "gefunden Rect(x=100, y=300, w=60, h=40)" in out
    result = files / "shot__home_button.png"
    assert load_image(result).shape == (640, 360, 3)


def test_all_and_region(script: ModuleType, files: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(script, files, "home/button", "--all", "--out", str(files / "r.png")) == 0
    assert "Treffer    : 1" in capsys.readouterr().out
    assert run(script, files, "home/button", "--region", "0,0,360,200") == 1


def test_errors(script: ModuleType, files: Path) -> None:
    assert run(script, files, "home/nope") == 2
    code: int = script.main([str(files / "missing.png"), "home/button"])
    assert code == 2
