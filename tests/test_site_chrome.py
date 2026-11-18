"""全站導覽、深色主題、品牌與舊路徑轉址。不碰燈號規則 v3.0。"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "home" / "index.html"
TREND = ROOT / "trend" / "index.html"
TA_BOOK = ROOT / "trend" / "ta-book" / "index.html"
STYLE = ROOT / "assets" / "style.css"
COMMON = ROOT / "assets" / "common.js"
BUILD = ROOT / "scripts" / "build_site.sh"


def _titles_and_h1():
    pages = [
        ROOT / "index.html",
        ROOT / "rules.html",
        ROOT / "history.html",
        HOME,
        TREND,
        ROOT / "trend" / "stage" / "index.html",
        ROOT / "trend" / "valuation" / "index.html",
        ROOT / "trend" / "drawdowns" / "index.html",
        ROOT / "trend" / "scale-in" / "index.html",
        ROOT / "trend" / "expectancy" / "index.html",
        ROOT / "trend" / "left-side-fix" / "index.html",
        TA_BOOK,
        ROOT / "yu-backtest" / "index.html",
    ]
    for p in pages:
        yield p, p.read_text(encoding="utf-8")


def test_trend_and_tabook_have_home_links():
    trend = TREND.read_text(encoding="utf-8")
    nav = re.search(r"<nav>.*?</nav>", trend, re.S).group(0)
    footer = re.search(r"<footer[^>]*>.*?</footer>", trend, re.S).group(0)
    assert '<a href="../">首頁</a>' in nav
    assert '<a href="../">← 首頁</a>' in footer
    assert "燈號站首頁" not in trend
    assert '<a href="../signals/">燈號詳情</a>' in footer

    book = TA_BOOK.read_text(encoding="utf-8")
    site_nav = re.search(r'<nav class="site-nav"[^>]*>.*?</nav>', book, re.S)
    assert site_nav, "教學頁缺少網站導覽"
    assert '<a href="../../">首頁</a>' in site_nav.group(0)
    assert '<a href="../">走勢觀察</a>' in site_nav.group(0)
    # 教學章節清單本身仍由 nav.js 產出，不含首頁（避免首頁章節列被污染）
    assert "renderTaBookNav(document.getElementById(\"nav\"))" in book


def test_signals_css_is_dark_and_status_tokens_readable():
    css = STYLE.read_text(encoding="utf-8")
    root = re.search(r":root\s*\{(.*?)\n\}", css, re.S).group(1)
    assert "--bg: #0f1724" in root
    assert "--red: #ff7482" in root
    assert "--yellow: #f2b552" in root
    assert "--red-bg: #2a1518" in root
    assert "--yellow-bg: #2b2412" in root
    assert "--red-ink: #ffb3ba" in root
    assert "--yellow-ink: #f6d48a" in root
    assert "color-scheme: dark" in root
    assert "#faf4ec" not in css
    # 熱圖與膠囊仍走語意 token，深底淺字
    assert "table.heatmap td.cell.red { background: var(--red-bg); }" in css
    assert "table.heatmap td.cell.yellow { background: var(--yellow-bg); }" in css
    assert ".pill.red { background: var(--red-bg); color: var(--red-ink); }" in css
    assert ".pill.yellow { background: var(--yellow-bg); color: var(--yellow-ink); }" in css


def test_tabook_has_no_dead_light_css():
    book = TA_BOOK.read_text(encoding="utf-8")
    assert "#f1f4f8" not in book
    assert "dark theme overrides" not in book
    assert "--bg:#0f1724" in book


def test_branding_titles_are_poker_king():
    for p, html in _titles_and_h1():
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        assert title, p
        t = title.group(1)
        if p.name == "index.html" and "home" not in p.parts and "trend" not in str(p) and "yu-backtest" not in str(p) and p.parent == ROOT:
            assert t.startswith("慈善撲克王")
        elif p == HOME:
            assert "慈善撲克王" in t
        else:
            assert "慈善撲克王" in t, f"{p} title={t}"
        # 標題列不再把 COBB我大哥當站名
        assert not t.startswith("COBB我大哥"), p
        assert "｜COBB我大哥" not in t


def test_cobb_kept_as_method_credit_not_nav_brand():
    common = COMMON.read_text(encoding="utf-8")
    assert '"慈善撲克王"' in common
    assert "COBB我大哥" not in common
    idx = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "<h1>慈善撲克王</h1>" in idx
    assert "方法來源：COBB我大哥" in idx
    # 規則本文未改
    rules_md = (ROOT / "rules.md").read_text(encoding="utf-8")
    assert "v3.0" in rules_md


def test_homepage_keeps_single_yu_entry():
    html = HOME.read_text(encoding="utf-8")
    assert "yu-card" not in html
    tools = re.search(r'<nav class="book-nav" aria-label="工具與研究">.*?</nav>', html, re.S)
    assert tools and '<a href="./yu-backtest/">皓角回測</a>' in tools.group(0)
    assert html.count("./yu-backtest/") == 1


def test_build_emits_redirects_without_clobbering_home():
    out = ROOT / ".pytest_tmp_site"
    if out.exists():
        # 由腳本自己 rm -rf
        pass
    subprocess.run([str(BUILD), str(out)], check=True, cwd=ROOT)
    try:
        root_index = (out / "index.html").read_text(encoding="utf-8")
        assert "<h1>慈善撲克王</h1>" in root_index
        assert "location.replace" not in root_index
        assert 'id="home-nav"' in root_index

        rules = (out / "rules.html").read_text(encoding="utf-8")
        history = (out / "history.html").read_text(encoding="utf-8")
        home_redir = (out / "home" / "index.html").read_text(encoding="utf-8")
        for page, target in (
            (rules, "./signals/rules.html"),
            (history, "./signals/history.html"),
            (home_redir, "../"),
        ):
            assert 'http-equiv="refresh"' in page
            assert target in page
            assert "location.replace" in page
            assert f'href="{target}"' in page

        sig_rules = (out / "signals" / "rules.html").read_text(encoding="utf-8")
        assert "<h1>判定規則</h1>" in sig_rules
        assert "location.replace" not in sig_rules
        assert "慈善撲克王｜判定規則" in sig_rules
    finally:
        subprocess.run(["rm", "-rf", str(out)], check=True)
