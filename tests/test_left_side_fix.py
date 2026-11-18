"""左側買修正規則頁：數字必須來自研究報告／CSV／引擎曲線，不碰燈號規則。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "trend" / "left-side-fix" / "index.html"
SUMMARY = ROOT / "trend" / "left-side-fix" / "summary.json"
CURVES = ROOT / "trend" / "left-side-fix" / "curves.json"
HOME = ROOT / "home" / "index.html"
SCALE = ROOT / "trend" / "scale-in" / "index.html"
DRAW = ROOT / "trend" / "drawdowns" / "index.html"


@pytest.fixture(scope="module")
def summary():
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def curves():
    return json.loads(CURVES.read_text(encoding="utf-8"))


def test_page_is_research_not_signal_rule():
    html = PAGE.read_text(encoding="utf-8")
    assert "<h1>左側買修正規則</h1>" in html
    assert "研究參考" in html
    assert "不是燈號規則" in html or "不是燈號" in html
    assert "v3.0" in html
    assert "非投資建議" in html
    assert "claude-opus" in html.lower() or "Claude opus" in html
    # 三步
    assert "跌 10% 開始分批買" in html
    assert "減半" in html
    assert "站回" in html
    # 限制
    assert "獨立樣本少" in html
    assert "不能預測未來" in html


def test_problem_numbers_from_csv(summary):
    by = {r["label"]: r for r in summary["problem_s0_from_10"]}
    # 與 REPORT §2 四捨五入後的數字一致
    assert by["NDX .com泡沫"]["min_vs_capital"] == pytest.approx(-0.76, abs=0.005)
    assert by["SOX .com泡沫"]["min_vs_capital"] == pytest.approx(-0.81, abs=0.005)
    assert by["SPX 2008金融海嘯"]["min_vs_capital"] == pytest.approx(-0.42, abs=0.005)
    assert by["台股 2000"]["min_vs_capital"] == pytest.approx(-0.51, abs=0.005)
    assert by["日經 1990"]["min_vs_capital"] == pytest.approx(-0.68, abs=0.005)
    assert by["SPX 1929"]["min_vs_capital"] == pytest.approx(-0.81, abs=0.005)
    # 反例
    assert by["SPX 1987股災"]["min_vs_capital"] == pytest.approx(-0.04, abs=0.005)
    assert by["SPX 2020疫情"]["min_vs_capital"] == pytest.approx(-0.11, abs=0.005)
    # 子彈在 1/4～1/3
    for key in ("NDX .com泡沫", "SOX .com泡沫", "日經 1990", "台股 2000", "SPX 1929"):
        share = by[key]["spent_share"]
        assert 0.24 <= share <= 0.42, (key, share)


def test_event_and_standard_from_csv(summary):
    s0 = summary["event_5y"]["S0_原計畫後抱"]
    fin = summary["event_5y"]["FIN_標準(R1+R2+閒置定額)"]
    cons = summary["event_5y"]["FIN_保守(R1+E1+閒置定額)"]
    assert s0["n"] == 180
    assert s0["worst_loss"] == pytest.approx(-0.778, abs=0.002)
    assert fin["worst_loss"] == pytest.approx(-0.426, abs=0.002)
    assert cons["worst_loss"] == pytest.approx(-0.208, abs=0.002)
    # 報告四捨五入
    assert round(s0["worst_loss"] * 100) == -78
    assert round(fin["worst_loss"] * 100) == -43
    # 只擋加碼救不了尾部
    for key in ("B1_低於200日線暫停加碼", "B2_本站空頭規則暫停加碼", "C_波動>1.5倍暫停加碼"):
        assert summary["event_5y"][key]["worst_loss"] == pytest.approx(-0.78, abs=0.01)
    # 最簡版引自報告
    assert summary["simple_event"]["worst_loss"] == pytest.approx(-0.61, abs=0.005)
    assert summary["simple_event"]["median_final"] == pytest.approx(1.63, abs=0.005)


def test_curves_match_engine_exports(curves, summary):
    by = {c["id"]: c for c in curves["crashes"]}
    assert by["NDX"]["start_mode"] == "跌10%才開始"
    assert by["SPX"]["start_mode"] == "跌10%才開始"
    ndx = {s["label"]: s for s in by["NDX"]["series"]}
    spx = {s["label"]: s for s in by["SPX"]["series"]}
    assert ndx["原計畫"]["min_vs_capital"] == pytest.approx(-0.761, abs=0.002)
    assert ndx["標準版"]["min_vs_capital"] == pytest.approx(-0.277, abs=0.002)
    assert ndx["一次買滿"]["min_vs_capital"] == pytest.approx(-0.799, abs=0.002)
    assert spx["原計畫"]["min_vs_capital"] == pytest.approx(-0.424, abs=0.002)
    assert spx["標準版"]["min_vs_capital"] == pytest.approx(-0.102, abs=0.002)
    # 與問題表同一起點
    prob = {r["label"]: r for r in summary["problem_s0_from_10"]}
    assert ndx["原計畫"]["min_vs_capital"] == pytest.approx(prob["NDX .com泡沫"]["min_vs_capital"], abs=1e-9)
    assert spx["原計畫"]["min_vs_capital"] == pytest.approx(prob["SPX 2008金融海嘯"]["min_vs_capital"], abs=1e-9)
    # 曲線有日期與淨值，起點／最低／終點都在
    for s in (ndx["標準版"], spx["標準版"]):
        assert s["start_date"] in s["dates"]
        assert s["min_date"] in s["dates"]
        assert s["end_date"] in s["dates"]
        assert s["values"][0] == pytest.approx(1.0, abs=0.02) or s["values"][0] <= 1.0


def test_cross_links_and_home_chip():
    html = PAGE.read_text(encoding="utf-8")
    for href in ("../scale-in/", "../drawdowns/", "../../yu-backtest/"):
        assert href in html
    home = HOME.read_text(encoding="utf-8")
    assert '<a href="./trend/left-side-fix/">左側修正</a>' in home
    nav = re.search(r'<nav class="book-nav" aria-label="工具與研究">.*?</nav>', home, re.S)
    assert nav and "left-side-fix/" in nav.group(0)
    for page, href in ((SCALE, "../left-side-fix/"), (DRAW, "../left-side-fix/")):
        assert href in page.read_text(encoding="utf-8")


def test_home_inner_nav_complete():
    html = HOME.read_text(encoding="utf-8")
    now = re.search(r'<nav class="book-nav" aria-label="看現況">.*?</nav>', html, re.S)
    tools = re.search(r'<nav class="book-nav" aria-label="工具與研究">.*?</nav>', html, re.S)
    learn = re.search(r'<nav class="book-nav" aria-label="學觀念">.*?</nav>', html, re.S)
    assert now and tools and learn
    expected_now = {
        "./signals/": "訊號詳情",
        "./trend/": "走勢",
        "./trend/stage/": "階段現況",
        "./trend/valuation/": "估值",
        "./trend/drawdowns/": "歷史跌幅",
    }
    expected_tools = {
        "./trend/scale-in/": "分批計算",
        "./trend/expectancy/": "期望值",
        "./trend/left-side-fix/": "左側修正",
        "./yu-backtest/": "皓角回測",
    }
    for href, label in expected_now.items():
        assert href in now.group(0) and label in now.group(0), (href, label)
    for href, label in expected_tools.items():
        assert href in tools.group(0) and label in tools.group(0), (href, label)
    assert '<a href="./trend/ta-book/">教學首頁</a>' in learn.group(0)
    assert "學觀念（技術分析書）" in html
    footer = re.search(r"<footer>.*?</footer>", html, re.S).group(0)
    # 頁腳不再重複列出導覽連結
    assert "trend/stage/" not in footer
    assert "僅供參考，非投資建議。" in footer
