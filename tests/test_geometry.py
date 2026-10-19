import pytest

from adbbot.core.types import Point, Rect, Size
from adbbot.utils.geometry import clip, intersection, iou, oriented, scale_factor, scale_point


def test_intersection_and_clip() -> None:
    assert intersection(Rect(0, 0, 10, 10), Rect(5, 5, 10, 10)) == Rect(5, 5, 5, 5)
    assert intersection(Rect(0, 0, 10, 10), Rect(10, 0, 5, 5)) is None
    assert clip(Rect(-5, 90, 20, 20), Size(100, 100)) == Rect(0, 90, 15, 10)
    assert clip(Rect(200, 200, 5, 5), Size(100, 100)) is None


def test_iou() -> None:
    assert iou(Rect(0, 0, 10, 10), Rect(0, 0, 10, 10)) == 1.0
    assert iou(Rect(0, 0, 10, 10), Rect(5, 0, 10, 10)) == pytest.approx(50 / 150)
    assert iou(Rect(0, 0, 10, 10), Rect(20, 20, 1, 1)) == 0.0


@pytest.mark.parametrize(
    ("reference", "actual", "factor"),
    [
        (Size(1080, 1920), Size(1080, 1920), 1.0),
        (Size(1080, 1920), Size(720, 1280), 720 / 1080),
        (Size(1080, 1920), Size(1080, 2400), 1.0),  # längeres Display, gleiche UI-Größe
        (Size(1080, 1920), Size(1920, 1080), 1.0),  # Querformat
        (Size(1080, 1920), Size(2560, 1440), 1440 / 1080),
    ],
)
def test_scale_factor(reference: Size, actual: Size, factor: float) -> None:
    assert scale_factor(reference, actual) == pytest.approx(factor)


def test_oriented_and_scale_point() -> None:
    assert oriented(Size(1080, 1920), Size(1920, 1080)) == Size(1920, 1080)
    assert oriented(Size(1080, 1920), Size(720, 1280)) == Size(1080, 1920)
    assert scale_point(Point(100, 51), 0.5) == Point(50, 26)
