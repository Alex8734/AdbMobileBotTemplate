"""mybot.ads.dismiss_ad mit den echten Werbe-Templates, eingesetzt in synthetische 1080x2412-Screens."""

import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from adbbot.bot.context import BotContext
from adbbot.core.clock import FakeClock
from adbbot.core.types import Image, Point
from adbbot.utils.image import load_image
from mybot.ads import REVEAL_POINT, dismiss_ad
from tests.botkit import fake, make_context
from tests.imagegen import paste, textured

ADS_DIR = Path(__file__).resolve().parents[1] / "assets" / "templates" / "ads"
W, H = 1080, 2412

# Position der Buttons wie auf den Original-Screenshots
BUTTONS = {
    "next_icon": Point(55, 297),
    "close_x": Point(975, 300),
    "close_x@2": Point(984, 620),
    "skip_icon": Point(956, 136),
    "close_x_left": Point(57, 136),
}


def screen(*buttons: str) -> Image:
    image = (textured(W, H, seed=3) // 4).astype(np.uint8)  # dunkler Hintergrund wie bei Werbung
    for name in buttons:
        image = paste(image, load_image(ADS_DIR / f"{name}.png"), BUTTONS[name])
    return image


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
        screens={
            "video": screen("next_icon"),
            "sheet": screen("close_x@2"),
            "end": screen("close_x"),
            "hidden": screen(),
            "skip": screen("skip_icon"),
            "x_left": screen("close_x_left"),
            "game": textured(W, H, seed=9),
        },
        scenario={
            "transitions": {
                "video": [tap_on("next_icon", "sheet")],
                "sheet": [tap_on("close_x@2", "end")],
                "end": [tap_on("close_x", "game")],
                "hidden": [{"tap": reveal, "goto": "skip"}],
                "skip": [tap_on("skip_icon", "x_left")],
                "x_left": [tap_on("close_x_left", "game")],
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
