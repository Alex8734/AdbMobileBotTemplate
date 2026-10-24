#!/usr/bin/env python
"""Werbung aufzeichnen: läuft nebenher, während du spielst oder der Bot läuft.

    uv run python scripts/capture_ads.py --profile example
    uv run python scripts/capture_ads.py --profile example --kind reward     # nur Bonus-Werbung ansehen

Erkennt Werbung an der Activity (Werbe-SDKs laufen in eigenen Activities) und an fremden Apps
(Play Store/Browser nach einem Tap auf die Werbung). Pro Werbung entsteht ein Ordner unter data/ads/ mit:

- Screenshots bei jeder sichtbaren Änderung (Video → X erscheint → Endcard)
- `session.json`: Activities, Packages, Zeitstempel je Frame, Art (Bonus/zufällig) und Schließen-Kandidaten
  aus `uiautomator dump` (Elemente mit „close“, „skip“, „×“ ... samt Position)

Nach jeder Werbung fragt das Skript nach der Art (abschaltbar mit --kind). Ende mit Ctrl-C.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np

from adbbot.core.clock import Clock, RealClock
from adbbot.core.config import deep_merge, load_config, parse_assignment
from adbbot.core.exceptions import BotError
from adbbot.core.types import Frame, Image
from adbbot.device.discovery import connect_with_retry, create_device
from adbbot.device.protocol import DeviceProtocol
from adbbot.utils import paths

# Activities bekannter Werbe-SDKs (Teilstrings, ohne Groß-/Kleinschreibung)
AD_ACTIVITY_PATTERNS = (
    "com.google.android.gms.ads",  # AdMob
    "com.unity3d.services.ads",
    "com.unity3d.ads",
    "com.applovin",
    "com.ironsource",
    "com.vungle",
    "com.liftoff",
    "com.mbridge",  # Mintegral
    "com.mintegral",
    "com.bytedance.sdk.openadsdk",  # Pangle
    "com.facebook.ads",
    "com.chartboost",
    "com.inmobi",
    "com.fyber",
    "com.adcolony",
    "com.digitalturbine",
    "com.moloco",
    "com.bigossp",
    "com.yandex.mobile.ads",
    "adactivity",
    "interstitial",
    "rewarded",
    "fullscreenad",
)

CLOSE_HINT = re.compile(r"close|skip|dismiss|schlie|überspring|×|✕|^x$", re.IGNORECASE)
BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

type Kind = Literal["game", "ad", "foreign", "none"]
type Labeler = Callable[["AdSession"], str | None]

KIND_LABELS = {"r": "reward", "i": "interstitial"}


def frame_difference(a: Image, b: Image) -> float:
    """Mittlere absolute Pixeldifferenz (0–255). Unterschiedliche Größen zählen als maximal verschieden."""
    if a.shape != b.shape:
        return 255.0
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def is_ad_activity(activity: str | None) -> bool:
    if not activity:
        return False
    lowered = activity.lower()
    return any(pattern in lowered for pattern in AD_ACTIVITY_PATTERNS)


def classify(
    package: str | None,
    activity: str | None,
    *,
    game_package: str,
    game_activities: set[str],
    allowlist: Sequence[str] = (),
) -> Kind:
    """`none`: keine fokussierte App (Sperrbildschirm) – zählt weder als Spiel noch als Werbung."""
    if package is None:
        return "none"
    if is_ad_activity(activity):
        return "ad"
    if package != game_package:
        return "game" if package in allowlist else "foreign"
    # Unbekannte Activity im Spiel-Package: vermutlich ein Werbe-SDK, das hier noch nicht gelistet ist
    if game_activities and activity is not None and activity not in game_activities:
        return "ad"
    return "game"


def close_candidates(xml: str) -> list[dict[str, Any]]:
    """Elemente aus einem `uiautomator dump`, die nach Schließen/Überspringen aussehen."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    found: list[dict[str, Any]] = []
    for node in root.iter("node"):
        text, desc, res_id = node.get("text", ""), node.get("content-desc", ""), node.get("resource-id", "")
        if not any(CLOSE_HINT.search(value) for value in (text, desc, res_id.rsplit("/", 1)[-1]) if value):
            continue
        m = BOUNDS.fullmatch(node.get("bounds", ""))
        if m is None:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        found.append(
            {
                "text": text,
                "desc": desc,
                "id": res_id,
                "clickable": node.get("clickable") == "true",
                "rect": [x1, y1, x2 - x1, y2 - y1],
            }
        )
    return found


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:60] or "unbekannt"


@dataclass
class AdSession:
    directory: Path
    started_at: str
    t0: float
    activities: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    frames: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    screen_size: tuple[int, int] | None = None
    duration_s: float = 0.0
    kind: str | None = None
    previous: Image | None = field(default=None, repr=False)
    last_dump_at: float | None = None
    last_dump_activity: str | None = None

    def note(self, package: str, activity: str | None) -> None:
        if package not in self.packages:
            self.packages.append(package)
        if activity and activity not in self.activities:
            self.activities.append(activity)

    def to_json(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 2),
            "kind": self.kind,
            "screen_size": self.screen_size,
            "activities": self.activities,
            "packages": self.packages,
            "frames": self.frames,
            "close_candidates": self.candidates,
        }

    def save(self) -> Path:
        path = self.directory / "session.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


class AdRecorder:
    """Ein `poll()` pro Intervall: Vordergrund prüfen, Sitzung starten/fortsetzen/beenden."""

    def __init__(
        self,
        device: DeviceProtocol,
        clock: Clock,
        out_root: Path,
        *,
        game_package: str,
        game_activities: Sequence[str] = (),
        allowlist: Sequence[str] = (),
        min_diff: float = 2.0,
        end_after: int = 2,
        dump_ui: bool = True,
        dump_interval_s: float = 5.0,
        log: Callable[[str], None] = print,
    ) -> None:
        self.device = device
        self.clock = clock
        self.out_root = out_root
        self.game_package = game_package
        self.game_activities = set(game_activities)
        self.allowlist = tuple(allowlist)
        self.min_diff = min_diff
        self.end_after = end_after
        self.dump_ui = dump_ui
        self.dump_interval_s = dump_interval_s
        self.log = log
        self.session: AdSession | None = None
        self._game_streak = 0

    def poll(self) -> AdSession | None:
        """Die beendete Sitzung, sobald wieder `end_after`-mal hintereinander das Spiel vorne ist."""
        package = self.device.current_app()
        activity = self.device.current_activity()
        if package == self.game_package and activity and not self.game_activities and self.session is None:
            self.game_activities.add(activity)
            self.log(f"Spiel-Activity: {activity}")
        kind = classify(
            package,
            activity,
            game_package=self.game_package,
            game_activities=self.game_activities,
            allowlist=self.allowlist,
        )
        if kind == "none":
            return None
        if kind == "game":
            if self.session is None:
                return None
            self._game_streak += 1
            if self._game_streak >= self.end_after:
                return self.finish()
            return None

        assert package is not None
        self._game_streak = 0
        if self.session is None:
            self._start(package, activity, kind)
        session = self.session
        assert session is not None
        if activity and activity not in session.activities:
            self.log(f"  {kind}: {package} / {activity}")
        session.note(package, activity)
        self._capture(session, package, activity)
        if self.dump_ui:
            self._dump(session, activity)
        return None

    def finish(self) -> AdSession | None:
        """Laufende Sitzung abschließen und speichern (auch bei Ctrl-C)."""
        session, self.session = self.session, None
        self._game_streak = 0
        if session is None:
            return None
        session.duration_s = self.clock.monotonic() - session.t0
        session.save()
        self.log(f"Werbung beendet nach {session.duration_s:.1f} s – {len(session.frames)} Bild(er)")
        return session

    def _start(self, package: str, activity: str | None, kind: Kind) -> None:
        stamp = datetime.now()
        name = f"{stamp:%Y%m%d-%H%M%S}_{_slug((activity or package).rsplit('.', 1)[-1])}"
        self.session = AdSession(
            self.out_root / name, stamp.isoformat(timespec="seconds"), self.clock.monotonic()
        )
        self.log(f"Werbung erkannt ({kind}): {package} / {activity or '-'} → {self.session.directory}")

    def _capture(self, session: AdSession, package: str, activity: str | None) -> None:
        frame: Frame = self.device.screenshot()
        session.screen_size = (frame.size.w, frame.size.h)
        if session.previous is not None and frame_difference(session.previous, frame.image) < self.min_diff:
            return
        t = round(self.clock.monotonic() - session.t0, 2)
        name = f"{len(session.frames) + 1:04d}_{t:07.2f}s.png"
        frame.save(session.directory / name)
        session.previous = frame.image
        session.frames.append({"file": name, "t": t, "package": package, "activity": activity})

    def _dump(self, session: AdSession, activity: str | None) -> None:
        now = self.clock.monotonic()
        due = session.last_dump_at is None or now - session.last_dump_at >= self.dump_interval_s
        if not due and activity == session.last_dump_activity:
            return
        session.last_dump_at, session.last_dump_activity = now, activity
        xml = self.device.dump_ui()
        if xml is None:
            return
        t = round(now - session.t0, 2)
        (session.directory / f"ui_{t:07.2f}s.xml").write_text(xml, encoding="utf-8")
        for candidate in close_candidates(xml):
            session.candidates.append({"t": t, "activity": activity, **candidate})


def ask_kind(session: AdSession) -> str | None:
    answer = input("Art der Werbung? [r] Bonus  [i] zufällig  [Enter] unbekannt: ").strip().lower()
    return KIND_LABELS.get(answer[:1])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", default="example")
    p.add_argument("--out", type=Path, help="Zielordner (Default: data/ads)")
    p.add_argument(
        "--interval", type=float, default=0.5, help="Sekunden zwischen zwei Prüfungen (Default 0.5)"
    )
    p.add_argument(
        "--min-diff", type=float, default=2.0, help="Bild nur speichern ab dieser Änderung (0–255)"
    )
    p.add_argument(
        "--kind",
        choices=["ask", "reward", "interstitial", "none"],
        default="ask",
        help="Art jeder Werbung: nachfragen (Default), fest setzen oder offen lassen",
    )
    p.add_argument("--game-activity", action="append", default=[], help="weitere Activity des Spiels")
    p.add_argument("--no-ui-dump", action="store_true", help="kein uiautomator dump (schneller)")
    p.add_argument("--duration", type=float, help="nach so vielen Sekunden aufhören")
    p.add_argument("--serial")
    p.add_argument("--backend", choices=["adb", "subprocess", "fake"])
    p.add_argument("--set", dest="assignments", action="append", default=[], metavar="PFAD=WERT")
    return p


def main(argv: Sequence[str] | None = None, *, labeler: Labeler = ask_kind) -> int:
    args = build_parser().parse_args(argv)
    root = paths.project_root()
    overrides: dict[str, Any] = {}
    for assignment in args.assignments:
        overrides = deep_merge(overrides, parse_assignment(assignment))
    if args.serial:
        overrides = deep_merge(overrides, {"device": {"serial": args.serial}})
    if args.backend:
        overrides = deep_merge(overrides, {"device": {"backend": args.backend}})

    clock = RealClock()
    try:
        config = load_config(args.profile, config_dir=paths.config_dir(root), overrides=overrides)
        device = create_device(config.device, clock, root=root)
        connect_with_retry(device, clock=clock, attempts=config.device.connect_attempts)
    except BotError as e:
        print(f"Fehler: {e}")
        return 1

    recorder = AdRecorder(
        device,
        clock,
        args.out or paths.data_dir(root) / "ads",
        game_package=config.app.package,
        game_activities=args.game_activity,
        allowlist=config.app.foreground_allowlist,
        min_diff=args.min_diff,
        dump_ui=not args.no_ui_dump,
    )
    deadline = clock.monotonic() + args.duration if args.duration else None
    sessions = 0
    print(f"Warte auf Werbung von {config.app.package} auf {device.serial} – Ende mit Ctrl-C")
    try:
        while deadline is None or clock.monotonic() < deadline:
            started = clock.monotonic()
            finished = recorder.poll()
            if finished is not None:
                sessions += 1
                _label(finished, args.kind, labeler)
            pause = args.interval - (clock.monotonic() - started)
            if pause > 0:
                clock.sleep(pause)
    except KeyboardInterrupt:
        print("Abgebrochen.")
    except BotError as e:
        print(f"Fehler: {e}")
        return 1
    finally:
        if (session := recorder.finish()) is not None:
            sessions += 1
            if args.kind not in ("ask", "none"):
                _label(session, args.kind, labeler)
    print(f"{sessions} Werbung(en) aufgezeichnet")
    return 0


def _label(session: AdSession, mode: str, labeler: Labeler) -> None:
    if mode == "none":
        return
    session.kind = labeler(session) if mode == "ask" else mode
    session.save()


if __name__ == "__main__":
    sys.exit(main())
