"""Werbung wegdrücken: X tippen, sonst Überspringen, sonst aufs Display tippen, damit die Buttons auftauchen.

Templates (assets/templates/ads/, Suchbereiche stehen in den JSON-Dateien):
- `close_x` (+ `@2`): X oben rechts bzw. im Play-Store-Fenster der Werbung
- `close_x_left`: X oben links (zweites Werbenetzwerk)
- `next_icon`: ▶| oben links – „Next“, „Google Play“ oder ohne Text
- `skip_icon`: ⏭ im Kreis oben rechts

„⏭ Store öffnen“ oben links wird bewusst nicht getippt – das öffnet nur den Store.
"""

from adbbot.bot.context import BotContext
from adbbot.core.exceptions import ElementNotFound
from adbbot.core.types import Match, Point

CLOSE_TEMPLATES = ("ads/close_x", "ads/close_x_left")
SKIP_TEMPLATES = ("ads/skip_icon", "ads/next_icon")
AD_TEMPLATES = (*CLOSE_TEMPLATES, *SKIP_TEMPLATES)

# Neutrale Stelle zum Einblenden der Buttons: oben in der Mitte, meist schwarzer Rand außerhalb der Werbung
REVEAL_POINT = Point(540, 40)


def dismiss_ad(
    ctx: BotContext,
    *,
    timeout: float = 60.0,
    reveal_after: float = 6.0,
    gone_after: float = 3.0,
    settle: float = 1.5,
) -> bool:
    """Drückt die offene Werbung weg. `True`, wenn nach einem X `gone_after` Sekunden kein Button mehr kommt.

    - X gefunden → tippen (eine Werbung kann mehrere X haben: Play-Store-Fenster, dann Endbild)
    - ▶| / ⏭ gefunden → tippen
    - `reveal_after` Sekunden nichts → einmal auf `REVEAL_POINT` tippen, weiter warten
    """
    log = ctx.log.bind(source="ads")
    deadline = ctx.clock.monotonic() + timeout
    closed = False
    taps = 0
    while (remaining := deadline - ctx.clock.monotonic()) > 0:
        wait = min(gone_after if closed else reveal_after, remaining)
        try:
            match: Match = ctx.vision.wait_for_any(AD_TEMPLATES, timeout=wait)
        except ElementNotFound:
            if closed:
                log.info("Werbung geschlossen", taps=taps)
                return True
            if ctx.clock.monotonic() >= deadline:
                break
            log.info("Kein Button sichtbar – tippe zum Einblenden", point=str(REVEAL_POINT))
            ctx.device.tap(ctx.vision.to_device_point(REVEAL_POINT))
            continue
        is_close = match.label in CLOSE_TEMPLATES
        log.info("Tippe", button=match.label, confidence=round(match.confidence, 3))
        ctx.device.tap(match.rect.center)
        taps += 1
        closed = closed or is_close
        ctx.sleep(settle)
    log.warning("Werbung nicht weggedrückt", timeout_s=timeout, taps=taps, closed=closed)
    return closed
