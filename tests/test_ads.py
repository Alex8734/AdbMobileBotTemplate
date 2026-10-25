"""mybot.ads.dismiss_ad mit den echten Werbe-Templates, eingesetzt in synthetische 1080x2412-Screens."""

import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from adbbot.bot.context import BotContext
from adbbot.bot.watchers import WatcherRunner, get_watcher_spec
from adbbot.core.clock import FakeClock
from adbbot.core.types import Frame, Image, Match, Point, Rect
from adbbot.utils.image import load_image
from mybot.ads import REVEAL_POINT, confirm_ad, dismiss_ad, progress_fraction, watch_reward_ad
from mybot.watchers import dismiss_random_ad
from tests.botkit import fake, make_context
from tests.imagegen import paste, textured

ADS_DIR = Path(__file__).resolve().parents[1] / "assets" / "templates" / "ads"
W, H = 1080, 2412
BONUS_BUTTON = textured(200, 100, seed=42)  # „Video ansehen für Bonus“ im Spiel
BONUS_AT = Point(440, 1200)

# Position der Buttons wie auf den Original-Screenshots
BUTTONS = {
    "next_icon": Point(55, 297),
    "close_x": Point(975, 300),
    "close_x@2": Point(984, 620),
    "skip_icon": Point(956, 136),
    "close_x_left": Point(57, 136),
    "reward_timer": Point(37, 2290),
    "skip_chevrons": Point(989, 77),
    "close_x_square": Point(986, 77),
}


def screen(*buttons: str, shift_y: int = 0) -> Image:
    """`shift_y`: manche Werbungen haben oben keinen schwarzen Rand – dort sitzt alles ~125 px höher."""
    image = (textured(W, H, seed=3) // 4).astype(np.uint8)  # dunkler Hintergrund wie bei Werbung
    for name in buttons:
        at = BUTTONS[name]
        image = paste(image, load_image(ADS_DIR / f"{name}.png"), Point(at.x, at.y + shift_y))
    return image


def with_progress(image: Image, fraction: float) -> Image:
    """Fortschrittsleiste wie im Screenshot: 10 px, blau gefüllt, grauviolett der Rest."""
    out = image.copy()
    out[-10:] = (170, 148, 148)
    out[-10:, : round(out.shape[1] * fraction)] = (222, 132, 75)
    return out


def tap_on(name: str, goto: str) -> dict[str, Any]:
    patch = load_image(ADS_DIR / f"{name}.png")
    at = BUTTONS[name]
    return {"tap": [at.x, at.y, patch.shape[1], patch.shape[0]], "goto": goto}


@pytest.fixture
def ctx(tmp_path: Path, fake_clock: FakeClock) -> BotContext:
    shutil.copytree(ADS_DIR, tmp_path / "templates" / "ads")
    reveal = [REVEAL_POINT.x - 5, REVEAL_POINT.y - 5, 10, 10]
    return make_context(
        tmp_path,
        clock=fake_clock,
        templates={"game/bonus": BONUS_BUTTON},
        screens={
            "video": screen("next_icon"),
            "sheet": screen("close_x@2"),
            "end": screen("close_x"),
            "hidden": screen(),
            "skip": screen("skip_icon"),
            "x_left": screen("close_x_left"),
            "game": textured(W, H, seed=9),
            "game_bonus": paste(textured(W, H, seed=9), BONUS_BUTTON, BONUS_AT),
            "reward": screen("reward_timer", "next_icon"),
            "video_bar": with_progress(screen(), 0.3),
            "video_bar_x": with_progress(screen("close_x"), 0.6),
            "video_bar_skip": with_progress(screen("skip_chevrons"), 0.8),
            "end_skip": screen("skip_chevrons"),
            "end_square_x": screen("close_x_square"),
            "video_high": screen("next_icon", shift_y=-125),
            "end_high": screen("close_x", shift_y=-125),
        },
        scenario={
            "transitions": {
                "video": [tap_on("next_icon", "sheet")],
                "sheet": [tap_on("close_x@2", "end")],
                "end": [tap_on("close_x", "game")],
                "hidden": [{"tap": reveal, "goto": "skip"}],
                "skip": [tap_on("skip_icon", "x_left")],
                "x_left": [tap_on("close_x_left", "game")],
                "video_high": [{"tap": [55, 172, 37, 31], "goto": "end_high"}],
                "end_high": [{"tap": [975, 175, 47, 45], "goto": "game"}],
                "video_bar_x": [tap_on("close_x", "game")],
                "game_bonus": [{"tap": [BONUS_AT.x, BONUS_AT.y, 200, 100], "goto": "end_skip"}],
                "video_bar_skip": [tap_on("skip_chevrons", "end_skip")],
                "end_skip": [tap_on("skip_chevrons", "end_square_x")],
                "end_square_x": [tap_on("close_x_square", "game")],
            }
        },
        # seltener prüfen: Masken-Matching auf 1080x2412 kostet ~150 ms pro Runde
        config={"device": {"reference_resolution": [1080, 1920]}, "vision": {"poll_interval_ms": 1000}},
    )


def test_google_ad_next_sheet_end_card(ctx: BotContext) -> None:
    fake(ctx).show("video")
    assert dismiss_ad(ctx)
    assert fake(ctx).current_screen == "game"
    assert [r.kind for r in fake(ctx).inputs] == ["tap"] * 3


def test_hidden_buttons_are_revealed(ctx: BotContext) -> None:
    fake(ctx).show("hidden")
    assert dismiss_ad(ctx, reveal_after=2)
    assert fake(ctx).current_screen == "game"
    assert fake(ctx).inputs[0].args[0] == REVEAL_POINT
    assert len(fake(ctx).inputs) == 3


def test_no_ad_gives_up_after_timeout(ctx: BotContext) -> None:
    fake(ctx).show("game")
    assert not dismiss_ad(ctx, timeout=10, reveal_after=4)
    assert [r.args[0] for r in fake(ctx).inputs] == [REVEAL_POINT, REVEAL_POINT]


def test_layout_without_top_border(ctx: BotContext) -> None:
    fake(ctx).show("video_high")
    assert dismiss_ad(ctx)
    assert fake(ctx).current_screen == "game"


def test_reward_timer_blocks_all_taps(ctx: BotContext) -> None:
    fake(ctx).show("reward")  # Timer und ▶| gleichzeitig sichtbar: trotzdem nichts tippen
    assert not dismiss_ad(ctx, timeout=10, reveal_after=4)
    assert fake(ctx).inputs == []


def test_progress_fraction() -> None:
    dark = np.full((100, 400, 3), 20, dtype=np.uint8)
    assert progress_fraction(with_progress(dark, 0.25)) == pytest.approx(0.25)
    assert progress_fraction(with_progress(dark, 1.0)) is None  # voll: keine Grenze mehr
    assert progress_fraction(dark) is None
    assert progress_fraction(textured(400, 100, seed=5)) is None

    gradient = dark.copy()
    gradient[-10:] = np.linspace(0, 255, 400, dtype=np.uint8)[None, :, None]
    assert progress_fraction(gradient) is None

    low_contrast = dark.copy()
    low_contrast[-10:, :100] = 40
    assert progress_fraction(low_contrast) is None


def test_running_video_is_not_tapped(ctx: BotContext) -> None:
    fake(ctx).show("video_bar")
    assert not dismiss_ad(ctx, timeout=10, reveal_after=4)
    assert fake(ctx).inputs == []  # kein Tippen zum Einblenden, solange die Leiste läuft


def test_x_is_tapped_despite_progress_bar(ctx: BotContext) -> None:
    fake(ctx).show("video_bar_x")
    assert dismiss_ad(ctx)
    assert fake(ctx).current_screen == "game"


def test_bonus_mode_waits_for_progress_bar(ctx: BotContext) -> None:
    fake(ctx).show("video_bar_x")
    assert not dismiss_ad(ctx, timeout=10, wait_for_progress=True)
    assert fake(ctx).inputs == []


def test_third_network_chevrons_then_square_x(ctx: BotContext) -> None:
    """Video mit Leiste und » → Endbild mit » → Endbild mit X (20261025-131019 … 131125)."""
    fake(ctx).show("video_bar_skip")
    assert dismiss_ad(ctx)
    assert fake(ctx).current_screen == "game"
    assert len(fake(ctx).inputs) == 3


def test_watcher_dismisses_ad_while_flow_waits(ctx: BotContext, fake_clock: FakeClock) -> None:
    """Werbung mitten im Step: der Watcher drückt sie weg, der Step läuft nicht in den Timeout."""
    spec = get_watcher_spec(dismiss_random_ad)
    assert spec is not None
    runner = WatcherRunner(ctx, [spec], interval_s=0, max_triggers_per_step=3)

    def poll(frame: Frame) -> None:
        ctx.checkpoint()
        runner.run(frame, flow_running=True)

    ctx.vision.poll_hook = poll
    ctx.control.set_deadline(fake_clock.monotonic() + 5)  # Step-Timeout kürzer als die Werbung

    fake(ctx).show("video")
    ctx.vision.wait_until(lambda f: fake(ctx).current_screen == "game", timeout=3)
    assert len(fake(ctx).inputs) == 3


def test_watcher_is_paused_while_bot_handles_the_ad(ctx: BotContext) -> None:
    spec = get_watcher_spec(dismiss_random_ad)
    assert spec is not None
    runner = WatcherRunner(ctx, [spec], interval_s=0, max_triggers_per_step=3)
    triggered: list[bool] = []

    def poll(frame: Frame) -> None:
        triggered.append(runner.run(frame, flow_running=True))

    ctx.vision.poll_hook = poll
    fake(ctx).show("video")
    assert dismiss_ad(ctx)
    assert triggered and not any(triggered)  # dismiss_ad pausiert die Gruppe „ads“


def test_reward_ad_started_by_button(ctx: BotContext) -> None:
    fake(ctx).show("game_bonus")
    assert watch_reward_ad(ctx, "game/bonus")
    assert fake(ctx).current_screen == "game"
    assert len(fake(ctx).inputs) == 3  # Bonus-Button, », X


def test_reward_ad_that_never_starts(ctx: BotContext) -> None:
    fake(ctx).show("game_bonus")
    fake(ctx).scenario = None  # Button tut nichts
    assert not watch_reward_ad(ctx, "game/bonus", start_timeout=3)
    assert len(fake(ctx).inputs) == 1


def test_confirm_ad(ctx: BotContext) -> None:
    match = Match(Rect(0, 0, 10, 10), 1.0, "ads/close_x")
    device = fake(ctx)
    device.activity = None
    assert confirm_ad(ctx, match)
    device.activity = "com.google.android.gms.ads.AdActivity"
    assert confirm_ad(ctx, match)
    device.activity = "com.example.test.MainActivity"
    assert confirm_ad(ctx, match)  # ohne app.activity im Profil nicht unterscheidbar

    app = ctx.config.app.model_copy(update={"activity": ".MainActivity"})
    ctx.config = ctx.config.model_copy(update={"app": app})
    assert not confirm_ad(ctx, match)  # Spiel-Activity vorne: X gehört zur App
    device.activity = "com.example.test.SomeSdkActivity"
    assert confirm_ad(ctx, match)
