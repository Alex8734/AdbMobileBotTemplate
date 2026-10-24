"""`DeviceProtocol`-Implementierung über `adbutils` (adb-Server-Socket)."""

from __future__ import annotations

import shlex
import struct

import adbutils
import numpy as np

from adbbot.core.clock import Clock
from adbbot.core.exceptions import DeviceError
from adbbot.core.types import Frame, Image, Point, Size
from adbbot.utils.image import decode_image

_ADB_ERRORS = (adbutils.AdbError, OSError)

# android.graphics.PixelFormat bzw. HAL-Formate, die `screencap` roh ausgibt
_RAW_RGB_FORMATS = {1, 2}  # RGBA_8888, RGBX_8888
_RAW_BGR_FORMATS = {5}  # BGRA_8888


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
    ) -> None:
        self._clock = clock
        self._requested_serial = serial
        self._timeout = command_timeout_s
        self._client = adbutils.AdbClient(host=host, port=port, socket_timeout=command_timeout_s)
        self._dev: adbutils.AdbDevice | None = None
        self._raw_screencap_ok = True

    @property
    def serial(self) -> str:
        if self._dev is not None:
            return str(self._dev.serial)
        return self._requested_serial or ""

    # --- Verbindung --------------------------------------------------------------------------------

    def connect(self) -> None:
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

    def disconnect(self) -> None:
        self._dev = None

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
