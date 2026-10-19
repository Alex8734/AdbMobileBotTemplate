from pathlib import Path

import pytest

from adbbot.core.config import deep_merge, env_overrides, load_config, parse_assignment
from adbbot.core.exceptions import ConfigError


def test_deep_merge_is_recursive_and_pure() -> None:
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    result = deep_merge(base, {"a": {"y": 3}, "c": 4})
    assert result == {"a": {"x": 1, "y": 3}, "b": 1, "c": 4}
    assert base == {"a": {"x": 1, "y": 2}, "b": 1}


def test_merge_order_default_profile_env_cli(config_dir: Path) -> None:
    cfg = load_config(
        "test",
        config_dir=config_dir,
        environ={"ADBBOT__BOT__DEFAULT_RETRIES": "3", "ADBBOT__BOT__STEP_TIMEOUT_S": "50", "OTHER": "x"},
        overrides={"bot": {"step_timeout_s": 60}},
    )
    assert cfg.profile == "test"
    assert cfg.app.package == "com.example.test"
    assert cfg.device.command_timeout_s == 10  # default.yaml
    assert cfg.bot.default_retries == 3  # ENV über default
    assert cfg.bot.step_timeout_s == 60  # CLI über ENV über Profil
    assert cfg.bot.watcher_interval_ms == 1000  # Modell-Default


def test_env_values_use_yaml_syntax() -> None:
    env = {
        "ADBBOT__BOT__HUMANIZE_INPUT": "false",
        "ADBBOT__DEVICE__REFERENCE_RESOLUTION": "[720, 1280]",
        "ADBBOT__DEVICE__SERIAL": "emulator-5554",
    }
    assert env_overrides(env) == {
        "bot": {"humanize_input": False},
        "device": {"reference_resolution": [720, 1280], "serial": "emulator-5554"},
    }


def test_numeric_serial_and_list_to_tuple(config_dir: Path) -> None:
    cfg = load_config(
        "test",
        config_dir=config_dir,
        environ={"ADBBOT__DEVICE__SERIAL": "1234567", "ADBBOT__DEVICE__REFERENCE_RESOLUTION": "[720, 1280]"},
    )
    assert cfg.device.serial == "1234567"
    assert cfg.device.reference_resolution == (720, 1280)


def test_unknown_key_is_rejected(config_dir: Path) -> None:
    with pytest.raises(ConfigError, match="step_timeut_s"):
        load_config("test", config_dir=config_dir, environ={}, overrides={"bot": {"step_timeut_s": 1}})


def test_invalid_value_is_rejected(config_dir: Path) -> None:
    with pytest.raises(ConfigError):
        load_config("test", config_dir=config_dir, environ={}, overrides={"device": {"backend": "usb"}})


def test_missing_profile_lists_available(config_dir: Path) -> None:
    with pytest.raises(ConfigError, match="Vorhanden: test"):
        load_config("nope", config_dir=config_dir, environ={})


def test_profile_without_app_fails(config_dir: Path) -> None:
    (config_dir / "profiles" / "empty.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="app"):
        load_config("empty", config_dir=config_dir, environ={})


def test_invalid_yaml(config_dir: Path) -> None:
    (config_dir / "profiles" / "broken.yaml").write_text("app: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="ungültiges YAML"):
        load_config("broken", config_dir=config_dir, environ={})


def test_parse_assignment() -> None:
    assert parse_assignment("bot.post_tap_delay_ms=[0, 0]") == {"bot": {"post_tap_delay_ms": [0, 0]}}
    assert parse_assignment("log_level=DEBUG") == {"log_level": "DEBUG"}
    with pytest.raises(ConfigError):
        parse_assignment("kein_gleichheitszeichen")


def test_shipped_config_is_valid() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = load_config("example", config_dir=root / "config", environ={})
    assert cfg.device.backend == "adb"
