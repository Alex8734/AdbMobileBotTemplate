"""Projektspezifische GUI-Buttons."""

from datetime import datetime

import cv2

from adbbot.bot.actions import action
from adbbot.bot.context import BotContext
from adbbot.utils.paths import screenshots_dir
from mybot.pages.home_page import HomePage


@action("Screenshot speichern", group="Debug")
def save_screenshot(ctx: BotContext) -> None:
    """Aktuellen Bildschirm nach data/screenshots/ speichern."""
    path = screenshots_dir(ctx.root) / f"{datetime.now():%Y%m%d-%H%M%S}.png"
    ctx.vision.capture().save(path)
    ctx.log.info("Screenshot gespeichert", source="action", path=str(path))


@action("Debug-Ansichten erzeugen", group="Debug")
def debug_views(ctx: BotContext) -> None:
    """Beispiel für vision.debug_image: Zwischenbilder erscheinen im Dropdown „Ansicht“ über dem Livebild."""
    frame = ctx.vision.capture()
    gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
    ctx.vision.debug_image("Graustufen", gray)
    ctx.vision.debug_image("Kanten (Canny)", cv2.Canny(gray, 80, 160))
    logo = ctx.vision.templates.get("home/logo").variants[0].image
    ctx.vision.debug_image("Heatmap home/logo", cv2.matchTemplate(frame.image, logo, cv2.TM_CCOEFF_NORMED))
    ctx.log.info("Debug-Ansichten erzeugt", source="action")


@action("Shop öffnen (Test)", group="Test")
def test_shop(ctx: BotContext) -> None:
    """Von der Startseite in den Shop wechseln."""
    ctx.page(HomePage).open_shop()
