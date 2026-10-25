"""Werbung wegdrücken: X tippen, sonst Überspringen, sonst aufs Display tippen, damit die Buttons auftauchen.

Templates (assets/templates/ads/, Suchbereiche stehen in den JSON-Dateien):
- `close_x` (+ `@2`): X oben rechts bzw. im Play-Store-Fenster der Werbung
- `close_x_left`: X oben links (zweites Werbenetzwerk)
- `next_icon`: ▶| oben links – „Next“, „Google Play“ oder ohne Text
- `skip_icon`: ⏭ im Kreis oben rechts
- `skip_chevrons` / `close_x_square`: » bzw. X auf dunklem Quadrat oben rechts (drittes Werbenetzwerk)
- `reward_timer`: Countdown-Ring unten links – Bonus-Werbung läuft noch, nichts tippen

Dazu ohne Template: die Fortschrittsleiste ganz unten (zwei einfarbige Abschnitte, z. B. blau | grau).
Sie zeigt nur, dass ein Video läuft – Bonus oder nicht. Ein X darf trotzdem getippt werden, außer der Bot
hat die Werbung selbst als Bonus gestartet (`wait_for_progress=True`).

„⏭ Store öffnen“ oben links wird bewusst nicht getippt – das öffnet nur den Store.

Drei Einstiege:
- `dismiss_ad()`: offene Werbung wegdrücken (Watcher in mybot/watchers.py, Test-Action F8)
- `watch_reward_ad()`: Bonus-Werbung selbst starten und bis zum Ende ansehen (Flows, Test-Action F9)
- `confirm_ad()`: bestätigt einen Treffer des Watchers über die Activity
"""

import numpy as np

from adbbot.bot.context import BotContext
from adbbot.core.exceptions import ElementNotFound
from adbbot.core.types import Frame, Image, Match, Point, Rect
from adbbot.device.activities import full_activity_name, is_ad_activity

ADS_GROUP = "ads"  # Watcher-Gruppe: während der Bot selbst eine Werbung behandelt, pausiert

CLOSE_TEMPLATES = ("ads/close_x", "ads/close_x_left", "ads/close_x_square")
SKIP_TEMPLATES = ("ads/skip_icon", "ads/next_icon", "ads/skip_chevrons")
REWARD_TIMER = "ads/reward_timer"
# Timer zuerst: solange er läuft, wird nichts getippt – sonst wäre der Bonus weg
AD_TEMPLATES = (REWARD_TIMER, *CLOSE_TEMPLATES, *SKIP_TEMPLATES)
PROGRESS = "ads/progress"  # kein Template, sondern `progress_fraction()`
PROGRESS_BAR_HEIGHT = 10  # Referenzpixel

# Neutrale Stelle zum Einblenden der Buttons: oben in der Mitte, meist schwarzer Rand außerhalb der Werbung
REVEAL_POINT = Point(540, 40)


def progress_fraction(image: Image) -> float | None:
    """Füllstand (0..1) der Fortschrittsleiste am unteren Rand, `None` ohne erkennbare Leiste.

    Geprüft werden zwei Zeilen kurz über dem Rand: Beide müssen aus genau zwei einfarbigen Abschnitten mit
    deutlich verschiedener Farbe bestehen, getrennt an derselben Stelle. Eine volle Leiste hat keine Grenze
    mehr und zählt als „keine Leiste“.
    """
    h = image.shape[0]
    fractions = [_two_color_split(image[h - offset]) for offset in (3, 6)]
    first, second = fractions
    if first is None or second is None or abs(first - second) > 0.01:
        return None
    return first


def _two_color_split(row: Image, *, tolerance: int = 12, min_contrast: int = 40) -> float | None:
    px = row.astype(np.int16)
    jumps = np.abs(np.diff(px, axis=0)).max(axis=1)
    edges = np.flatnonzero(jumps > tolerance)
    if len(edges) == 0 or edges[-1] - edges[0] > 4:  # genau eine Grenze, Kantenglättung bis 4 px
        return None
    left, right = px[: edges[0] + 1], px[edges[-1] + 1 :]
    if len(left) < 2 or len(right) < 2:
        return None
    if np.abs(left - left[0]).max() > tolerance or np.abs(right - right[-1]).max() > tolerance:
        return None
    if np.abs(left.mean(axis=0) - right.mean(axis=0)).max() < min_contrast:
        return None
    return float((edges[0] + 1) / len(px))


def find_ad_state(ctx: BotContext, frame: Frame, *, wait_for_progress: bool = False) -> Match | None:
    """Wichtigstes Werbe-Merkmal auf dem Frame: Timer > (Leiste, falls Bonus) > X > ▶|/⏭ > Leiste."""
    match = ctx.vision.find(REWARD_TIMER, frame=frame)
    if match is None and wait_for_progress:
        match = _progress_match(ctx, frame)
    if match is None:
        match = ctx.vision.find_any((*CLOSE_TEMPLATES, *SKIP_TEMPLATES), frame=frame)
    if match is None and not wait_for_progress:
        match = _progress_match(ctx, frame)
    return match


def _progress_match(ctx: BotContext, frame: Frame) -> Match | None:
    fraction = progress_fraction(frame.image)
    if fraction is None:
        return None
    size = frame.size
    bar = max(1, round(PROGRESS_BAR_HEIGHT * ctx.vision.scale))
    match = Match(Rect(0, size.h - bar, max(1, round(size.w * fraction)), bar), fraction, PROGRESS)
    ctx.vision.overlay.found(match, frame.ts)
    return match


def dismiss_ad(
    ctx: BotContext,
    *,
    timeout: float = 90.0,
    reveal_after: float = 6.0,
    gone_after: float = 3.0,
    settle: float = 1.5,
    wait_for_progress: bool = False,
) -> bool:
    """Drückt die offene Werbung weg. `True`, wenn nach einem X `gone_after` Sekunden kein Button mehr kommt.

    - Bonus-Timer sichtbar → nur warten
    - X gefunden → tippen (eine Werbung kann mehrere X haben: Play-Store-Fenster, dann Endbild)
    - ▶| / ⏭ gefunden → tippen
    - nur Fortschrittsleiste → Video läuft, warten (mit `wait_for_progress` hat sie Vorrang vor X und ▶|)
    - `reveal_after` Sekunden nichts → einmal auf `REVEAL_POINT` tippen, weiter warten
    """
    with ctx.suspend_watchers(ADS_GROUP):  # sonst würde der Werbe-Watcher in den Warteschleifen mitmischen
        return _dismiss_ad(ctx, timeout, reveal_after, gone_after, settle, wait_for_progress)


def _dismiss_ad(
    ctx: BotContext,
    timeout: float,
    reveal_after: float,
    gone_after: float,
    settle: float,
    wait_for_progress: bool,
) -> bool:
    log = ctx.log.bind(source="ads")
    deadline = ctx.clock.monotonic() + timeout
    closed = reward = playing = False
    taps = 0
    while (remaining := deadline - ctx.clock.monotonic()) > 0:
        wait = min(gone_after if closed else reveal_after, remaining)
        try:
            match: Match = ctx.vision.wait_until(
                lambda f: find_ad_state(ctx, f, wait_for_progress=wait_for_progress),
                timeout=wait,
                description="kein Werbe-Button",
                error=ElementNotFound,
            )
        except ElementNotFound:
            if closed:
                log.info("Werbung geschlossen", taps=taps, bonus=reward)
                return True
            if ctx.clock.monotonic() >= deadline:
                break
            log.info("Kein Button sichtbar – tippe zum Einblenden", point=str(REVEAL_POINT))
            ctx.device.tap(ctx.vision.to_device_point(REVEAL_POINT))
            continue
        if match.label == REWARD_TIMER:
            if not reward:
                log.info("Bonus-Werbung – warte, bis der Timer abgelaufen ist")
            reward = True
            ctx.sleep(1.0)
            continue
        if match.label == PROGRESS:
            if not playing:
                log.info("Video läuft – warte", fortschritt=f"{match.confidence:.0%}")
            playing = True
            ctx.sleep(1.0)
            continue
        is_close = match.label in CLOSE_TEMPLATES
        log.info("Tippe", button=match.label, confidence=round(match.confidence, 3))
        ctx.device.tap(match.rect.center)
        taps += 1
        closed = closed or is_close
        ctx.sleep(settle)
    log.warning("Werbung nicht weggedrückt", timeout_s=timeout, taps=taps, closed=closed)
    return closed


def watch_reward_ad(
    ctx: BotContext,
    button: str | None,
    *,
    start_timeout: float = 15.0,
    timeout: float = 150.0,
) -> bool:
    """Bonus-Werbung über `button` (Template) starten und bis zum Ende ansehen.

    `button=None`: die Werbung wurde schon gestartet (z. B. von Hand). Im Bonus-Modus wird nichts getippt,
    solange Timer oder Fortschrittsleiste laufen. `False`, wenn keine Werbung startet oder sie nicht
    geschlossen werden konnte – ob der Bonus angekommen ist, prüft der Aufrufer im Spiel.
    """
    log = ctx.log.bind(source="ads")
    with ctx.suspend_watchers(ADS_GROUP):
        if button is not None:
            ctx.element(button).tap()
            try:
                ctx.vision.wait_until(
                    lambda f: find_ad_state(ctx, f, wait_for_progress=True) or _ad_activity_open(ctx),
                    timeout=start_timeout,
                    description="Werbung startet nicht",
                    error=ElementNotFound,
                )
            except ElementNotFound:
                log.warning("Keine Werbung gestartet", button=button, timeout_s=start_timeout)
                return False
        log.info("Bonus-Werbung läuft")
        return dismiss_ad(ctx, timeout=timeout, wait_for_progress=True)


def confirm_ad(ctx: BotContext, match: Match) -> bool:
    """Ist wirklich eine Werbung offen? Schützt davor, ein X der App selbst wegzutippen.

    - Activity eines Werbe-SDKs → ja
    - `app.activity` im Profil gesetzt → ja, wenn eine andere Activity vorne ist
    - sonst lässt sich das nicht unterscheiden → ja (dann entscheiden allein die Templates)
    """
    activity = ctx.device.current_activity()
    if activity is None or is_ad_activity(activity):
        return True
    app = ctx.config.app
    if app.activity:
        return activity != full_activity_name(app.package, app.activity)
    return True


def _ad_activity_open(ctx: BotContext) -> bool:
    return is_ad_activity(ctx.device.current_activity())
