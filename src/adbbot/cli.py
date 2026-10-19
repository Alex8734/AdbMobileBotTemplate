"""Kommandozeilen-Argumente."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from adbbot.core.config import deep_merge, parse_assignment
from adbbot.core.exceptions import ConfigError


def build_parser(prog: str = "mybot") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description="ADB-Bot")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--gui", dest="mode", action="store_const", const="gui", help="mit Oberfläche (Default)"
    )
    mode.add_argument(
        "--headless", dest="mode", action="store_const", const="headless", help="ohne Oberfläche"
    )
    p.set_defaults(mode="gui")

    p.add_argument("--profile", default="example", help="Profil aus config/profiles/<name>.yaml")
    p.add_argument("--flow", help="Flow, der im Headless-Modus gestartet wird")
    p.add_argument("--params", metavar="JSON", help="Flow-Parameter als JSON-Objekt, z. B. '{\"anzahl\": 3}'")

    tools = p.add_argument_group("Werkzeuge")
    tools.add_argument("--list-devices", action="store_true", help="verbundene Geräte anzeigen und beenden")
    tools.add_argument(
        "--screenshot", type=Path, metavar="DATEI", help="einen Screenshot speichern und beenden"
    )

    over = p.add_argument_group("Config-Overrides")
    over.add_argument("--serial", help="= --set device.serial=...")
    over.add_argument("--backend", choices=["adb", "subprocess", "fake"], help="= --set device.backend=...")
    over.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    over.add_argument(
        "--set",
        dest="assignments",
        action="append",
        default=[],
        metavar="PFAD=WERT",
        help="beliebiges Config-Feld setzen, z. B. --set bot.step_timeout_s=60 (mehrfach möglich)",
    )
    return p


def parse_params(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"--params ist kein gültiges JSON: {e}") from e
    if not isinstance(value, dict):
        raise ConfigError("--params muss ein JSON-Objekt sein, z. B. '{\"anzahl\": 3}'")
    return value


def parse_args(argv: Sequence[str] | None = None, prog: str = "mybot") -> argparse.Namespace:
    return build_parser(prog).parse_args(argv)


def cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """CLI-Flags als verschachteltes Dict – letzte Stufe des Config-Merges."""
    result: dict[str, Any] = {}
    for assignment in args.assignments:
        result = deep_merge(result, parse_assignment(assignment))
    if args.serial is not None:
        result = deep_merge(result, {"device": {"serial": args.serial}})
    if args.backend is not None:
        result = deep_merge(result, {"device": {"backend": args.backend}})
    if args.log_level is not None:
        result["log_level"] = args.log_level
    return result
