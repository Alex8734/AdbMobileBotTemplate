"""Commands: GUI/CLI → Engine.

Steuer-Commands (`ControlCommand`) wirken sofort über `RunControl`, auch während ein Flow läuft.
Arbeits-Commands (`WorkCommand`) werden von der Engine sequenziell aus der Queue abgearbeitet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from adbbot.core.types import Point


class Command:
    __slots__ = ()


class ControlCommand(Command):
    __slots__ = ()


class WorkCommand(Command):
    __slots__ = ()


# --- Steuer-Commands -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pause(ControlCommand):
    pass


@dataclass(frozen=True, slots=True)
class Resume(ControlCommand):
    pass


@dataclass(frozen=True, slots=True)
class Stop(ControlCommand):
    pass


# --- Arbeits-Commands ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StartFlow(WorkCommand):
    flow_name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunAction(WorkCommand):
    action_name: str


@dataclass(frozen=True, slots=True)
class TapAt(WorkCommand):
    point: Point


@dataclass(frozen=True, slots=True)
class KeyEvent(WorkCommand):
    keycode: int


@dataclass(frozen=True, slots=True)
class CaptureFrame(WorkCommand):
    pass


@dataclass(frozen=True, slots=True)
class ConnectDevice(WorkCommand):
    serial: str | None = None


@dataclass(frozen=True, slots=True)
class ReloadTemplates(WorkCommand):
    pass
