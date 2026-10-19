"""Templates: PNG-Dateien unter `assets/templates/`, adressiert als `"<page>/<name>"`.

Konventionen im Template-Ordner:
- `close_x.png`, `close_x@2.png`, ... → Varianten desselben Templates `close_x` (beste gewinnt).
- `close_x.json` → optionale Metadaten für alle Varianten (threshold, region, reference_resolution, mask).
- `close_x_mask.png` bzw. `close_x@2_mask.png` → Maske (weiß = relevant). Alternativ: Alphakanal im PNG
  oder `"mask": "datei.png"` im JSON. Masken-Dateien sind selbst nie Templates.

Weicht die Bildschirmgröße von der Referenzauflösung ab, werden Bilder, Masken und Regionen beim Laden
skaliert (siehe `utils.geometry.scale_factor`). Das ist eine Notlösung – feste Auflösung ist besser.
"""

from __future__ import annotations

import difflib
import glob
import json
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from adbbot.core.exceptions import TemplateError
from adbbot.core.types import Image, Rect, Size
from adbbot.utils.geometry import scale_factor
from adbbot.utils.image import save_image

MASK_SUFFIX = "_mask"
DEFAULT_REFERENCE_RESOLUTION = Size(1080, 1920)
_NAME_RE = re.compile(r"[a-z0-9_]+(?:/[a-z0-9_]+)*")
_VARIANT_RE = re.compile(r"^(?P<base>.+?)(?:@(?P<n>\d+))?$")


class TemplateMeta(BaseModel):
    """Inhalt der optionalen `<name>.json`. Koordinaten in `reference_resolution`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    region: tuple[int, int, int, int] | None = None
    reference_resolution: tuple[int, int] | None = None
    mask: str | None = None

    @classmethod
    def load(cls, path: Path) -> TemplateMeta:
        try:
            return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValidationError) as e:
            raise TemplateError(f"{path}: ungültige Template-Metadaten: {e}") from e


@dataclass(frozen=True, slots=True, eq=False)
class TemplateVariant:
    image: Image  # BGR, bereits auf die Bildschirmgröße skaliert
    mask: Image | None  # 1 Kanal, Werte 0/1, gleiche Größe wie image
    source: Path | None
    flat: bool  # einfarbig (innerhalb der Maske) → eigenes Matching-Verfahren

    @classmethod
    def create(cls, image: Image, mask: Image | None = None, source: Path | None = None) -> TemplateVariant:
        if mask is not None:
            if mask.shape[:2] != image.shape[:2]:
                raise TemplateError(f"{source}: Maske hat eine andere Größe als das Template")
            mask = (mask > 0).astype(np.uint8)
            if not mask.any():
                raise TemplateError(f"{source}: Maske ist leer")
            pixels = image[mask.astype(bool)]
        else:
            pixels = image.reshape(-1, 3)
        return cls(image, mask, source, flat=bool(pixels.std(axis=0).max() < 1.0))

    @property
    def size(self) -> Size:
        h, w = self.image.shape[:2]
        return Size(w, h)


@dataclass(frozen=True, slots=True, eq=False)
class Template:
    name: str
    variants: tuple[TemplateVariant, ...]
    threshold: float | None = None
    region: Rect | None = None  # Geräte-Koordinaten (bereits skaliert)
    scale: float = 1.0

    @classmethod
    def from_image(
        cls,
        name: str,
        image: Image,
        *,
        mask: Image | None = None,
        threshold: float | None = None,
        region: Rect | None = None,
    ) -> Template:
        """Ad-hoc-Template ohne Datei (Tests, Werkzeuge)."""
        return cls(name, (TemplateVariant.create(image, mask),), threshold, region)


class TemplateStore:
    """Lädt Templates lazy und cached sie. Thread-sicher."""

    def __init__(self, root: Path, *, reference_resolution: Size = DEFAULT_REFERENCE_RESOLUTION) -> None:
        self.root = root
        self.reference_resolution = reference_resolution
        self._screen_size: Size | None = None
        self._cache: dict[str, Template] = {}
        self._lock = threading.RLock()

    @property
    def screen_size(self) -> Size | None:
        return self._screen_size

    def set_screen_size(self, size: Size | None) -> None:
        """Bei geänderter Größe (anderes Gerät, Rotation) werden Templates neu skaliert geladen."""
        with self._lock:
            if size != self._screen_size:
                self._screen_size = size
                self._cache.clear()

    def reload(self) -> None:
        with self._lock:
            self._cache.clear()

    def names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        found: set[str] = set()
        for path in self.root.rglob("*.png"):
            if path.stem.endswith(MASK_SUFFIX):
                continue
            base = self._split_variant(path.stem)[0]
            parent = path.parent.relative_to(self.root).as_posix()
            found.add(base if parent == "." else f"{parent}/{base}")
        return sorted(found)

    def exists(self, name: str) -> bool:
        return bool(self._variant_files(name))

    def save(
        self,
        name: str,
        image: Image,
        *,
        threshold: float | None = None,
        region: Rect | None = None,
        reference_resolution: Size | None = None,
        variant: bool = False,
        overwrite: bool = False,
    ) -> Path:
        """Speichert ein Template (z. B. aus der GUI ausgeschnitten) und ergänzt die Metadaten-JSON.

        Existiert das Template schon: `variant=True` → `name@<n>.png`, `overwrite=True` → Basisdatei
        ersetzen, sonst `TemplateError`. `region` gilt in `reference_resolution` (Default: Store-Referenz).
        """
        if not _NAME_RE.fullmatch(name):
            raise TemplateError(f"Ungültiger Template-Name {name!r} – erlaubt: a–z, 0–9, _ und / als Trenner")
        directory, base = self._directory(name)
        existing = self._variant_files(name)
        if existing and variant:
            number = max(self._split_variant(p.stem)[1] for p in existing) + 1
            path = directory / f"{base}@{number}.png"
        elif existing and not overwrite:
            raise TemplateError(
                f"Template {name!r} existiert bereits – als Variante speichern oder überschreiben"
            )
        else:
            path = directory / f"{base}.png"
        save_image(path, image)

        updates: dict[str, object] = {}
        if threshold is not None:
            updates["threshold"] = threshold
        if region is not None:
            updates["region"] = [region.x, region.y, region.w, region.h]
        if reference_resolution is not None:
            updates["reference_resolution"] = [reference_resolution.w, reference_resolution.h]
        if updates:
            meta_path = directory / f"{base}.json"
            data = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
            data.update(updates)
            meta_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        with self._lock:
            self._cache.pop(name, None)
        return path

    def get(self, name: str) -> Template:
        with self._lock:
            template = self._cache.get(name)
            if template is None:
                template = self._load(name)
                self._cache[name] = template
            return template

    def validate(self, names: Iterable[str]) -> None:
        """Prüft beim Start, dass alle referenzierten Templates existieren und ladbar sind."""
        errors: list[str] = []
        for name in sorted(set(names)):
            try:
                self.get(name)
            except TemplateError as e:
                errors.append(str(e))
        if errors:
            raise TemplateError(f"{len(errors)} Template(s) fehlerhaft:\n  - " + "\n  - ".join(errors))

    # --- intern ------------------------------------------------------------------------------------

    @staticmethod
    def _split_variant(stem: str) -> tuple[str, int]:
        m = _VARIANT_RE.match(stem)
        assert m is not None  # das Muster passt auf jeden nicht-leeren String
        return m.group("base"), int(m.group("n") or 1)

    def _directory(self, name: str) -> tuple[Path, str]:
        rel = PurePosixPath(name)
        return self.root.joinpath(*rel.parent.parts), rel.name

    def _variant_files(self, name: str) -> list[Path]:
        directory, base = self._directory(name)
        if not directory.is_dir():
            return []
        files: list[tuple[int, str, Path]] = []
        for path in directory.glob(f"{glob.escape(base)}*.png"):
            if path.stem.endswith(MASK_SUFFIX):
                continue
            variant_base, n = self._split_variant(path.stem)
            if variant_base == base:
                files.append((n, path.name, path))
        return [path for _, _, path in sorted(files)]

    def _load(self, name: str) -> Template:
        files = self._variant_files(name)
        if not files:
            hint = difflib.get_close_matches(name, self.names(), n=3)
            suffix = f" – meintest du {', '.join(hint)}?" if hint else ""
            raise TemplateError(f"Template {name!r} nicht gefunden in {self.root}{suffix}")

        directory, base = self._directory(name)
        meta_path = directory / f"{base}.json"
        meta = TemplateMeta.load(meta_path) if meta_path.is_file() else TemplateMeta()

        reference = (
            Size(*meta.reference_resolution) if meta.reference_resolution else self.reference_resolution
        )
        scale = scale_factor(reference, self._screen_size) if self._screen_size else 1.0
        region = Rect(*meta.region).scaled(scale) if meta.region else None
        variants = tuple(self._load_variant(path, meta, scale) for path in files)
        return Template(name, variants, meta.threshold, region, scale)

    def _load_variant(self, path: Path, meta: TemplateMeta, scale: float) -> TemplateVariant:
        image, alpha = _read_with_alpha(path)

        mask: Image | None = None
        mask_path = path.with_name(f"{path.stem}{MASK_SUFFIX}.png")
        if not mask_path.is_file() and meta.mask:
            mask_path = path.parent / meta.mask
        if mask_path.is_file():
            mask = _read_gray(mask_path)
        elif meta.mask:
            raise TemplateError(f"{path}: Maske {meta.mask!r} nicht gefunden")
        elif alpha is not None and (alpha < 255).any():
            mask = alpha

        if scale != 1.0:
            h, w = image.shape[:2]
            size = (max(1, round(w * scale)), max(1, round(h * scale)))
            interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
            image = np.asarray(cv2.resize(image, size, interpolation=interpolation), dtype=np.uint8)
            if mask is not None:
                mask = np.asarray(cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST), dtype=np.uint8)

        return TemplateVariant.create(image, mask, path)


def _decode(path: Path, flags: int) -> Image:
    raw = cv2.imdecode(np.fromfile(path, dtype=np.uint8), flags)
    if raw is None:
        raise TemplateError(f"{path}: kein lesbares Bild")
    if raw.dtype == np.uint16:
        raw = (raw // 257).astype(np.uint8)
    return np.asarray(raw, dtype=np.uint8)


def _read_with_alpha(path: Path) -> tuple[Image, Image | None]:
    raw = _decode(path, cv2.IMREAD_UNCHANGED)
    if raw.ndim == 2:
        return np.asarray(cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR), dtype=np.uint8), None
    channels = raw.shape[2]
    if channels == 3:
        return raw, None
    if channels == 4:
        return np.ascontiguousarray(raw[:, :, :3]), np.ascontiguousarray(raw[:, :, 3])
    raise TemplateError(f"{path}: {channels} Farbkanäle werden nicht unterstützt")


def _read_gray(path: Path) -> Image:
    return _decode(path, cv2.IMREAD_GRAYSCALE)
