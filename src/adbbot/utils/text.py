"""Namen und Dateinamen aus Klassennamen und Titeln ableiten."""

from __future__ import annotations

import re

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


def snake_case(name: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def default_name(class_name: str, suffix: str) -> str:
    """`HomePage` → `home`, `DailyFlow` → `daily`, `Page` → `page`."""
    return snake_case(class_name.removesuffix(suffix) or class_name)


def slugify(text: str, max_length: int = 60) -> str:
    """Dateinamen-tauglich: `Tagesgeschenk abholen` → `Tagesgeschenk_abholen`."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text.translate(_UMLAUTS)).strip("_")
    return slug[:max_length].strip("_") or "unbenannt"
