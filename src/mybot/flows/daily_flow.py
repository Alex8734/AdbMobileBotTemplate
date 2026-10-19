from adbbot.bot.flow import step
from adbbot.core.exceptions import StepSkipped
from adbbot.device.protocol import KEY_BACK
from adbbot.vision.ocr import NoOcr
from mybot.base import ProjectFlow


class DailyFlow(ProjectFlow):
    """Startet die App, holt das Tagesgeschenk ab und schaut im Shop vorbei."""

    name = "daily"
    description = "Tägliche Belohnungen einsammeln"

    def run(self) -> None:
        self.ensure_app_running()
        self.go_to_home()
        self.read_gold()
        self.collect_daily_gift()
        self.visit_shop()

    @step("Zur Startseite navigieren", retries=2)
    def go_to_home(self) -> None:
        if not self.home.is_active():
            self.device.key(KEY_BACK)
            self.home.wait_until_active(timeout=10)

    @step("Gold lesen", optional=True)
    def read_gold(self) -> None:
        if isinstance(self.vision.ocr, NoOcr):
            raise StepSkipped("Keine OCR-Engine konfiguriert (vision.ocr_engine)")
        self.log.info("Kontostand", gold=self.home.gold.read_number())

    @step("Tagesgeschenk abholen")
    def collect_daily_gift(self) -> None:
        # Mit --params '{"once_per_day": true}' merkt sich der Bot im Storage, dass es heute erledigt ist
        if self.params.get("once_per_day") and self.store.done_today("daily_gift"):
            raise StepSkipped("Heute schon abgeholt")
        if not self.home.daily_gift.exists():
            raise StepSkipped("Kein Geschenk verfügbar")
        self.home.daily_gift.tap()
        self.tap("popup/claim_button")
        self.store.mark_done_today("daily_gift")

    @step("Shop besuchen")
    def visit_shop(self) -> None:
        shop = self.home.open_shop()  # ein Bewertungs-Popup davor schließt der Watcher
        shop.close()
