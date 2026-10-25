"""Projektspezifische GUI-Buttons."""

from contextlib import nullcontext
from datetime import datetime

import cv2

from adbbot.bot.actions import action
from adbbot.bot.context import BotContext
from adbbot.bot.foreground import FOREGROUND_GROUP
from adbbot.utils.paths import screenshots_dir
from mybot.ads import dismiss_ad, watch_reward_ad
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


@action("Werbung wegdrücken (Test)", group="Test", shortcut="F8")
def test_dismiss_ad(ctx: BotContext) -> None:
    """Die gerade offene Werbung wegdrücken – X, ▶|/⏭ oder aufs Display tippen (mybot/ads.py)."""
    log = ctx.log.bind(source="action")
    current, package = ctx.device.current_app(), ctx.config.app.package
    log.info("Werbung wegdrücken", app=current, activity=ctx.device.current_activity())
    # Stimmt app.package im Profil nicht, würde der Vordergrund-Wächter die falsche App holen
    guard_off = current is not None and current != package
    if guard_off:
        log.warning(
            "app.package im Profil passt nicht zur offenen App – Vordergrund-Wächter aus", profil=package
        )
    with ctx.suspend_watchers(FOREGROUND_GROUP) if guard_off else nullcontext():
        closed = dismiss_ad(ctx)
    log.info("Ergebnis", geschlossen=closed, app=ctx.device.current_app())


@action("Bonus-Werbung abwarten (Test)", group="Test", shortcut="F9")
def test_reward_ad(ctx: BotContext) -> None:
    """Bonus-Werbung von Hand starten, dann F9: wartet Timer/Leiste ab und schließt erst danach."""
    log = ctx.log.bind(source="action")
    log.info("Bonus-Werbung abwarten", app=ctx.device.current_app(), activity=ctx.device.current_activity())
    closed = watch_reward_ad(ctx, None)
    log.info("Ergebnis", geschlossen=closed, app=ctx.device.current_app())
