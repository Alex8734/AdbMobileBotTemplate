"""AdbDevice ohne echtes Gerät: Dekodierung der screencap-Rohdaten."""

import struct

import numpy as np
import pytest

from adbbot.core.exceptions import DeviceError
from adbbot.device.adb_device import decode_raw_screencap


def raw(rgba: np.ndarray, fmt: int, header_size: int) -> bytes:
    h, w = rgba.shape[:2]
    header = struct.pack("<III", w, h, fmt)
    if header_size == 16:
        header += struct.pack("<I", 1)  # colorspace
    return header + rgba.tobytes()


@pytest.mark.parametrize("header_size", [12, 16])
def test_rgba_to_bgr(header_size: int) -> None:
    rgba = np.zeros((3, 2, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2], rgba[..., 3] = 10, 20, 30, 255
    image = decode_raw_screencap(raw(rgba, fmt=1, header_size=header_size))
    assert image.shape == (3, 2, 3)
    assert image.flags["C_CONTIGUOUS"]
    assert tuple(image[0, 0]) == (30, 20, 10)


def test_bgra_format() -> None:
    bgra = np.zeros((2, 2, 4), dtype=np.uint8)
    bgra[..., 0], bgra[..., 1], bgra[..., 2] = 1, 2, 3
    assert tuple(decode_raw_screencap(raw(bgra, fmt=5, header_size=16))[1, 1]) == (1, 2, 3)


def test_png_data_is_rejected() -> None:
    with pytest.raises(DeviceError):
        decode_raw_screencap(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)


def test_unsupported_format() -> None:
    with pytest.raises(DeviceError, match="Pixelformat"):
        decode_raw_screencap(raw(np.zeros((2, 2, 4), dtype=np.uint8), fmt=4, header_size=12))


def test_too_short() -> None:
    with pytest.raises(DeviceError):
        decode_raw_screencap(b"\x00\x01")
