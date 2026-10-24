"""Fake-Gerät: liefert Frames aus Bildern und protokolliert alle Eingaben.

Damit lassen sich Flows ohne Handy entwickeln und testen.

Modi:
- `sequential`: jeder Screenshot rückt ein Bild weiter; am Ende bleibt das letzte stehen (oder `loop`).
- `manual`: Bild wechselt nur über `show(name)`, ein Szenario oder `on_input`.

Szenario (`scenario.yaml` im Screen-Ordner): ein kleiner Zustandsautomat, z. B.

    start: home
    start_app: home            # Screen nach start_app()
    transitions:
      home:
        - {tap: [170, 560, 200, 80], goto: shop}   # Tap in diesem Rechteck → Screen "shop"
      shop:
        - {tap: [472, 22, 48, 48], goto: "@back"}  # zurück zum vorherigen Screen
      "*":
        - {key: 4, goto: "@back"}                  # gilt auf jedem Screen

Ein Übergang kann zusätzlich die Vordergrund-App bzw. Activity setzen, z. B. Tap auf Werbung → Play Store:

        - {tap: [0, 0, 360, 640], goto: store, app: com.android.vending}

`bring_to_front()` kehrt danach auf den Screen zurück, von dem aus die App verlassen wurde.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from adbbot.core.clock import Clock, RealClock
from adbbot.core.exceptions import ConfigError, DeviceError
from adbbot.core.types import Frame, Image, Point, Rect, Size
from adbbot.utils.image import load_image

BACK = "@back"
ANY_SCREEN = "*"
SCENARIO_FILE = "scenario.yaml"


@dataclass(frozen=True, slots=True)
class InputRecord:
    kind: str  # "tap", "swipe", "long_press", "key", "text", "start_app", "stop_app", "bring_to_front"
    args: tuple[Any, ...]


# --- Szenario ----------------------------------------------------------------------------------------


class _TransitionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tap: tuple[int, int, int, int] | None = None
    key: int | None = None
    goto: str
    app: str | None = None  # Vordergrund-App nach dem Übergang
    activity: str | None = None

    @model_validator(mode="after")
    def _exactly_one_trigger(self) -> _TransitionModel:
        if (self.tap is None) == (self.key is None):
            raise ValueError("genau eins von 'tap' oder 'key' angeben")
        return self


class _ScenarioModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str | None = None
    start_app: str | None = None
    transitions: dict[str, list[_TransitionModel]] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Transition:
    goto: str
    tap: Rect | None = None
    key: int | None = None
    app: str | None = None
    activity: str | None = None

    def matches(self, record: InputRecord) -> bool:
        if self.tap is not None:
            return record.kind == "tap" and self.tap.contains(record.args[0])
        return record.kind == "key" and record.args[0] == self.key


@dataclass(frozen=True, slots=True)
class FakeScenario:
    transitions: Mapping[str, tuple[Transition, ...]]
    start: str | None = None
    start_app: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, source: str = "Szenario") -> FakeScenario:
        try:
            model = _ScenarioModel.model_validate(data)
        except ValidationError as e:
            raise ConfigError(f"{source}: ungültiges Szenario: {e}") from e
        transitions = {
            screen: tuple(
                Transition(t.goto, Rect(*t.tap) if t.tap else None, t.key, t.app, t.activity) for t in items
            )
            for screen, items in model.transitions.items()
        }
        return cls(transitions, model.start, model.start_app)

    @classmethod
    def load(cls, path: Path) -> FakeScenario:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"{path}: ungültiges YAML: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: oberste Ebene muss ein Mapping sein")
        return cls.from_dict(data, source=str(path))

    def screen_names(self) -> set[str]:
        """Alle Screens, auf die das Szenario verweist."""
        names = {n for n in (self.start, self.start_app) if n}
        names.update(s for s in self.transitions if s != ANY_SCREEN)
        names.update(t.goto for items in self.transitions.values() for t in items if t.goto != BACK)
        return names

    def target(self, screen: str, record: InputRecord) -> Transition | None:
        for transition in (*self.transitions.get(screen, ()), *self.transitions.get(ANY_SCREEN, ())):
            if transition.matches(record):
                return transition
        return None


# --- Gerät -------------------------------------------------------------------------------------------


class FakeDevice:
    def __init__(
        self,
        screens: Mapping[str, Image] | Sequence[Image],
        *,
        clock: Clock | None = None,
        serial: str = "fake",
        mode: Literal["sequential", "manual"] = "sequential",
        loop: bool = False,
        current_package: str | None = None,
        current_activity: str | None = None,
        on_input: Callable[[FakeDevice, InputRecord], None] | None = None,
        scenario: FakeScenario | None = None,
    ) -> None:
        if isinstance(screens, Mapping):
            self._screens: dict[str, Image] = dict(screens)
        else:
            self._screens = {f"{i:04d}": img for i, img in enumerate(screens)}
        if not self._screens:
            raise ValueError("FakeDevice braucht mindestens ein Bild")
        self._names = list(self._screens)
        self._index = 0
        self._history: list[str] = []
        self._clock = clock or RealClock()
        self._serial = serial
        self.mode = mode
        self.loop = loop
        self.current_package = current_package
        self.activity = current_activity
        self.ui_xml: str | None = None  # Ergebnis von dump_ui()
        self._resume_screen: str | None = None  # Screen, von dem aus die App verlassen wurde
        self.on_input = on_input
        self.scenario = scenario
        self.inputs: list[InputRecord] = []
        self.screenshot_count = 0
        self._connected = False
        self._pending_error: DeviceError | None = None

        if scenario is not None:
            unknown = scenario.screen_names() - set(self._screens)
            if unknown:
                raise ConfigError(
                    f"Szenario verweist auf unbekannte Screens: {', '.join(sorted(unknown))} "
                    f"(vorhanden: {', '.join(self._names)})"
                )
            self.mode = "manual"
            if scenario.start:
                self._set_screen(scenario.start)

    @classmethod
    def from_dir(cls, directory: Path, **kwargs: Any) -> FakeDevice:
        """Lädt alle PNGs (alphabetisch) und – falls vorhanden – `scenario.yaml`.

        Name eines Screens = Dateiname ohne Endung.
        """
        files = sorted(directory.glob("*.png"))
        if not files:
            raise DeviceError(f"Keine PNG-Screenshots in {directory}")
        scenario_path = directory / SCENARIO_FILE
        if "scenario" not in kwargs and scenario_path.is_file():
            kwargs["scenario"] = FakeScenario.load(scenario_path)
        return cls({f.stem: load_image(f) for f in files}, **kwargs)

    # --- Steuerung für Tests -----------------------------------------------------------------------

    @property
    def names(self) -> list[str]:
        return list(self._names)

    @property
    def current_screen(self) -> str:
        return self._names[self._index]

    @property
    def history(self) -> list[str]:
        return list(self._history)

    def show(self, name: str) -> None:
        """Screen fest anzeigen und in den `manual`-Modus wechseln."""
        self._set_screen(name)
        self.mode = "manual"

    def fail_next(self, error: DeviceError | None = None) -> None:
        """Der nächste Geräteaufruf wirft `error` (simuliert adb-Probleme)."""
        self._pending_error = error or DeviceError("simulierter Gerätefehler")

    # --- DeviceProtocol ----------------------------------------------------------------------------

    @property
    def serial(self) -> str:
        return self._serial

    def connect(self) -> None:
        self._raise_pending()
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def screen_size(self) -> Size:
        self._check()
        h, w = self._screens[self.current_screen].shape[:2]
        return Size(w, h)

    def screenshot(self) -> Frame:
        self._check()
        frame = Frame(self._screens[self.current_screen], self._clock.monotonic())
        self.screenshot_count += 1
        if self.mode == "sequential":
            if self._index < len(self._names) - 1:
                self._index += 1
            elif self.loop:
                self._index = 0
        return frame

    def tap(self, p: Point) -> None:
        self._record("tap", p)

    def swipe(self, a: Point, b: Point, duration_ms: int) -> None:
        self._record("swipe", a, b, duration_ms)

    def long_press(self, p: Point, duration_ms: int) -> None:
        self._record("long_press", p, duration_ms)

    def key(self, keycode: int) -> None:
        self._record("key", keycode)

    def input_text(self, text: str) -> None:
        self._record("text", text)

    def start_app(self, package: str, activity: str | None = None) -> None:
        self._record("start_app", package, activity)
        self.current_package = package
        self.activity = activity
        self._resume_screen = None
        if self.scenario is not None and self.scenario.start_app:
            self._history.clear()
            self._set_screen(self.scenario.start_app)

    def stop_app(self, package: str) -> None:
        self._record("stop_app", package)
        if self.current_package == package:
            self.current_package = None
            self.activity = None

    def bring_to_front(self, package: str) -> None:
        self._record("bring_to_front", package)
        if self.current_package == package:
            return
        self.current_package = package
        self.activity = None
        if self._resume_screen is not None:
            self._set_screen(self._resume_screen)
            self._resume_screen = None
        elif self.scenario is not None and self.scenario.start_app:
            self._history.clear()
            self._set_screen(self.scenario.start_app)

    def current_app(self) -> str | None:
        self._check()
        return self.current_package

    def current_activity(self) -> str | None:
        self._check()
        return self.activity

    def dump_ui(self) -> str | None:
        self._check()
        return self.ui_xml

    # --- intern ------------------------------------------------------------------------------------

    def _set_screen(self, name: str) -> None:
        if name not in self._screens:
            raise KeyError(f"Unbekannter Screen {name!r}, vorhanden: {', '.join(self._names)}")
        self._index = self._names.index(name)

    def _go(self, target: str) -> None:
        if target == BACK:
            if self._history:
                self._set_screen(self._history.pop())
            return
        self._history.append(self.current_screen)
        self._set_screen(target)

    def _apply(self, transition: Transition) -> None:
        if transition.app is not None and transition.app != self.current_package:
            if self._resume_screen is None:
                self._resume_screen = self.current_screen
            self.current_package = transition.app
            self.activity = None
        if transition.activity is not None:
            self.activity = transition.activity
        self._go(transition.goto)

    def _raise_pending(self) -> None:
        if self._pending_error is not None:
            error, self._pending_error = self._pending_error, None
            raise error

    def _check(self) -> None:
        self._raise_pending()
        if not self._connected:
            raise DeviceError("FakeDevice nicht verbunden")

    def _record(self, kind: str, *args: Any) -> None:
        self._check()
        record = InputRecord(kind, args)
        self.inputs.append(record)
        if self.scenario is not None:
            transition = self.scenario.target(self.current_screen, record)
            if transition is not None:
                self._apply(transition)
        if self.on_input is not None:
            self.on_input(self, record)
