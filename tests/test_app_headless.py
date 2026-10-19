"""Ende-zu-Ende: Beispielprojekt `mybot` mit Profil `demo` gegen das Fake-Gerät."""

import os
from pathlib import Path

import pytest

from adbbot import app
from adbbot.utils.paths import ROOT_ENV
from mybot.bootstrap import register

ROOT = Path(__file__).resolve().parents[1]
FAST = [
    "--set",
    "bot.humanize_input=false",
    "--set",
    "vision.poll_interval_ms=30",
    "--set",
    "bot.watcher_interval_ms=0",
    "--set",
    "bot.persist_state=false",
]


@pytest.fixture(autouse=True)
def project_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ROOT_ENV, str(ROOT))
    for key in [k for k in os.environ if k.startswith("ADBBOT__")]:
        monkeypatch.delenv(key)


def test_demo_daily_flow(capsys: pytest.CaptureFixture[str]) -> None:
    code = app.main(
        register,
        ["--headless", "--profile", "demo", "--flow", "daily", "--set", "bot.failure_snapshots=false", *FAST],
    )
    output = capsys.readouterr().err
    assert code == app.EXIT_OK, output
    for expected in [
        "Tagesgeschenk abholen",
        "Watcher ausgelöst",
        "Bewertungs-Popup",
        "Shop besuchen",
        "result=OK",
    ]:
        assert expected in output


def test_demo_shop_flow_uses_navigator(capsys: pytest.CaptureFixture[str]) -> None:
    code = app.main(
        register,
        ["--headless", "--profile", "demo", "--flow", "shop", "--set", "bot.failure_snapshots=false", *FAST],
    )
    output = capsys.readouterr().err
    assert code == app.EXIT_OK, output
    for expected in ["Übergang", "nach=shop", "nach=home", "besuche=1"]:
        assert expected in output


@pytest.mark.parametrize(
    "argv",
    [
        ["--headless", "--profile", "demo"],
        ["--headless", "--profile", "demo", "--flow", "gibts_nicht"],
        ["--headless", "--profile", "demo", "--flow", "daily", "--params", "[1, 2]"],
    ],
)
def test_usage_errors(argv: list[str]) -> None:
    assert app.main(register, argv) == app.EXIT_USAGE
