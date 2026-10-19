import sys
from pathlib import Path

import pytest

import mybot.bootstrap
from adbbot.bot.element import Element
from adbbot.bot.flow import Flow
from adbbot.bot.page import Page
from adbbot.bot.registry import FlowRegistry, Registries
from adbbot.core.exceptions import BotError
from adbbot.core.types import Size
from adbbot.vision.template import TemplateStore

ROOT = Path(__file__).resolve().parents[1]


def test_example_project_registration() -> None:
    reg = Registries()
    mybot.bootstrap.register(reg)
    assert reg.flows.names() == ["daily", "shop"]
    assert reg.pages.names() == ["home", "shop"]
    assert [w.title for w in reg.watchers] == ["Bewertungs-Popup"]
    assert reg.actions.names() == ["save_screenshot", "debug_views", "test_shop"]
    assert list(reg.actions.groups()) == ["Debug", "Test"]
    assert reg.actions.get("save_screenshot").description.startswith("Aktuellen Bildschirm")


def test_example_templates_exist() -> None:
    reg = Registries()
    mybot.bootstrap.register(reg)
    names = reg.template_names()
    assert {"home/logo", "shop/logo", "common/rate_later"} <= names
    TemplateStore(ROOT / "assets" / "templates", reference_resolution=Size(540, 960)).validate(names)


def test_discover_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "fakeproj"
    (pkg / "flows").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "flows" / "__init__.py").write_text("")
    (pkg / "pages.py").write_text(
        "from adbbot.bot.element import Element\n"
        "from adbbot.bot.page import Page\n"
        "class BasePage(Page):\n    pass\n"
        "class LoginPage(BasePage):\n    anchor = Element('login/logo')\n"
    )
    (pkg / "flows" / "login_flow.py").write_text(
        "from adbbot.bot.flow import Flow\n"
        "from fakeproj.pages import LoginPage\n"
        "class ProjectBase(Flow):\n    pass\n"
        "class LoginFlow(ProjectBase):\n    def run(self) -> None:\n        pass\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        import fakeproj

        reg = Registries()
        reg.discover(fakeproj)
        assert reg.pages.names() == ["login"]
        assert reg.flows.names() == ["login"]
        assert reg.template_names() == {"login/logo"}
    finally:
        for name in [m for m in sys.modules if m.startswith("fakeproj")]:
            del sys.modules[name]


def test_flow_registry_errors() -> None:
    class A(Flow):
        name = "same"

        def run(self) -> None: ...

    class B(Flow):
        name = "same"

        def run(self) -> None: ...

    flows = FlowRegistry()
    flows.register(A)
    flows.register(A)
    with pytest.raises(BotError, match="doppelt"):
        flows.register(B)
    with pytest.raises(BotError, match="Vorhanden: same"):
        flows.get("nope")


def test_unknown_action() -> None:
    with pytest.raises(BotError, match="Unbekannte Action"):
        Registries().actions.get("nope")


def test_page_classes_without_anchor_are_ignored() -> None:
    class Base(Page):
        pass

    class Real(Base):
        anchor = Element("real/anchor")

    assert not Base.has_anchor()
    assert Real.has_anchor()
