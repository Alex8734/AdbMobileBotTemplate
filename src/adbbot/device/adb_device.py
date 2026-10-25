"""`DeviceProtocol`-Implementierung über `adbutils` (adb-Server-Socket).

Screenshots kommen bei `capture="auto"`/`"scrcpy"` aus einem scrcpy-Videostream (scrcpy_stream.py), sonst per
`screencap`. Fällt der Stream aus, wird er einmal neu gestartet; bei `"auto"` geht es danach mit `screencap`
weiter.
"""

from __future__ import annotations

import re
import shlex
import struct
from pathlib import Path
from typing import Literal

import adbutils
import cv2
import numpy as np
import structlog
from structlog.typing import FilteringBoundLogger

from adbbot.core.clock import Clock
from adbbot.core.exceptions import DeviceError
from adbbot.core.types import Frame, Image, Point, Size
from adbbot.device.scrcpy_stream import ScrcpyStream, find_server
from adbbot.utils.image import decode_image

_ADB_ERRORS = (adbutils.AdbError, OSError)

# android.graphics.PixelFormat bzw. HAL-Formate, die `screencap` roh ausgibt
_RAW_RGB_FORMATS = {1, 2}  # RGBA_8888, RGBX_8888
_RAW_BGR_FORMATS = {5}  # BGRA_8888
_WM_SIZE = re.compile(r"(\d+)x(\d+)")
_STREAM_RETRY_S = 30.0  # nach einem gescheiterten Stream-Neustart so lange screencap, dann erneut versuchen

type CaptureMode = Literal["auto", "scrcpy", "screencap"]


def decode_raw_screencap(data: bytes) -> Image:
    """Rohausgabe von `screencap` (ohne `-p`) → BGR-Bild.

    Header: width, height, format (je uint32 LE), ab Android 9 zusätzlich colorspace → 12 oder 16 Bytes.
    Deutlich schneller als `screencap -p`, weil das Gerät kein PNG kodieren muss.
    """
    if len(data) < 12:
        raise DeviceError(f"screencap: zu wenig Daten ({len(data)} Bytes)")
    w, h, fmt = struct.unpack_from("<III", data, 0)
    pixel_bytes = w * h * 4
    header = len(data) - pixel_bytes
    if w == 0 or h == 0 or header not in (12, 16):
        raise DeviceError(f"screencap: unerwartetes Format ({w}x{h}, {len(data)} Bytes)")
    rgba = np.frombuffer(data, dtype=np.uint8, count=pixel_bytes, offset=header).reshape(h, w, 4)
    if fmt in _RAW_RGB_FORMATS:
        return np.ascontiguousarray(rgba[:, :, 2::-1])
    if fmt in _RAW_BGR_FORMATS:
        return np.ascontiguousarray(rgba[:, :, :3])
    raise DeviceError(f"screencap: Pixelformat {fmt} nicht unterstützt")


class AdbDevice:
    """Jeder Aufruf hat ein Timeout (`command_timeout_s`) und wirft `DeviceError`,
    damit ein hängendes adb den Bot-Thread nicht dauerhaft blockiert."""

    def __init__(
        self,
        *,
        clock: Clock,
        serial: str | None = None,
        host: str = "127.0.0.1",
        port: int = 5037,
        command_timeout_s: float = 10.0,
        capture: CaptureMode = "screencap",
        scrcpy_server: Path | None = None,
        scrcpy_version: str | None = None,
        scrcpy_max_fps: int = 30,
        scrcpy_bit_rate: int = 8_000_000,
        log: FilteringBoundLogger | None = None,
    ) -> None:
        self._clock = clock
        self._requested_serial = serial
        self._timeout = command_timeout_s
        self._client = adbutils.AdbClient(host=host, port=port, socket_timeout=command_timeout_s)
        self._dev: adbutils.AdbDevice | None = None
        self._raw_screencap_ok = True
        self.capture = capture
        self._scrcpy_server = scrcpy_server
        self._scrcpy_version = scrcpy_version
        self._scrcpy_max_fps = scrcpy_max_fps
        self._scrcpy_bit_rate = scrcpy_bit_rate
        self._log = log or structlog.get_logger().bind(source="device")
        self._stream: ScrcpyStream | None = None
        self._stream_retry_at: float | None = None
        self._physical: tuple[int, int] | None = None  # (kurze, lange Seite) laut `wm size`

    @property
    def serial(self) -> str:
        if self._dev is not None:
            return str(self._dev.serial)
        return self._requested_serial or ""

    # --- Verbindung --------------------------------------------------------------------------------

    def connect(self) -> None:
        self._stop_stream()  # Reconnect: alten Stream nicht weiterlaufen lassen
        serial = self._requested_serial
        try:
            if serial is None:
                ready = [d.serial for d in self._client.list() if d.state == "device"]
                if not ready:
                    raise DeviceError("Kein verbundenes Gerät gefunden (adb devices ist leer)")
                serial = ready[0]
            elif ":" in serial:  # Netzwerk-Gerät, z. B. 127.0.0.1:5555
                self._client.connect(serial, timeout=self._timeout)
            dev = self._client.device(serial)
            state = dev.get_state()
        except _ADB_ERRORS as e:
            raise DeviceError(f"Verbindung zu {serial or 'Gerät'} fehlgeschlagen: {e}") from e
        if state != "device":
            raise DeviceError(f"Gerät {serial} ist nicht bereit (Status: {state})")
        self._dev = dev
        self._raw_screencap_ok = True
        if self.capture != "screencap":
            self._start_stream()

    def disconnect(self) -> None:
        self._stop_stream()
        self._dev = None

    @property
    def streaming(self) -> bool:
        """Kommen Screenshots gerade aus dem scrcpy-Stream?"""
        return self._stream is not None and self._stream.alive

    def is_connected(self) -> bool:
        if self._dev is None:
            return False
        try:
            return bool(self._dev.get_state() == "device")
        except _ADB_ERRORS:
            return False

    # --- Bild --------------------------------------------------------------------------------------

    def screen_size(self) -> Size:
        dev = self._require()
        try:
            ws = dev.window_size()
        except _ADB_ERRORS as e:
            raise DeviceError(f"Bildschirmgröße nicht lesbar: {e}") from e
        return Size(int(ws.width), int(ws.height))

    def screenshot(self) -> Frame:
        streamed = self._stream_image()
        if streamed is not None:
            return Frame(streamed, self._clock.monotonic())
        image: Image | None = None
        if self._raw_screencap_ok:
            try:
                image = decode_raw_screencap(self._shell_bytes(["screencap"]))
            except DeviceError:
                # Manche (alte) Geräte verfälschen Binärdaten über `shell` → dauerhaft auf PNG wechseln
                self._raw_screencap_ok = False
        if image is None:
            try:
                image = decode_image(self._shell_bytes(["screencap", "-p"]))
            except ValueError as e:
                raise DeviceError(f"Screenshot nicht dekodierbar: {e}") from e
        return Frame(image, self._clock.monotonic())

    # --- Eingaben ----------------------------------------------------------------------------------

    def tap(self, p: Point) -> None:
        self._shell(["input", "tap", str(p.x), str(p.y)])

    def swipe(self, a: Point, b: Point, duration_ms: int) -> None:
        self._shell(["input", "swipe", str(a.x), str(a.y), str(b.x), str(b.y), str(duration_ms)])

    def long_press(self, p: Point, duration_ms: int) -> None:
        self.swipe(p, p, duration_ms)

    def key(self, keycode: int) -> None:
        self._shell(["input", "keyevent", str(keycode)])

    def input_text(self, text: str) -> None:
        """Nur ASCII zuverlässig (Einschränkung von `input text`)."""
        escaped = shlex.quote(text.replace(" ", "%s"))
        self._shell(f"input text {escaped}")

    # --- Apps --------------------------------------------------------------------------------------

    def start_app(self, package: str, activity: str | None = None) -> None:
        if activity:
            self._shell(["am", "start", "-n", f"{package}/{activity}"])
        else:
            self._shell(["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])

    def stop_app(self, package: str) -> None:
        self._shell(["am", "force-stop", package])

    def bring_to_front(self, package: str) -> None:
        """Launcher-Intent: holt den bestehenden Task nach vorne, statt eine neue Activity zu erzeugen."""
        self._shell(["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])

    def current_app(self) -> str | None:
        dev = self._require()
        try:
            return str(dev.app_current().package) or None
        except _ADB_ERRORS as e:
            if not self.is_connected():
                raise DeviceError(f"Aktuelle App nicht lesbar: {e}") from e
            return None  # z. B. Sperrbildschirm: keine fokussierte App

    def current_activity(self) -> str | None:
        """Vollständiger Klassenname, z. B. `com.google.android.gms.ads.AdActivity`."""
        dev = self._require()
        try:
            info = dev.app_current()
        except _ADB_ERRORS as e:
            if not self.is_connected():
                raise DeviceError(f"Aktuelle Activity nicht lesbar: {e}") from e
            return None
        activity = str(info.activity or "")
        if activity.startswith("."):  # relativ zum Package
            activity = f"{info.package}{activity}"
        return activity or None

    def dump_ui(self) -> str | None:
        """UI-Hierarchie als XML (`uiautomator dump`). Langsam (1–3 s); `None`, wenn der Dump scheitert –
        etwa während ein Video läuft und die UI nie „idle“ wird."""
        dev = self._require()
        try:
            return str(dev.dump_hierarchy())
        except _ADB_ERRORS as e:
            if not self.is_connected():
                raise DeviceError(f"UI-Dump nicht möglich: {e}") from e
            return None

    # --- Stream ------------------------------------------------------------------------------------

    def _start_stream(self) -> bool:
        dev = self._require()
        try:
            server = find_server(self._scrcpy_server, self._scrcpy_version)
            stream = ScrcpyStream(
                dev, server, clock=self._clock, max_fps=self._scrcpy_max_fps, bit_rate=self._scrcpy_bit_rate
            )
            stream.start()
            self._physical = self._physical or self._physical_size()
        except DeviceError as e:
            if self.capture == "scrcpy":
                raise
            self._log.warning("Bildschirm-Stream nicht verfügbar – nutze screencap", reason=str(e))
            self._stream_retry_at = self._clock.monotonic() + _STREAM_RETRY_S
            return False
        self._stream = stream
        self._stream_retry_at = None
        self._log.info("Bildschirm-Stream aktiv", scrcpy=server.version, max_fps=self._scrcpy_max_fps)
        return True

    def _stop_stream(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream = None

    def _stream_image(self) -> Image | None:
        """Bild aus dem Stream; `None` → screencap benutzen. Ein abgebrochener Stream wird neu gestartet."""
        if self.capture == "screencap" or self._dev is None:
            return None
        if self._stream is not None and not self._stream.alive:
            self._log.warning("Bildschirm-Stream abgebrochen – starte neu")
            self._stop_stream()
            self._start_stream()
        elif self._stream is None:
            if self._stream_retry_at is None or self._clock.monotonic() < self._stream_retry_at:
                return None
            self._start_stream()
        if self._stream is None:
            return None
        image = self._stream.latest()
        return None if image is None else self._fit(image)

    def _fit(self, image: Image) -> Image:
        """Der Encoder rundet auf Vielfache von 8 (2412 → 2408) – zurück auf die echte Bildschirmgröße,
        damit Koordinaten pixelgenau zu Taps passen."""
        if self._physical is None:
            return image
        h, w = image.shape[:2]
        short, long = self._physical
        target = (short, long) if w <= h else (long, short)
        if (w, h) == target or abs(w - target[0]) > 16 or abs(h - target[1]) > 16:
            return image  # gleich groß oder bewusst verkleinert
        return np.asarray(cv2.resize(image, target, interpolation=cv2.INTER_LINEAR), dtype=np.uint8)

    def _physical_size(self) -> tuple[int, int] | None:
        try:
            sizes = [tuple(map(int, m)) for m in _WM_SIZE.findall(self._shell("wm size"))]
        except DeviceError:
            return None
        if not sizes:
            return None
        w, h = sizes[-1]  # „Override size“ steht nach „Physical size“
        return (min(w, h), max(w, h))

    # --- intern ------------------------------------------------------------------------------------

    def _require(self) -> adbutils.AdbDevice:
        if self._dev is None:
            raise DeviceError("Gerät nicht verbunden")
        return self._dev

    def _shell(self, cmd: str | list[str]) -> str:
        dev = self._require()
        try:
            return str(dev.shell(cmd, timeout=self._timeout))
        except _ADB_ERRORS as e:
            raise DeviceError(f"adb shell {cmd!r} fehlgeschlagen: {e}") from e

    def _shell_bytes(self, cmd: list[str]) -> bytes:
        dev = self._require()
        try:
            data = dev.shell(cmd, timeout=self._timeout, encoding=None, rstrip=False)
        except _ADB_ERRORS as e:
            raise DeviceError(f"adb shell {cmd!r} fehlgeschlagen: {e}") from e
        if not isinstance(data, bytes):
            raise DeviceError(f"adb shell {cmd!r}: unerwarteter Rückgabetyp {type(data).__name__}")
        return data
