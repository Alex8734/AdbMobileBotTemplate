from adbbot.utils.text import default_name, slugify, snake_case


def test_names() -> None:
    assert snake_case("DailyRewardFlow") == "daily_reward_flow"
    assert default_name("HomePage", "Page") == "home"
    assert default_name("ShopItemsPage", "Page") == "shop_items"
    assert default_name("Page", "Page") == "page"


def test_slugify() -> None:
    assert slugify("Tagesgeschenk abholen") == "Tagesgeschenk_abholen"
    assert slugify("Größe › Übersicht!") == "Groesse_Uebersicht"
    assert slugify("???") == "unbenannt"
    assert len(slugify("x" * 200, max_length=20)) == 20
