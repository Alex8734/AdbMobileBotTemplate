"""Geräte finden, Device-Factory, Verbinden mit Backoff."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import adbutils
from structlog.typing import FilteringBoundLogger

from adbbot.core.clock import Clock
from adbbot.core.config import DeviceConfig
from adbbot.core.events import DeviceStatus, EventBus
from adbbot.core.exceptions import ConfigError, DeviceError
from adbbot.core.types import Size
from adbbot.device.adb_device import AdbDevice
from adbbot.device.fake_device import FakeDevice
from adbbot.device.protocol import DeviceProtocol
from adbbot.utils.paths import resolve
from adbbot.utils.retry import retry


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    serial: str
    state: str  # "device", "offline", "unauthorized", ...
    model: str | None = None


def list_devices(host: str = "127.0.0.1", port: int = 5037, timeout_s: float = 5.0) -> list[DeviceInfo]:
    client = adbutils.AdbClient(host=host, port=port, socket_timeout=timeout_s)
    try:
        entries = client.list()
    except (adbutils.AdbError, OSError) as e:
        raise DeviceError(f"adb-Server {host}:{port} nicht erreichbar: {e}") from e

    result: list[DeviceInfo] = []
    for entry in entries:
        model: str | None = None
        if entry.state == "device":
            try:
                model = str(client.device(entry.serial).getprop("ro.product.model")) or None
            except (adbutils.AdbError, OSError):
                model = None
        result.append(DeviceInfo(str(entry.serial), str(entry.state), model))
    return result


def create_device(config: DeviceConfig, clock: Clock, *, root: Path) -> DeviceProtocol:
    """Factory nach `device.backend`. Das Gerät ist danach noch nicht verbunden."""
    match config.backend:
        case "adb":
            return AdbDevice(
                clock=clock,
                serial=config.serial,
                host=config.adb_host,
                port=config.adb_port,
                command_timeout_s=config.command_timeout_s,
            )
        case "fake":
            if config.fake_screens_dir is None:
                raise ConfigError("device.backend=fake braucht device.fake_screens_dir")
            return FakeDevice.from_dir(
                resolve(config.fake_screens_dir, root), clock=clock, serial=config.serial or "fake"
            )
        case "subprocess":
            raise ConfigError("device.backend=subprocess ist noch nicht implementiert")


def connect_with_retry(
    device: DeviceProtocol,
    *,
    clock: Clock,
    attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 10.0,
    bus: EventBus | None = None,
    log: FilteringBoundLogger | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Size:
    """Verbindet mit Backoff und publiziert `DeviceStatus`. Gibt die Bildschirmgröße zurück."""

    def publish(status: DeviceStatus) -> None:
        if bus is not None:
            bus.publish(status)

    def on_retry(attempt: int, error: Exception, wait: float) -> None:
        if log is not None:
            log.warning(
                "Verbindung fehlgeschlagen",
                source="device",
                attempt=attempt,
                retry_in_s=wait,
                error=str(error),
            )
        publish(DeviceStatus(connected=False, serial=device.serial or None, size=None, error=str(error)))

    def attempt() -> Size:
        device.connect()
        return device.screen_size()

    try:
        size = retry(
            attempt,
            clock=clock,
            attempts=attempts,
            base_delay=base_delay_s,
            max_delay=max_delay_s,
            exceptions=(DeviceError,),
            on_retry=on_retry,
            sleep=sleep,
        )
    except DeviceError as e:
        publish(DeviceStatus(connected=False, serial=device.serial or None, size=None, error=str(e)))
        raise

    if log is not None:
        log.info("Gerät verbunden", source="device", serial=device.serial, size=f"{size.w}x{size.h}")
    publish(DeviceStatus(connected=True, serial=device.serial, size=size))
    return size
