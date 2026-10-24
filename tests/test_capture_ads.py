import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from adbbot.core.clock import FakeClock
from adbbot.device.fake_device import FakeDevice
from tests.imagegen import textured

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "capture_ads.py"
GAME, GAME_ACTIVITY = "com.example.game", "com.unity3d.player.UnityPlayerActivity"
AD_ACTIVITY = "com.google.android.gms.ads.AdActivity"

UI_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node text="" content-desc="Interstitial close button" resource-id="" clickable="true"
        bounds="[980,40][1060,120]">
    <node text="Install" content-desc="" resource-id="com.ad:id/cta" clickable="true"
          bounds="[100,2000][980,2150]"/>
    <node text="Skip" content-desc="" resource-id="" clickable="false" bounds="[900,60][1000,100]"/>
  </node>
</hierarchy>"""


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_ads_script", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # Dataclasses brauchen ihr Modul in sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def device(fake_clock: FakeClock) -> FakeDevice:
    d = FakeDevice(
        {
            "game": textured(108, 192, seed=1),
            "video": textured(108, 192, seed=2),
            "end": textured(108, 192, seed=3),
        },
        clock=fake_clock,
        mode="manual",
        current_package=GAME,
        current_activity=GAME_ACTIVITY,
    )
    d.connect()
    return d


def test_classify(script: ModuleType) -> None:
    def kind(package: str | None, activity: str | None, game: set[str] | None = None) -> str:
        known = {GAME_ACTIVITY} if game is None else game
        result: str = script.classify(
            package, activity, game_package=GAME, game_activities=known, allowlist=["com.google.android.gms"]
        )
        return result

    assert kind(GAME, GAME_ACTIVITY) == "game"
    assert kind(GAME, AD_ACTIVITY) == "ad"
    assert kind(GAME, "com.applovin.adview.AppLovinFullscreenActivity") == "ad"
    assert kind(GAME, "com.example.game.SomeNewSdkActivity") == "ad"  # unbekannte Activity im Spiel
    assert kind(GAME, "com.example.game.SomeNewSdkActivity", set()) == "game"  # nichts gelernt: Spiel
    assert kind("com.android.vending", "com.google.android.finsky.activities.MainActivity") == "foreign"
    assert kind("com.google.android.gms", "com.google.android.gms.auth.Login") == "game"
    assert kind(None, None) == "none"


def test_close_candidates(script: ModuleType) -> None:
    found = script.close_candidates(UI_XML)
    assert [(c["desc"] or c["text"], c["rect"], c["clickable"]) for c in found] == [
        ("Interstitial close button", [980, 40, 80, 80], True),
        ("Skip", [900, 60, 100, 40], False),
    ]
    assert script.close_candidates("kein xml") == []


def test_records_ad_session(
    script: ModuleType, device: FakeDevice, fake_clock: FakeClock, tmp_path: Path
) -> None:
    logs: list[str] = []
    rec = script.AdRecorder(
        device, fake_clock, tmp_path, game_package=GAME, dump_interval_s=5.0, log=logs.append
    )
    assert rec.poll() is None
    assert rec.game_activities == {GAME_ACTIVITY}  # beim Start gelernt
    assert rec.session is None

    device.activity, device.ui_xml = AD_ACTIVITY, UI_XML
    device.show("video")
    for _ in range(3):  # gleiches Bild: nur einmal speichern, nur ein UI-Dump
        rec.poll()
        fake_clock.advance(1.0)
    device.show("end")
    rec.poll()  # Endcard
    fake_clock.advance(1.0)
    device.current_package, device.activity = "com.android.vending", "com.google.android.finsky.Main"
    rec.poll()  # versehentlich in den Store
    device.current_package, device.activity = GAME, GAME_ACTIVITY
    device.show("game")
    assert rec.poll() is None  # einmal Spiel reicht noch nicht
    session = rec.poll()

    assert session is not None
    assert rec.session is None
    assert session.activities == [AD_ACTIVITY, "com.google.android.finsky.Main"]
    assert session.packages == [GAME, "com.android.vending"]
    assert [f["file"] for f in session.frames] == ["0001_0000.00s.png", "0002_0003.00s.png"]
    data = json.loads((session.directory / "session.json").read_text(encoding="utf-8"))
    assert data["duration_s"] == 4.0
    assert data["screen_size"] == [108, 192]
    assert {c["t"] for c in data["close_candidates"]} == {0.0, 4.0}  # Start + neue Activity (Store)
    assert len(list(session.directory.glob("ui_*.xml"))) == 2
    assert any("Werbung erkannt" in line for line in logs)


def test_finish_saves_running_session(
    script: ModuleType, device: FakeDevice, fake_clock: FakeClock, tmp_path: Path
) -> None:
    rec = script.AdRecorder(
        device, fake_clock, tmp_path, game_package=GAME, dump_ui=False, log=lambda _: None
    )
    device.activity = AD_ACTIVITY
    rec.poll()
    session = rec.finish()
    assert session is not None
    assert (session.directory / "session.json").is_file()
    assert rec.finish() is None


def test_main_without_ads(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from adbbot.utils.image import save_image
    from adbbot.utils.paths import ROOT_ENV

    (tmp_path / "config" / "profiles").mkdir(parents=True)
    (tmp_path / "config" / "default.yaml").write_text("log_level: INFO\n", encoding="utf-8")
    (tmp_path / "config" / "profiles" / "test.yaml").write_text(
        "app:\n  package: com.example.test\ndevice:\n  backend: fake\n  fake_screens_dir: screens\n",
        encoding="utf-8",
    )
    save_image(tmp_path / "screens" / "0.png", textured(40, 60, seed=1))
    monkeypatch.setenv(ROOT_ENV, str(tmp_path))
    argv = ["--profile", "test", "--interval", "0.05", "--duration", "0.2", "--out", str(tmp_path / "ads")]
    assert script.main(argv, labeler=lambda _session: None) == 0
    assert "0 Werbung(en) aufgezeichnet" in capsys.readouterr().out
