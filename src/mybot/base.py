"""Projekt-Basisklasse für Flows: typisierte Page-Shortcuts und Erholung zwischen Retries."""

from adbbot.bot.flow import Flow
from adbbot.core.exceptions import StepFailed
from mybot.pages.home_page import HomePage
from mybot.pages.shop_page import ShopPage


class ProjectFlow(Flow):
    @property
    def home(self) -> HomePage:
        return self.ctx.page(HomePage)

    @property
    def shop(self) -> ShopPage:
        return self.ctx.page(ShopPage)

    def recover(self, error: StepFailed) -> None:
        """Zwischen zwei Versuchen: bekannte Dialoge schließen."""
        self.log.info("Erholung nach Fehler", error=str(error))
        frame = self.vision.capture()
        self.tap_if_present("common/close_x", frame=frame)
        self.tap_if_present("common/rate_later", frame=frame)
