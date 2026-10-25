"""Popups und Störungen, die in jedem Schritt auftauchen können."""

from adbbot.bot.context import BotContext
from adbbot.bot.watchers import watcher
from adbbot.core.types import Match
from mybot.ads import ADS_GROUP, CLOSE_TEMPLATES, SKIP_TEMPLATES, confirm_ad, dismiss_ad


@watcher("Bewertungs-Popup", template="common/rate_later", priority=10, cooldown_s=1.0)
def dismiss_rating(ctx: BotContext, match: Match) -> None:
    """„Bewerte uns“-Dialog mit „Later“ schließen."""
    ctx.device.tap(match.rect.center)


@watcher(
    "Werbung",
    template=[*CLOSE_TEMPLATES, *SKIP_TEMPLATES],
    priority=20,
    cooldown_s=5.0,
    group=ADS_GROUP,
    confirm=confirm_ad,
)
def dismiss_random_ad(ctx: BotContext, match: Match) -> None:
    """Zufällige Werbung mitten im Flow: komplett wegdrücken (X, ▶|/⏭, Endbild …)."""
    dismiss_ad(ctx)
