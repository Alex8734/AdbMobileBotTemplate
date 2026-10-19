import json
from pathlib import Path

import numpy as np
import pytest

from adbbot.core.exceptions import TemplateError
from adbbot.core.types import Rect, Size
from adbbot.utils.image import save_image
from adbbot.vision.template import Template, TemplateStore
from tests.imagegen import solid_patch, textured


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "templates"
    save_image(root / "home" / "shop_button.png", textured(60, 40, seed=1))
    save_image(root / "home" / "shop_button@2.png", textured(60, 40, seed=2))
    save_image(root / "home" / "shop_button@10.png", textured(60, 40, seed=3))
    save_image(root / "home" / "shop_button_big.png", textured(120, 80, seed=4))
    (root / "home" / "shop_button.json").write_text(
        json.dumps({"threshold": 0.9, "region": [0, 100, 360, 200]}), encoding="utf-8"
    )

    star = textured(30, 30, seed=5)
    save_image(root / "common" / "star.png", star)
    mask = np.full((30, 30), 255, dtype=np.uint8)
    mask[:10, :10] = 0
    save_image(root / "common" / "star_mask.png", np.dstack([mask] * 3))

    with_alpha = np.dstack([textured(20, 20, seed=6), np.full((20, 20), 255, dtype=np.uint8)])
    with_alpha[:5, :, 3] = 0
    save_image(root / "common" / "close_x.png", with_alpha)

    save_image(root / "logo.png", solid_patch(10, 10, (0, 0, 255)))
    return root


def test_names_skip_masks_and_group_variants(root: Path) -> None:
    store = TemplateStore(root)
    assert store.names() == [
        "common/close_x",
        "common/star",
        "home/shop_button",
        "home/shop_button_big",
        "logo",
    ]
    assert store.exists("logo")
    assert not store.exists("home/nope")


def test_variants_and_metadata(root: Path) -> None:
    tpl = TemplateStore(root).get("home/shop_button")
    assert [v.source.name for v in tpl.variants if v.source] == [
        "shop_button.png",
        "shop_button@2.png",
        "shop_button@10.png",
    ]
    assert tpl.threshold == 0.9
    assert tpl.region == Rect(0, 100, 360, 200)
    assert tpl.scale == 1.0
    assert len(TemplateStore(root).get("home/shop_button_big").variants) == 1


def test_mask_file_and_alpha(root: Path) -> None:
    store = TemplateStore(root)
    star_mask = store.get("common/star").variants[0].mask
    assert star_mask is not None
    assert set(np.unique(star_mask)) == {0, 1}
    assert star_mask[:10, :10].sum() == 0

    alpha_mask = store.get("common/close_x").variants[0].mask
    assert alpha_mask is not None
    assert alpha_mask[:5].sum() == 0
    assert alpha_mask[5:].all()
    assert store.get("common/close_x").variants[0].image.shape == (20, 20, 3)


def test_mask_from_json(root: Path) -> None:
    save_image(root / "home" / "shared_mask_file.png", np.full((40, 60, 3), 255, dtype=np.uint8))
    (root / "home" / "shop_button_big.json").write_text(json.dumps({"mask": "shared_mask_file.png"}))
    with pytest.raises(TemplateError, match="andere Größe"):
        TemplateStore(root).get("home/shop_button_big")


def test_flat_detection(root: Path) -> None:
    store = TemplateStore(root)
    assert store.get("logo").variants[0].flat
    assert not store.get("home/shop_button").variants[0].flat


def test_cache_and_reload(root: Path) -> None:
    store = TemplateStore(root)
    first = store.get("logo")
    assert store.get("logo") is first
    store.set_screen_size(None)  # unverändert → Cache bleibt
    assert store.get("logo") is first
    store.reload()
    assert store.get("logo") is not first


def test_missing_template_suggests_names(root: Path) -> None:
    with pytest.raises(TemplateError, match="meintest du home/shop_button"):
        TemplateStore(root).get("home/shop_buton")


def test_validate_collects_all_errors(root: Path) -> None:
    store = TemplateStore(root)
    store.validate(["logo", "home/shop_button"])
    with pytest.raises(TemplateError, match="2 Template") as exc:
        store.validate(["logo", "a/b", "c/d"])
    assert "a/b" in str(exc.value)
    assert "c/d" in str(exc.value)


@pytest.mark.parametrize("content", ["{kaputt", json.dumps({"treshold": 0.5}), json.dumps({"threshold": 2})])
def test_invalid_metadata(root: Path, content: str) -> None:
    (root / "logo.json").write_text(content, encoding="utf-8")
    with pytest.raises(TemplateError, match="Metadaten"):
        TemplateStore(root).get("logo")


def test_scaling_to_screen_size(root: Path) -> None:
    store = TemplateStore(root, reference_resolution=Size(720, 1280))
    store.set_screen_size(Size(360, 640))
    tpl = store.get("home/shop_button")
    assert tpl.scale == 0.5
    assert tpl.variants[0].size == Size(30, 20)
    assert tpl.region == Rect(0, 50, 180, 100)
    star_mask = store.get("common/star").variants[0].mask
    assert star_mask is not None
    assert star_mask.shape == (15, 15)


def test_per_template_reference_resolution(root: Path) -> None:
    (root / "logo.json").write_text(json.dumps({"reference_resolution": [360, 640]}), encoding="utf-8")
    store = TemplateStore(root, reference_resolution=Size(1080, 1920))
    store.set_screen_size(Size(720, 1280))
    assert store.get("logo").scale == 2.0
    assert store.get("logo").variants[0].size == Size(20, 20)


def test_from_image() -> None:
    tpl = Template.from_image("adhoc", textured(10, 10, seed=1), threshold=0.5)
    assert tpl.name == "adhoc"
    assert tpl.variants[0].source is None
    with pytest.raises(TemplateError, match="leer"):
        Template.from_image("x", textured(10, 10, seed=1), mask=np.zeros((10, 10), dtype=np.uint8))
