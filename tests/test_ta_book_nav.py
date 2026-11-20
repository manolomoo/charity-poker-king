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
    ta = TA.read_text(encoding="utf-8")
    assert "./trend/ta-book/nav.js" in home
    assert "renderTaBookNav" in home
    assert 'id="ta-nav"' in home
    assert "./trend/ta-book/" in home
    # 首頁只在教學章節列過濾期望值／估值；教學頁本身仍用完整清單
    assert 'exclude:["expectancy","valuation"]' in home
    assert "exclude" not in ta


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



def _group_nav(home: str, label: str) -> str:
    m = re.search(rf'<nav class="book-nav" aria-label="{re.escape(label)}">.*?</nav>', home, re.S)
    assert m, f"缺少「{label}」晶片列"
    return m.group(0)


def test_homepage_nav_grouped():
    home = HOME.read_text(encoding="utf-8")
    assert 'id="home-nav"' in home
    assert "看現況" in home
    assert "工具與研究" in home
    assert "學觀念（技術分析書）" in home
    # 不再用含混的「內頁」列，避免跟教學晶片重複、分不清差在哪
    assert "inner-nav" not in home
    assert 'aria-label="內頁"' not in home

    now = _group_nav(home, "看現況")
    assert [m.group(1) for m in re.finditer(r'<a href="([^"]+)">', now)] == [
        "./signals/", "./trend/", "./trend/stage/", "./trend/valuation/", "./trend/drawdowns/",
    ]
    assert "訊號詳情" in now and "走勢" in now and "階段現況" in now and "估值" in now and "歷史跌幅（與反彈）" in now

    tools = _group_nav(home, "工具與研究")
    assert [m.group(1) for m in re.finditer(r'<a href="([^"]+)">', tools)] == [
        "./trend/scale-in/", "./trend/expectancy/", "./trend/left-side-fix/", "./yu-backtest/",
    ]
    assert "分批計算" in tools and "期望值" in tools and "左側修正" in tools and "皓角回測" in tools

    learn = home[home.find('id="learn-nav"'):home.find('id="pe-charts"')]
    assert '<a href="./trend/ta-book/">教學首頁</a>' in learn
    assert "<details" in learn and "<summary>教學章節</summary>" in learn
    assert 'id="ta-nav"' in learn
    # 教學章節摺疊列不再重複期望值、估值（已分別在工具與研究／看現況）
    # 來源清單本身仍含這兩項，只在 render 時排除
    assert "期望值" not in _group_nav(home, "學觀念")
    assert "估值" not in _group_nav(home, "學觀念")

    home_at = home.find('id="home-nav"')
    pe_at = home.find('id="pe-charts"')
    idx_at = home.find('id="idx"')
    assert 0 < idx_at < home_at < pe_at

    footer = re.search(r"<footer>.*?</footer>", home, re.S).group(0)
    assert "僅供參考，非投資建議。" in footer
    assert "trend/stage/" not in footer
    assert "scale-in" not in footer


if __name__ == "__main__":
    test_nav_js_matches_ta_book_sections()
    test_homepage_uses_same_nav_source()
    test_homepage_has_valuation_entry()
    test_homepage_has_valuation_charts()
    test_expected_labels_in_order()
    test_homepage_nav_grouped()
    print("ok", " / ".join(label for _, label in load_nav_items()))
