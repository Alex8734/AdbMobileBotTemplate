from pathlib import Path

import cv2
import numpy as np
import pytest

from adbbot.core.types import Point, Rect, Size
from adbbot.utils.image import save_image
from adbbot.vision.matcher import TemplateMatcher
from adbbot.vision.template import Template, TemplateStore
from tests.imagegen import paste, solid_patch, textured

BUTTON_AT = Point(100, 300)


@pytest.fixture
def button() -> np.ndarray:
    return textured(60, 40, seed=7)


@pytest.fixture
def screen(button: np.ndarray) -> np.ndarray:
    return paste(textured(360, 640, seed=1), button, BUTTON_AT)


@pytest.fixture
def matcher() -> TemplateMatcher:
    return TemplateMatcher(default_threshold=0.85)


def test_exact_match(matcher: TemplateMatcher, screen: np.ndarray, button: np.ndarray) -> None:
    match = matcher.find(screen, Template.from_image("home/button", button))
    assert match is not None
    assert match.rect == Rect(100, 300, 60, 40)
    assert match.confidence > 0.99
    assert match.label == "home/button"


def test_absent_template(matcher: TemplateMatcher, screen: np.ndarray) -> None:
    tpl = Template.from_image("other", textured(60, 40, seed=99))
    assert matcher.find(screen, tpl) is None
    best = matcher.best(screen, tpl)
    assert best is not None
    assert best.confidence < 0.85


def test_region(matcher: TemplateMatcher, screen: np.ndarray, button: np.ndarray) -> None:
    tpl = Template.from_image("b", button)
    assert matcher.find(screen, tpl, region=Rect(0, 0, 360, 300)) is None
    match = matcher.find(screen, tpl, region=Rect(80, 280, 120, 100))
    assert match is not None
    assert match.rect == Rect(100, 300, 60, 40)
    # teilweise außerhalb des Bildes → wird beschnitten
    assert matcher.find(screen, tpl, region=Rect(90, 290, 1000, 1000)) is not None


def test_template_region_and_override(
    matcher: TemplateMatcher, screen: np.ndarray, button: np.ndarray
) -> None:
    tpl = Template.from_image("b", button, region=Rect(0, 0, 360, 200))
    assert matcher.find(screen, tpl) is None
    assert matcher.find(screen, tpl, region=Rect(0, 250, 360, 200)) is not None


def test_region_smaller_than_template(
    matcher: TemplateMatcher, screen: np.ndarray, button: np.ndarray
) -> None:
    tpl = Template.from_image("b", button)
    assert matcher.best(screen, tpl, region=Rect(100, 300, 30, 30)) is None
    assert matcher.find_all(screen, tpl, region=Rect(100, 300, 30, 30)) == []


def test_threshold_precedence(matcher: TemplateMatcher, button: np.ndarray) -> None:
    assert matcher.threshold_for(Template.from_image("a", button)) == 0.85
    with_meta = Template.from_image("a", button, threshold=0.7)
    assert matcher.threshold_for(with_meta) == 0.7
    assert matcher.threshold_for(with_meta, 0.95) == 0.95


def test_best_variant_wins(matcher: TemplateMatcher, screen: np.ndarray, button: np.ndarray) -> None:
    other = Template.from_image("x", textured(60, 40, seed=42)).variants[0]
    real = Template.from_image("x", button).variants[0]
    tpl = Template("x", (other, real))
    scores = matcher.score_variants(screen, tpl)
    assert len(scores) == 2
    assert scores[1].match is not None
    assert scores[1].match.confidence > 0.99
    match = matcher.find(screen, tpl)
    assert match is not None
    assert match.rect.x == 100


def test_mask_ignores_changed_pixels(matcher: TemplateMatcher, button: np.ndarray) -> None:
    changed = button.copy()
    changed[:20, :30] = textured(30, 20, seed=123)
    screen = paste(textured(360, 640, seed=1), changed, BUTTON_AT)

    mask = np.ones((40, 60), dtype=np.uint8)
    mask[:20, :30] = 0
    masked = matcher.best(screen, Template.from_image("m", button, mask=mask))
    plain = matcher.best(screen, Template.from_image("p", button))
    assert masked is not None
    assert plain is not None
    assert masked.rect == Rect(100, 300, 60, 40)
    assert masked.confidence > 0.99
    assert plain.confidence < masked.confidence


def test_masked_template_ignores_flat_screen_areas(matcher: TemplateMatcher) -> None:
    """Einfarbige Fenster liefern bei maskiertem CCOEFF Rechenmüll (> 1) – das darf kein Treffer sein.

    Echtes Werbe-X: auf schwarzem Rand ergab es vorher Confidence 1.0.
    """
    store = TemplateStore(Path(__file__).resolve().parents[1] / "assets" / "templates")
    template = store.get("ads/close_x_left")
    glyph = template.variants[0].image

    screen = np.zeros((350, 250, 3), dtype=np.uint8)  # schwarzer Rand über dunkelgrauer Werbung
    screen[126:] = 30
    best = matcher.best(screen, template, region=Rect(0, 0, 250, 350))
    assert best is None or best.confidence < 0.85

    screen[200:] = (90, 100, 95)
    with_glyph = paste(screen, glyph, Point(120, 250))
    found = matcher.find(with_glyph, template, region=Rect(0, 0, 250, 350))
    assert found is not None
    assert found.rect == Rect(120, 250, *glyph.shape[1::-1])


def test_grayscale_matching(matcher: TemplateMatcher, screen: np.ndarray, button: np.ndarray) -> None:
    mask = np.ones((40, 60), dtype=np.uint8)
    for template in (
        Template.from_image("gray", button, grayscale=True),
        Template.from_image("gray_masked", button, mask=mask, grayscale=True),
    ):
        found = matcher.find(screen, template)
        assert found is not None
        assert found.rect == Rect(100, 300, 60, 40)
        assert found.confidence > 0.99
    other = Template.from_image("other", textured(60, 40, seed=99), grayscale=True)
    assert matcher.find(screen, other) is None


def test_flat_templates(matcher: TemplateMatcher) -> None:
    screen = paste(textured(360, 640, seed=1), solid_patch(30, 30, (0, 0, 255)), Point(50, 50))
    red = matcher.find(screen, Template.from_image("red", solid_patch(20, 20, (0, 0, 255))))
    assert red is not None
    assert red.confidence > 0.99
    assert Rect(50, 50, 30, 30).contains(red.rect.center)

    blue = matcher.best(screen, Template.from_image("blue", solid_patch(20, 20, (255, 0, 0))))
    assert blue is not None
    assert blue.confidence < 0.85


def test_find_all(matcher: TemplateMatcher, button: np.ndarray) -> None:
    screen = textured(360, 640, seed=1)
    positions = [Point(20, 50), Point(200, 50), Point(100, 400)]
    for p in positions:
        screen = paste(screen, button, p)

    tpl = Template.from_image("b", button)
    matches = matcher.find_all(screen, tpl)
    assert {(m.rect.x, m.rect.y) for m in matches} == {(p.x, p.y) for p in positions}
    assert [m.confidence for m in matches] == sorted((m.confidence for m in matches), reverse=True)
    assert len(matcher.find_all(screen, tpl, max_results=2)) == 2
    assert len(matcher.find_all(screen, tpl, region=Rect(0, 0, 360, 200))) == 2


def test_find_all_flat_template_has_no_duplicates(matcher: TemplateMatcher) -> None:
    screen = paste(textured(360, 640, seed=1), solid_patch(40, 40, (0, 255, 0)), Point(10, 10))
    matches = matcher.find_all(screen, Template.from_image("g", solid_patch(20, 20, (0, 255, 0))))
    assert 1 <= len(matches) <= 4  # Plateau: höchstens wenige, nicht überlappende Treffer


def test_scaled_matching(tmp_path: Path, matcher: TemplateMatcher) -> None:
    reference = textured(720, 1280, seed=3, cell=16)
    save_image(tmp_path / "home" / "icon.png", reference[600:680, 200:320])
    device_screen = np.asarray(
        cv2.resize(reference, (360, 640), interpolation=cv2.INTER_AREA), dtype=np.uint8
    )

    store = TemplateStore(tmp_path, reference_resolution=Size(720, 1280))
    store.set_screen_size(Size(360, 640))
    match = matcher.find(device_screen, store.get("home/icon"))
    assert match is not None
    assert abs(match.rect.x - 100) <= 1
    assert abs(match.rect.y - 300) <= 1
    assert match.confidence > 0.9
