"""Konfiguration: Pydantic-Modelle + Loader.

Ladereihenfolge (jede Stufe überschreibt die vorige, verschachtelte Dicts werden gemergt):
`config/default.yaml` → `config/profiles/<name>.yaml` → ENV (`ADBBOT__DEVICE__SERIAL=...`) → CLI.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from adbbot.core.exceptions import ConfigError

ENV_PREFIX = "ADBBOT__"


class _Model(BaseModel):
    # extra="forbid": Tippfehler in YAML fallen sofort auf. Zahlen → str, damit z. B. eine rein
    # numerische Serial aus ENV/YAML nicht an der Validierung scheitert.
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)


class TargetAppConfig(_Model):
    package: str
    activity: str | None = None
    restart_if_not_running: bool = True
    start_timeout_s: float = 30.0  # so lange darf der App-Start dauern
    foreground_allowlist: list[str] = Field(default_factory=list)  # fremde Packages, die der Wächter duldet


class DeviceConfig(_Model):
    serial: str | None = None  # None = erstes gefundenes Gerät
    backend: Literal["adb", "subprocess", "fake"] = "adb"
    fake_screens_dir: Path | None = None
    reference_resolution: tuple[int, int] = (1080, 1920)
    command_timeout_s: float = 10.0  # damit Stop nicht an hängendem adb scheitert
    adb_host: str = "127.0.0.1"
    adb_port: int = 5037
    connect_attempts: int = Field(default=3, ge=1)
    reconnect_delay_s: float = Field(default=1.0, gt=0)  # erster Abstand zwischen Reconnect-Versuchen
    reconnect_max_delay_s: float = Field(default=30.0, gt=0)  # Backoff-Obergrenze
    health_check_interval_s: float = Field(default=5.0, gt=0)  # Verbindungsprüfung ohne Livebild
    # Screenshots: scrcpy-Videostream (~30 Bilder/s) statt screencap (~1 s pro Bild über USB).
    # auto = Stream, wenn scrcpy installiert ist, sonst screencap
    capture: Literal["auto", "scrcpy", "screencap"] = "auto"
    scrcpy_server: Path | None = None  # None = neben `scrcpy` bzw. an den üblichen Orten suchen
    scrcpy_version: str | None = None  # None = aus `scrcpy --version`
    scrcpy_max_fps: int = Field(default=30, ge=1, le=120)
    scrcpy_bit_rate: int = Field(default=8_000_000, gt=0)


class VisionConfig(_Model):
    default_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    templates_dir: Path = Path("assets/templates")
    ocr_engine: Literal["rapidocr", "tesseract", "none"] = "none"
    poll_interval_ms: int = Field(default=300, ge=0)


class BotConfig(_Model):
    step_timeout_s: float = 30.0
    element_timeout_s: float = 10.0  # Default für element.wait()/tap(), wenn kein timeout angegeben ist
    default_retries: int = Field(default=1, ge=0)
    humanize_input: bool = True
    tap_jitter_px: int = Field(default=3, ge=0)
    post_tap_delay_ms: tuple[int, int] = (150, 400)
    watcher_interval_ms: int = 1000
    watcher_max_triggers_per_step: int = 5
    foreground_guard: bool = True  # App zurückholen, wenn sie während eines Flows den Vordergrund verliert
    foreground_check_interval_ms: int = Field(default=2000, ge=0)  # eigener adb-Aufruf → nicht pro Frame
    idle_frame_interval_ms: int = 500  # Livebild ohne laufenden Flow (0 = aus)
    failure_snapshots: bool = True
    persist_state: bool = True  # ctx.store nach data/<profil>/state.json schreiben (false = nur im Speicher)
    navigation_max_back: int = Field(default=3, ge=0)  # Navigator: Zurück-Versuche auf unbekanntem Screen
    navigation_restart_app: bool = True  # Navigator: danach die App neu starten


class GuiConfig(_Model):
    enabled: bool = True
    max_log_lines: int = 5000
    click_to_tap: bool = False


class AppConfig(_Model):
    app: TargetAppConfig
    device: DeviceConfig = Field(default_factory=DeviceConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    bot: BotConfig = Field(default_factory=BotConfig)
    gui: GuiConfig = Field(default_factory=GuiConfig)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_file: Path | None = None
    profile: str


# --- Loader ----------------------------------------------------------------------------------------


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Neues Dict; verschachtelte Mappings werden rekursiv gemergt, alles andere ersetzt."""
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            result[key] = deep_merge(current, value)
        else:
            result[key] = value
    return result


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: ungültiges YAML: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: oberste Ebene muss ein Mapping sein")
    return data


def _nested(parts: list[str], value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {parts[-1]: value}
    for part in reversed(parts[:-1]):
        result = {part: result}
    return result


def _parse_scalar(raw: str) -> Any:
    """YAML-Syntax für Werte: `true`, `5`, `[1080, 1920]`, `null`. Unparsbares bleibt String."""
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def env_overrides(environ: Mapping[str, str], prefix: str = ENV_PREFIX) -> dict[str, Any]:
    """`ADBBOT__DEVICE__SERIAL=abc` → `{"device": {"serial": "abc"}}`."""
    result: dict[str, Any] = {}
    for key, raw in environ.items():
        if not key.startswith(prefix):
            continue
        parts = [p.lower() for p in key[len(prefix) :].split("__") if p]
        if parts:
            result = deep_merge(result, _nested(parts, _parse_scalar(raw)))
    return result


def parse_assignment(assignment: str) -> dict[str, Any]:
    """`device.serial=abc` → `{"device": {"serial": "abc"}}` (für `--set` auf der CLI)."""
    key, sep, raw = assignment.partition("=")
    parts = [p for p in key.strip().split(".") if p]
    if not sep or not parts:
        raise ConfigError(f"Ungültige Zuweisung {assignment!r}, erwartet: pfad.zum.feld=wert")
    return _nested(parts, _parse_scalar(raw))


def available_profiles(config_dir: Path) -> list[str]:
    return sorted(p.stem for p in (config_dir / "profiles").glob("*.yaml"))


def load_config(
    profile: str,
    *,
    config_dir: Path,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> AppConfig:
    data: dict[str, Any] = {}
    default_path = config_dir / "default.yaml"
    if default_path.is_file():
        data = load_yaml(default_path)

    profile_path = config_dir / "profiles" / f"{profile}.yaml"
    if not profile_path.is_file():
        known = ", ".join(available_profiles(config_dir)) or "keine"
        raise ConfigError(f"Profil {profile!r} nicht gefunden ({profile_path}). Vorhanden: {known}")
    data = deep_merge(data, load_yaml(profile_path))
    data["profile"] = profile

    data = deep_merge(data, env_overrides(os.environ if environ is None else environ))
    if overrides:
        data = deep_merge(data, overrides)

    try:
        return AppConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"Ungültige Konfiguration (Profil {profile!r}):\n{e}") from e
