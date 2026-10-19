from dataclasses import dataclass, field
from pathlib import Path

import pytest

from adbbot.bot.context import BotContext
from adbbot.bot.element import (
    BoundColorElement,
    BoundElement,
    BoundTextElement,
    ColorElement,
    Element,
    TextElement,
)
from adbbot.bot.page import Page, PageRegistry
from adbbot.core.clock import FakeClock
from adbbot.core.exceptions import BotError, BotStopped, ElementNotFound, StepTimeout, WrongPage
from adbbot.core.types import Image, Point, Rect
from adbbot.vision.ocr import OcrResult
from tests.botkit import fake, make_context
from tests.imagegen import paste, solid_patch, textured


class HomePage(Page):
    anchor = Element("home/logo", region=Rect(0, 0, 360, 100))
    button = Element("home/button")
    alternative = Element(["common/popup", "home/button"], name="alt")
    gold = TextElement(Rect(10, 50, 100, 20))
    red_dot = ColorElement("#ff0000", point=Point(350, 630))
    red_area = ColorElement((255, 0, 0), region=Rect(340, 620, 20, 20))


class OtherPage(Page):
    name = "other_custom"
    anchor = Element("common/popup")


class NoAnchorPage(Page):
    button = Element("home/button")


@pytest.fixture
def ctx(tmp_path: Path, fake_clock: FakeClock) -> BotContext:
    background = textured(360, 640, seed=1)
    logo, button, popup = textured(80, 30, seed=11), textured(60, 40, seed=7), textured(50, 30, seed=8)
    home = paste(
        paste(paste(background, logo, Point(10, 10)), button, Point(100, 300)),
        solid_patch(20, 20, (0, 0, 255)),
        Point(340, 620),
    )
    return make_context(
        tmp_path,
        clock=fake_clock,
        screens={"home": home, "other": paste(background, popup, Point(200, 50))},
        templates={"home/logo": logo, "home/button": button, "common/popup": popup},
        scenario={"transitions": {"home": [{"tap": [100, 300, 60, 40], "goto": "other"}]}},
    )


def test_names_and_descriptors() -> None:
    assert HomePage.name == "home"
    assert OtherPage.name == "other_custom"
    assert isinstance(HomePage.button, Element)
    assert HomePage.button.label == "home.button"
    assert HomePage.alternative.name == "alt"
    assert HomePage.template_names() == {"home/logo", "home/button", "common/popup"}
    assert Element("common/close_x").name == "close_x"


def test_bound_access(ctx: BotContext) -> None:
    page = ctx.page(HomePage)
    assert ctx.page(HomePage) is page
    assert isinstance(page.button, BoundElement)
    assert isinstance(page.gold, BoundTextElement)
    assert isinstance(page.red_dot, BoundColorElement)


def test_page_activity(ctx: BotContext) -> None:
    home = ctx.page(HomePage)
    assert home.is_active()
    assert home.wait_until_active() is home
    fake(ctx).show("other")
    assert not home.is_active()
    with pytest.raises(WrongPage, match="home"):
        home.wait_until_active(timeout=0.3)
    with pytest.raises(WrongPage):
        home.ensure_active()
    assert ctx.page(OtherPage).ensure_active()


def test_tap_center_without_humanize(ctx: BotContext) -> None:
    match = ctx.page(HomePage).button.tap()
    assert match.rect == Rect(100, 300, 60, 40)
    assert fake(ctx).inputs[-1].args == (Point(130, 320),)
    assert fake(ctx).current_screen == "other"  # Szenario hat reagiert


def test_tap_random_point_with_humanize(tmp_path: Path, fake_clock: FakeClock) -> None:
    button = textured(60, 40, seed=7)
    ctx = make_context(
        tmp_path,
        clock=fake_clock,
        screens={"s": paste(textured(360, 640, seed=1), button, Point(100, 300))},
        templates={"home/button": button},
        config={"bot": {"humanize_input": True}},
    )
    points = set()
    for _ in range(30):
        ctx.element("home/button").tap()
        points.add(fake(ctx).inputs[-1].args[0])
    assert all(115 <= p.x <= 145 and 310 <= p.y <= 330 for p in points)
    assert len(points) > 1


def test_missing_element(ctx: BotContext) -> None:
    fake(ctx).show("other")
    page = ctx.page(HomePage)
    assert not page.button.tap_if_present()
    assert fake(ctx).inputs == []
    with pytest.raises(ElementNotFound, match=r"home\.button nicht gefunden nach 0.5 s"):
        page.button.wait(timeout=0.5)


def test_alternatives(ctx: BotContext) -> None:
    match = ctx.page(HomePage).alternative.find()
    assert match is not None
    assert match.label == "home/button"


def test_wait_gone(ctx: BotContext) -> None:
    page = ctx.page(HomePage)
    with pytest.raises(StepTimeout, match="noch sichtbar"):
        page.button.wait_gone(timeout=0.3)
    fake(ctx).show("other")
    page.button.wait_gone(timeout=0.3)


@dataclass
class FakeOcr:
    text: str
    calls: list[Image] = field(default_factory=list)

    def read(self, image: Image) -> list[OcrResult]:
        self.calls.append(image)
        return [OcrResult(self.text, Rect(0, 0, 10, 10), 0.9)]


def test_text_element(ctx: BotContext) -> None:
    ctx.vision.ocr = FakeOcr("Gold 1,250")
    page = ctx.page(HomePage)
    assert page.gold.read_text() == "Gold 1,250"
    assert page.gold.read_number() == 1250


def test_color_elements(ctx: BotContext) -> None:
    page = ctx.page(HomePage)
    assert page.red_dot.exists()
    assert page.red_area.exists()
    page.red_area.wait(timeout=0.1)
    fake(ctx).show("other")
    assert not page.red_dot.exists()
    with pytest.raises(ElementNotFound, match="#ff0000"):
        page.red_area.wait(timeout=0.3)
    page.red_dot.wait_gone(timeout=0.1)
    with pytest.raises(ValueError):
        ColorElement("#ff0000")


def test_element_access_shortcuts(ctx: BotContext) -> None:
    page = ctx.page(HomePage)
    assert page.exists("home/button")
    assert not page.exists("common/popup")
    page.tap("home/button")
    assert fake(ctx).current_screen == "other"
    assert page.wait_for("common/popup").label == "common/popup"


def test_page_without_anchor(ctx: BotContext) -> None:
    assert not NoAnchorPage.has_anchor()
    with pytest.raises(TypeError, match="anchor"):
        ctx.page(NoAnchorPage).is_active()
    with pytest.raises(BotError, match="anchor"):
        PageRegistry().register(NoAnchorPage)


def test_page_registry_duplicates() -> None:
    class Duplicate(Page):
        name = "home"
        anchor = Element("x/y")

    registry = PageRegistry()
    registry.register(HomePage)
    registry.register(HomePage)
    with pytest.raises(BotError, match="doppelt"):
        registry.register(Duplicate)
    assert registry.get_class("home") is HomePage
    with pytest.raises(BotError, match="Vorhanden: home"):
        registry.get_class("nope")


def test_ctx_sleep_is_interruptible(ctx: BotContext) -> None:
    ctx.control.request_stop()
    with pytest.raises(BotStopped):
        ctx.sleep(10)
