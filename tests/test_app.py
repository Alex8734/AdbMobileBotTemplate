"""CLI-Einstieg mit FakeDevice-Backend – ohne Handy."""

from pathlib import Path

import numpy as np
import pytest

from adbbot import app, cli
from adbbot.core.exceptions import DeviceError
from adbbot.device.discovery import DeviceInfo
from adbbot.utils.image import load_image, save_image
from adbbot.utils.paths import ROOT_ENV
from tests.conftest import solid


@pytest.fixture
def project(tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    screens = tmp_path / "screens"
    save_image(screens / "a.png", solid((1, 2, 3)))
    monkeypatch.setenv(ROOT_ENV, str(tmp_path))
    for key in [k for k in __import__("os").environ if k.startswith("ADBBOT__")]:
        monkeypatch.delenv(key)
    return tmp_path


def test_cli_overrides() -> None:
    args = cli.parse_args(
        [
            "--serial",
            "abc",
            "--set",
            "bot.step_timeout_s=5",
            "--set",
            "device.adb_port=5038",
            "--log-level",
            "DEBUG",
            "--headless",
        ]
    )
    assert args.mode == "headless"
    assert cli.cli_overrides(args) == {
        "bot": {"step_timeout_s": 5},
        "device": {"adb_port": 5038, "serial": "abc"},
        "log_level": "DEBUG",
    }


def test_screenshot_with_fake_backend(project: Path) -> None:
    out = project / "out" / "shot.png"
    code = app.main(
        argv=[
            "--profile",
            "test",
            "--backend",
            "fake",
            "--set",
            "device.fake_screens_dir=screens",
            "--screenshot",
            str(out),
        ]
    )
    assert code == 0
    assert np.array_equal(load_image(out), solid((1, 2, 3)))


def test_unknown_profile_returns_error(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert app.main(argv=["--profile", "nope", "--list-devices"]) == 2
    assert "nicht gefunden" in capsys.readouterr().out


def test_fake_backend_without_dir_fails(project: Path) -> None:
    assert app.main(argv=["--profile", "test", "--backend", "fake", "--screenshot", "x.png"]) == 1


def test_list_devices(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        app,
        "list_devices",
        lambda host, port: [
            DeviceInfo("emulator-5554", "device", "sdk_gphone64"),
            DeviceInfo("R58M", "unauthorized"),
        ],
    )
    assert app.main(argv=["--profile", "test", "--list-devices"]) == 0
    out = capsys.readouterr().out
    assert "emulator-5554  device" in out
    assert "sdk_gphone64" in out
    assert "unauthorized" in out


def test_list_devices_without_adb_server(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(host: str, port: int) -> list[DeviceInfo]:
        raise DeviceError("adb-Server nicht erreichbar")

    monkeypatch.setattr(app, "list_devices", fail)
    assert app.main(argv=["--profile", "test", "--list-devices"]) == 1
