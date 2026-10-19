"""BotEngine: genau ein Worker-Thread mit einer Command-Queue.

- Steuer-Commands (Pause/Resume/Stop) wirken sofort über `RunControl` – auch während ein Flow läuft,
  denn dann liest der Bot-Thread die Queue nicht. Stop verwirft zusätzlich alle wartenden Arbeits-Commands.
- Arbeits-Commands (StartFlow, RunAction, TapAt, ...) werden sequenziell abgearbeitet.
- Ohne Arbeit: Idle-Tick (Reconnect mit Backoff, optional Livebild + Idle-Watcher).
- `vision.poll_hook` → `checkpoint()` + Watcher auf jedem Frame der Warteschleifen.
"""

from __future__ import annotations

import functools
import queue
import threading
from collections.abc import Callable

from adbbot.bot.context import BotContext
from adbbot.bot.failure import mark_reported, was_reported
from adbbot.bot.registry import Registries
from adbbot.bot.watchers import WatcherRunner
from adbbot.core.commands import (
    CaptureFrame,
    Command,
    ConnectDevice,
    KeyEvent,
    Pause,
    ReloadTemplates,
    Resume,
    RunAction,
    StartFlow,
    Stop,
    TapAt,
    WorkCommand,
)
from adbbot.core.events import DeviceStatus, FlowFinished, FlowStarted, RegistriesChanged, StateChanged
from adbbot.core.exceptions import BotError, BotStopped, DeviceError, StepSkipped
from adbbot.core.state import BotState
from adbbot.core.types import Frame, StepResult
from adbbot.device.discovery import connect_with_retry


class BotEngine:
    def __init__(self, ctx: BotContext, registries: Registries, *, idle_frames: bool = True) -> None:
        self.ctx = ctx
        self.registries = registries
        bot = ctx.config.bot
        self.watchers = WatcherRunner(
            ctx,
            registries.watchers,
            interval_s=bot.watcher_interval_ms / 1000,
            max_triggers_per_step=bot.watcher_max_triggers_per_step,
        )
        self._idle_frames = idle_frames and bot.idle_frame_interval_ms > 0
        device = ctx.config.device
        self._idle_interval_s = (
            bot.idle_frame_interval_ms / 1000
            if self._idle_frames
            else min(1.0, device.health_check_interval_s, device.reconnect_delay_s)
        )
        self._queue: queue.Queue[WorkCommand | None] = queue.Queue()  # None = beenden
        self._lock = threading.Lock()
        self._state = BotState.DISCONNECTED
        self._job: str | None = None
        self._thread: threading.Thread | None = None
        self._connected = False
        self._reconnect_delay_s = device.reconnect_delay_s
        self._next_connect_at = 0.0
        self._next_health_check_at = 0.0
        self.log = ctx.log.bind(source="engine")
        ctx.vision.poll_hook = self._on_poll
        ctx.control.on_pause_changed = self._on_pause_changed

    # --- öffentliche API (beliebiger Thread) -------------------------------------------------------

    @property
    def state(self) -> BotState:
        with self._lock:
            return self._state

    @property
    def current_job(self) -> str | None:
        with self._lock:
            return self._job

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_alive:
            return
        self._thread = threading.Thread(target=self._loop, name="bot-engine", daemon=True)
        self._thread.start()

    def submit(self, command: Command) -> None:
        match command:
            case Pause():
                self.ctx.control.request_pause()
                self.log.info("Pause angefordert")
            case Resume():
                self.ctx.control.request_resume()
                self.log.info("Fortsetzen angefordert")
            case Stop():
                self.ctx.control.request_stop()
                dropped = self._drain()
                self.log.info("Stop angefordert", verworfene_commands=dropped)
                if self.current_job is not None:
                    self._set_state(BotState.STOPPING)
            case WorkCommand():
                self._queue.put(command)
            case _:
                raise TypeError(f"Unbekanntes Command: {command!r}")

    def _drain(self) -> int:
        """Wartende Arbeits-Commands verwerfen; ein Beenden-Signal bleibt erhalten."""
        dropped = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return dropped
            if item is None:
                self._queue.put(None)
                return dropped
            dropped += 1

    def shutdown(self, timeout: float = 5.0) -> bool:
        """Laufenden Flow stoppen, Thread beenden. `True`, wenn der Thread rechtzeitig endete."""
        self.ctx.control.request_stop()
        self._drain()
        self._queue.put(None)
        self.watchers.close()
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    # --- Bot-Thread --------------------------------------------------------------------------------

    def _loop(self) -> None:
        self.log.debug("Engine gestartet")
        while True:
            try:
                command = self._queue.get(timeout=self._idle_interval_s)
            except queue.Empty:
                self._guarded(self._idle_tick)
                continue
            if command is None:
                break
            self._guarded(functools.partial(self._handle, command))
        self.log.debug("Engine beendet")

    def _guarded(self, fn: Callable[[], None]) -> None:
        """Der Bot-Thread darf nie an einem Fehler sterben."""
        try:
            fn()
        except BotStopped:
            pass
        except Exception as e:
            self.log.exception("Unerwarteter Fehler in der Engine", error=f"{type(e).__name__}: {e}")

    def _handle(self, command: WorkCommand) -> None:
        match command:
            case StartFlow(flow_name=name, params=params):
                try:
                    flow_cls = self.registries.flows.get(name)
                except BotError as e:
                    self._reject(name, str(e))
                    return
                self._run_job(name, lambda: flow_cls(self.ctx, params).run())
            case RunAction(action_name=name):
                try:
                    spec = self.registries.actions.get(name)
                except BotError as e:
                    self._reject(name, str(e))
                    return
                self._run_job(spec.title, lambda: spec.func(self.ctx))
            case TapAt(point=point):
                self._device_command(lambda: self.ctx.device.tap(point))
            case KeyEvent(keycode=keycode):
                self._device_command(lambda: self.ctx.device.key(keycode))
            case CaptureFrame():
                self._device_command(self.ctx.vision.capture)
            case ConnectDevice(serial=serial):
                if serial is not None and serial != self.ctx.device.serial:
                    self.log.warning(
                        "Gerätewechsel noch nicht unterstützt – verbinde aktuelles Gerät neu",
                        requested=serial,
                        current=self.ctx.device.serial,
                    )
                self._connected = False
                self._connect()
            case ReloadTemplates():
                self.ctx.vision.templates.reload()
                self.log.info("Templates neu geladen")
                self.ctx.events.publish(RegistriesChanged())

    def _run_job(self, name: str, body: Callable[[], None]) -> None:
        control = self.ctx.control
        control.reset()
        self.watchers.reset()
        if not self._ensure_connected():
            self._reject(name, "Kein Gerät verbunden")
            return

        with self._lock:
            self._job = name
        self._set_state(BotState.RUNNING)
        self.ctx.events.publish(FlowStarted(name))
        log = self.log.bind(flow=name)
        log.info("Gestartet")
        started = self.ctx.clock.monotonic()

        result, cancelled, error = StepResult.OK, False, None
        try:
            body()
        except BotStopped:
            result, cancelled = StepResult.SKIPPED, True
            log.warning("Abgebrochen")
        except StepSkipped as e:
            result = StepResult.SKIPPED
            log.info("Übersprungen", reason=str(e))
        except Exception as e:
            result = StepResult.FAILED
            error = f"{type(e).__name__}: {e}"
            if not was_reported(e):
                if isinstance(e, BotError):
                    log.error("Fehlgeschlagen", error=error)
                else:
                    log.exception("Unerwarteter Fehler", error=error)
                self._record_failure(name, e)
                mark_reported(e)
            if isinstance(e, DeviceError) and not self.ctx.device.is_connected():
                self._mark_disconnected(str(e))
        finally:
            control.reset()
            with self._lock:
                self._job = None
            self._set_state(BotState.IDLE if self._connected else BotState.DISCONNECTED)

        duration = self.ctx.clock.monotonic() - started
        self.ctx.events.publish(FlowFinished(name, result, cancelled, error))
        log.info("Beendet", result=result.name, cancelled=cancelled, duration_s=round(duration, 2))

    def _reject(self, name: str, error: str) -> None:
        self.log.error("Nicht gestartet", job=name, error=error)
        self.ctx.events.publish(FlowFinished(name, StepResult.FAILED, error=error))

    def _record_failure(self, name: str, error: Exception) -> None:
        if self.ctx.failures is None:
            return
        try:
            path = self.ctx.failures.record(flow=name, step=None, error=error)
        except Exception as e:
            self.log.warning("Fehler-Snapshot konnte nicht gespeichert werden", error=str(e))
            return
        self.log.info("Fehler-Snapshot gespeichert", path=str(path))

    def _device_command(self, fn: Callable[[], object]) -> None:
        if not self._ensure_connected():
            self.log.warning("Kein Gerät verbunden – Command ignoriert")
            return
        try:
            fn()
        except DeviceError as e:
            self.log.error("Gerätefehler", error=str(e))
            self._mark_disconnected(str(e))

    # --- Verbindung und Idle -----------------------------------------------------------------------

    def _ensure_connected(self) -> bool:
        if self._connected and self.ctx.device.is_connected():
            return True
        return self._connect()

    def _connect(self) -> bool:
        try:
            connect_with_retry(
                self.ctx.device,
                clock=self.ctx.clock,
                attempts=self.ctx.config.device.connect_attempts,
                bus=self.ctx.events,
                log=self.log,
            )
        except DeviceError as e:
            self._connected = False
            delay = self._reconnect_delay_s
            self._next_connect_at = self.ctx.clock.monotonic() + delay
            self.log.warning("Gerät nicht verbunden", error=str(e), next_try_s=delay)
            self.ctx.events.publish(
                DeviceStatus(
                    connected=False,
                    serial=self.ctx.device.serial or None,
                    size=None,
                    error=str(e),
                    retry_in_s=delay,
                )
            )
            self._reconnect_delay_s = min(delay * 2, self.ctx.config.device.reconnect_max_delay_s)
            if self.current_job is None:
                self._set_state(BotState.DISCONNECTED)
            return False
        self._connected = True
        self._reconnect_delay_s = self.ctx.config.device.reconnect_delay_s
        if self.current_job is None:
            self._set_state(BotState.IDLE)
        return True

    def _mark_disconnected(self, error: str) -> None:
        self._connected = False
        self._next_connect_at = self.ctx.clock.monotonic()  # beim nächsten Idle-Tick sofort neu versuchen
        self.ctx.events.publish(
            DeviceStatus(connected=False, serial=self.ctx.device.serial or None, size=None, error=error)
        )
        if self.current_job is None:
            self._set_state(BotState.DISCONNECTED)

    def _idle_tick(self) -> None:
        if not self._connected:
            if self.ctx.clock.monotonic() >= self._next_connect_at:
                self._connect()
            return
        if not self._idle_frames:
            self._health_check()
            return
        try:
            frame = self.ctx.vision.capture()
        except DeviceError as e:
            self.log.warning("Livebild fehlgeschlagen", error=str(e))
            self._mark_disconnected(str(e))
            return
        self.watchers.run(frame, flow_running=False)

    def _health_check(self) -> None:
        """Ohne Livebild fällt ein Verbindungsverlust sonst erst beim nächsten Flow auf."""
        now = self.ctx.clock.monotonic()
        if now < self._next_health_check_at:
            return
        self._next_health_check_at = now + self.ctx.config.device.health_check_interval_s
        if not self.ctx.device.is_connected():
            self.log.warning("Verbindung zum Gerät verloren")
            self._mark_disconnected("Verbindung verloren")

    # --- Hooks -------------------------------------------------------------------------------------

    def _on_poll(self, frame: Frame) -> None:
        self.ctx.checkpoint()
        if self.current_job is not None:
            self.watchers.run(frame, flow_running=True)

    def _on_pause_changed(self, paused: bool) -> None:
        if self.current_job is None:
            return
        if paused:
            self._set_state(BotState.PAUSED)
        else:
            self._set_state(BotState.STOPPING if self.ctx.control.stop_requested else BotState.RUNNING)

    def _set_state(self, state: BotState) -> None:
        with self._lock:
            if state == self._state:
                return
            self._state = state
        self.ctx.events.publish(StateChanged(state))
