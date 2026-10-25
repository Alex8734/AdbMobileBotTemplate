"""scrcpy-Stream ohne Gerät: echtes H.264 (libx264 aus PyAV) über einen socketpair statt adb."""

import socket
import subprocess
import threading
from fractions import Fraction
from pathlib import Path
from typing import Any

import av
import numpy as np
import pytest

from adbbot.core.clock import RealClock
from adbbot.core.exceptions import DeviceError
from adbbot.core.types import Image, Size
from adbbot.device import scrcpy_stream
from adbbot.device.adb_device import AdbDevice
from adbbot.device.scrcpy_stream import H264Decoder, ScrcpyServer, ScrcpyStream, find_server
from tests.imagegen import textured

W, H = 64, 128


def encode_h264(images: list[Image]) -> bytes:
    codec = av.CodecContext.create("libx264", "w")
    codec.width, codec.height, codec.pix_fmt = W, H, "yuv420p"
    codec.time_base = Fraction(1, 30)
    codec.options = {"preset": "ultrafast", "tune": "zerolatency"}
    out = bytearray()
    for i, image in enumerate(images):
        frame = av.VideoFrame.from_ndarray(image, format="bgr24")
        frame.pts = i
        for packet in codec.encode(frame):
            out += bytes(packet)
    for packet in codec.encode(None):
        out += bytes(packet)
    return bytes(out)


def frames() -> list[Image]:
    return [textured(W, H, seed=s, cell=16) for s in (1, 2, 3)]


# --- Server finden -----------------------------------------------------------------------------------


def test_find_server_explicit(tmp_path: Path) -> None:
    server = tmp_path / "scrcpy-server"
    server.write_bytes(b"jar")
    assert find_server(server, "3.3.4") == ScrcpyServer(server, "3.3.4")
    with pytest.raises(DeviceError, match="zu alt"):
        find_server(server, "1.25")
    with pytest.raises(DeviceError, match="nicht gefunden"):
        find_server(tmp_path / "fehlt", "3.3.4")


def test_find_server_next_to_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "scrcpy"
    exe.write_text("")
    (tmp_path / "scrcpy-server").write_bytes(b"jar")
    monkeypatch.delenv(scrcpy_stream.SERVER_ENV, raising=False)
    monkeypatch.setattr("adbbot.device.scrcpy_stream.shutil.which", lambda name: str(exe))
    monkeypatch.setattr(
        "adbbot.device.scrcpy_stream.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="scrcpy 3.3.4 <https://github.com>\n"),
    )
    assert find_server() == ScrcpyServer(tmp_path / "scrcpy-server", "3.3.4")


# --- Dekoder -----------------------------------------------------------------------------------------


def test_decoder_in_small_chunks() -> None:
    images = frames()
    data = encode_h264(images)
    decoder = H264Decoder()
    decoded = []
    for i in range(0, len(data), 997):  # Pakete über Chunk-Grenzen hinweg
        frame = decoder.feed(data[i : i + 997])
        if frame is not None:
            decoded.append(frame.to_ndarray(format="bgr24"))
    decoder_tail = decoder.feed(b"")
    if decoder_tail is not None:
        decoded.append(decoder_tail.to_ndarray(format="bgr24"))
    assert decoded
    last = decoded[-1]
    assert last.shape == (H, W, 3)
    assert np.abs(last.astype(int) - images[-1].astype(int)).mean() < 12  # verlustbehaftet, aber nah dran


def test_decoder_skips_garbage() -> None:
    assert H264Decoder().feed(b"\x00\x00\x01\x09kaputt" * 10) is None


# --- Stream ------------------------------------------------------------------------------------------


class FakeShell:
    def read(self, n: int) -> bytes:
        return b""

    def close(self) -> None: ...


class FakeAdb:
    """Ersatz für adbutils.AdbDevice: create_connection liefert ein Ende eines socketpair."""

    def __init__(self, fail_first: int = 0) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.commands: list[str] = []
        self.server_side: socket.socket | None = None
        self._fail_first = fail_first
        self.sync = self

    def push(self, src: str, dst: str) -> None:
        self.pushed.append((src, dst))

    def shell(self, cmd: str, stream: bool = False) -> FakeShell:
        self.commands.append(cmd)
        return FakeShell()

    def create_connection(self, network: Any, name: str) -> socket.socket:
        client, server = socket.socketpair()
        if self._fail_first > 0:  # Server lauscht noch nicht: adb schließt sofort
            self._fail_first -= 1
            server.close()
            return client
        self.server_side = server
        server.sendall(b"\x00")  # Dummy-Byte
        return client


def ended(stream: ScrcpyStream, timeout: float = 2.0) -> bool:
    for _ in range(int(timeout / 0.01)):
        if not stream.alive:
            return True
        threading.Event().wait(0.01)
    return False


def stream_with(adb: FakeAdb) -> ScrcpyStream:
    server = ScrcpyServer(Path("/usr/share/scrcpy/scrcpy-server"), "3.3.4")
    return ScrcpyStream(adb, server, clock=RealClock(), start_timeout_s=5)


def test_stream_delivers_latest_frame() -> None:
    adb = FakeAdb(fail_first=2)
    stream = stream_with(adb)
    data = encode_h264(frames())

    def feed() -> None:
        while adb.server_side is None:
            pass
        adb.server_side.sendall(data)

    threading.Thread(target=feed, daemon=True).start()
    stream.start()
    try:
        assert adb.pushed == [("/usr/share/scrcpy/scrcpy-server", scrcpy_stream.SERVER_REMOTE)]
        command = adb.commands[0]
        assert "com.genymobile.scrcpy.Server 3.3.4" in command
        assert "audio=false" in command
        assert "control=false" in command
        assert stream.alive
        image = stream.latest()
        assert image is not None
        assert image.shape == (H, W, 3)
        assert stream.latest() is image  # gecacht bis zum nächsten Bild

        assert adb.server_side is not None
        adb.server_side.close()  # Server beendet
        assert ended(stream)
    finally:
        stream.stop()


def test_stream_without_frames_times_out() -> None:
    adb = FakeAdb()
    stream = ScrcpyStream(
        adb,
        ScrcpyServer(Path("x"), "3.3.4"),
        clock=RealClock(),
        start_timeout_s=0.3,
    )
    with pytest.raises(DeviceError, match="kein Bild"):
        stream.start()
    assert not stream.alive


# --- AdbDevice mit Stream ----------------------------------------------------------------------------


class FakeStream:
    def __init__(self, image: Image | None, alive: bool = True) -> None:
        self.image, self.alive, self.stopped = image, alive, False

    def latest(self) -> Image | None:
        return self.image

    def stop(self) -> None:
        self.stopped = True


def adb_device(capture: Any = "auto") -> AdbDevice:
    device = AdbDevice(clock=RealClock(), capture=capture)
    device._dev = object()
    device._physical = (1080, 2412)
    return device


def test_stream_frame_is_scaled_to_real_screen_size() -> None:
    device = adb_device()
    device._stream = FakeStream(np.zeros((2408, 1080, 3), dtype=np.uint8))  # type: ignore[assignment]
    assert device.screenshot().size == Size(1080, 2412)
    device._stream = FakeStream(np.zeros((1080, 2408, 3), dtype=np.uint8))  # type: ignore[assignment]
    assert device.screenshot().size == Size(2412, 1080)  # quer
    device._stream = FakeStream(np.zeros((960, 432, 3), dtype=np.uint8))  # type: ignore[assignment]
    assert device.screenshot().size == Size(432, 960)  # bewusst verkleinert: so lassen


def test_dead_stream_is_restarted(monkeypatch: pytest.MonkeyPatch) -> None:
    device = adb_device()
    dead = FakeStream(None, alive=False)
    fresh = FakeStream(np.zeros((2412, 1080, 3), dtype=np.uint8))
    device._stream = dead  # type: ignore[assignment]

    def restart() -> bool:
        device._stream = fresh  # type: ignore[assignment]
        return True

    monkeypatch.setattr(device, "_start_stream", restart)
    assert device.screenshot().size == Size(1080, 2412)
    assert dead.stopped


def test_auto_falls_back_when_scrcpy_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object) -> ScrcpyServer:
        raise DeviceError("scrcpy-server nicht gefunden")

    monkeypatch.setattr("adbbot.device.adb_device.find_server", missing)
    device = adb_device("auto")
    assert not device._start_stream()
    assert device._stream is None

    strict = adb_device("scrcpy")
    with pytest.raises(DeviceError, match="nicht gefunden"):
        strict._start_stream()
