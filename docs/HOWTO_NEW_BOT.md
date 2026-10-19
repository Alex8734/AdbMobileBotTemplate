# Neuen Bot erstellen

Schritt für Schritt vom Template zum eigenen Bot. Als Vorlage dient das Beispielprojekt `src/mybot`;
Hintergründe stehen in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 1. Projekt umbenennen

1. `src/mybot` nach `src/<deinbot>` kopieren oder umbenennen.
2. In `pyproject.toml` anpassen:
   ```toml
   [project.scripts]
   deinbot = "deinbot.__main__:main"

   [tool.hatch.build.targets.wheel]
   packages = ["src/adbbot", "src/deinbot"]
   ```
3. Alle Importe `mybot.` → `deinbot.` ersetzen (auch in `tests/`, die das Beispielprojekt nutzen).
4. `uv sync` – danach startet der Bot mit `uv run deinbot`.

## 2. Profil anlegen

`config/profiles/deinbot.yaml`:

```yaml
app:
  package: com.beispiel.spiel        # adb shell dumpsys window | grep mCurrentFocus
  activity: null                     # null = Launcher-Activity

device:
  serial: null                       # null = erstes Gerät
  reference_resolution: [1080, 1920] # Auflösung, in der Templates und Regionen aufgenommen werden

bot:
  step_timeout_s: 30
  default_retries: 1
```

**Tipp:** Feste Auflösung verwenden (Emulator oder `adb shell wm size 1080x1920`). Skalierung zwischen
Auflösungen funktioniert, macht das Matching aber spürbar unzuverlässiger.

## 3. Screenshots und Templates aufnehmen

```bash
uv run deinbot --profile deinbot          # GUI starten, App auf dem Gerät öffnen
```

1. **Template aufnehmen** in der Toolbar → das Livebild friert ein.
2. Rechteck um einen Button ziehen → Dialog: Page (`home`), Name (`shop_button`), optional eigener
   Threshold und Suchregion → **Speichern**. Die Datei landet in `assets/templates/home/shop_button.png`,
   Templates werden automatisch neu geladen.
3. Pro Screen mindestens den **Anchor** aufnehmen: ein Merkmal, das nur auf diesem Screen sichtbar ist
   (Titel, Logo) – am besten mit Suchregion.

Screenshot-Serien für Tests oder zum späteren Ausschneiden:

```bash
uv run python scripts/capture_screenshots.py --profile deinbot --on-change --out tests/fixtures/screens/deinbot
```

Konventionen: Namen `<page>/<element>` in snake_case; Varianten (z. B. gedrückt/ungedrückt) als
`name@2.png`; Masken als `name_mask.png` (weiß = relevant) oder Alphakanal.

## 4. Pages schreiben

Eine Datei pro Screen in `pages/`:

```python
from adbbot.bot.element import ColorElement, Element, TextElement
from adbbot.bot.page import Page
from adbbot.core.types import Point, Rect
from deinbot.pages.shop_page import ShopPage


class HomePage(Page):
    """Startseite – erkennbar am Titel oben links."""

    name = "home"
    anchor = Element("home/title", region=Rect(0, 0, 540, 200))

    shop_button = Element("home/shop_button")
    daily_gift = Element(["home/gift", "home/gift_glowing"], threshold=0.9)   # Alternativen
    gold = TextElement(Rect(700, 40, 300, 60))                                 # OCR-Region
    event_active = ColorElement("#ff3b30", point=Point(1010, 250))            # roter Punkt

    transitions = {"shop": lambda page: page.shop_button.tap()}               # für den Navigator

    def open_shop(self) -> ShopPage:
        self.shop_button.tap()
        return self.ctx.page(ShopPage).wait_until_active()
```

- Koordinaten (`region`, `Point`) gelten in der Referenzauflösung.
- `transitions` beschreibt, wie man von dieser Seite zu anderen kommt; Ziele als Name oder Klasse.
- Popups, die eine Seite nur teilweise verdecken, bekommen `priority = 10`.

## 5. Basisklasse für Flows

`base.py` – typisierte Abkürzungen und die Erholung zwischen zwei Versuchen eines Steps:

```python
class ProjectFlow(Flow):
    @property
    def home(self) -> HomePage:
        return self.ctx.page(HomePage)

    def recover(self, error: StepFailed) -> None:
        frame = self.vision.capture()
        self.tap_if_present("common/close_x", frame=frame)
```

## 6. Watcher für Störungen

Popups, die jederzeit auftauchen können, gehören nicht in jeden Step, sondern in `watchers.py`:

```python
@watcher("Verbindung verloren", template="common/reconnect", priority=10, cooldown_s=5)
def reconnect(ctx: BotContext, match: Match) -> None:
    ctx.device.tap(match.rect.center)
```

Watcher laufen in jeder Warteschleife auf dem ohnehin geholten Frame. Feuert einer dauerhaft, bricht
der Step mit `WatcherLoop` ab, statt endlos zu klicken.

## 7. Flows schreiben

Eine Datei pro Ablauf in `flows/`. `run()` ist die Inhaltsangabe, die Steps sind die Kapitel:

```python
class DailyFlow(ProjectFlow):
    """Tägliche Belohnungen einsammeln."""

    name = "daily"
    description = "Tägliche Belohnungen"

    def run(self) -> None:
        self.ensure_app_running()
        self.go_home()
        self.collect_gift()

    @step("Zur Startseite", retries=2)
    def go_home(self) -> None:
        self.go_to(HomePage)                        # Navigator: Weg über Page.transitions

    @step("Geschenk abholen", optional=True)
    def collect_gift(self) -> None:
        if self.store.done_today("gift", day_start_hour=5):
            raise StepSkipped("Heute schon abgeholt")
        if not self.home.daily_gift.exists():
            raise StepSkipped("Kein Geschenk da")
        self.home.daily_gift.tap()
        self.tap("popup/claim")
        self.store.mark_done_today("gift")
```

Regeln, die Ärger ersparen:

- **Steps sind wiederholbar.** Ein Retry führt die ganze Methode erneut aus – erst Zustand prüfen, dann handeln.
- **Nie `time.sleep()`**, immer `self.sleep()` – sonst reagieren Pause und Stop nicht.
- **`BotStopped` nie fangen.** `except Exception` ist erlaubt und schluckt einen Stop nicht.
- Für mehrere Prüfungen auf demselben Bild: `frame = self.vision.capture()` holen und `frame=` weitergeben.
- Logging mit `self.log.info("Nachricht", key=wert)`, nie `print`.

## 8. Actions für die GUI

```python
@action("Gold auslesen", group="Debug")
def read_gold(ctx: BotContext) -> None:
    ctx.log.info("Gold", source="action", gold=ctx.page(HomePage).gold.read_number())
```

## 9. Registrieren

`bootstrap.py` sammelt alles automatisch ein:

```python
def register(reg: Registries) -> None:
    reg.discover(deinbot.pages)
    reg.discover(deinbot.flows)
    reg.discover(deinbot.watchers)
    reg.discover(deinbot.actions)
```

Beim Start wird geprüft, dass alle in Pages und Watchern referenzierten Templates existieren.

## 10. Laufen lassen und nachjustieren

```bash
uv run deinbot --profile deinbot                                  # GUI
uv run deinbot --headless --profile deinbot --flow daily          # ohne GUI, Exit-Code 0/1/2/130
```

- Overlays im Livebild zeigen jede Erkennung: grün = gefunden (mit Confidence), rot gestrichelt = nicht
  gefunden (an der besten Stelle oder in der Suchregion).
- Schwache Treffer: Template mit `scripts/test_template.py` gegen einen Screenshot prüfen, dann Threshold
  in `<name>.json` anpassen, Variante aufnehmen oder Suchregion setzen.
- Fehlgeschlagene Steps liegen als Bild + JSON (inkl. Log-Auszug) in `data/failures/`.
- Eigene OpenCV-Zwischenbilder (Graustufen, Masken, Match-Heatmaps) mit `self.vision.debug_image(name, bild)`
  an die GUI schicken – sie erscheinen im Dropdown **Ansicht** über dem Livebild (pro Name das neueste Bild):

  ```python
  frame = self.vision.capture()
  gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
  self.vision.debug_image("Graustufen", gray)
  self.vision.debug_image("Maske", cv2.inRange(frame.image, (0, 0, 150), (80, 80, 255)))
  self.vision.debug_image("Heatmap", cv2.matchTemplate(gray, tpl_gray, cv2.TM_CCOEFF_NORMED))
  ```

  Graustufen, bool-Masken, BGR/BGRA und Float-Bilder (auf 0–255 normiert) gehen direkt. Hat das Bild die
  Größe des Frames, werden dessen Overlays darauf gezeichnet und „Klick = Tap“ funktioniert auch dort.
  Beispiel: Action „Debug-Ansichten erzeugen“ in `mybot/actions.py`.

## 11. Tests ohne Handy

Screenshots in einen Ordner legen und mit einem `scenario.yaml` verbinden:

```yaml
start: home
start_app: home
transitions:
  home:
    - {tap: [170, 560, 200, 80], goto: shop}      # Tap in diesem Rechteck → Screen "shop"
  shop:
    - {tap: [472, 22, 48, 48], goto: "@back"}
  "*":
    - {key: 4, goto: "@back"}                     # Zurück-Taste auf jedem Screen
```

Dann läuft der Bot mit `--backend fake --set device.fake_screens_dir=<ordner>` – oder in pytest mit
`FakeDevice` + `FakeClock`, sodass Timeouts von 30 s in Millisekunden vergehen. Beispiele:
`tests/botkit.py`, `tests/test_flow.py`, `tests/test_navigator.py`.
