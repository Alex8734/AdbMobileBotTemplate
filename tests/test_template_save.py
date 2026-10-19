import json
from pathlib import Path

import numpy as np
import pytest

from adbbot.core.exceptions import TemplateError
from adbbot.core.types import Rect, Size
from adbbot.vision.template import TemplateStore
from tests.imagegen import textured


@pytest.fixture
def store(tmp_path: Path) -> TemplateStore:
    return TemplateStore(tmp_path, reference_resolution=Size(1080, 1920))


def test_save_new_template_with_metadata(store: TemplateStore, tmp_path: Path) -> None:
    image = textured(30, 20, seed=1)
    path = store.save(
        "home/play_button",
        image,
        threshold=0.9,
        region=Rect(0, 0, 200, 100),
        reference_resolution=Size(720, 1280),
    )
    assert path == tmp_path / "home" / "play_button.png"
    assert json.loads((tmp_path / "home" / "play_button.json").read_text(encoding="utf-8")) == {
        "threshold": 0.9,
        "region": [0, 0, 200, 100],
        "reference_resolution": [720, 1280],
    }
    template = store.get("home/play_button")
    assert np.array_equal(template.variants[0].image, image)
    assert template.threshold == 0.9


def test_conflicts_variants_and_overwrite(store: TemplateStore) -> None:
    a, b, c = (textured(30, 20, seed=s) for s in (1, 2, 3))
    store.save("home/play", a)
    cached = store.get("home/play")
    assert not (store.root / "home" / "play.json").exists()  # ohne Metadaten keine JSON-Datei

    with pytest.raises(TemplateError, match="existiert bereits"):
        store.save("home/play", b)
    assert store.save("home/play", b, variant=True).name == "play@2.png"
    assert store.save("home/play", c, variant=True).name == "play@3.png"
    template = store.get("home/play")
    assert template is not cached  # Cache wurde invalidiert
    assert len(template.variants) == 3

    store.save("home/play", c, overwrite=True)
    assert np.array_equal(store.get("home/play").variants[0].image, c)


def test_metadata_is_merged(store: TemplateStore, tmp_path: Path) -> None:
    store.save("shop/buy", textured(30, 20, seed=1), threshold=0.8)
    store.save("shop/buy", textured(30, 20, seed=2), variant=True, region=Rect(1, 2, 3, 4))
    assert json.loads((tmp_path / "shop" / "buy.json").read_text(encoding="utf-8")) == {
        "threshold": 0.8,
        "region": [1, 2, 3, 4],
    }


@pytest.mark.parametrize("name", ["Play", "home/", "../x", "home play", "", "home//x"])
def test_invalid_names(store: TemplateStore, name: str) -> None:
    with pytest.raises(TemplateError, match="Ungültiger Template-Name"):
        store.save(name, textured(10, 10, seed=1))
