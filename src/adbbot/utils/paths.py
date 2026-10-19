"""Projekt-Pfade auflösen."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_ENV = "ADBBOT_ROOT"


def project_root(start: Path | None = None) -> Path:
    """`$ADBBOT_ROOT`, sonst das erste Verzeichnis ab `start` (Default: cwd) aufwärts,
    das `config/default.yaml` enthält. Wird nichts gefunden, ist es `start` selbst.
    """
    if env := os.environ.get(ROOT_ENV):
        return Path(env).resolve()
    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "config" / "default.yaml").is_file():
            return candidate
    return origin


def resolve(path: Path, root: Path) -> Path:
    """Relative Pfade aus der Config gelten relativ zum Projekt-Root."""
    return path if path.is_absolute() else root / path


def config_dir(root: Path) -> Path:
    return root / "config"


def data_dir(root: Path) -> Path:
    return root / "data"


def screenshots_dir(root: Path) -> Path:
    return data_dir(root) / "screenshots"


def failures_dir(root: Path) -> Path:
    return data_dir(root) / "failures"
