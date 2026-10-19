#!/usr/bin/env python
"""Erzeugt die Demo-Assets des Beispielprojekts – deterministisch, ohne Handy:

- assets/demo_screens/*.png + scenario.yaml   → Fake-Gerät für `--profile demo`
- assets/templates/<page>/*.png                → aus den Screens ausgeschnittene Templates

    uv run python scripts/make_demo_assets.py

Zum Schluss wird eine Matrix "Template × Screen → beste Confidence" ausgegeben, damit man sieht,
dass jedes Template nur dort passt, wo es hingehört.
"""

from __future__ import annotations

import sys

import cv2
import numpy as np

from adbbot.core.types import Image, Rect
from adbbot.utils.image import save_image
from adbbot.utils.paths import project_root
from adbbot.vision.matcher import TemplateMatcher
from adbbot.vision.template import Template

W, H = 540, 960
FONT = cv2.FONT_HERSHEY_DUPLEX
WHITE = (245, 245, 245)
DARK = (35, 35, 35)
GOLD = (60, 205, 255)

# Tap- und Template-Bereiche in Screen-Pixeln
LOGO = Rect(10, 10, 170, 70)
GOLD_BOX = Rect(330, 22, 190, 46)
GIFT = Rect(380, 160, 110, 110)
SHOP_BUTTON = Rect(170, 560, 200, 80)
CLAIM_BUTTON = Rect(170, 540, 200, 80)
LATER_BUTTON = Rect(170, 560, 200, 70)
CLOSE_X = Rect(472, 22, 48, 48)


def grow(r: Rect, px: int) -> Rect:
    return Rect(r.x - px, r.y - px, r.w + 2 * px, r.h + 2 * px)


def background() -> Image:
    t = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None, None]
    top, bottom = np.array([80, 45, 30], np.float32), np.array([35, 20, 15], np.float32)
    return np.ascontiguousarray(np.repeat(top * (1 - t) + bottom * t, W, axis=1).astype(np.uint8))


def put_text(
    img: Image, text: str, org: tuple[int, int], scale: float, color: tuple[int, int, int], thickness: int = 2
) -> None:
    cv2.putText(img, text, org, FONT, scale, color, thickness, cv2.LINE_AA)


def centered(
    img: Image, text: str, rect: Rect, scale: float, color: tuple[int, int, int], thickness: int = 2
) -> None:
    (tw, th), _ = cv2.getTextSize(text, FONT, scale, thickness)
    put_text(img, text, (rect.x + (rect.w - tw) // 2, rect.y + (rect.h + th) // 2), scale, color, thickness)


def box(
    img: Image,
    rect: Rect,
    color: tuple[int, int, int],
    border: tuple[int, int, int] | None = None,
    thickness: int = 3,
) -> None:
    p1, p2 = (rect.x, rect.y), (rect.right - 1, rect.bottom - 1)
    cv2.rectangle(img, p1, p2, color, -1)
    if border is not None:
        cv2.rectangle(img, p1, p2, border, thickness)


def button(
    img: Image, rect: Rect, fill: tuple[int, int, int], label: str, text_color: tuple[int, int, int]
) -> None:
    border = (max(0, fill[0] - 60), max(0, fill[1] - 60), max(0, fill[2] - 60))
    box(img, rect, fill, border)
    centered(img, label, rect, 1.0, text_color)


def dim(img: Image, factor: float = 0.35) -> Image:
    return (img.astype(np.float32) * factor).astype(np.uint8)


def home(with_gift: bool) -> Image:
    img = background()
    box(img, Rect(0, 0, W, 90), (110, 60, 35))
    cv2.fillPoly(img, [np.array([[150, 55], [170, 30], [190, 55]], np.int32)], (60, 180, 240))  # Dach-Symbol
    put_text(img, "HOME", (24, 64), 1.5, WHITE, 3)
    box(img, GOLD_BOX, (45, 28, 18))
    put_text(img, "Gold 1,250", (342, 55), 0.8, GOLD)
    put_text(img, "Welcome back!", (40, 140), 1.0, WHITE)
    if with_gift:
        box(img, Rect(400, 195, 70, 65), (40, 40, 200), border=(20, 20, 120))
        box(img, Rect(392, 178, 86, 20), (60, 60, 230), border=(20, 20, 120), thickness=2)
        box(img, Rect(430, 178, 10, 82), (40, 200, 240))
        cv2.circle(img, (425, 172), 9, (40, 200, 240), -1)
        cv2.circle(img, (445, 172), 9, (40, 200, 240), -1)
    box(img, Rect(40, 300, 460, 200), (95, 60, 40), border=(130, 90, 60))
    put_text(img, "Summer Cup", (64, 350), 1.0, WHITE)
    put_text(img, "Ends in 2d 14h", (64, 395), 0.7, (200, 200, 200), 1)
    put_text(img, "Rank #128", (64, 440), 0.7, GOLD, 1)
    button(img, SHOP_BUTTON, (40, 140, 240), "SHOP", DARK)
    button(img, Rect(40, 720, 200, 70), (120, 90, 60), "QUESTS", WHITE)
    button(img, Rect(300, 720, 200, 70), (120, 90, 60), "FRIENDS", WHITE)
    return img


def popup_panel(img: Image, rect: Rect, title: str, subtitle: str) -> None:
    box(img, rect, (235, 230, 225), border=(160, 150, 140), thickness=4)
    centered(img, title, Rect(rect.x, rect.y + 20, rect.w, 50), 1.0, DARK)
    centered(img, subtitle, Rect(rect.x, rect.y + 80, rect.w, 50), 0.8, (90, 90, 90))


def gift_popup() -> Image:
    img = dim(home(with_gift=True))
    popup_panel(img, Rect(70, 300, 400, 360), "Daily reward!", "+100 Gold")
    button(img, CLAIM_BUTTON, (80, 170, 60), "CLAIM", WHITE)
    return img


def rating_popup() -> Image:
    img = dim(home(with_gift=False))
    popup_panel(img, Rect(70, 330, 400, 320), "Enjoying the game?", "Rate us 5 stars!")
    button(img, LATER_BUTTON, (150, 150, 150), "LATER", DARK)
    return img


def shop() -> Image:
    img = background()
    box(img, Rect(0, 0, W, 90), (50, 120, 40))
    put_text(img, "SHOP", (24, 64), 1.5, WHITE, 3)
    cv2.circle(img, (496, 46), 24, (60, 60, 210), -1)
    cv2.line(img, (486, 36), (506, 56), WHITE, 4, cv2.LINE_AA)
    cv2.line(img, (506, 36), (486, 56), WHITE, 4, cv2.LINE_AA)
    for i, (item, price) in enumerate(
        [("Sword", "500"), ("Shield", "350"), ("Potion", "50"), ("Helmet", "420")]
    ):
        card = Rect(40 + (i % 2) * 240, 140 + (i // 2) * 260, 220, 230)
        box(img, card, (95, 60, 40), border=(130, 90, 60))
        centered(img, item, Rect(card.x, card.y + 30, card.w, 40), 0.9, WHITE)
        centered(img, price, Rect(card.x, card.y + 150, card.w, 40), 0.9, GOLD)
    return img


TEMPLATES: dict[str, tuple[str, Rect]] = {
    "home/logo": ("home", LOGO),
    "home/shop_button": ("home", grow(SHOP_BUTTON, 4)),
    "home/daily_gift": ("home", GIFT),
    "popup/claim_button": ("gift_popup", grow(CLAIM_BUTTON, 4)),
    "common/rate_later": ("rating_popup", grow(LATER_BUTTON, 4)),
    "shop/logo": ("shop", LOGO),
    "common/close_x": ("shop", CLOSE_X),
}


def _r(rect: Rect) -> str:
    return f"[{rect.x}, {rect.y}, {rect.w}, {rect.h}]"


SCENARIO = f"""# Erzeugt von scripts/make_demo_assets.py – Übergänge des Fake-Geräts (Screen-Pixel)
start: home
start_app: home
transitions:
  home:
    - {{tap: {_r(GIFT)}, goto: gift_popup}}
    - {{tap: {_r(SHOP_BUTTON)}, goto: shop}}
  home_no_gift:
    - {{tap: {_r(SHOP_BUTTON)}, goto: shop}}
  gift_popup:
    - {{tap: {_r(CLAIM_BUTTON)}, goto: rating_popup}}
  rating_popup:
    - {{tap: {_r(LATER_BUTTON)}, goto: home_no_gift}}
  shop:
    - {{tap: {_r(CLOSE_X)}, goto: "@back"}}
  "*":
    - {{key: 4, goto: "@back"}}
"""


def main() -> int:
    root = project_root()
    screens = {
        "home": home(with_gift=True),
        "home_no_gift": home(with_gift=False),
        "gift_popup": gift_popup(),
        "rating_popup": rating_popup(),
        "shop": shop(),
    }
    screens_dir = root / "assets" / "demo_screens"
    for name, image in screens.items():
        save_image(screens_dir / f"{name}.png", image)
    (screens_dir / "scenario.yaml").write_text(SCENARIO, encoding="utf-8")

    templates_dir = root / "assets" / "templates"
    crops: dict[str, Image] = {}
    for name, (screen, rect) in TEMPLATES.items():
        crops[name] = screens[screen][rect.y : rect.bottom, rect.x : rect.right].copy()
        save_image(templates_dir / f"{name}.png", crops[name])

    print(f"Screens:   {screens_dir}")
    print(f"Templates: {templates_dir}\n")
    matcher = TemplateMatcher()
    print(f"{'':22}" + "".join(f"{s:>14}" for s in screens))
    for name, crop in crops.items():
        template = Template.from_image(name, crop)
        cells = []
        for image in screens.values():
            best = matcher.best(image, template)
            cells.append(f"{best.confidence:14.2f}" if best else f"{'-':>14}")
        print(f"{name:22}" + "".join(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
