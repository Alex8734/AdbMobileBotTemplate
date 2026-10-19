"""Popups und Störungen, die in jedem Schritt auftauchen können."""

from adbbot.bot.context import BotContext
from adbbot.bot.watchers import watcher
from adbbot.core.types import Match


@watcher("Bewertungs-Popup", template="common/rate_later", priority=10, cooldown_s=1.0)
def dismiss_rating(ctx: BotContext, match: Match) -> None:
    """„Bewerte uns“-Dialog mit „Later“ schließen."""
    ctx.device.tap(match.rect.center)
