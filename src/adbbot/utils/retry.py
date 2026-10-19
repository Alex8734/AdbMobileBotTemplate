"""Retry mit exponentiellem Backoff."""

from __future__ import annotations

from collections.abc import Callable

from adbbot.core.clock import Clock


def retry[T](
    fn: Callable[[], T],
    *,
    clock: Clock,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    factor: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable[[int, Exception, float], None] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Ruft `fn` bis zu `attempts`-mal auf. Der letzte Fehler wird weitergeworfen.

    `BotStopped` ist eine `BaseException` und wird daher nie wiederholt.
    `sleep` erlaubt ein unterbrechbares Warten (z. B. `ctx.sleep`), Default ist `clock.sleep`.
    """
    if attempts < 1:
        raise ValueError("attempts muss >= 1 sein")
    do_sleep = sleep or clock.sleep
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except exceptions as e:
            if attempt == attempts:
                raise
            wait = min(delay, max_delay)
            if on_retry is not None:
                on_retry(attempt, e, wait)
            do_sleep(wait)
            delay *= factor
    raise AssertionError("unerreichbar")
