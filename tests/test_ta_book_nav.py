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


def test_expected_labels_in_order():
    labels = [label for _, label in load_nav_items()]
    assert labels == [
        "核心觀念", "趨勢", "牛熊階段", "反轉形態", "整固形態",
        "突破與目標", "缺口", "支撐與趨勢線", "交易戰術", "期望值",
    ]


if __name__ == "__main__":
    test_nav_js_matches_ta_book_sections()
    test_homepage_uses_same_nav_source()
    test_expected_labels_in_order()
    print("ok", " / ".join(label for _, label in load_nav_items()))
