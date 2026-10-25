"""Bildschirm als Video-Stream über den scrcpy-Server – statt einzelner `screencap`-Aufrufe.

`screencap` überträgt jedes Bild unkomprimiert (1080x2412 ≈ 10 MB), über USB dauert das ~1 s. scrcpy kodiert
auf dem Gerät als H.264 und schickt nur Änderungen: bis zu 30–60 Bilder/s bei ~50–100 ms Latenz.

Ablauf: Server-JAR der installierten scrcpy-Version nach /data/local/tmp schieben, per `app_process` starten,
über adb direkt mit seinem Abstract-Socket verbinden (kein Port-Forwarding) und H.264 mit PyAV dekodieren.
Ein Lese-Thread hält immer das neueste Bild bereit; die Umwandlung nach BGR passiert erst bei `latest()`.

Server und Client müssen exakt dieselbe Version haben – sie kommt aus `scrcpy --version` (oder der Config).
"""

from __future__ import annotations

import contextlib
import os
import random
import re
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import adbutils
import av
import numpy as np

from adbbot.core.clock import Clock
from adbbot.core.exceptions import DeviceError
from adbbot.core.types import Image

SERVER_REMOTE = "/data/local/tmp/adbbot-scrcpy-server.jar"
SERVER_ENV = "SCRCPY_SERVER_PATH"  # dieselbe Variable nutzt auch scrcpy selbst
SERVER_LOCATIONS = (
    Path("/usr/share/scrcpy/scrcpy-server"),
    Path("/usr/local/share/scrcpy/scrcpy-server"),
    Path("/opt/homebrew/share/scrcpy/scrcpy-server"),
    Path("/snap/scrcpy/current/usr/share/scrcpy/scrcpy-server"),
)
_VERSION = re.compile(r"scrcpy\s+v?(\d+\.\d+(?:\.\d+)?)")
_RECV_SIZE = 1 << 16


@dataclass(frozen=True, slots=True)
class ScrcpyServer:
    path: Path
    version: str


def find_server(path: Path | None = None, version: str | None = None) -> ScrcpyServer:
    """Server-Datei und Version finden: Config → $SCRCPY_SERVER_PATH → neben `scrcpy` → übliche Orte."""
    executable = shutil.which("scrcpy")
    candidates = [path] if path is not None else []
    if path is None:
        if env := os.environ.get(SERVER_ENV):
            candidates.append(Path(env))
        if executable:
            candidates.append(Path(executable).resolve().parent / "scrcpy-server")
        candidates.extend(SERVER_LOCATIONS)
    server = next((c for c in candidates if c.is_file()), None)
    if server is None:
        raise DeviceError(
            "scrcpy-server nicht gefunden – scrcpy installieren oder device.scrcpy_server setzen "
            f"(gesucht: {', '.join(str(c) for c in candidates)})"
        )
    version = version or _installed_version(executable)
    major = int(version.split(".", 1)[0])
    if major < 2:
        raise DeviceError(f"scrcpy {version} ist zu alt – mindestens 2.0 nötig")
    return ScrcpyServer(server, version)


def _installed_version(executable: str | None) -> str:
    if executable is None:
        raise DeviceError("scrcpy-Version unbekannt – scrcpy nicht im PATH, device.scrcpy_version setzen")
    try:
        out = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DeviceError(f"scrcpy --version fehlgeschlagen: {e}") from e
    m = _VERSION.search(out)
    if m is None:
        raise DeviceError(f"scrcpy-Version nicht lesbar aus: {out.strip()[:80]!r}")
    return m.group(1)


class H264Decoder:
    """Roher H.264-Bytestrom (Annex B) → Videoframes. Kaputte Pakete werden übersprungen."""

    def __init__(self) -> None:
        self._codec = av.CodecContext.create("h264", "r")

    def feed(self, data: bytes) -> av.VideoFrame | None:
        """Letztes vollständig dekodierte Bild aus diesem Datenstück, sonst `None`."""
        latest: av.VideoFrame | None = None
        for packet in self._codec.parse(data):
            try:
                for frame in self._codec.decode(packet):
                    latest = frame
            except av.error.FFmpegError:
                continue  # z. B. Daten vor dem ersten Keyframe
        return latest


class ScrcpyStream:
    """Ein laufender scrcpy-Server pro Gerät. `start()` wartet auf das erste Bild, `stop()` räumt auf."""

    def __init__(
        self,
        dev: adbutils.AdbDevice,
        server: ScrcpyServer,
        *,
        clock: Clock,
        max_fps: int = 30,
        bit_rate: int = 8_000_000,
        start_timeout_s: float = 10.0,
    ) -> None:
        self._dev = dev
        self.server = server
        self._clock = clock
        self._max_fps = max_fps
        self._bit_rate = bit_rate
        self._start_timeout_s = start_timeout_s
        self._lock = threading.Lock()
        self._first_frame = threading.Event()
        self._frame: av.VideoFrame | None = None
        self._image: Image | None = None
        self._frames = 0
        self._sock: socket.socket | None = None
        self._shell: adbutils.AdbConnection | None = None
        self._server_log: list[bytes] = []
        self._reader: threading.Thread | None = None
        self._stopped = False
        self._alive = False

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def frame_count(self) -> int:
        with self._lock:
            return self._frames

    def start(self) -> None:
        scid = random.randint(0, 0x7FFFFFFF)
        try:
            self._dev.sync.push(str(self.server.path), SERVER_REMOTE)
            self._shell = self._dev.shell(self._command(scid), stream=True)
        except (adbutils.AdbError, OSError) as e:
            raise DeviceError(f"scrcpy-Server nicht startbar: {e}") from e
        threading.Thread(target=self._collect_log, name="scrcpy-log", daemon=True).start()

        self._sock = self._connect(f"scrcpy_{scid:08x}")
        self._alive = True
        self._reader = threading.Thread(target=self._read, name="scrcpy-stream", daemon=True)
        self._reader.start()
        if not self._first_frame.wait(self._start_timeout_s):
            self.stop()
            raise DeviceError(f"scrcpy: kein Bild nach {self._start_timeout_s:g} s{self._log_hint()}")

    def latest(self) -> Image | None:
        """Neuestes Bild als BGR-Array (wird gecacht, bis ein neues Bild kommt)."""
        with self._lock:
            if self._image is None and self._frame is not None:
                self._image = np.ascontiguousarray(self._frame.to_ndarray(format="bgr24"))
            return self._image

    def stop(self) -> None:
        self._stopped = True
        self._alive = False
        for closable in (self._sock, self._shell):
            if closable is not None:
                with contextlib.suppress(OSError):
                    closable.close()
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=2)

    # --- intern ------------------------------------------------------------------------------------

    def _command(self, scid: int) -> str:
        options = {
            "scid": f"{scid:08x}",
            "tunnel_forward": "true",
            "audio": "false",
            "control": "false",
            "cleanup": "true",
            "video_codec": "h264",
            "max_fps": str(self._max_fps),
            "video_bit_rate": str(self._bit_rate),
            # nur der rohe H.264-Strom; das Dummy-Byte meldet, dass der Server bereit ist
            "send_device_meta": "false",
            "send_frame_meta": "false",
            "send_codec_meta": "false",
            "send_dummy_byte": "true",
            "log_level": "info",
        }
        args = " ".join(f"{k}={v}" for k, v in options.items())
        server = f"CLASSPATH={SERVER_REMOTE} app_process / com.genymobile.scrcpy.Server"
        return f"{server} {self.server.version} {args}"

    def _connect(self, name: str) -> socket.socket:
        """Bis der Server lauscht, schließt adb die Verbindung sofort wieder – erst das Dummy-Byte zählt."""
        deadline = self._clock.monotonic() + self._start_timeout_s
        last_error: Exception | None = None
        while self._clock.monotonic() < deadline:
            try:
                sock: socket.socket = self._dev.create_connection(adbutils.Network.LOCAL_ABSTRACT, name)
                sock.settimeout(self._start_timeout_s)
                if sock.recv(1):
                    sock.settimeout(None)
                    return sock
                sock.close()
            except (adbutils.AdbError, OSError) as e:
                last_error = e
            self._clock.sleep(0.1)
        self.stop()
        raise DeviceError(f"scrcpy: keine Verbindung zum Server ({last_error}){self._log_hint()}")

    def _read(self) -> None:
        decoder = H264Decoder()
        sock = self._sock
        assert sock is not None
        try:
            while not self._stopped:
                data = sock.recv(_RECV_SIZE)
                if not data:
                    break
                frame = decoder.feed(data)
                if frame is not None:
                    with self._lock:
                        self._frame, self._image = frame, None
                        self._frames += 1
                    self._first_frame.set()
        except OSError:
            pass
        finally:
            self._alive = False

    def _collect_log(self) -> None:
        shell = self._shell
        if shell is None:
            return
        try:
            while chunk := shell.read(4096):
                self._server_log.append(chunk)
        except (adbutils.AdbError, OSError):
            pass

    def _log_hint(self) -> str:
        text = b"".join(self._server_log).decode("utf-8", "replace").strip()
        return f" – Server: {text[-300:]}" if text else ""
