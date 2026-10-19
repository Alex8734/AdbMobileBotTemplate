"""Composition Root: Config → Context → Registries → Engine → GUI oder Headless."""

from __future__ import annotations

import random
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from structlog.typing import FilteringBoundLogger

from adbbot import cli
from adbbot.bot.context import BotContext
from adbbot.bot.engine import BotEngine
from adbbot.bot.failure import FailureRecorder
from adbbot.bot.page import PageRegistry
from adbbot.bot.registry import Registries
from adbbot.bot.storage import Storage
from adbbot.core.clock import Clock, RealClock
from adbbot.core.commands import StartFlow, Stop
from adbbot.core.config import AppConfig, load_config
from adbbot.core.events import EventBus, FlowFinished
from adbbot.core.exceptions import BotError
from adbbot.core.logging import create_logger
from adbbot.core.state import RunControl
from adbbot.core.types import StepResult
from adbbot.device.discovery import connect_with_retry, create_device, list_devices
from adbbot.device.humanize import humanize
from adbbot.device.protocol import DeviceProtocol
from adbbot.utils import paths
from adbbot.vision.vision import Vision

RegisterFn = Callable[[Registries], None]

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130


def build_device(
    config: AppConfig,
    clock: Clock,
    *,
    root: Path,
    sleep: Callable[[float], None] | None = None,
    rng: random.Random | None = None,
) -> DeviceProtocol:
    device = create_device(config.device, clock, root=root)
    return humanize(device, config.bot, sleep=sleep or clock.sleep, rng=rng)


def build_context(
    config: AppConfig,
    *,
    root: Path,
    clock: Clock | None = None,
    bus: EventBus | None = None,
    log: FilteringBoundLogger | None = None,
    device: DeviceProtocol | None = None,
    rng: random.Random | None = None,
) -> BotContext:
    """Baut alle Laufzeit-Objekte. Ein übergebenes `device` wird unverändert (ohne Humanize) benutzt."""
    clock = clock or RealClock()
    bus = bus or EventBus()
    rng = rng or random.Random()
    log = log or create_logger(level=config.log_level, bus=bus, log_file=_log_file(config, root))
    control = RunControl(clock)
    device = device or build_device(config, clock, root=root, sleep=control.sleep, rng=rng)
    vision = Vision.from_config(config, device, clock=clock, root=root, bus=bus, log=log)
    failures = (
        FailureRecorder(vision, bus, paths.failures_dir(root)) if config.bot.failure_snapshots else None
    )
    if config.bot.persist_state:
        store = Storage(paths.data_dir(root) / config.profile / "state.json")
        if store.recovered_from is not None:
            log.warning(
                "Zustandsdatei war beschädigt und wurde beiseitegelegt",
                source="app",
                backup=str(store.recovered_from),
            )
    else:
        store = Storage.in_memory()
    return BotContext(
        config=config,
        device=device,
        vision=vision,
        events=bus,
        control=control,
        clock=clock,
        log=log,
        pages=PageRegistry(),
        root=root,
        rng=rng,
        failures=failures,
        store=store,
    )


def build_app(
    config: AppConfig,
    register: RegisterFn | None,
    *,
    root: Path,
    clock: Clock | None = None,
    bus: EventBus | None = None,
    log: FilteringBoundLogger | None = None,
    device: DeviceProtocol | None = None,
) -> tuple[BotContext, Registries]:
    """Context + Projekt-Registrierung + Template-Validierung (Tippfehler fallen beim Start auf)."""
    ctx = build_context(config, root=root, clock=clock, bus=bus, log=log, device=device)
    registries = Registries(ctx.pages)
    if register is not None:
        register(registries)
    ctx.vision.templates.validate(registries.template_names())
    return ctx, registries


def run_headless(
    ctx: BotContext, registries: Registries, flow: str | None, params: Mapping[str, Any] | None = None
) -> int:
    """Einen Flow ohne GUI ausführen. Ctrl-C → Stop (kooperativ)."""
    log = ctx.log.bind(source="app")
    if not flow or flow not in registries.flows.names():
        log.error(
            "Unbekannter oder fehlender Flow (--flow)" if flow else "Kein Flow angegeben (--flow)",
            flow=flow,
            vorhanden=", ".join(registries.flows.names()) or "keine",
        )
        return EXIT_USAGE

    finished = threading.Event()
    outcome: list[FlowFinished] = []

    def on_finished(event: FlowFinished) -> None:
        outcome.append(event)
        finished.set()

    unsubscribe = ctx.events.subscribe(FlowFinished, on_finished)
    engine = BotEngine(ctx, registries, idle_frames=False)
    engine.start()
    engine.submit(StartFlow(flow, dict(params or {})))
    try:
        try:
            while not finished.wait(0.2):
                pass
        except KeyboardInterrupt:
            log.warning("Abbruch angefordert (Ctrl-C) – warte auf sauberes Ende")
            engine.submit(Stop())
            finished.wait(ctx.config.device.command_timeout_s + 5)
    finally:
        engine.shutdown()
        unsubscribe()

    if not outcome:
        return EXIT_FAILED
    result = outcome[0]
    if result.cancelled:
        return EXIT_CANCELLED
    return EXIT_FAILED if result.result is StepResult.FAILED else EXIT_OK


def main(
    register: RegisterFn | None = None, argv: Sequence[str] | None = None, *, prog: str = "mybot"
) -> int:
    args = cli.parse_args(argv, prog)
    root = paths.project_root()

    try:
        config = load_config(
            args.profile, config_dir=paths.config_dir(root), overrides=cli.cli_overrides(args)
        )
        params = cli.parse_params(args.params)
    except BotError as e:
        print(f"Fehler: {e}")
        return EXIT_USAGE

    bus = EventBus()
    base_log = create_logger(level=config.log_level, bus=bus, log_file=_log_file(config, root))
    log = base_log.bind(source="app")
    clock = RealClock()

    try:
        if args.list_devices:
            return _list_devices(config)
        if args.screenshot is not None:
            return _screenshot(config, clock, root, args.screenshot, log)
        ctx, registries = build_app(config, register, root=root, clock=clock, bus=bus, log=base_log)
        if args.mode == "headless":
            return run_headless(ctx, registries, args.flow, params)
        from adbbot.gui.app import run_gui  # Qt nur laden, wenn die GUI gebraucht wird

        return run_gui(ctx, registries)
    except BotError as e:
        log.error(str(e), error_type=type(e).__name__)
        return EXIT_FAILED


def _log_file(config: AppConfig, root: Path) -> Path | None:
    return paths.resolve(config.log_file, root) if config.log_file else None


def _list_devices(config: AppConfig) -> int:
    devices = list_devices(config.device.adb_host, config.device.adb_port)
    if not devices:
        print("Keine Geräte gefunden.")
        return EXIT_OK
    width = max(len(d.serial) for d in devices)
    for d in devices:
        print(f"{d.serial:<{width}}  {d.state:<12}  {d.model or ''}")
    return EXIT_OK


def _screenshot(config: AppConfig, clock: Clock, root: Path, out: Path, log: FilteringBoundLogger) -> int:
    device = build_device(config, clock, root=root)
    connect_with_retry(device, clock=clock, attempts=config.device.connect_attempts, log=log)
    frame = device.screenshot()
    frame.save(out)
    log.info("Screenshot gespeichert", path=str(out), size=f"{frame.size.w}x{frame.size.h}")
    return EXIT_OK
