"""Fehler-Hierarchie des Frameworks."""


class BotError(Exception):
    """Basis aller fachlichen Fehler."""


class ConfigError(BotError):
    """Konfiguration fehlt oder ist ungültig."""


class DeviceError(BotError):
    """adb nicht erreichbar, Timeout, Gerät getrennt, ..."""


class TemplateError(BotError):
    """Template fehlt, ist unlesbar oder hat ungültige Metadaten."""


class OcrUnavailable(BotError):
    """Keine OCR-Engine konfiguriert oder das optionale Paket ist nicht installiert."""


class StepFailed(BotError):
    """Schritt fehlgeschlagen – ein Retry ist möglich."""


class ElementNotFound(StepFailed): ...


class WrongPage(StepFailed): ...


class StepTimeout(StepFailed): ...


class WatcherLoop(StepFailed):
    """Ein Watcher feuert dauerhaft."""


class NavigationError(StepFailed):
    """Zielseite nicht erreichbar (kein Weg, unbekannter Screen, Übergänge greifen nicht)."""


class StepSkipped(Exception):
    """Kontrollfluss: Schritt bewusst überspringen."""


class BotStopped(BaseException):
    """Stop angefordert.

    Absichtlich `BaseException`: ein `except Exception` in Projektcode darf einen Stop nie schlucken.
    """
