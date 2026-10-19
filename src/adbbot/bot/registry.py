"""Registries für Pages, Flows, Watcher und Actions + automatisches Einsammeln aus Projekt-Paketen.

def register(reg: Registries) -> None:     # mybot/bootstrap.py
    reg.discover(mybot.pages)
    reg.discover(mybot.flows)
    reg.discover(mybot.watchers)
    reg.discover(mybot.actions)
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Iterator, Mapping
from types import ModuleType
from typing import TYPE_CHECKING, Any

from adbbot.bot.actions import ActionSpec, get_action_spec
from adbbot.bot.flow import Flow
from adbbot.bot.page import Page, PageRegistry
from adbbot.bot.watchers import WatcherSpec, get_watcher_spec
from adbbot.core.exceptions import BotError

if TYPE_CHECKING:
    from adbbot.bot.context import BotContext


class FlowRegistry:
    def __init__(self) -> None:
        self._flows: dict[str, type[Flow]] = {}

    def register(self, cls: type[Flow]) -> None:
        existing = self._flows.get(cls.name)
        if existing is not None and existing is not cls:
            raise BotError(f"Flow-Name {cls.name!r} doppelt: {existing.__qualname__} und {cls.__qualname__}")
        self._flows[cls.name] = cls

    def names(self) -> list[str]:
        return sorted(self._flows)

    def classes(self) -> list[type[Flow]]:
        return [self._flows[name] for name in self.names()]

    def get(self, name: str) -> type[Flow]:
        try:
            return self._flows[name]
        except KeyError:
            known = ", ".join(self.names()) or "keine"
            raise BotError(f"Unbekannter Flow {name!r}. Vorhanden: {known}") from None

    def create(self, name: str, ctx: BotContext, params: Mapping[str, Any] | None = None) -> Flow:
        return self.get(name)(ctx, params)


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, ActionSpec] = {}

    def register(self, spec: ActionSpec) -> None:
        existing = self._actions.get(spec.name)
        if existing is not None and existing.func is not spec.func:
            raise BotError(f"Action-Name {spec.name!r} doppelt")
        self._actions[spec.name] = spec

    def names(self) -> list[str]:
        return list(self._actions)

    def specs(self) -> list[ActionSpec]:
        return list(self._actions.values())

    def get(self, name: str) -> ActionSpec:
        try:
            return self._actions[name]
        except KeyError:
            known = ", ".join(self._actions) or "keine"
            raise BotError(f"Unbekannte Action {name!r}. Vorhanden: {known}") from None

    def groups(self) -> dict[str, list[ActionSpec]]:
        result: dict[str, list[ActionSpec]] = {}
        for spec in self._actions.values():
            result.setdefault(spec.group, []).append(spec)
        return result


class Registries:
    def __init__(self, pages: PageRegistry | None = None) -> None:
        self.pages = pages if pages is not None else PageRegistry()
        self.flows = FlowRegistry()
        self.actions = ActionRegistry()
        self.watchers: list[WatcherSpec] = []

    def add_watcher(self, spec: WatcherSpec) -> None:
        if all(w.key != spec.key for w in self.watchers):
            self.watchers.append(spec)

    def discover(self, module: ModuleType) -> None:
        """Sammelt alles, was in `module` (und bei Paketen allen Untermodulen) definiert ist.

        - Page-Unterklassen mit `anchor`
        - Flow-Unterklassen, die `run()` implementieren (Basisklassen wie `ProjectFlow` nicht)
        - mit `@watcher` / `@action` dekorierte Funktionen
        Importierte Namen werden ignoriert – nur Definitionen im jeweiligen Modul zählen.
        """
        for mod in _modules(module):
            for obj in list(vars(mod).values()):
                if getattr(obj, "__module__", None) != mod.__name__:
                    continue
                if inspect.isclass(obj):
                    if issubclass(obj, Page) and obj is not Page and obj.has_anchor():
                        self.pages.register(obj)
                    elif issubclass(obj, Flow) and obj is not Flow and obj.run is not Flow.run:
                        self.flows.register(obj)
                    continue
                if (watcher_spec := get_watcher_spec(obj)) is not None:
                    self.add_watcher(watcher_spec)
                if (action_spec := get_action_spec(obj)) is not None:
                    self.actions.register(action_spec)

    def template_names(self) -> set[str]:
        """Alle statisch bekannten Template-Referenzen (für `TemplateStore.validate`)."""
        names = self.pages.template_names()
        for spec in self.watchers:
            names.update(spec.templates)
        return names


def _modules(module: ModuleType) -> Iterator[ModuleType]:
    yield module
    path = getattr(module, "__path__", None)
    if path is None:
        return
    for info in pkgutil.walk_packages(path, prefix=f"{module.__name__}."):
        yield importlib.import_module(info.name)
