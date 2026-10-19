from adbbot.bot.element import Element
from adbbot.bot.page import Page
from adbbot.core.types import Rect


class ShopPage(Page):
    """Shop – erkennbar am SHOP-Logo oben links."""

    name = "shop"
    anchor = Element("shop/logo", region=Rect(0, 0, 270, 100))

    close_button = Element("common/close_x", region=Rect(400, 0, 140, 100))

    transitions = {"home": lambda page: page.close_button.tap()}  # für den Navigator

    def close(self) -> None:
        self.close_button.tap()
        self.anchor.wait_gone()
