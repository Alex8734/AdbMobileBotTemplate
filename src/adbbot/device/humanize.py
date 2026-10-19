"""Humanisierte Eingaben als Decorator um ein beliebiges `DeviceProtocol`.

Kleiner zufälliger Versatz bei Taps/Swipes, leicht variierende Swipe-Dauer, zufälliges Post-Delay.
Die Wahl eines zufälligen Punkts *innerhalb eines Elements* passiert eine Schicht höher
(`Rect.random_point` in `BoundElement.tap`); hier geht es nur um Pixel-Jitter und Timing.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from adbbot.core.config import BotConfig
from adbbot.core.types import Frame, Point, Size
from adbbot.device.protocol import DeviceProtocol


class HumanizedDevice:
    def __init__(
        self,
        inner: DeviceProtocol,
        *,
        sleep: Callable[[float], None],
        rng: random.Random | None = None,
        tap_jitter_px: int = 3,
        swipe_jitter_px: int = 8,
        duration_jitter: float = 0.15,
        post_input_delay_ms: tuple[int, int] = (150, 400),
    ) -> None:
        self.inner = inner
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._tap_jitter = tap_jitter_px
        self._swipe_jitter = swipe_jitter_px
        self._duration_jitter = duration_jitter
        lo, hi = post_input_delay_ms
        self._delay_ms = (min(lo, hi), max(lo, hi))

    @property
    def serial(self) -> str:
        return self.inner.serial

    # --- humanisiert -------------------------------------------------------------------------------

    def tap(self, p: Point) -> None:
        self.inner.tap(self._jitter(p, self._tap_jitter))
        self._post_delay()

    def swipe(self, a: Point, b: Point, duration_ms: int) -> None:
        self.inner.swipe(
            self._jitter(a, self._swipe_jitter), self._jitter(b, self._swipe_jitter), self._vary(duration_ms)
        )
        self._post_delay()

    def long_press(self, p: Point, duration_ms: int) -> None:
        self.inner.long_press(self._jitter(p, self._tap_jitter), self._vary(duration_ms))
        self._post_delay()

    def key(self, keycode: int) -> None:
        self.inner.key(keycode)
        self._post_delay()

    def input_text(self, text: str) -> None:
        self.inner.input_text(text)
        self._post_delay()

    # --- durchgereicht -----------------------------------------------------------------------------

    def connect(self) -> None:
        self.inner.connect()

    def disconnect(self) -> None:
        self.inner.disconnect()

    def is_connected(self) -> bool:
        return self.inner.is_connected()

    def screen_size(self) -> Size:
        return self.inner.screen_size()

    def screenshot(self) -> Frame:
        return self.inner.screenshot()

    def start_app(self, package: str, activity: str | None = None) -> None:
        self.inner.start_app(package, activity)

    def stop_app(self, package: str) -> None:
        self.inner.stop_app(package)

    def current_app(self) -> str | None:
        return self.inner.current_app()

    # --- intern ------------------------------------------------------------------------------------

    def _jitter(self, p: Point, px: int) -> Point:
        if px <= 0:
            return p
        dx, dy = self._rng.randint(-px, px), self._rng.randint(-px, px)
        return Point(max(0, p.x + dx), max(0, p.y + dy))

    def _vary(self, duration_ms: int) -> int:
        factor = 1.0 + self._rng.uniform(-self._duration_jitter, self._duration_jitter)
        return max(1, round(duration_ms * factor))

    def _post_delay(self) -> None:
        lo, hi = self._delay_ms
        if hi > 0:
            self._sleep(self._rng.randint(lo, hi) / 1000)


def humanize(
    device: DeviceProtocol,
    config: BotConfig,
    *,
    sleep: Callable[[float], None],
    rng: random.Random | None = None,
) -> DeviceProtocol:
    """Wickelt `device` ein, wenn `bot.humanize_input` aktiv ist, sonst unverändert zurück."""
    if not config.humanize_input:
        return device
    return HumanizedDevice(
        device,
        sleep=sleep,
        rng=rng,
        tap_jitter_px=config.tap_jitter_px,
        post_input_delay_ms=config.post_tap_delay_ms,
    )
