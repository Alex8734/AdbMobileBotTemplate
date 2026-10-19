from adbbot.bot.element import Element, TextElement
from adbbot.bot.page import Page
from adbbot.core.types import Rect
from mybot.pages.shop_page import ShopPage


class HomePage(Page):
    """Startseite – erkennbar am HOME-Logo oben links."""

    name = "home"
    anchor = Element("home/logo", region=Rect(0, 0, 270, 100))

    shop_button = Element("home/shop_button")
    daily_gift = Element("home/daily_gift", threshold=0.9)
    gold = TextElement(Rect(330, 22, 190, 46))

    transitions = {"shop": lambda page: page.shop_button.tap()}  # für den Navigator

    def open_shop(self) -> ShopPage:
        self.shop_button.tap()
        return self.ctx.page(ShopPage).wait_until_active()
