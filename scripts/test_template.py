#!/usr/bin/env python
"""Ein Template gegen einen Screenshot testen – mit Konsolenausgabe und Ergebnisbild.

    uv run python scripts/test_template.py data/screenshots/home.png home/shop_button
    uv run python scripts/test_template.py shot.png home/shop_button --all --region 0,1500,1080,420

Exit-Code: 0 = gefunden, 1 = nicht gefunden, 2 = Fehler (Datei/Template).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from adbbot.core.clock import FakeClock
from adbbot.core.exceptions import TemplateError
from adbbot.core.types import Rect, Size
from adbbot.device.fake_device import FakeDevice
from adbbot.utils import paths
from adbbot.utils.image import load_image
from adbbot.vision.matcher import TemplateMatcher
from adbbot.vision.template import TemplateStore
from adbbot.vision.vision import Vision


def _rect(value: str) -> Rect:
    try:
        x, y, w, h = (int(v) for v in value.split(","))
    except ValueError as e:
        raise argparse.ArgumentTypeError("erwartet x,y,w,h") from e
    return Rect(x, y, w, h)


def _size(value: str) -> Size:
    try:
        w, h = (int(v) for v in value.lower().split("x"))
    except ValueError as e:
        raise argparse.ArgumentTypeError("erwartet BREITExHÖHE, z. B. 1080x1920") from e
    return Size(w, h)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("screenshot", type=Path)
    p.add_argument("template", help='Template-Name, z. B. "home/shop_button"')
    p.add_argument("--templates-dir", type=Path, help="Default: <projekt>/assets/templates")
    p.add_argument("--reference", type=_size, default=Size(1080, 1920), help="Referenzauflösung (1080x1920)")
    p.add_argument("--threshold", type=float, help="überschreibt Template-JSON und Default (0.85)")
    p.add_argument("--region", type=_rect, help="x,y,w,h in Referenz-Koordinaten")
    p.add_argument("--all", action="store_true", help="alle Vorkommen suchen")
    p.add_argument("--out", type=Path, help="Ergebnisbild (Default: neben dem Screenshot)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    templates_dir = args.templates_dir or paths.project_root() / "assets" / "templates"

    try:
        image = load_image(args.screenshot)
    except (FileNotFoundError, ValueError) as e:
        print(f"Fehler: Screenshot nicht lesbar: {e}")
        return 2

    clock = FakeClock()
    device = FakeDevice([image], clock=clock)
    device.connect()
    vision = Vision(
        device,
        TemplateStore(templates_dir, reference_resolution=args.reference),
        clock=clock,
        matcher=TemplateMatcher(),
    )
    frame = vision.capture()

    try:
        template = vision.templates.get(args.template)
    except TemplateError as e:
        print(f"Fehler: {e}")
        return 2

    region = vision.to_device(args.region) if args.region else None
    threshold = vision.matcher.threshold_for(template, args.threshold)
    print(f"Screenshot : {args.screenshot} ({frame.size.w}x{frame.size.h}), Skalierung {vision.scale:.3f}")
    print(
        f"Template   : {template.name}, Threshold {threshold:.2f}, Region {region or template.region or '-'}"
    )
    for score in vision.matcher.score_variants(frame.image, template, region=region):
        source = score.variant.source.name if score.variant.source else "?"
        size = f"{score.variant.size.w}x{score.variant.size.h}"
        extras = " maske" if score.variant.mask is not None else ""
        extras += " einfarbig" if score.variant.flat else ""
        if score.match is None:
            print(f"  {source:<28} {size:>9}{extras}  Suchbereich kleiner als Template")
        else:
            m = score.match
            mark = "✔" if m.confidence >= threshold else "✗"
            print(f"  {source:<28} {size:>9}{extras}  {mark} {m.confidence:.3f} bei {m.rect}")

    if args.all:
        matches = vision.find_all(args.template, frame=frame, region=args.region, threshold=args.threshold)
        print(f"Treffer    : {len(matches)}")
        for m in matches:
            print(f"  {m.confidence:.3f}  {m.rect}  Mitte {m.rect.center}")
        found = bool(matches)
    else:
        match = vision.find(args.template, frame=frame, region=args.region, threshold=args.threshold)
        print(f"Ergebnis   : {'gefunden ' + str(match.rect) if match else 'nicht gefunden'}")
        found = match is not None

    safe_name = args.template.replace("/", "_")
    out = args.out or args.screenshot.with_name(f"{args.screenshot.stem}__{safe_name}.png")
    from adbbot.utils.image import save_image

    save_image(out, vision.overlay.render(frame.image))
    print(f"Bild       : {out}")
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
