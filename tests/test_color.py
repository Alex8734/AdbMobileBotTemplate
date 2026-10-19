import numpy as np
import pytest

from adbbot.core.types import Point, Rect
from adbbot.vision.color import Rgb, color_distance, color_matches, color_ratio, mean_color, pixel_at


@pytest.fixture
def image() -> np.ndarray:
    img = np.zeros((10, 20, 3), dtype=np.uint8)
    img[:, :10] = (30, 20, 10)  # BGR → RGB (10, 20, 30)
    img[:, 10:] = (0, 0, 255)  # rot
    return img


def test_pixel_is_rgb(image: np.ndarray) -> None:
    assert pixel_at(image, Point(0, 0)) == Rgb(10, 20, 30)
    assert pixel_at(image, Point(15, 5)) == (255, 0, 0)
    with pytest.raises(ValueError):
        pixel_at(image, Point(20, 0))


def test_color_matches_tolerance(image: np.ndarray) -> None:
    assert color_distance((10, 20, 30), (15, 20, 10)) == 20
    assert color_matches(image, Point(0, 0), (20, 20, 30), tolerance=10)
    assert not color_matches(image, Point(0, 0), (21, 20, 30), tolerance=10)


def test_color_ratio_and_mean(image: np.ndarray) -> None:
    assert color_ratio(image, Rect(0, 0, 20, 10), Rgb(255, 0, 0)) == 0.5
    assert color_ratio(image, Rect(10, 0, 10, 10), (250, 5, 5), tolerance=5) == 1.0
    assert mean_color(image, Rect(10, 0, 10, 10)) == Rgb(255, 0, 0)
    with pytest.raises(ValueError):
        color_ratio(image, Rect(50, 50, 5, 5), (0, 0, 0))


def test_hex() -> None:
    assert Rgb.from_hex("#ff8800") == Rgb(255, 136, 0)
    assert Rgb(255, 136, 0).hex() == "#ff8800"
    with pytest.raises(ValueError):
        Rgb.from_hex("#fff")
