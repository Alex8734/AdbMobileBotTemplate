"""Persistenter Key-Value-Store pro Profil (`data/<profil>/state.json`).

    if not ctx.store.done_today("daily_gift"):
        ...
        ctx.store.mark_done_today("daily_gift")

Werte müssen JSON-serialisierbar sein. Jede Änderung wird sofort atomar geschrieben (temporäre Datei +
rename) – ein Absturz hinterlässt nie eine halbe Datei. Eine kaputte Datei wird beiseitegelegt
(`state.json.corrupt-<zeit>`) und der Store startet leer.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


class Storage:
    def __init__(self, path: Path | None, *, now: Callable[[], datetime] = datetime.now) -> None:
        """`path=None` = nur im Speicher (Tests, `bot.persist_state: false`)."""
        self.path = path
        self.recovered_from: Path | None = None
        self._now = now
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        if path is not None and path.is_file():
            self._load(path)

    @classmethod
    def in_memory(cls) -> Storage:
        return cls(None)

    # --- Werte -------------------------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        try:
            json.dumps(value)
        except TypeError as e:
            raise TypeError(f"Wert für {key!r} ist nicht JSON-serialisierbar: {e}") from e
        with self._lock:
            self._data[key] = value
            self._save()

    def delete(self, key: str) -> bool:
        with self._lock:
            if key not in self._data:
                return False
            del self._data[key]
            self._save()
            return True

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._data)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data

    def increment(self, key: str, by: int = 1) -> int:
        with self._lock:
            current = self._data.get(key, 0)
            if not isinstance(current, int):
                raise TypeError(f"{key!r} ist keine Zahl: {current!r}")
            self.set(key, current + by)
            return current + by

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._save()

    # --- Zeit-Helfer -------------------------------------------------------------------------------

    def mark(self, key: str) -> None:
        """Aktuellen Zeitpunkt unter `key` speichern."""
        self.set(key, self._now().isoformat(timespec="seconds"))

    def last(self, key: str) -> datetime | None:
        raw = self.get(key)
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def since(self, key: str) -> timedelta | None:
        last = self.last(key)
        return None if last is None else self._now() - last

    def done_within(self, key: str, period: timedelta) -> bool:
        elapsed = self.since(key)
        return elapsed is not None and elapsed < period

    def game_day(self, moment: datetime | None = None, *, day_start_hour: int = 0) -> date:
        """Spieltag eines Zeitpunkts – viele Spiele setzen Dailies nicht um Mitternacht zurück."""
        return ((moment or self._now()) - timedelta(hours=day_start_hour)).date()

    def done_today(self, key: str, *, day_start_hour: int = 0) -> bool:
        last = self.last(key)
        return last is not None and self.game_day(last, day_start_hour=day_start_hour) == self.game_day(
            day_start_hour=day_start_hour
        )

    def mark_done_today(self, key: str) -> None:
        self.mark(key)

    # --- Datei -------------------------------------------------------------------------------------

    def _load(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("oberste Ebene ist kein Objekt")
        except (ValueError, UnicodeDecodeError):
            backup = path.with_name(f"{path.name}.corrupt-{self._now():%Y%m%d-%H%M%S}")
            path.replace(backup)
            self.recovered_from = backup
            return
        self._data = data

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
