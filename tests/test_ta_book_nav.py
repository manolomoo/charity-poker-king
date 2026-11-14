"""教學頁導覽列與首頁晶片共用 trend/ta-book/nav.js，避免兩邊對不上。"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
NAV_JS = ROOT / "trend" / "ta-book" / "nav.js"
TA = ROOT / "trend" / "ta-book" / "index.html"
HOME = ROOT / "home" / "index.html"


def load_nav_items():
    text = NAV_JS.read_text(encoding="utf-8")
    items = re.findall(r'id:\s*"([^"]+)"\s*,\s*label:\s*"([^"]+)"', text)
    assert items, "nav.js 裡沒有讀到段落清單"
    return items


def test_nav_js_matches_ta_book_sections():
    items = load_nav_items()
    html = TA.read_text(encoding="utf-8")
    for sid, label in items:
        assert f'id="{sid}"' in html, f"教學頁缺少段落 #{sid}（{label}）"
    # 教學頁必須用這份清單來畫導覽，而不是另寫一排連結
    assert 'src="nav.js"' in html
    assert "renderTaBookNav" in html


def test_homepage_uses_same_nav_source():
    home = HOME.read_text(encoding="utf-8")
    assert "./trend/ta-book/nav.js" in home
    assert "renderTaBookNav" in home
    assert 'id="ta-nav"' in home
    assert "./trend/ta-book/" in home


def test_homepage_has_valuation_entry():
    home = HOME.read_text(encoding="utf-8")
    assert "./trend/valuation/" in home
    assert "pe-tag" in home
    val = ROOT / "trend" / "valuation" / "index.html"
    assert val.exists(), "缺少 /trend/valuation/ 頁面"


def test_homepage_has_valuation_charts():
    home = HOME.read_text(encoding="utf-8")
    # 極簡首頁既有內容下面（晶片列之後、頁腳之前）放兩張 Forward P/E 圖
    assert 'id="pe-charts"' in home
    assert 'id="pe-sox"' in home
    assert 'id="pe-ndx"' in home
    assert 'id="ta-nav"' in home
    nav_at = home.find('id="ta-nav"')
    pe_at = home.find('id="pe-charts"')
    foot_at = home.find("<footer>")
    assert 0 < nav_at < pe_at < foot_at, "估值圖應在教學晶片下面、頁腳上面"
    assert 'data-w="2016"' in home
    assert 'data-w="2020"' in home
    assert 'data-w="2011"' in home
    # 既有小標仍在
    assert "pe-tag" in home


def test_expected_labels_in_order():
    labels = [label for _, label in load_nav_items()]
    assert labels == [
        "核心觀念", "趨勢", "牛熊階段", "反轉形態", "整固形態",
        "突破與目標", "缺口", "支撐與趨勢線", "交易戰術", "期望值", "估值",
    ]


if __name__ == "__main__":
    test_nav_js_matches_ta_book_sections()
    test_homepage_uses_same_nav_source()
    test_homepage_has_valuation_entry()
    test_homepage_has_valuation_charts()
    test_expected_labels_in_order()
    print("ok", " / ".join(label for _, label in load_nav_items()))
