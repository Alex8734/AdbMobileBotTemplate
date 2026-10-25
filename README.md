# AdbBotTemplate

Python-Template für Android-Bots über ADB: Bilderkennung per Template Matching, Abläufe als lesbare
Flows mit benannten Schritten, eine Debug-GUI mit Livebild und Overlays – und ein Fake-Gerät, mit dem
sich alles ohne Handy entwickeln und testen lässt.

- **`src/adbbot`** – das Framework. Wird für einen neuen Bot nicht angefasst.
- **`src/mybot`** – ein lauffähiges Beispielprojekt. Für einen neuen Bot kopieren und umbenennen.

## Schnellstart

Voraussetzungen: Python 3.12, [uv](https://docs.astral.sh/uv/), für echte Geräte `adb` im `PATH`.
Empfohlen: [scrcpy](https://github.com/Genymobile/scrcpy) ≥ 2.0 installieren – dann kommen Screenshots als
Videostream (~1 ms statt ~1 s pro Bild). Ohne scrcpy wird automatisch `screencap` benutzt.

```bash
uv sync                          # optional: uv sync --extra ocr   (RapidOCR für read_text/read_number)
uv run mybot --profile demo      # GUI mit dem Beispielprojekt gegen das Fake-Gerät
```

In der GUI im Flow-Panel `daily` wählen und **Start** (F5) drücken. Ohne Oberfläche:

```bash
uv run mybot --headless --profile demo --flow daily
uv run mybot --headless --profile demo --flow shop     # Navigator + Storage
```

### Mit echtem Gerät oder Emulator

```bash
adb devices                                    # USB-Debugging an? Gerät autorisiert?
uv run mybot --list-devices
uv run mybot --screenshot data/screenshots/start.png
uv run mybot --profile example                 # config/profiles/example.yaml anpassen (app.package!)
```

## Was drin ist

| Bereich | Kurz |
|---|---|
| **Device** | `adbutils`-Backend mit Timeouts, scrcpy-Bildschirmstream, humanisierte Eingaben, Fake-Gerät mit `scenario.yaml` (Tap → Screen) |
| **Vision** | Template-Store (Varianten `name@2.png`, Masken, JSON-Metadaten, Validierung beim Start), Matching, Farbe, OCR, automatische Overlays |
| **Bot** | Pages mit Elementen, Flows mit `@step` (Retry, Skip, Timeout, optional), Watcher für Popups, Actions, Navigator, persistenter Storage, Fehler-Snapshots |
| **Engine** | Ein Bot-Thread, Pause/Stop sofort wirksam, Auto-Reconnect mit Backoff |
| **GUI** | Livebild + Overlays, Ansicht-Dropdown für eigene OpenCV-Zwischenbilder (`vision.debug_image`), Debug-Log mit Filtern, Step-Timeline, Actions, Klick = Tap, Template-Aufnahme |

## Projektstruktur

```
config/                 default.yaml + profiles/<profil>.yaml
assets/templates/       Templates, gruppiert nach Page (home/shop_button.png, …)
assets/demo_screens/    Fake-Screens + scenario.yaml für --profile demo
src/adbbot/             Framework: core → device → vision → bot → gui
src/mybot/              Beispielprojekt: pages/, flows/, watchers.py, actions.py, bootstrap.py
scripts/                Werkzeuge (siehe unten)
tests/                  pytest (GUI-Tests laufen offscreen)
data/                   Laufzeitdaten: failures/, screenshots/, <profil>/state.json (gitignored)
```

## Werkzeuge

| Befehl | Zweck |
|---|---|
| `uv run python scripts/test_template.py shot.png home/shop_button` | Template gegen einen Screenshot testen, Ergebnisbild mit Overlays |
| `uv run python scripts/capture_screenshots.py --on-change --out DIR` | Screenshot-Serie aufnehmen (Fake-Screens, Template-Quellen) |
| `uv run python scripts/capture_ads.py --profile P` | Werbung nebenher aufzeichnen (Screens, Activities, Schließen-Buttons) nach `data/ads/` |
| `uv run python scripts/make_demo_assets.py` | Demo-Screens und -Templates des Beispielprojekts neu erzeugen |
| GUI → **Template aufnehmen** | Bereich im Livebild aufziehen und direkt als Template speichern |

## Konfiguration

Jede Stufe überschreibt die vorige: `config/default.yaml` → `config/profiles/<profil>.yaml` →
Umgebungsvariablen → Kommandozeile.

```bash
ADBBOT__DEVICE__SERIAL=emulator-5554 uv run mybot --profile example
uv run mybot --profile example --set bot.step_timeout_s=60 --set vision.ocr_engine=rapidocr
```

Alle Felder mit Defaults: `src/adbbot/core/config.py`. Unbekannte Felder sind Fehler – Tippfehler fallen
sofort auf.

## Entwicklung

```bash
uv run pytest                     # alle Tests, inkl. GUI (offscreen) und Demo-Flows Ende-zu-Ende
uv run ruff check && uv run ruff format --check
uv run mypy                       # strict für src/adbbot
git init && uv run pre-commit install   # ruff + mypy vor jedem Commit
```

## Dokumentation

- [`docs/HOWTO_NEW_BOT.md`](docs/HOWTO_NEW_BOT.md) – Schritt für Schritt zum eigenen Bot
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) – Schichten, Konzepte, Threading, Designentscheidungen
