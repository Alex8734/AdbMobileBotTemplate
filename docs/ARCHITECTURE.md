# AdbBotTemplate – Architektur- und Entwicklungsplan

> Ziel: Ein wiederverwendbares Python-Template für ADB-basierte Handy-Bots (Android),
> mit dem ein neuer Bot in wenigen Stunden aufgesetzt werden kann.
> Oberste Prioritäten: **Skalierbarkeit** und **Übersichtlichkeit**.
>
> Stand: Revision 2 (Design-Review eingearbeitet, siehe Abschnitt 12).

---

## Inhalt

1. [Leitprinzipien](#1-leitprinzipien)
2. [Technologie-Entscheidungen](#2-technologie-entscheidungen)
3. [Projektstruktur](#3-projektstruktur)
4. [Schichtenmodell](#4-schichtenmodell)
5. [Kernkonzepte im Detail](#5-kernkonzepte-im-detail)
   - 5.1 Core
   - 5.2 Device-Schicht
   - 5.3 Vision-Schicht
   - 5.4 Bot-Schicht (Context, Element, Page, Flow, Watcher, Action, Engine)
   - 5.5 GUI-Schicht
6. [Threading und Steuerung](#6-threading-und-steuerung)
7. [Datenfluss – ein kompletter Durchlauf](#7-datenfluss--ein-kompletter-durchlauf)
8. [Konventionen](#8-konventionen)
9. [Neuen Bot aus dem Template erstellen](#9-neuen-bot-aus-dem-template-erstellen)
10. [Entwicklungsphasen](#10-entwicklungsphasen)
11. [Spätere Erweiterungen](#11-spätere-erweiterungen)
12. [Designentscheidungen und verworfene Alternativen](#12-designentscheidungen-und-verworfene-alternativen)

---

## 1. Leitprinzipien

| Prinzip | Bedeutung für dieses Projekt |
|---|---|
| **Schichten mit klaren Grenzen** | Core ← Device ← Vision ← Bot ← GUI. Importe zeigen nur nach unten. Die GUI kennt den Bot nur über Events und Commands. |
| **Bot-Logik liest sich wie Prosa** | Ein Flow ist eine Folge benannter Schritte (`@step`). Wer `run()` liest, versteht den Ablauf ohne Implementierungsdetails. |
| **Alles, was erkannt wird, wird sichtbar** | Jeder Vision-Aufruf erzeugt automatisch ein Overlay (Rechteck + Text) für die GUI. Kein manuelles Zeichnen nötig. |
| **Kooperative Steuerung** | Pause, Stop und Timeouts wirken über `checkpoint()` in allen Warteschleifen, nie über Thread-Abbruch. Der Zustand bleibt immer konsistent. |
| **Austauschbarkeit durch Protokolle** | `DeviceProtocol`, `OcrEngine`, `Clock` sind Interfaces. Echtes Gerät ↔ Fake-Gerät ist ein Config-Eintrag. |
| **Kein globaler Zustand** | Alles hängt am `BotContext`. Testbar, mehrere Geräte möglich. |
| **Framework ist eine Library, das Projekt ist die App** | `adbbot` wird nie projektspezifisch angefasst. Ein Bot = eigenes Paket mit Pages, Flows, Watchers, Actions + Templates + Profil. |
| **Fehler sind Daten** | Schlägt ein Schritt endgültig fehl, wird automatisch ein Screenshot mit Overlays + Log-Auszug gespeichert. |
| **Konfiguration statt Code** | Timeouts, Schwellwerte, Gerät, Ziel-App in YAML – validiert per Pydantic. |

---

## 2. Technologie-Entscheidungen

| Bereich | Wahl | Begründung | Alternative |
|---|---|---|---|
| Sprache | Python 3.12 | Bereits im Projekt, riesiges CV-Ökosystem | – |
| Paketverwaltung | `uv` + `pyproject.toml` (hatchling) | Schnell, Lockfile, src-Layout mit zwei Paketen | pip + requirements.txt |
| ADB-Anbindung | `adbutils` | Screenshot über adb-Socket (Emulator ~50–100 ms, echtes Gerät 100–300 ms), Tap/Swipe/Keyevent, Geräteliste, App-Start | Eigener `subprocess`-Wrapper (als zweite `DeviceProtocol`-Implementierung vorgesehen) |
| Bildverarbeitung | `opencv-python-headless` + `numpy` | Template Matching, Farbchecks, Masken | – |
| OCR | `rapidocr-onnxruntime` (optional) + Adapter für `pytesseract` | Keine System-Abhängigkeit, gut bei Spiele-Fonts | EasyOCR/PaddleOCR (schwer) |
| GUI | **PySide6 (Qt)** | `QGraphicsView` ideal für Bild + Overlays + Zoom, Docks, Thread-sichere Zustellung in den GUI-Thread | Tkinter (Overlays/Zoom mühsam), DearPyGui (Docks/Log schwach), Web-UI (Overhead) |
| Config | `pydantic` + YAML (eigener Deep-Merge-Loader) | Validierung, Typen, Defaults; ENV-Override selbst gebaut (10 Zeilen) statt `pydantic-settings` | dataclasses + yaml |
| Logging | `structlog` mit Bus-Prozessor | Log-Zeilen sind Dicts mit Kontext (flow, step, page) → GUI kann filtern, ohne Strings zu parsen | std `logging` + `extra` |
| Tests | `pytest`, `pytest-qt` | Fake-Device + Fake-Clock → Flows laufen in Tests in Millisekunden | – |
| Code-Qualität | `ruff` (lint + format), `mypy --strict` für das Framework | Ein Tool für Lint und Format | black + flake8 |

### pyproject.toml

```toml
[project]
name = "adbbot-template"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "adbutils>=2.8",
    "opencv-python-headless>=4.10",
    "numpy>=2.0",
    "PySide6>=6.7",
    "pydantic>=2.8",
    "pyyaml>=6.0",
    "structlog>=24.0",
]

[project.optional-dependencies]
ocr = ["rapidocr-onnxruntime>=1.3"]
dev = ["pytest", "pytest-qt", "ruff", "mypy", "types-PyYAML"]

[project.scripts]
mybot = "mybot.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/adbbot", "src/mybot"]
```

---

## 3. Projektstruktur

```
AdbBotTemplate/
├── pyproject.toml
├── README.md
├── docs/
│   ├── ARCHITECTURE.md              ← dieses Dokument
│   └── HOWTO_NEW_BOT.md             ← Schritt-für-Schritt für neue Bots
│
├── config/
│   ├── default.yaml                 ← Framework-Defaults (Timeouts, Thresholds, Log-Level)
│   └── profiles/
│       └── example.yaml             ← Bot-spezifisch: Ziel-App, Gerät, Auflösung, Overrides
│
├── assets/
│   └── templates/                   ← PNG-Templates, gruppiert nach Page
│       ├── common/
│       │   ├── close_x.png
│       │   └── close_x@2.png        ← Variante (wird automatisch als Alternative geladen)
│       ├── home/
│       │   ├── shop_button.png
│       │   └── shop_button.json     ← optionale Metadaten (Region, Threshold, Referenz-Auflösung)
│       └── shop/
│
├── data/                            ← Laufzeitdaten (gitignored)
│   ├── failures/                    ← automatische Fehler-Screenshots
│   ├── screenshots/                 ← manuell gespeicherte Frames
│   └── <profile>/state.json         ← persistenter Bot-Zustand (optional, Phase 5)
│
├── src/adbbot/                      ← FRAMEWORK (projektunabhängig, später als eigenes Paket auslagerbar)
│   ├── __init__.py
│   ├── app.py                       ← Composition Root: build_context(), run_gui(), run_headless(), main()
│   ├── cli.py                       ← Argumente: --gui/--headless, --profile, --flow, --list-devices, --screenshot
│   │
│   ├── core/                        ← Grundbausteine, kennen keine andere Schicht
│   │   ├── types.py                 ← Point, Rect, Size, Match, Detection, Frame, StepResult
│   │   ├── config.py                ← Pydantic-Modelle + Loader (YAML-Merge + ENV)
│   │   ├── events.py                ← EventBus + alle Event-Dataclasses
│   │   ├── commands.py              ← Command-Dataclasses (GUI/CLI → Engine)
│   │   ├── state.py                 ← BotState-Enum + RunControl (Pause/Stop/Deadline, checkpoint)
│   │   ├── logging.py               ← structlog-Setup, Bus-Prozessor, Datei-/Konsolen-Ausgabe
│   │   ├── clock.py                 ← Clock-Protocol: RealClock, FakeClock (Tests)
│   │   └── exceptions.py            ← BotError-Hierarchie, BotStopped
│   │
│   ├── device/                      ← Schicht 1: Hardware-Zugriff
│   │   ├── protocol.py              ← DeviceProtocol (Interface)
│   │   ├── adb_device.py            ← Implementierung mit adbutils
│   │   ├── subprocess_device.py     ← Fallback: reines adb-CLI
│   │   ├── fake_device.py           ← Replay aus Screenshot-Ordner, loggt Inputs
│   │   ├── discovery.py             ← Geräte finden, verbinden, Reconnect mit Backoff
│   │   └── humanize.py              ← Zufälliger Offset/Delay/Swipe-Kurven (per Config abschaltbar)
│   │
│   ├── vision/                      ← Schicht 2: Bilderkennung
│   │   ├── template.py              ← Template-Dataclass, TemplateStore (lädt Assets, Varianten, Masken)
│   │   ├── matcher.py               ← TemplateMatcher (cv2.matchTemplate, Multi-Match, Region, Skalierung)
│   │   ├── ocr.py                   ← OcrEngine-Protocol + RapidOCR/Tesseract-Adapter
│   │   ├── color.py                 ← Pixel-/Farbbereichs-Checks
│   │   ├── overlay.py               ← DetectionStore: sammelt Detections pro Frame, rendert sie in ein Bild
│   │   └── vision.py                ← Vision-Fassade: capture / find / find_all / wait_for / read_text
│   │
│   ├── bot/                         ← Schicht 3: Ablauflogik
│   │   ├── context.py               ← BotContext (config, device, vision, events, control, clock, log, pages, store)
│   │   ├── element.py               ← Element (Deklaration, Descriptor) + BoundElement (Aktionen)
│   │   ├── page.py                  ← Page-Basisklasse (Page-Object-Pattern), PageRegistry
│   │   ├── flow.py                  ← Flow-Basisklasse, @step-Decorator, recover()-Hook
│   │   ├── watchers.py              ← @watcher: Popup-/Störungs-Handler, WatcherRunner
│   │   ├── actions.py               ← @action: Funktionen → Buttons in der GUI
│   │   ├── registry.py              ← Registries (Flows, Pages, Watchers, Actions) + discover()
│   │   ├── failure.py               ← Fehler-Snapshot: Frame + Overlays + Log-Auszug speichern
│   │   ├── engine.py                ← BotEngine: Worker-Thread, Command-Queue, Idle-Frames, Poll-Hook
│   │   ├── navigator.py             ← (Phase 5) go_to(Page) über deklarierte Übergänge
│   │   └── storage.py               ← (Phase 5) persistenter Key-Value-Store pro Profil
│   │
│   ├── gui/                         ← Schicht 4: Oberfläche
│   │   ├── app.py                   ← QApplication, Theme, Fenster starten
│   │   ├── main_window.py           ← Layout, Docks, Toolbar, Menü
│   │   ├── bridge.py                ← EventBus → Queue → QTimer-Drain → Qt-Signals (Thread-Grenze)
│   │   ├── widgets/
│   │   │   ├── screen_view.py       ← QGraphicsView: Frame + Overlays, Zoom, Cursor-Koordinaten, Klick→Tap
│   │   │   ├── overlay_item.py      ← QGraphicsItem für Rechteck + Label + Confidence
│   │   │   ├── debug_panel.py       ← Log mit Level-Filter, Quellen-Filter, Suche, Ringpuffer
│   │   │   ├── flow_panel.py        ← Flow-Auswahl, Step-Timeline mit Dauer, aktueller Step
│   │   │   ├── action_panel.py      ← Start/Pause/Stop + registrierte @actions (gruppiert)
│   │   │   ├── device_panel.py      ← Geräteliste, Verbinden/Trennen, Status
│   │   │   ├── status_bar.py        ← State, FPS, Laufzeit, letzter Frame
│   │   │   └── template_capture.py  ← Rechteck aufziehen → Template speichern → Store neu laden
│   │   └── resources/               ← Icons, QSS-Stylesheet
│   │
│   └── utils/
│       ├── geometry.py              ← Rect-Operationen, Skalierung zwischen Auflösungen
│       ├── retry.py                 ← Retry-Helper mit Backoff
│       └── paths.py                 ← Projekt-Pfade auflösen
│
├── src/mybot/                       ← PROJEKT (pro Bot umbenennen und anpassen)
│   ├── __init__.py
│   ├── __main__.py                  ← `python -m mybot` → adbbot.app.main(register)
│   ├── bootstrap.py                 ← register(): Pages/Flows/Watchers/Actions ans Framework melden
│   ├── base.py                      ← ProjectFlow(Flow): recover(), Page-Shortcuts (self.home, ...)
│   ├── pages/                       ← Eine Datei pro Screen der App
│   │   ├── home_page.py
│   │   └── shop_page.py
│   ├── flows/                       ← Eine Datei pro Ablauf
│   │   ├── daily_flow.py
│   │   └── farm_flow.py
│   ├── watchers.py                  ← Popups, Verbindungsabbrüche, Level-Up-Dialoge ...
│   └── actions.py                   ← Projektspezifische GUI-Buttons
│
├── scripts/
│   ├── capture_screenshots.py       ← Screenshot-Serie für FakeDevice aufnehmen
│   ├── capture_ads.py               ← Werbung aufzeichnen (Activity-Erkennung, UI-Dump)
│   └── test_template.py             ← Ein Template gegen einen Screenshot testen (CLI, mit Bildausgabe)
│
└── tests/
    ├── conftest.py                  ← Fixtures: fake_device, fake_clock, context, sample_frames
    ├── fixtures/screens/
    ├── test_config.py
    ├── test_events.py
    ├── test_matcher.py
    ├── test_element.py
    ├── test_flow.py                 ← Retry, Skip, optional, Timeout, Stop
    ├── test_watchers.py
    ├── test_engine.py
    └── test_gui_bridge.py
```

**Warum zwei Pakete unter `src/`?**
`adbbot` ist die Library, `mybot` die Anwendung mit dem Einstiegspunkt. Ein neuer Bot = `mybot` kopieren/umbenennen.
`adbbot` kann später unverändert als eigenes Paket veröffentlicht und per `pip install` in mehreren Bot-Projekten genutzt werden.

---

## 4. Schichtenmodell

```
┌─────────────────────────────────────────────────────────────────┐
│  GUI (PySide6)                                                  │
│  MainWindow · ScreenView · DebugPanel · FlowPanel · ActionPanel │
│        ▲ Events (Bridge)                   │ Commands           │
└────────┼───────────────────────────────────┼────────────────────┘
         │                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│  BOT                                                            │
│  BotEngine · BotContext · Flow/@step · Page · Element ·         │
│  Watcher · Action · Registries                                  │
│        │ nutzt                       ▲ poll_hook (Callback)     │
└────────┼─────────────────────────────┼──────────────────────────┘
         ▼                             │
┌─────────────────────────────────────────────────────────────────┐
│  VISION                                                         │
│  Vision · TemplateMatcher · OCR · Color · DetectionStore        │
│        │ nutzt                                                  │
└────────┼────────────────────────────────────────────────────────┘
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  DEVICE                                                         │
│  DeviceProtocol → AdbDevice | SubprocessDevice | FakeDevice     │
└─────────────────────────────────────────────────────────────────┘
         ▲
┌────────┴────────────────────────────────────────────────────────┐
│  CORE (überall verwendbar, kennt keine andere Schicht)          │
│  types · config · events · commands · state · logging · clock   │
└─────────────────────────────────────────────────────────────────┘
```

Regeln:

- **Importe zeigen nur nach unten.** `vision` importiert nie `bot`, `bot` importiert nie `gui`, `core` importiert nichts aus dem Framework.
- Zwei kontrollierte Ausnahmen nach oben, beide über **Callbacks** (Dependency Inversion), nicht über Importe:
  1. `EventBus` – Bot → GUI.
  2. `Vision.poll_hook` – ein Callback `(Frame) -> None`, den die Bot-Schicht setzt. Vision ruft ihn in jeder Warteschleife pro geholtem Frame auf. Darüber laufen `checkpoint()` und die Watcher (siehe 5.4).

---

## 5. Kernkonzepte im Detail

### 5.1 Core

#### `types.py`

```python
@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int

@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    w: int
    h: int
    @property
    def center(self) -> Point: ...
    def scaled(self, factor: float) -> "Rect": ...
    def contains(self, p: Point) -> bool: ...
    def random_point(self, margin: float = 0.2) -> Point: ...   # für humanisierte Taps

@dataclass(frozen=True, slots=True)
class Match:
    rect: Rect
    confidence: float
    label: str                  # Template-Name

@dataclass(frozen=True, slots=True)
class Detection:                # das, was die GUI zeichnet
    rect: Rect
    label: str
    kind: Literal["found", "missing", "text", "mark"]   # bestimmt Farbe/Stil in der GUI
    frame_ts: float
    confidence: float | None = None
    text: str | None = None

@dataclass(slots=True)
class Frame:                    # per Konvention unveränderlich: nie in frame.image schreiben
    image: np.ndarray           # BGR
    ts: float                   # clock.monotonic()
    @property
    def size(self) -> Size: ...
    def crop(self, rect: Rect) -> "Frame": ...
    def save(self, path: Path) -> None: ...

class StepResult(Enum):
    OK = auto()
    SKIPPED = auto()
    FAILED = auto()
```

#### `config.py`

Ein Pydantic-Modell pro Bereich. Ladereihenfolge (jede Stufe überschreibt die vorige):
`config/default.yaml` → `config/profiles/<name>.yaml` → ENV (`ADBBOT__DEVICE__SERIAL=...`) → CLI-Flags.

```python
class TargetAppConfig(BaseModel):
    package: str                                # z. B. "com.example.game"
    activity: str | None = None
    restart_if_not_running: bool = True

class DeviceConfig(BaseModel):
    serial: str | None = None                   # None = erstes gefundenes Gerät
    backend: Literal["adb", "subprocess", "fake"] = "adb"
    fake_screens_dir: Path | None = None
    reference_resolution: tuple[int, int] = (1080, 1920)
    command_timeout_s: float = 10.0             # damit Stop nicht an hängendem adb scheitert

class VisionConfig(BaseModel):
    default_threshold: float = 0.85
    templates_dir: Path = Path("assets/templates")
    ocr_engine: Literal["rapidocr", "tesseract", "none"] = "none"
    poll_interval_ms: int = 300                 # Frame-Abstand in wait_for-Schleifen

class BotConfig(BaseModel):
    step_timeout_s: float = 30.0
    default_retries: int = 1
    humanize_input: bool = True
    post_tap_delay_ms: tuple[int, int] = (150, 400)
    watcher_interval_ms: int = 1000             # Watcher höchstens so oft prüfen
    watcher_max_triggers_per_step: int = 5
    foreground_guard: bool = True               # App zurückholen, wenn sie den Vordergrund verliert
    foreground_check_interval_ms: int = 2000    # current_app() ist ein eigener adb-Aufruf
    idle_frame_interval_ms: int = 500           # Livebild ohne laufenden Flow (0 = aus)
    failure_snapshots: bool = True

class GuiConfig(BaseModel):
    enabled: bool = True
    max_log_lines: int = 5000
    click_to_tap: bool = False

class AppConfig(BaseModel):
    app: TargetAppConfig
    device: DeviceConfig = DeviceConfig()
    vision: VisionConfig = VisionConfig()
    bot: BotConfig = BotConfig()
    gui: GuiConfig = GuiConfig()
    log_level: str = "INFO"
    profile: str
```

#### `events.py` – EventBus

Der einzige Kanal vom Bot zur GUI. Thread-sicher, typisiert, ohne Qt-Abhängigkeit.
`publish` ruft Handler **synchron im Thread des Publishers** auf – Handler müssen daher billig sein
(die GUI-Bridge legt nur in eine Queue).

```python
class Event: ...

@dataclass(slots=True) class FrameCaptured(Event):      frame: Frame
@dataclass(slots=True) class DetectionsUpdated(Event):  frame_ts: float; detections: list[Detection]
@dataclass(slots=True) class LogEmitted(Event):         ts: float; level: str; source: str; message: str; context: dict
@dataclass(slots=True) class StateChanged(Event):       state: BotState
@dataclass(slots=True) class DeviceStatus(Event):       connected: bool; serial: str | None; size: Size | None
@dataclass(slots=True) class FlowStarted(Event):        flow: str
@dataclass(slots=True) class StepStarted(Event):        flow: str; step: str; index: int
@dataclass(slots=True) class StepFinished(Event):       flow: str; step: str; index: int; result: StepResult; duration_s: float; error: str | None
@dataclass(slots=True) class FlowFinished(Event):       flow: str; result: StepResult; cancelled: bool; error: str | None
@dataclass(slots=True) class WatcherTriggered(Event):   watcher: str
@dataclass(slots=True) class RegistriesChanged(Event):  ...   # nach ReloadTemplates o. ä.

class EventBus:
    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> Callable[[], None]   # gibt unsubscribe zurück
    def subscribe_all(self, handler: Callable[[Event], None]) -> Callable[[], None]
    def publish(self, event: Event) -> None
```

#### `commands.py` – Commands (GUI/CLI → Engine)

Zwei Klassen von Commands, die die Engine unterschiedlich behandelt (siehe 5.4 Engine):

```python
# Steuer-Commands: wirken SOFORT, auch während ein Flow läuft (über RunControl)
@dataclass class Pause(Command): ...
@dataclass class Resume(Command): ...
@dataclass class Stop(Command): ...

# Arbeits-Commands: werden sequenziell in der Queue abgearbeitet
@dataclass class StartFlow(Command):       flow_name: str; params: dict[str, Any] = field(default_factory=dict)
@dataclass class RunAction(Command):       action_name: str
@dataclass class TapAt(Command):           point: Point            # Debug: Klick im ScreenView
@dataclass class KeyEvent(Command):        keycode: int
@dataclass class CaptureFrame(Command):    ...
@dataclass class ConnectDevice(Command):   serial: str | None
@dataclass class ReloadTemplates(Command): ...
```

#### `state.py` – BotState und RunControl

```python
class BotState(Enum):
    DISCONNECTED = auto()
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()

class RunControl:
    """Thread-sicher. Wird vom GUI-Thread gesetzt und vom Bot-Thread in checkpoint() gelesen."""
    def request_pause(self) -> None
    def request_resume(self) -> None
    def request_stop(self) -> None
    def reset(self) -> None                       # vor jedem neuen Flow
    def set_deadline(self, monotonic_ts: float | None) -> None   # von @step gesetzt

    def checkpoint(self) -> None:
        """
        1. Stop angefordert   → raise BotStopped
        2. Pause angefordert  → blockiert (in 100-ms-Schritten, Stop bleibt möglich)
        3. Deadline überschritten → raise StepTimeout
        """
```

#### `exceptions.py`

```python
class BotError(Exception): ...                   # Basis aller fachlichen Fehler
class DeviceError(BotError): ...                 # adb weg, Timeout, ...
class StepFailed(BotError): ...                  # Schritt fehlgeschlagen → Retry möglich
class ElementNotFound(StepFailed): ...
class WrongPage(StepFailed): ...
class StepTimeout(StepFailed): ...
class WatcherLoop(StepFailed): ...               # ein Watcher feuert dauerhaft
class StepSkipped(Exception): ...                # Kontrollfluss: Schritt bewusst überspringen
class BotStopped(BaseException): ...             # ABSICHTLICH BaseException: ein `except Exception`
                                                 # in Projektcode darf einen Stop nie schlucken
```

#### `logging.py`

structlog mit zwei Ausgaben: Konsole/Datei (formatiert) und ein Prozessor, der jeden Log-Eintrag als
`LogEmitted` auf den Bus legt. `log.bind(flow=..., step=..., page=...)` liefert den Kontext,
nach dem das DebugPanel filtern kann.

#### `clock.py`

```python
class Clock(Protocol):
    def monotonic(self) -> float
    def sleep(self, seconds: float) -> None
```

`RealClock` und `FakeClock` (Tests: `sleep` rückt nur die Zeit vor). Alle Warte- und Timeout-Logik
im Framework benutzt ausschließlich `ctx.clock`, nie `time` direkt.

### 5.2 Device-Schicht

```python
class DeviceProtocol(Protocol):
    serial: str
    def connect(self) -> None
    def disconnect(self) -> None
    def is_connected(self) -> bool
    def screen_size(self) -> Size
    def screenshot(self) -> Frame
    def tap(self, p: Point) -> None
    def swipe(self, a: Point, b: Point, duration_ms: int) -> None
    def long_press(self, p: Point, duration_ms: int) -> None
    def key(self, keycode: int) -> None            # KEY_BACK, KEY_HOME, ...
    def input_text(self, text: str) -> None
    def start_app(self, package: str, activity: str | None = None) -> None
    def stop_app(self, package: str) -> None
    def current_app(self) -> str | None
    def current_activity(self) -> str | None       # z. B. com.google.android.gms.ads.AdActivity
    def bring_to_front(self, package: str) -> None  # bestehenden Task nach vorne, kein Neustart
    def dump_ui(self) -> str | None                # uiautomator-XML, langsam; None bei Fehlschlag
```

- **`AdbDevice`** (adbutils). Jeder Aufruf hat ein Timeout (`command_timeout_s`) und wirft `DeviceError`,
  damit ein hängendes `adb` den Bot-Thread nicht dauerhaft blockiert und Stop wirksam bleibt.
- **`SubprocessDevice`**: `adb -s X exec-out screencap -p` / `adb shell input ...`. Langsamer, ohne Zusatzpaket.
- **`FakeDevice`**: liefert Frames aus einem Ordner (sequenziell oder per Skript `name → Frame`), loggt alle Inputs
  in eine Liste (`device.inputs`), die Tests prüfen können. Damit lassen sich Flows ohne Handy entwickeln und testen.
- **`humanize.py`**: Dekoriert ein `DeviceProtocol` (Decorator-Pattern): Tap auf zufälligen Punkt im Rect statt
  exakt Center, zufälliges Post-Delay, leichte Swipe-Kurven. Per Config abschaltbar; FakeDevice ohne Humanize.
- **`discovery.py`**: `list_devices()`, `connect(serial | None)`, Reconnect mit Backoff. Publiziert `DeviceStatus`.

### 5.3 Vision-Schicht

#### Templates

PNG + optionales JSON mit gleichem Namen. Varianten mit `@n`-Suffix werden automatisch als Alternativen
desselben Templates geladen (z. B. Button im gedrückten/ungedrückten Zustand, zwei Themes).

```json
{
  "threshold": 0.88,
  "region": [0, 1500, 1080, 420],
  "reference_resolution": [1080, 1920],
  "mask": "shop_button_mask.png",
  "grayscale": false
}
```

`grayscale: true` matcht in Graustufen – etwa 3x schneller und für helle Symbole auf wechselndem
Hintergrund (X, ▶|) sogar trennschärfer. Einfarbige Bildbereiche zählen nie als Treffer: maskiertes
`TM_CCOEFF_NORMED` liefert dort sonst Rechenmüll (Werte > 1).

`TemplateStore` lädt lazy, cached als numpy, indexiert nach `"<page>/<name>"`.
`TemplateStore.validate(names)` prüft beim Start, dass alle von Pages/Elementen/Watchern referenzierten
Templates existieren – Tippfehler fallen beim Start auf, nicht nach 20 Minuten Laufzeit.

#### Vision-Fassade

Das Einzige aus dieser Schicht, was die Bot-Schicht benutzt. **Keine Geräte-Eingaben** – Vision sieht nur.

```python
class Vision:
    poll_hook: Callable[[Frame], None] | None = None   # wird von der Engine gesetzt

    def capture(self) -> Frame                          # Screenshot + FrameCaptured-Event + DetectionStore für neuen Frame
    @property
    def last_frame(self) -> Frame | None

    # Einzelbild-Operationen: frame=None → frischer Screenshot
    def find(self, template: str, *, frame: Frame | None = None, region: Rect | None = None, threshold: float | None = None) -> Match | None
    def find_all(self, template: str, ...) -> list[Match]
    def exists(self, template: str, ...) -> bool
    def read_text(self, region: Rect, *, frame=None) -> str
    def read_number(self, region: Rect, *, frame=None) -> int | None
    def pixel(self, p: Point, *, frame=None) -> tuple[int, int, int]
    def color_matches(self, p: Point, expected: tuple[int,int,int], tolerance: int = 20, *, frame=None) -> bool

    # Warteschleifen: capture → poll_hook(frame) → prüfen → clock.sleep(poll_interval)
    def wait_for(self, template: str, *, timeout: float, region=None, threshold=None) -> Match      # wirft ElementNotFound
    def wait_for_any(self, templates: list[str], *, timeout: float) -> Match
    def wait_until_gone(self, template: str, *, timeout: float) -> None

    overlay: DetectionStore
```

**Frame-Regel:** Jeder Aufruf ohne `frame=` macht einen frischen Screenshot. Wer mehrere Prüfungen auf
demselben Bild braucht, holt sich einmal `frame = self.vision.capture()` und reicht es weiter.
Es gibt bewusst keinen "aktuellen Frame"-Cache (Begründung in Abschnitt 12).

**Automatisches Overlay:** Jeder `find*`/`read_*`/`wait_*`-Aufruf legt seine Detection in den `DetectionStore`
(`found` = grün mit Confidence, `missing` = rot gestrichelt in der Suchregion, `text` = gelb mit erkanntem Text).
Der Store gehört zum aktuellen Frame (`frame_ts`) und wird bei `capture()` geleert; nach jeder Änderung
wird `DetectionsUpdated` publiziert. Für manuelle Markierungen: `self.vision.overlay.mark(rect, "label")`.
`DetectionStore.render(frame) -> np.ndarray` zeichnet die Overlays in ein Bild – genutzt für Fehler-Snapshots.

#### Auflösungsunabhängigkeit

Templates tragen ihre Referenzauflösung. Weicht das Gerät ab, wird ein Skalierungsfaktor berechnet und
Templates werden beim Laden skaliert; Regionen in Pages werden in Referenz-Koordinaten angegeben und beim
Zugriff umgerechnet. **Ehrlicher Hinweis:** Matching nach Skalierung ist merklich unzuverlässiger.
Empfohlen ist, im Profil eine feste Auflösung vorzugeben (Emulator) und die Skalierung nur als Notlösung zu sehen.

### 5.4 Bot-Schicht

#### `context.py` – BotContext

```python
@dataclass
class BotContext:
    config: AppConfig
    device: DeviceProtocol
    vision: Vision
    events: EventBus
    control: RunControl
    clock: Clock
    log: BoundLogger
    pages: PageRegistry
    store: Storage | None = None          # Phase 5

    def checkpoint(self) -> None:         # = control.checkpoint()
    def sleep(self, seconds: float) -> None:
        """Schlafen in 100-ms-Schritten, jeder Schritt mit checkpoint()."""
    def page(self, cls: type[P]) -> P:    # typsicher: ctx.page(HomePage) -> HomePage
    def element(self, template: str, **kw) -> BoundElement   # Ad-hoc-Element ohne Page
```

#### Element

Ein Element ist ein benannter, wiederfindbarer Bestandteil eines Screens. Es wird als **Klassenattribut**
der Page deklariert und ist ein **Descriptor**: Beim Zugriff über eine Page-Instanz entsteht ein
`BoundElement`, das den Context kennt. So bleibt die Deklaration frei von Laufzeit-Objekten.

```python
class Element:
    def __init__(self, template: str | list[str], *, region: Rect | None = None,
                 threshold: float | None = None, name: str | None = None): ...
    def __set_name__(self, owner: type, name: str) -> None: ...     # Name automatisch aus dem Attribut
    def __get__(self, page: "Page | None", owner: type) -> "BoundElement | Element": ...

class TextElement(Element):    # OCR-Region
class ColorElement(Element):   # Pixel-/Farbcheck, z. B. "Button aktiv" an der Farbe erkennen

class BoundElement:
    def find(self, *, frame: Frame | None = None) -> Match | None
    def exists(self, *, frame=None) -> bool
    def wait(self, *, timeout: float | None = None) -> Match           # wirft ElementNotFound
    def wait_gone(self, *, timeout: float | None = None) -> None
    def tap(self, *, timeout: float | None = None) -> Match           # wait + humanisierter Tap + Post-Delay
    def tap_if_present(self, *, frame=None) -> bool
    def read_text(self) -> str                                        # TextElement
    def read_number(self) -> int | None
```

Alle Geräte-Eingaben aus der Bot-Logik laufen über `BoundElement` oder direkt über `ctx.device`.
Vision tappt nie.

#### Page (Page-Object-Pattern)

Eine Page = ein Screen der App. Sie deklariert ihre Elemente, ihren Erkennungsanker und Navigationsmethoden.

```python
class HomePage(Page):
    name = "home"
    anchor = Element("home/logo", region=Rect(0, 0, 1080, 200))        # Page ist aktiv, wenn sichtbar

    shop_button = Element("home/shop_button", region=Rect(0, 1600, 1080, 320))
    daily_gift  = Element("home/daily_gift", threshold=0.9)
    gold        = TextElement(region=Rect(700, 40, 300, 60))

    def open_shop(self) -> "ShopPage":
        self.shop_button.tap()
        return self.ctx.page(ShopPage).wait_until_active()
```

```python
class Page(ElementAccess):
    name: ClassVar[str]
    anchor: ClassVar[Element]
    def __init__(self, ctx: BotContext): ...
    def is_active(self, *, frame=None) -> bool
    def wait_until_active(self, timeout: float | None = None) -> Self
    def ensure_active(self) -> Self                       # wirft WrongPage
```

`ElementAccess` ist ein Mixin für Page **und** Flow mit Kurzformen für Ad-hoc-Templates,
die intern `ctx.element(...)` benutzen:

```python
self.el("common/close_x")           -> BoundElement
self.tap("common/close_x")          # wait + tap
self.tap_if_present("common/close_x") -> bool
self.exists("common/close_x")       -> bool
self.wait_for("popup/claim", timeout=5) -> Match
```

#### Flow + @step

Ein Flow ist ein Ablauf. `run()` ist die **Inhaltsangabe**, die Steps sind die Kapitel.

```python
class DailyFlow(ProjectFlow):
    name = "daily"
    description = "Tägliche Belohnungen einsammeln"

    def run(self) -> None:
        self.ensure_app_running()          # generischer Step aus der Flow-Basisklasse
        self.go_to_home()
        self.collect_daily_gift()
        self.collect_mail()
        self.close_popups()

    @step("Zur Startseite navigieren", retries=2)
    def go_to_home(self) -> None:
        if not self.home.is_active():
            self.device.key(KEY_BACK)
            self.home.wait_until_active(timeout=10)

    @step("Tagesgeschenk abholen")
    def collect_daily_gift(self) -> None:
        if not self.home.daily_gift.exists():
            raise StepSkipped("Kein Geschenk verfügbar")
        self.home.daily_gift.tap()
        self.tap("popup/claim_button")

    @step("Post abholen", optional=True)        # Fehler wird geloggt, Flow läuft weiter
    def collect_mail(self) -> None: ...

    @step("Popups schließen")
    def close_popups(self) -> None:
        for _ in range(3):
            if not self.tap_if_present("common/close_x"):
                break
            self.sleep(0.5)
```

`self.home` kommt aus der projektspezifischen Basisklasse (typsicher, keine Attribut-Magie):

```python
# mybot/base.py
class ProjectFlow(Flow):
    @property
    def home(self) -> HomePage: return self.ctx.page(HomePage)
    @property
    def shop(self) -> ShopPage: return self.ctx.page(ShopPage)

    def recover(self, error: StepFailed) -> None:
        """Zwischen zwei Retry-Versuchen: bekannte Popups schließen, ggf. Back."""
        self.tap_if_present("common/close_x")
```

Was `@step(title, *, retries=None, optional=False, timeout=None)` automatisch macht:

1. `checkpoint()` vor dem Start (Pause/Stop respektieren).
2. `StepStarted`-Event mit laufender Nummer → FlowPanel zeigt "3 · Tagesgeschenk abholen".
3. Log-Kontext binden (`step=...`), damit das DebugPanel nach Step filtern kann.
4. Deadline setzen (`RunControl.set_deadline`) – der Timeout ist **kooperativ** und wird in jedem
   `checkpoint()` geprüft, also in jeder Warteschleife.
5. Fängt `StepSkipped` → Ergebnis `SKIPPED`.
6. Fängt `StepFailed` (inkl. `ElementNotFound`, `WrongPage`, `StepTimeout`) → `flow.recover(error)` →
   Step-Methode erneut ausführen, bis `retries` erschöpft. **Achtung:** Der Retry führt die gesamte
   Step-Methode nochmal aus. Steps müssen daher so geschrieben sein, dass ein zweiter Durchlauf harmlos ist
   (Zustand prüfen statt blind tappen).
7. Endgültiger Fehler: Fehler-Snapshot speichern (`failure.py`: Frame mit Overlays + letzte 50 Log-Zeilen
   nach `data/failures/<ts>_<flow>_<step>.png/.json`), dann `optional` → weiter, sonst Flow abbrechen.
8. `BotStopped` wird **nie** gefangen und läuft bis zur Engine durch.
9. `StepFinished`-Event mit Dauer und Ergebnis.

`Flow`-Basisklasse: `ctx`, `device`, `vision`, `log`, `sleep()`, `params` (dict aus `StartFlow`),
`run_flow(OtherFlow)` für Komposition (Steps des Unterflows erscheinen mit Präfix), generische Steps
`ensure_app_running()` und `restart_app()` auf Basis von `config.app`.

Der Decorator ist mit `ParamSpec` typisiert, damit mypy die Signatur der Step-Methoden behält.

#### Watcher – Störungen, die überall auftreten können

Popups ("Bewerte die App", "Verbindung verloren", Level-Up) tauchen unabhängig vom aktuellen Step auf.
Sie in jedem Step zu behandeln, skaliert nicht. Watcher sind global registrierte Handler,
die die Engine in jeder Vision-Warteschleife über den `poll_hook` prüft – auf dem ohnehin geholten Frame,
ohne zusätzlichen Screenshot.

```python
# mybot/watchers.py
@watcher("Verbindung verloren", template="common/reconnect_button", priority=10, cooldown_s=5)
def reconnect(ctx: BotContext, match: Match) -> None:
    ctx.device.tap(match.rect.center)

@watcher("Bewertungs-Popup", template="common/rate_later", region=Rect(0, 800, 1080, 600))
def dismiss_rating(ctx: BotContext, match: Match) -> None:
    ctx.device.tap(match.rect.center)
```

`WatcherRunner` (in der Engine):

- prüft höchstens alle `watcher_interval_ms`, nach Priorität sortiert, nur wenn ein Flow läuft
  (Idle-Modus konfigurierbar);
- ist **nicht reentrant**: während ein Watcher-Handler läuft, werden keine Watcher geprüft
  (sonst Rekursion, wenn der Handler selbst `wait_for` benutzt);
- zählt Auslösungen pro Step; ab `watcher_max_triggers_per_step` wird `WatcherLoop` geworfen
  (ein Popup, das nie verschwindet, darf nicht zur Endlosschleife werden);
- publiziert `WatcherTriggered` und loggt mit `source=watcher`;
- überspringt Watcher, deren `group` gerade pausiert ist. Beispiel: Der Bot startet selbst eine
  Bonus-Werbung, die bis zum Ende laufen muss. Der Werbe-Watcher (`group="ads"`) darf sie nicht schließen:

  ```python
  with ctx.suspend_watchers("ads"):
      ...  # Werbung abwarten
  ```
- bestätigt Treffer optional über `confirm(ctx, match)` – z. B. über die Activity, damit ein X der App selbst
  nicht für eine Werbung gehalten wird;
- pausiert während des Handlers die Step-Deadline (`control.deadline_paused()`): Eine 60-s-Werbung, die ein
  Watcher wegdrückt, lässt den unterbrochenen Step nicht in den Timeout laufen. Gilt auch für den
  Vordergrund-Wächter.

#### Vordergrund-Wächter – App zurückholen

Ein Tap auf eine Werbung öffnet oft den Play Store oder den Browser. Dort finden Template-Watcher nichts.
`ForegroundGuard` (`bot/foreground.py`) läuft deshalb im `poll_hook` vor den Watchern, solange ein Flow läuft:

- prüft höchstens alle `foreground_check_interval_ms` per `device.current_app()`;
- ist eine fremde App vorne (nicht `app.package`, nicht in `app.foreground_allowlist`, nicht `None` wie beim
  Sperrbildschirm), holt `device.bring_to_front()` den **bestehenden** Task per Launcher-Intent zurück
  (kein Neustart, Spielstand bleibt) und wartet bis `app.start_timeout_s`, sonst `StepFailed`;
- zählt wie der `WatcherRunner` pro Step mit (`WatcherLoop`), publiziert `WatcherTriggered("App im Vordergrund")`;
- lässt sich pro Block mit `ctx.suspend_watchers("foreground")` abschalten, z. B. für einen Flow, der absichtlich
  die Systemeinstellungen öffnet.

#### Actions – GUI-Buttons aus Code

```python
# mybot/actions.py
@action("Screenshot speichern", group="Debug")
def save_screenshot(ctx: BotContext) -> None:
    ctx.vision.capture().save(paths.screenshots() / f"{now_str()}.png")

@action("Shop öffnen (Test)", group="Test")
def test_shop(ctx: BotContext) -> None:
    ctx.page(HomePage).open_shop()
```

`ActionRegistry` sammelt alle `@action`-Funktionen; das `ActionPanel` erzeugt daraus gruppierte Buttons.
Klick → `RunAction`-Command → Engine führt die Funktion im Bot-Thread aus (mit vollem Context,
Overlays, Logging). Flows erscheinen zusätzlich als startbare Einträge im FlowPanel.

#### Registries und Bootstrap

```python
# mybot/bootstrap.py
def register(reg: Registries) -> None:
    reg.discover(mybot.pages)      # sammelt alle Page-Subklassen im Paket
    reg.discover(mybot.flows)      # sammelt alle Flow-Subklassen
    reg.discover(mybot.watchers)   # sammelt @watcher
    reg.discover(mybot.actions)    # sammelt @action
```

Explizit aufgerufen, automatisch gesammelt. Nach dem Bootstrap ruft `app.build_context()`
`TemplateStore.validate()` mit allen referenzierten Template-Namen auf.

#### BotEngine

```python
class BotEngine:
    def __init__(self, ctx: BotContext, registries: Registries): ...
    def start(self) -> None                     # startet Worker-Thread
    def submit(self, cmd: Command) -> None      # thread-sicher, aus GUI/CLI
    def shutdown(self, timeout: float = 5) -> None

    def submit(self, cmd):
        match cmd:
            case Pause():  self.ctx.control.request_pause()    # sofort wirksam, KEINE Queue
            case Resume(): self.ctx.control.request_resume()
            case Stop():   self.ctx.control.request_stop()
            case _:        self._queue.put(cmd)                # sequenziell

    def _loop(self):                                          # Bot-Thread
        while not self._shutdown:
            try:
                cmd = self._queue.get(timeout=idle_interval)
            except queue.Empty:
                self._idle_tick()                              # Livebild + Reconnect-Check
                continue
            self._handle(cmd)

    def _run_flow(self, name, params):
        self.ctx.control.reset()
        flow = self.registries.flows.create(name, self.ctx, params)
        self._set_state(RUNNING); publish(FlowStarted)
        try:
            flow.run()
            publish(FlowFinished(OK))
        except BotStopped:
            publish(FlowFinished(cancelled=True))
        except BotError as e:
            publish(FlowFinished(FAILED, error=str(e)))
        finally:
            self._set_state(IDLE)

    def _on_poll(self, frame: Frame) -> None:                 # = vision.poll_hook
        self.ctx.checkpoint()
        self.watchers.run(frame)
```

Wichtige Eigenschaften:

- **Steuer-Commands umgehen die Queue.** Während ein Flow läuft, ist der Bot-Thread in `flow.run()` und
  liest die Queue nicht. Pause/Stop müssen daher über `RunControl` laufen, das `checkpoint()` liest.
- Arbeits-Commands sind sequenziell: Ein `RunAction` während eines laufenden Flows wird danach ausgeführt.
  Die GUI deaktiviert währenddessen die Buttons (außer Pause/Stop).
- Der Idle-Tick liefert ein Livebild, wenn kein Flow läuft, und versucht bei `DeviceError` einen Reconnect.
- Es gibt genau **einen** Bot-Thread pro Engine. Flows brauchen nie eigenes Threading.

### 5.5 GUI-Schicht

#### Layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Toolbar:  [⟲ Verbinden] [▶ Start] [⏸ Pause] [■ Stop]  Flow: [daily ▾]     │
│           [📷 Frame] [✂ Template aufnehmen] [☐ Klick = Tap]  ● emulator-5554 │
├────────────────────────────┬─────────────────────────────────────────────┤
│ DEBUG LOG                  │ SCREEN VIEW                                 │
│ [Level ▾][Quelle ▾][Suche] │ ┌─────────────────────────────────────────┐ │
│ 12:00:01 INFO  daily       │ │                                         │ │
│   ▶ 1 · Zur Startseite     │ │   ┌────────────┐                        │ │
│ 12:00:02 DEBUG vision      │ │   │shop_button │ 0.93                   │ │
│   find home/shop_button    │ │   └────────────┘                        │ │
│   → (412,1650) 0.93        │ │                                         │ │
│ 12:00:03 WARN  watcher     │ │        ┌ ─ ─ ─ ─ ┐                      │ │
│   Bewertungs-Popup         │ │         daily_gift  ✗                   │ │
├────────────────────────────┤ │        └ ─ ─ ─ ─ ┘                      │ │
│ FLOW                       │ │                                         │ │
│ daily · RUNNING · 00:14    │ │                                         │ │
│ ✔ 1 Zur Startseite   1.2s  │ │                                         │ │
│ ▶ 2 Tagesgeschenk    …     │ │                                         │ │
├────────────────────────────┤ │                                         │ │
│ ACTIONS                    │ │                                         │ │
│ ▾ Debug                    │ │                                         │ │
│   [Screenshot speichern]   │ └─────────────────────────────────────────┘ │
│ ▾ Test                     │ Zoom 45%   Cursor (523, 1211)               │
│   [Shop öffnen (Test)]     │                                             │
├────────────────────────────┴─────────────────────────────────────────────┤
│ RUNNING · 3.2 fps · Laufzeit 00:02:14 · letzter Frame vor 0.3 s          │
└──────────────────────────────────────────────────────────────────────────┘
```

Linke Spalte als **Dock-Widgets** (verschieb-/ausblendbar), rechts der `ScreenView`. Layout wird in
`QSettings` gespeichert.

#### Widgets

| Widget | Verantwortung |
|---|---|
| `ScreenView` | `QGraphicsView` mit Pixmap-Item für den Frame und Overlay-Items. Mausrad-Zoom, Fit-to-Window, Cursor-Koordinaten in Geräte-Pixeln. Bei aktivem "Klick = Tap": Klick → `TapAt`-Command. Der Frame wird beim Umwandeln in `QImage` **kopiert** (das numpy-Array gehört dem Bot-Thread). Dropdown **Ansicht** darüber: Livebild oder ein Zwischenbild aus `vision.debug_image(name, bild)` (Event `DebugImageUpdated`, in der Bridge pro Name koalesziert); Template-Aufnahme schaltet immer aufs Livebild. |
| `OverlayItem` | Rechteck (durchgezogen/gestrichelt), Label, Confidence/Text. Stil nach `Detection.kind`. Es werden nur Detections zum aktuell angezeigten Frame (`frame_ts`) gezeichnet; Toggle "letzte Overlays halten". |
| `DebugPanel` | `QPlainTextEdit` (performant) + Level-Filter + Quellen-Filter (flow/step/vision/watcher/device) + Textsuche + Auto-Scroll + Ringpuffer (`max_log_lines`). Farbcodierung pro Level. |
| `FlowPanel` | Dropdown aller Flows, Parameter-Feld (JSON, optional), Step-**Timeline** (fertige Steps mit Dauer und Ergebnis, aktueller Step hervorgehoben). Kein "x/y"-Zähler, weil die Gesamtzahl der Steps vorab nicht bekannt ist (Steps sind Methodenaufrufe, teils bedingt). |
| `ActionPanel` | Buttons aus `ActionRegistry`, gruppiert als Collapsible-Sections. Während ein Flow läuft deaktiviert (außer Pause/Stop). |
| `DevicePanel` | Geräteliste (Refresh), Verbinden/Trennen (`ConnectDevice`), Auflösung, Backend. |
| `StatusBar` | State, FPS, Laufzeit, Alter des letzten Frames, Watcher-Auslösungen. |
| `TemplateCapture` | Modus im ScreenView: Rechteck aufziehen → Dialog (Page, Name, Threshold, Region übernehmen?) → speichert PNG (+ JSON) nach `assets/templates/<page>/` → `ReloadTemplates`-Command. Beschleunigt die Bot-Entwicklung am meisten. |

#### Bridge – die Thread-Grenze

```python
class EventBridge(QObject):
    # Signals werden nur innerhalb des GUI-Threads emittiert (aus _drain)
    frame_ready        = Signal(object)          # Frame
    detections_ready   = Signal(object)          # DetectionsUpdated
    log_ready          = Signal(object)          # LogEmitted
    state_changed      = Signal(object)
    step_event         = Signal(object)          # StepStarted / StepFinished / FlowStarted / FlowFinished
    device_status      = Signal(object)

    def __init__(self, bus: EventBus):
        self._queue: queue.SimpleQueue[Event] = queue.SimpleQueue()
        self._latest_frame: Frame | None = None            # unter Lock, wird koalesziert
        bus.subscribe_all(self._enqueue)                   # läuft im Bot-Thread, nur put()
        self._timer = QTimer(self, interval=33)            # ~30 Hz im GUI-Thread
        self._timer.timeout.connect(self._drain)

    def _drain(self):
        # 1. Neuesten Frame (falls vorhanden) einmal emittieren – ältere Frames verfallen
        # 2. Alle anderen Events in Reihenfolge emittieren (max N pro Tick, Rest im nächsten)
```

Warum Queue + Timer statt ein Qt-Signal pro Event aus dem Bot-Thread: Frames müssen koalesziert werden
(die GUI zeichnet nur den neuesten), Log-Bursts dürfen die GUI nicht blockieren, und die Thread-Grenze
liegt an genau einer Stelle, die man testen kann.

---

## 6. Threading und Steuerung

```
GUI-Thread (Qt Main)                         Bot-Thread (BotEngine)
──────────────────────                       ────────────────────────────
Klick "Start"                                _loop(): cmd = queue.get()
  engine.submit(StartFlow) ── Queue ───────►  _run_flow()
                                               step → element → vision.wait_for
Klick "Pause"                                    capture → poll_hook(frame)
  engine.submit(Pause) ── RunControl ──────►        ctx.checkpoint()  ← blockiert hier bei Pause
                                                    watchers.run(frame)
                                               events.publish(...)
  bridge._enqueue()  ◄──── synchron ────────── (im Bot-Thread, nur Queue-put)
  QTimer → _drain → Signals → Widgets
```

- Gerätezugriff **nur** aus dem Bot-Thread. Die GUI schickt `TapAt`/`KeyEvent`/`CaptureFrame` als Command.
- `checkpoint()` wird aufgerufen: am Step-Anfang, in `ctx.sleep()`, in jeder Vision-Warteschleife (über den
  Poll-Hook). Dadurch reagieren Pause/Stop/Timeout in ≤ `poll_interval_ms`.
- Stop kann einen laufenden `adb`-Aufruf nicht abbrechen; deshalb haben Device-Aufrufe ein Timeout.
- Nach Stop: `RunControl.reset()` beim nächsten Start. Ein Stop ist kein Fehler (`FlowFinished(cancelled=True)`).
- Headless-Modus (`--headless --flow daily`): Engine + Konsolen-Logger, kein Qt. Gleiche Flows, gleicher Code.
  Ctrl-C → `Stop`.

---

## 7. Datenfluss – ein kompletter Durchlauf

1. `python -m mybot --gui --profile example` (oder `uv run mybot ...`).
2. `adbbot.app.main(register)`: Config laden (default → profile → ENV → CLI), Logging aufsetzen.
3. `build_context()`: Device per Factory (`config.device.backend`), `TemplateStore`, `Vision`, `EventBus`,
   `RunControl`, `Clock`, `PageRegistry` → `BotContext`.
4. `register(registries)` aus `mybot.bootstrap` → Pages, Flows, Watchers, Actions. Dann `TemplateStore.validate()`.
5. `BotEngine(ctx, registries)`, setzt `vision.poll_hook = engine._on_poll`, startet den Worker-Thread.
   Der Idle-Tick verbindet das Gerät und liefert Livebilder.
6. GUI startet, `EventBridge` hängt am Bus, Panels füllen sich aus den Registries.
7. Klick auf Start → `StartFlow("daily")` → Engine instanziiert `DailyFlow(ctx, params)` und ruft `run()`.
8. `@step` publiziert `StepStarted` → FlowPanel zeigt den Step in der Timeline.
9. Step ruft `self.home.shop_button.tap()` → `Vision.wait_for()` → Screenshot → Poll-Hook (checkpoint, Watcher)
   → Matching → Detection im Store → `DetectionsUpdated` → Overlay im ScreenView → `device.tap()` (humanisiert).
10. Ein Watcher erkennt ein Popup auf einem der Poll-Frames, schließt es, `WatcherTriggered` erscheint im Log.
11. Log-Zeilen laufen über den structlog-Prozessor auf den Bus → Bridge → DebugPanel.
12. Flow endet → `FlowFinished` → Buttons wieder aktiv, State IDLE, Idle-Tick übernimmt das Livebild.

---

## 8. Konventionen

- **Typisierung überall**, `mypy --strict` für `adbbot/`.
- **Dataclasses `frozen=True, slots=True`** für Werttypen. `Frame` ist nicht frozen (numpy), wird aber nie mutiert.
- **Ein Screen = eine Page-Datei, ein Ablauf = eine Flow-Datei.** Keine Sammeldateien.
- **Template-Namen**: `<page>/<element>` in snake_case; Dateiname = Elementname; Varianten `name@2.png`.
- **Steps benennen wie eine Überschrift**: "Zur Startseite navigieren", nicht "step1".
- **Steps sind wiederholbar**: Zustand prüfen, dann handeln – nie blind tappen (Retry führt die Methode erneut aus).
- **Kein `time.sleep()` und kein `time.monotonic()`** im Framework und in Flows → `ctx.sleep()` / `ctx.clock`.
- **Kein `cv2`-Aufruf außerhalb von `vision/`.** Kein `device`-Aufruf außerhalb von `bot/` (und Actions).
- **Kein Gerätezugriff aus der GUI.** Immer über Commands.
- **`BotStopped` niemals fangen.** `except Exception` ist in Steps erlaubt, schluckt Stop aber nicht (BaseException).
- **Logging**: `self.log.info("Nachricht", key=value)`, nie `print`. Quellen: `flow`, `step`, `page`, `vision`, `watcher`, `device`, `engine`, `gui`.
- **Docstring pro Flow, Page und Watcher** (eine Zeile: was, wann).
- `ruff format` + `ruff check` + `mypy` vor jedem Commit (pre-commit-Hook).

---

## 9. Neuen Bot aus dem Template erstellen

1. Repo als Template verwenden, `src/mybot` umbenennen (Paketname, `[project.scripts]`, `packages`).
2. `config/profiles/<bot>.yaml` anlegen: `app.package`, Serial/Backend, Referenzauflösung.
3. App auf dem Gerät öffnen, GUI mit `--profile <bot>` starten, per **Template-Capture** für jeden Screen
   den Anchor und die Buttons ausschneiden.
4. Pro Screen eine `pages/<screen>_page.py` mit Elementen und Navigationsmethoden anlegen.
5. `base.py`: `ProjectFlow` mit Page-Shortcuts und `recover()`.
6. `watchers.py`: bekannte Popups eintragen.
7. Pro Ablauf eine `flows/<name>_flow.py` mit `run()` und `@step`-Methoden anlegen.
8. Flow in der GUI starten, Overlays und Log beobachten, Thresholds in den Template-JSONs nachjustieren.
   Fehlgeschlagene Steps in `data/failures/` ansehen.
9. Screenshot-Serie mit `scripts/capture_screenshots.py` aufnehmen → Tests mit `FakeDevice` + `FakeClock`.

Details in `docs/HOWTO_NEW_BOT.md`.

---

## 10. Entwicklungsphasen

Jede Phase ist für sich lauffähig und testbar.

### Phase 1 – Fundament (Core + Device)
- [x] `pyproject.toml` (hatchling, zwei Pakete), ruff/mypy-Konfiguration, `uv sync`
- [x] `core/types.py`, `core/exceptions.py`, `core/clock.py`
- [x] `core/config.py` mit Deep-Merge-Loader (default → profile → ENV → CLI)
- [x] `core/events.py` EventBus + Events, `core/commands.py`, `core/state.py` (RunControl mit checkpoint)
- [x] `core/logging.py` (structlog, Bus-Prozessor)
- [x] `device/protocol.py`, `device/adb_device.py` (mit Timeouts), `device/fake_device.py`, `device/discovery.py`, `device/humanize.py`
- [x] `cli.py` + `app.py` (Grundgerüst): `--list-devices`, `--screenshot out.png`
- [x] Tests: Config-Merge, EventBus, RunControl (Pause/Stop/Deadline), FakeDevice, FakeClock

Umsetzungsnotizen Phase 1:
- `utils/image.py` ist die einzige `cv2`-Nutzung außerhalb von `vision/` – nur Datei-I/O (PNG lesen/schreiben).
- `AdbDevice.screenshot()` nutzt rohes `screencap` (kein PNG-Encoding auf dem Gerät) und fällt bei
  unlesbaren Daten automatisch auf `screencap -p` zurück.
- `RunControl` schlägt die Pausendauer auf die Step-Deadline auf; `on_pause_changed` meldet echtes Blockieren.
- Zusätzliche Config-Felder: `device.adb_host/adb_port/connect_attempts`, `bot.tap_jitter_px`, `log_file`.
- Swipe-Kurven (humanize) sind noch linear; echte Kurven bräuchten `input motionevent` (Android 11+).
- `device/subprocess_device.py` ist noch offen (Factory meldet `ConfigError`).

### Phase 2 – Vision
- [x] `vision/template.py` (Store, Varianten, JSON-Metadaten, `validate()`)
- [x] `vision/matcher.py` (Single/Multi-Match, Masken, Region, Skalierung)
- [x] `vision/overlay.py` DetectionStore inkl. `render()`
- [x] `vision/vision.py` Fassade inkl. `wait_for*` mit `poll_hook`, automatisches Overlay
- [x] `vision/color.py`, `vision/ocr.py` (RapidOCR-Adapter, optional installierbar)
- [x] `scripts/test_template.py`
- [x] Tests mit Fixture-Screenshots

Umsetzungsnotizen Phase 2:
- **Koordinaten**: `region`/Punkte in Vision-Aufrufen und Template-JSONs sind Referenz-Koordinaten;
  `Match.rect` ist Geräte-Koordinate (direkt tappbar). `vision.to_device()` rechnet um.
- **Farben** in der API sind RGB (`Rgb`, `Rgb.from_hex("#ff8800")`), Frames bleiben intern BGR.
- **Masken**: `<name>_mask.png`, Alphakanal im PNG oder `"mask"` im JSON.
- **Einfarbige Templates** (keine Varianz) werden über die quadratische Abweichung verglichen,
  weil `TM_CCOEFF_NORMED` dort nicht definiert ist.
- **Missing-Overlay**: in der Suchregion, ohne Region an der besten (zu schwachen) Position mit Confidence.
- Zusätzlich: `Vision.wait_until(check, timeout=...)` (allgemeine Warteschleife), `find_any`,
  `color_ratio`, `Vision.from_config()`; `Template.from_image()` für Ad-hoc-Templates.
- Test-Screens werden synthetisch erzeugt (`tests/imagegen.py`) statt als PNG eingecheckt.
- Skalierung bezieht sich auf die kürzere Bildschirmseite (`utils/geometry.scale_factor`).

### Phase 3 – Bot-Schicht (Headless lauffähig)
- [x] `bot/context.py` (`checkpoint`, `sleep`, `page()`, `element()`)
- [x] `bot/element.py` (Descriptor, BoundElement, Text-/ColorElement), `bot/page.py`, `PageRegistry`
- [x] `bot/flow.py` mit `@step` (Skip, Retry + recover, kooperativer Timeout, optional, Events), `run_flow`, `ensure_app_running`
- [x] `bot/failure.py` Fehler-Snapshots
- [x] `bot/watchers.py` (Runner: Intervall, Priorität, Cooldown, Reentrancy-Schutz, Trigger-Limit)
- [x] `bot/actions.py`, `bot/registry.py` mit `discover()`
- [x] `bot/engine.py` (Steuer-Commands sofort, Arbeits-Commands sequenziell, Idle-Tick, Poll-Hook)
- [x] `app.py` komplett: `build_context`, `run_headless`; `python -m mybot --headless --flow daily`
- [x] Beispielprojekt `mybot`: `HomePage`, `ShopPage`, `DailyFlow`, ein Watcher, zwei Actions – läuft gegen FakeDevice
- [x] Tests: Step-Retry/Skip/optional/Timeout, Stop mitten im wait_for, Watcher-Trigger-Limit, Engine-Command-Reihenfolge

Umsetzungsnotizen Phase 3:
- **Demo ohne Handy**: `uv run mybot --headless --profile demo --flow daily`. Screens, Templates und
  `scenario.yaml` erzeugt `scripts/make_demo_assets.py` (inkl. Kontroll-Matrix Template × Screen).
- **FakeDevice-Szenario** (`scenario.yaml` im Screen-Ordner): Tap-/Key-Übergänge zwischen Screens,
  `"@back"` für den vorherigen Screen, `"*"` für alle Screens, `start_app` für den Startbildschirm.
- **Step-Timeout gilt pro Versuch**; bei verschachtelten Steps gilt die frühere Deadline.
- Ein endgültig fehlgeschlagener innerer Step wird von äußeren Steps **nicht erneut wiederholt**
  (ein Snapshot, keine multiplizierten Retries).
- `optional=True` fängt jeden Fehler außer `BotStopped`; wiederholt wird nur bei `StepFailed`.
- Step-Methoden liefern beim Aufruf ein `StepResult` (OK/SKIPPED/FAILED).
- `FlowFinished` nach Stop: `cancelled=True`, `result=SKIPPED`. Stop verwirft wartende Arbeits-Commands.
- Actions laufen wie Flows (`FlowStarted`/`FlowFinished` mit dem Action-Titel); `RunAction` nimmt den
  Funktionsnamen. Watcher/Actions werden per Decorator markiert und über `discover()` eingesammelt.
- Neu in der Config: `bot.element_timeout_s`, `app.start_timeout_s`; CLI: `--params '{"k": 1}'`.
- Headless-Exit-Codes: 0 OK, 1 Fehler, 2 Aufruf-/Config-Fehler, 130 abgebrochen (Ctrl-C).
- Offen: `ConnectDevice` mit anderer Serial verbindet nur neu (kein Gerätewechsel zur Laufzeit).

### Phase 4 – GUI
- [x] `gui/app.py`, `gui/main_window.py` (Docks, Toolbar, QSettings)
- [x] `gui/bridge.py` (Queue + QTimer-Drain, Frame-Koaleszenz)
- [x] `ScreenView` + `OverlayItem` (Zoom, Fit, Cursor-Koordinaten, Overlays nur zum passenden Frame)
- [x] `DebugPanel` (Level-/Quellen-Filter, Suche, Ringpuffer)
- [x] `FlowPanel` (Timeline), `ActionPanel`, `DevicePanel`, `StatusBar`
- [x] Klick-zu-Tap Debug-Modus
- [x] Tests mit `pytest-qt`: Bridge-Drain, DebugPanel-Filter

Umsetzungsnotizen Phase 4:
- **Start**: `uv run mybot --profile demo` (GUI ist der Default-Modus). Qt wird nur im GUI-Modus importiert.
- Die **Flow-Auswahl** sitzt im FlowPanel (Dropdown + JSON-Parameter), nicht in der Toolbar;
  Toolbar-Start/F5 startet den dort gewählten Flow.
- **Shortcuts**: F5 Start, F6 Pause/Fortsetzen, Shift+F5 Stop, Ctrl+0 Einpassen, Ctrl+R Templates neu laden.
- **Overlays**: Linien kosmetisch (zoomunabhängig breit), Beschriftungen in fester Bildschirmgröße.
- **DevicePanel** lädt die Geräteliste in einem Hilfsthread (adb-Server-Abfrage, kein Gerätezugriff);
  beim Fake-Backend erscheint das Fake-Gerät.
- **QSettings pro Profil** (`AdbBot-<profil>`): Fenstergeometrie, Dock-Layout, „Overlays halten“;
  „Layout zurücksetzen“ im Ansicht-Menü.
- „Template aufnehmen“ ist als deaktivierte Toolbar-Aktion vorbereitet (Phase 5).
- **GUI-Tests laufen offscreen** (`tests/gui/conftest.py` setzt `QT_QPA_PLATFORM=offscreen`), inklusive
  Demo-Flow Ende-zu-Ende über das Hauptfenster mit echter Engine.

### Phase 5 – Produktivität und Robustheit
- [x] `TemplateCapture` in der GUI + `ReloadTemplates`
- [x] `scripts/capture_screenshots.py`
- [x] Auto-Reconnect im Idle-Tick, `DeviceStatus`-Anzeige
- [x] `bot/storage.py` (JSON-Store pro Profil, z. B. "Daily heute schon abgeholt")
- [x] `bot/navigator.py`: Pages deklarieren `transitions = {ShopPage: lambda self: self.shop_button.tap()}`,
      `Navigator.current_page(frame)` prüft alle Anchors auf einem Frame, `go_to(PageCls)` per Breitensuche,
      unbekannter Screen → Back-Taste bis N-mal, dann App-Neustart
- [x] `docs/HOWTO_NEW_BOT.md`, `README.md`, pre-commit-Hooks

Umsetzungsnotizen Phase 5:
- **Template-Aufnahme**: „Template aufnehmen“ friert das Livebild ein; aufgezogenes Rechteck → Dialog
  (Page, Name, Threshold, Suchregion mit Rand, Variante/Überschreiben) → `TemplateStore.save()` →
  `ReloadTemplates`. Weicht die Geräteauflösung von der Referenz ab, wird sie ins JSON geschrieben.
- **Navigator**: `Page.transitions` akzeptiert Ziel-Klassen oder -Namen (vermeidet zirkuläre Importe);
  `Page.priority` für Popups; nach jedem Übergang wird neu erkannt und neu geplant; `NavigationError`
  ist ein `StepFailed` (Retry möglich). Zugriff: `ctx.navigator.go_to(...)` bzw. `self.go_to(...)` im Flow.
- **Storage**: `ctx.store` / `self.store`; atomar geschrieben, kaputte Datei wird beiseitegelegt;
  Zeit-Helfer `mark`, `since`, `done_within`, `done_today(day_start_hour=...)`.
  `bot.persist_state: false` hält den Store nur im Speicher.
- **Auto-Reconnect**: Backoff konfigurierbar (`device.reconnect_delay_s`, `reconnect_max_delay_s`);
  ohne Idle-Livebild prüft ein Health-Check alle `device.health_check_interval_s`; `DeviceStatus.retry_in_s`
  erscheint im DevicePanel.
- Neuer Beispiel-Flow `shop` (Navigator + Storage); `daily` merkt sich mit `{"once_per_day": true}` das Geschenk.
- **pre-commit** (`.pre-commit-config.yaml`) nutzt die Tools aus dem Projekt-venv; braucht ein Git-Repository.

---

## 11. Spätere Erweiterungen

Das Design lässt diese Punkte zu, ohne bestehenden Code umzubauen:

- **Mehrere Geräte parallel**: eine Engine + ein Context pro Gerät, GUI mit Tabs.
- **Scheduler**: Flows zeitgesteuert oder priorisiert (z. B. "alle 4 h Daily, sonst Farm") – setzt nur `StartFlow`-Commands ab.
- **Session-Recording / Replay**: Frames + Detections + Inputs speichern und in der GUI abspielen.
- **Feature-Matching (ORB/SIFT)** als weiterer Matcher für rotations-/skalierungsinvariante Erkennung.
- **Ziffern-Templates** statt OCR für stilisierte Spiele-Zahlen (`vision/digits.py`).
- **scrcpy/minicap-Backend** als `DeviceProtocol` für 30+ fps.
- **Remote-Steuerung**: Commands/Events über WebSocket → Web-Dashboard oder Telegram als weitere "GUI".
- **Hot-Reload des Projektpakets** während der Entwicklung (`ReloadProject`-Command).
- **Vision-Cache**: Frame-Hash → Match-Ergebnis, wenn sich das Bild nicht geändert hat.

---

## 12. Designentscheidungen und verworfene Alternativen

Kurzform-ADRs. Die Punkte mit ⚠ waren Fehler in Revision 1 dieses Dokuments und sind korrigiert.

| # | Entscheidung | Begründung / verworfene Alternative |
|---|---|---|
| 1 | **Synchroner Code, ein Bot-Thread** statt asyncio | Flows lesen sich wie Prosa; keine `await`-Verschmutzung; Screenshot/Matching sind ohnehin blockierend. asyncio bringt hier nur Komplexität. |
| 2 | **Thread statt Prozess** für die Engine | Frames (6 MB) müssten sonst über IPC. Die GIL stört nicht: OpenCV und adb-I/O geben sie frei. |
| 3 ⚠ | **Steuer-Commands (Pause/Stop) umgehen die Command-Queue** | Rev. 1 hatte sie in der Queue – die wird während eines laufenden Flows nicht gelesen. Jetzt über `RunControl` + `checkpoint()`. |
| 4 ⚠ | **Timeouts kooperativ** über `checkpoint()`-Deadline | Ein synchroner Methodenaufruf im selben Thread lässt sich nicht "abschießen". Da jede Warteschleife `checkpoint()` aufruft, reicht die Deadline-Prüfung dort. |
| 5 ⚠ | **`BotContext` und `BotState`/`RunControl` neu einsortiert** | Rev. 1 hatte `BotContext` in `core/`, das `Vision` und `PageRegistry` importiert hätte (Schichtverletzung). Jetzt: `RunControl`/`BotState` in `core/state.py` (dependency-frei), `BotContext` in `bot/`. |
| 6 ⚠ | **Elemente als Descriptor** | Klassenattribute `shop_button = Element(...)` brauchen zur Laufzeit den Context. Rev. 1 hat nicht gesagt, wie. Der Descriptor bindet beim Zugriff (`page.shop_button` → `BoundElement`). |
| 7 ⚠ | **`ctx.page(HomePage)` statt `ctx.pages.home`** | Attribut-Magie ist nicht typsicher; mypy und IDE wissen nichts von `.home`. Lesbarkeit kommt über typisierte Properties in `ProjectFlow`. |
| 8 ⚠ | **Watcher für Popups** | Fehlte in Rev. 1 komplett. Popups sind in Handy-Bots die häufigste Fehlerquelle; sie pro Step zu behandeln, skaliert nicht. Realisiert über `Vision.poll_hook` (Callback nach unten gereicht, keine Import-Verletzung). |
| 9 ⚠ | **Vision tappt nicht** | Rev. 1 hatte `vision.tap(template)`. Eingaben gehören in die Bot-Schicht (`BoundElement.tap`). |
| 10 | **Kein "aktueller Frame"-Cache** | Ein Cache mit Max-Alter (z. B. 80 ms) spart Screenshots, kann aber nach einem Tap ein Vor-Tap-Bild liefern. Explizites `frame=` reicht; Screenshots sind billig genug. |
| 11 ⚠ | **Bridge = Queue + QTimer-Drain** statt Signal pro Event | Frames müssen koalesziert werden, Log-Bursts dürfen die GUI nicht fluten, Thread-Grenze an einer testbaren Stelle. |
| 12 ⚠ | **Kein "Step x/y"** in der GUI | Die Gesamtzahl ist nicht vorab bekannt (Steps sind bedingte Methodenaufrufe). Stattdessen Timeline mit Dauer. |
| 13 | **`BotStopped` erbt von `BaseException`** | Wie `KeyboardInterrupt`: ein `except Exception` in Projektcode darf den Stop nicht verschlucken. |
| 14 | **`StepSkipped` als Exception** statt Rückgabewert | Funktioniert auch aus Hilfsmethoden heraus; Step-Methoden bleiben `-> None`. |
| 15 | **`recover()`-Hook zwischen Retries** | Ein blinder Retry desselben Steps hilft selten; meist muss erst ein Popup weg oder zurück navigiert werden. Projektspezifisch in `ProjectFlow`. |
| 16 ⚠ | **Fehler-Snapshots automatisch** | Ohne Bild vom Moment des Fehlers ist Debugging von Bots Raten. Frame + Overlays + Log-Auszug kosten nichts. |
| 17 ⚠ | **Projekt als eigenes Paket `src/mybot`** mit eigenem Einstieg | Rev. 1 hatte `project/` außerhalb von `src/` – nicht installiert, nicht importierbar ohne `sys.path`-Tricks. Jetzt ist das Framework eine Library, das Projekt die App. |
| 18 | **`Clock`-Protocol** | Flows mit `wait_for(timeout=30)` in Tests ohne FakeClock dauern 30 s. Mit FakeClock Millisekunden. |
| 19 | **Device-Timeouts** | Ein hängendes `adb` würde sonst den Bot-Thread und damit Stop blockieren. |
| 20 | **Template-Validierung beim Start** | String-Referenzen auf Templates sind flexibel, aber tippfehleranfällig. Der Check beim Start macht sie sicher. |
| 21 | **`pydantic` ohne `pydantic-settings`** | Deep-Merge mehrerer YAMLs + ENV-Override sind ~15 Zeilen; `pydantic-settings` würde mehr Konzepte bringen als nutzen. |
| 22 | **Navigator und Storage erst in Phase 5** | Beides ist wertvoll, aber die Kernschleife (Page → Element → Flow → Engine → GUI) muss zuerst stehen. Das Design sieht beides bereits vor. |
| 23 | **Auflösungs-Skalierung nur als Notlösung** | Matching nach Resampling ist unzuverlässig. Feste Auflösung pro Profil ist die eigentliche Empfehlung. |
