import random
from pathlib import Path

import numpy as np

from adbbot.core.types import Frame, Point, Rect, Size
from adbbot.utils.image import load_image


def test_rect_geometry() -> None:
    r = Rect(10, 20, 100, 50)
    assert r.center == Point(60, 45)
    assert (r.right, r.bottom) == (110, 70)
    assert r.contains(Point(10, 20))
    assert not r.contains(Point(110, 20))
    assert r.scaled(0.5) == Rect(5, 10, 50, 25)


def test_random_point_respects_margin() -> None:
    r = Rect(0, 0, 100, 100)
    rng = random.Random(1)
    for _ in range(500):
        p = r.random_point(margin=0.2, rng=rng)
        assert 20 <= p.x <= 79
        assert 20 <= p.y <= 79


def test_random_point_on_tiny_rect() -> None:
    assert Rect(5, 5, 1, 1).random_point(rng=random.Random(0)) == Point(5, 5)


def test_frame_size_crop_and_save(tmp_path: Path) -> None:
    image = np.arange(60 * 40 * 3, dtype=np.uint32).reshape(60, 40, 3).astype(np.uint8)
    frame = Frame(image, ts=1.0)
    assert frame.size == Size(40, 60)

    crop = frame.crop(Rect(5, 10, 20, 30))
    assert crop.size == Size(20, 30)
    assert crop.ts == 1.0

    path = tmp_path / "sub dir" / "äöü.png"
    frame.save(path)
    assert np.array_equal(load_image(path), image)
