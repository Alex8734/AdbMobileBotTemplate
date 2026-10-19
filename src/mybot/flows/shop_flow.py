from adbbot.bot.flow import step
from mybot.base import ProjectFlow
from mybot.pages.home_page import HomePage
from mybot.pages.shop_page import ShopPage


class ShopFlow(ProjectFlow):
    """Mit dem Navigator in den Shop und zurück – Beispiel für Page.transitions und den Storage."""

    name = "shop"
    description = "Per Navigator in den Shop und zurück"

    def run(self) -> None:
        self.ensure_app_running()
        self.open_shop()
        self.back_home()

    @step("Shop per Navigator öffnen")
    def open_shop(self) -> None:
        self.go_to(ShopPage)
        self.log.info("Shop geöffnet", besuche=self.store.increment("shop_visits"))

    @step("Zurück zur Startseite")
    def back_home(self) -> None:
        self.go_to(HomePage)
