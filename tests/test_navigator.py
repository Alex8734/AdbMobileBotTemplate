from pathlib import Path
from typing import Any

import pytest

from adbbot.bot.context import BotContext
from adbbot.bot.element import Element
from adbbot.bot.navigator import Navigator
from adbbot.bot.page import Page
from adbbot.core.clock import FakeClock
from adbbot.core.exceptions import BotError, NavigationError
from adbbot.core.types import Image, Point, Rect
from adbbot.device.protocol import KEY_BACK
from tests.botkit import fake, make_context
from tests.imagegen import paste, textured

TOP = Rect(0, 0, 200, 60)


class NavHome(Page):
    name = "home"
    anchor = Element("home/logo", region=TOP)
    shop_button = Element("home/shop_button")
    gear = Element("home/gear")
    transitions = {  # noqa: RUF012
        "shop": lambda page: page.shop_button.tap(),
        "settings": lambda page: page.gear.tap(),
    }


class NavShop(Page):
    name = "shop"
    anchor = Element("shop/logo", region=TOP)
    close = Element("shop/close")
    transitions = {"home": lambda page: page.close.tap()}  # noqa: RUF012


class NavSettings(Page):
    name = "settings"
    anchor = Element("settings/logo", region=TOP)
    transitions = {NavHome: lambda page: page.ctx.device.key(KEY_BACK)}  # noqa: RUF012


class NavPopup(Page):
    name = "popup"
    priority = 10
    anchor = Element("popup/logo")
    transitions = {"home": lambda page: page.ctx.device.key(KEY_BACK)}  # noqa: RUF012


def world_images() -> tuple[dict[str, Image], dict[str, Image]]:
    bg = textured(360, 640, seed=1)
    t = {
        "home/logo": textured(80, 30, seed=21),
        "shop/logo": textured(80, 30, seed=22),
        "settings/logo": textured(80, 30, seed=23),
        "popup/logo": textured(80, 30, seed=24),
        "home/shop_button": textured(60, 40, seed=31),
        "home/gear": textured(40, 40, seed=32),
        "shop/close": textured(40, 40, seed=33),
    }
    home = paste(
        paste(paste(bg, t["home/logo"], Point(10, 10)), t["home/shop_button"], Point(100, 300)),
        t["home/gear"],
        Point(300, 300),
    )
    screens = {
        "home": home,
        "shop": paste(paste(bg, t["shop/logo"], Point(10, 10)), t["shop/close"], Point(300, 10)),
        "settings": paste(bg, t["settings/logo"], Point(10, 10)),
        "home_popup": paste(home, t["popup/logo"], Point(150, 500)),
        "unknown": bg,
        "stuck": textured(360, 640, seed=99),
    }
    return screens, t


SCENARIO: dict[str, Any] = {
    "start": "home",
    "start_app": "home",
    "transitions": {
        "home": [
            {"tap": [100, 300, 60, 40], "goto": "shop"},
            {"tap": [300, 300, 40, 40], "goto": "settings"},
        ],
        "shop": [{"tap": [300, 10, 40, 40], "goto": "home"}],
        "settings": [{"key": KEY_BACK, "goto": "home"}],
        "home_popup": [{"key": KEY_BACK, "goto": "home"}],
        "unknown": [{"key": KEY_BACK, "goto": "home"}],
    },
}


def make(
    tmp_path: Path, clock: FakeClock, *, scenario: dict[str, Any] = SCENARIO, **config: Any
) -> BotContext:
    screens, templates = world_images()
    ctx = make_context(
        tmp_path,
        clock=clock,
        screens=screens,
        templates=templates,
        scenario=scenario,
        config={"bot": {"navigation_max_back": 2, **config}},
    )
    for cls in (NavHome, NavShop, NavSettings, NavPopup):
        ctx.pages.register(cls)
    return ctx


@pytest.fixture
def ctx(tmp_path: Path, fake_clock: FakeClock) -> BotContext:
    return make(tmp_path, fake_clock)


def kinds(ctx: BotContext) -> list[str]:
    return [record.kind for record in fake(ctx).inputs]


def test_current_page(ctx: BotContext) -> None:
    nav = ctx.navigator
    assert nav is ctx.navigator  # einmal pro Context
    for screen, expected in [
        ("home", NavHome),
        ("shop", NavShop),
        ("settings", NavSettings),
        ("home_popup", NavPopup),  # Popup hat Priorität vor der halb verdeckten Startseite
        ("unknown", None),
    ]:
        fake(ctx).show(screen)
        assert nav.current_page() is expected, screen


def test_path(ctx: BotContext) -> None:
    nav = ctx.navigator
    assert nav.path(NavSettings, NavShop) == [NavSettings, NavHome, NavShop]
    assert nav.path(NavShop, NavSettings) == [NavShop, NavHome, NavSettings]
    assert nav.path(NavHome, NavHome) == [NavHome]
    assert nav.path(NavHome, NavPopup) is None


def test_go_to_over_several_pages(ctx: BotContext) -> None:
    fake(ctx).show("settings")
    page = ctx.navigator.go_to(NavShop)
    assert isinstance(page, NavShop)
    assert fake(ctx).current_screen == "shop"
    assert kinds(ctx) == ["key", "tap"]


def test_go_to_current_page_does_nothing(ctx: BotContext) -> None:
    ctx.navigator.go_to(NavHome)
    assert kinds(ctx) == []


def test_popup_is_closed_on_the_way(ctx: BotContext) -> None:
    fake(ctx).show("home_popup")
    ctx.navigator.go_to(NavShop)
    assert fake(ctx).current_screen == "shop"
    assert kinds(ctx) == ["key", "tap"]


def test_unknown_screen_back_helps(ctx: BotContext) -> None:
    fake(ctx).show("unknown")
    ctx.navigator.go_to(NavShop)
    assert kinds(ctx) == ["key", "tap"]


def test_unknown_screen_restarts_app(ctx: BotContext) -> None:
    fake(ctx).show("stuck")
    ctx.navigator.go_to(NavShop)
    assert kinds(ctx) == ["key", "key", "stop_app", "start_app", "tap"]
    assert fake(ctx).current_screen == "shop"


def test_unknown_screen_without_restart(tmp_path: Path, fake_clock: FakeClock) -> None:
    ctx = make(tmp_path, fake_clock, navigation_restart_app=False)
    fake(ctx).show("stuck")
    with pytest.raises(NavigationError, match="Zurück hat nicht geholfen"):
        ctx.navigator.go_to(NavShop)
    assert kinds(ctx) == ["key", "key"]


def test_unreachable_and_unregistered(ctx: BotContext) -> None:
    with pytest.raises(NavigationError, match="Kein Weg"):
        ctx.navigator.go_to(NavPopup)

    class Unregistered(Page):
        anchor = Element("x/y")

    with pytest.raises(NavigationError, match="nicht registriert"):
        ctx.navigator.go_to(Unregistered)


def test_transition_to_unknown_page_name(ctx: BotContext) -> None:
    class Broken(Page):
        name = "broken"
        anchor = Element("home/gear")
        transitions = {"gibts_nicht": lambda page: None}  # noqa: RUF012

    ctx.pages.register(Broken)
    with pytest.raises(BotError, match="gibts_nicht"):
        ctx.navigator.go_to(NavShop)


def test_gives_up_after_max_hops(tmp_path: Path, fake_clock: FakeClock) -> None:
    broken = {**SCENARIO, "transitions": {**SCENARIO["transitions"], "shop": []}}  # Schließen wirkt nicht
    ctx = make(tmp_path, fake_clock, scenario=broken)
    fake(ctx).show("shop")
    nav = Navigator(ctx, transition_timeout_s=0.3, max_hops=3)
    with pytest.raises(NavigationError, match="nach 3 Übergängen"):
        nav.go_to(NavHome)
    assert kinds(ctx) == ["tap", "tap", "tap"]
