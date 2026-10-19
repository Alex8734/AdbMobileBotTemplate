"""Navigator: Seiten-Graph aus `Page.transitions`, aktuelle Seite erkennen, per Breitensuche navigieren.

    class HomePage(Page):
        transitions = {"shop": lambda page: page.shop_button.tap()}   # Ziel als Page-Name oder -Klasse

    ctx.navigator.go_to(ShopPage)      # bzw. in Flows: self.go_to(ShopPage)

- Nach jedem Übergang wird die aktuelle Seite neu erkannt und neu geplant – ein unerwarteter Screen
  (Popup, Ladebildschirm) bringt die Navigation nicht aus dem Tritt.
- Unbekannter Screen → bis zu `bot.navigation_max_back` × Zurück, danach einmal App-Neustart
  (`bot.navigation_restart_app`).
- Pages mit höherer `priority` werden bei der Erkennung zuerst geprüft (z. B. Popups, die eine andere
  Seite nur teilweise verdecken).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from adbbot.bot.page import Page
from adbbot.core.exceptions import NavigationError, WrongPage
from adbbot.core.types import Frame
from adbbot.device.protocol import KEY_BACK

if TYPE_CHECKING:
    from adbbot.bot.context import BotContext

type TransitionAction = Callable[[Any], None]
type PageGraph = dict[type[Page], dict[type[Page], TransitionAction]]


class Navigator:
    def __init__(
        self,
        ctx: BotContext,
        *,
        max_back_presses: int | None = None,
        restart_app: bool | None = None,
        transition_timeout_s: float | None = None,
        back_delay_s: float = 0.5,
        max_hops: int = 10,
    ) -> None:
        bot = ctx.config.bot
        self.ctx = ctx
        self.max_back_presses = bot.navigation_max_back if max_back_presses is None else max_back_presses
        self.restart_app = bot.navigation_restart_app if restart_app is None else restart_app
        self.transition_timeout_s = transition_timeout_s  # None = bot.element_timeout_s
        self.back_delay_s = back_delay_s
        self.max_hops = max_hops
        self.log = ctx.log.bind(source="navigator")

    # --- Erkennen und Planen -----------------------------------------------------------------------

    def pages(self) -> list[type[Page]]:
        return sorted(self.ctx.pages.classes, key=lambda cls: -cls.priority)

    def current_page(self, frame: Frame | None = None) -> type[Page] | None:
        """Prüft alle Anchors auf *einem* Frame. `None` = unbekannter Screen."""
        frame = frame if frame is not None else self.ctx.vision.capture()
        for cls in self.pages():
            if self.ctx.page(cls).is_active(frame=frame):
                return cls
        return None

    def graph(self) -> PageGraph:
        """Wirft `BotError`, wenn eine Transition auf einen unbekannten Page-Namen zeigt."""
        pages = self.ctx.pages
        return {
            cls: {
                (pages.get_class(target) if isinstance(target, str) else target): action
                for target, action in cls.transitions.items()
            }
            for cls in pages.classes
        }

    def path(
        self, start: type[Page], target: type[Page], graph: PageGraph | None = None
    ) -> list[type[Page]] | None:
        """Kürzester Weg (Breitensuche) inklusive Start und Ziel, `None` wenn unerreichbar."""
        graph = graph if graph is not None else self.graph()
        previous: dict[type[Page], type[Page] | None] = {start: None}
        queue: deque[type[Page]] = deque([start])
        while queue:
            page = queue.popleft()
            if page is target:
                route = [page]
                while (before := previous[route[-1]]) is not None:
                    route.append(before)
                return route[::-1]
            for neighbour in graph.get(page, {}):
                if neighbour not in previous:
                    previous[neighbour] = page
                    queue.append(neighbour)
        return None

    # --- Navigieren --------------------------------------------------------------------------------

    def go_to[P: Page](self, target: type[P]) -> P:
        graph = self.graph()
        if target not in graph:
            raise NavigationError(f"Page {target.__name__} ist nicht registriert")
        restarted = False
        for _ in range(self.max_hops):
            self.ctx.checkpoint()
            current = self.current_page()
            if current is target:
                return self.ctx.page(target)
            if current is None:
                restarted = self._recover_unknown(allow_restart=not restarted) or restarted
                continue
            route = self.path(current, target, graph)
            if route is None:
                raise NavigationError(
                    f"Kein Weg von {current.name!r} nach {target.name!r} – Page.transitions prüfen"
                )
            next_page = route[1]
            self.log.info("Übergang", von=current.name, nach=next_page.name, ziel=target.name)
            graph[current][next_page](self.ctx.page(current))
            try:
                self.ctx.page(next_page).wait_until_active(timeout=self.transition_timeout_s)
            except WrongPage as e:
                self.log.warning("Übergang nicht angekommen – plane neu", nach=next_page.name, error=str(e))
        raise NavigationError(f"Seite {target.name!r} nach {self.max_hops} Übergängen nicht erreicht")

    def _recover_unknown(self, *, allow_restart: bool) -> bool:
        """Unbekannter Screen: Zurück, notfalls App-Neustart. `True`, wenn neu gestartet wurde."""
        for press in range(1, self.max_back_presses + 1):
            self.log.warning("Unbekannter Screen – Zurück", versuch=press)
            self.ctx.device.key(KEY_BACK)
            self.ctx.sleep(self.back_delay_s)
            if self.current_page() is not None:
                return False
        if not (self.restart_app and allow_restart):
            raise NavigationError("Unbekannter Screen – Zurück hat nicht geholfen")

        app = self.ctx.config.app
        self.log.warning("Unbekannter Screen – App wird neu gestartet", package=app.package)
        self.ctx.device.stop_app(app.package)
        self.ctx.sleep(1.0)
        self.ctx.device.start_app(app.package, app.activity)
        deadline = self.ctx.clock.monotonic() + app.start_timeout_s
        while self.current_page() is None:
            if self.ctx.clock.monotonic() >= deadline:
                raise NavigationError("Nach dem App-Neustart wurde keine bekannte Seite erkannt")
            self.ctx.sleep(0.5)
        return True
