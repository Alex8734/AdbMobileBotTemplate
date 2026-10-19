"""Actions: Funktionen, die in der GUI als Buttons erscheinen (und per `RunAction` startbar sind).

    @action("Screenshot speichern", group="Debug")
    def save_screenshot(ctx: BotContext) -> None:
        ...

Die Engine führt sie im Bot-Thread aus – mit vollem Context, Overlays und Logging.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adbbot.bot.context import BotContext

ACTION_ATTR = "__adbbot_action__"

type ActionFunc = Callable[[BotContext], None]


@dataclass(frozen=True, slots=True)
class ActionSpec:
    title: str
    func: ActionFunc
    group: str = "Allgemein"
    description: str = ""

    @property
    def name(self) -> str:
        """Eindeutiger Name für `RunAction` = Funktionsname."""
        return str(getattr(self.func, "__name__", self.title))


def action(
    title: str, *, group: str = "Allgemein", description: str = ""
) -> Callable[[ActionFunc], ActionFunc]:
    def decorate(func: ActionFunc) -> ActionFunc:
        doc = (getattr(func, "__doc__", None) or "").strip().splitlines()
        setattr(func, ACTION_ATTR, ActionSpec(title, func, group, description or (doc[0] if doc else "")))
        return func

    return decorate


def get_action_spec(obj: object) -> ActionSpec | None:
    spec = getattr(obj, ACTION_ATTR, None)
    return spec if isinstance(spec, ActionSpec) else None
