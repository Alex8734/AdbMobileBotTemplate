import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from adbbot.bot.storage import Storage


class Now:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def test_values_persist(tmp_path: Path) -> None:
    path = tmp_path / "demo" / "state.json"
    store = Storage(path)
    store.set("runs", 3)
    store.set("names", ["a", "ä"])

    again = Storage(path)
    assert again.get("runs") == 3
    assert again.get("fehlt", "default") == "default"
    assert set(again.keys()) == {"runs", "names"}
    assert "names" in again
    assert json.loads(path.read_text(encoding="utf-8"))["names"] == ["a", "ä"]
    assert not list(path.parent.glob("*.tmp"))

    assert store.delete("runs")
    assert not store.delete("runs")
    assert "runs" not in Storage(path)
    store.clear()
    assert Storage(path).keys() == []


def test_increment(tmp_path: Path) -> None:
    store = Storage(tmp_path / "state.json")
    assert store.increment("visits") == 1
    assert store.increment("visits", by=5) == 6
    store.set("text", "x")
    with pytest.raises(TypeError):
        store.increment("text")


def test_rejects_values_that_are_not_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    with pytest.raises(TypeError, match="JSON"):
        Storage(path).set("x", object())
    assert not path.exists()


def test_recovers_from_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{kaputt", encoding="utf-8")
    store = Storage(path, now=Now(datetime(2026, 9, 13, 12, 0)))
    assert store.keys() == []
    assert store.recovered_from == tmp_path / "state.json.corrupt-20260913-120000"
    assert store.recovered_from.read_text(encoding="utf-8") == "{kaputt"
    store.set("ok", True)
    assert Storage(path).get("ok") is True


def test_time_helpers() -> None:
    now = Now(datetime(2026, 9, 12, 23, 0))
    store = Storage(None, now=now)
    assert store.last("daily") is None
    assert store.since("daily") is None
    assert not store.done_today("daily")

    store.mark_done_today("daily")
    now.value = datetime(2026, 9, 13, 4, 0)
    assert store.since("daily") == timedelta(hours=5)
    assert not store.done_today("daily")  # Mitternacht liegt dazwischen
    assert store.done_today("daily", day_start_hour=5)  # Spieltag beginnt um 5 Uhr
    assert store.done_within("daily", timedelta(hours=6))
    assert not store.done_within("daily", timedelta(hours=4))

    store.set("daily", "kein Datum")
    assert store.last("daily") is None


def test_in_memory_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = Storage.in_memory()
    store.set("a", 1)
    assert store.get("a") == 1
    assert list(tmp_path.iterdir()) == []
