#!/usr/bin/env python
"""Screenshot-Serie vom Gerät aufnehmen – z. B. als Fake-Screens für Tests oder Template-Quelle.

    uv run python scripts/capture_screenshots.py --profile example --count 20 --interval 1
    uv run python scripts/capture_screenshots.py --on-change --out tests/fixtures/screens/shop

Ohne --count/--duration läuft die Aufnahme bis Ctrl-C. Mit --on-change werden nur Frames gespeichert,
die sich sichtbar vom zuletzt gespeicherten unterscheiden.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from adbbot.core.clock import RealClock
from adbbot.core.config import deep_merge, load_config, parse_assignment
from adbbot.core.exceptions import BotError
from adbbot.core.types import Image
from adbbot.device.discovery import connect_with_retry, create_device
from adbbot.utils import paths


def frame_difference(a: Image, b: Image) -> float:
    """Mittlere absolute Pixeldifferenz (0–255). Unterschiedliche Größen zählen als maximal verschieden."""
    if a.shape != b.shape:
        return 255.0
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", default="example")
    p.add_argument("--out", type=Path, help="Zielordner (Default: data/screenshots/<zeit>)")
    p.add_argument("--count", type=int, help="nach so vielen gespeicherten Bildern aufhören")
    p.add_argument("--duration", type=float, help="nach so vielen Sekunden aufhören")
    p.add_argument("--interval", type=float, default=1.0, help="Sekunden zwischen Screenshots (Default 1)")
    p.add_argument("--on-change", action="store_true", help="nur speichern, wenn sich das Bild ändert")
    p.add_argument("--min-diff", type=float, default=2.0, help="Schwelle für --on-change (0–255, Default 2)")
    p.add_argument("--prefix", default="screen", help="Dateinamen-Präfix (Default: screen)")
    p.add_argument("--serial")
    p.add_argument("--backend", choices=["adb", "subprocess", "fake"])
    p.add_argument("--set", dest="assignments", action="append", default=[], metavar="PFAD=WERT")
    return p


def main(argv: Sequence[str] | None = None) -> int:
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

    out: Path = args.out or paths.screenshots_dir(root) / datetime.now().strftime("%Y%m%d-%H%M%S")
    deadline = clock.monotonic() + args.duration if args.duration else None
    saved = 0
    previous: Image | None = None
    print(f"Aufnahme von {device.serial} nach {out} – Ende mit Ctrl-C")
    try:
        while args.count is None or saved < args.count:
            if deadline is not None and clock.monotonic() >= deadline:
                break
            started = clock.monotonic()
            frame = device.screenshot()
            if (
                not args.on_change
                or previous is None
                or frame_difference(previous, frame.image) >= args.min_diff
            ):
                saved += 1
                path = out / f"{args.prefix}_{saved:04d}.png"
                frame.save(path)
                previous = frame.image
                print(f"  {path.name}  {frame.size.w}x{frame.size.h}")
            pause = args.interval - (clock.monotonic() - started)
            if pause > 0:
                clock.sleep(pause)
    except KeyboardInterrupt:
        print("Abgebrochen.")
    except BotError as e:
        print(f"Fehler: {e}")
        return 1
    print(f"{saved} Screenshot(s) in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
