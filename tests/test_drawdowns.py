"""歷史重大下跌段（/trend/drawdowns/）：重算 Excel 公式，對照快取與產出 JSON。不碰燈號規則。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from statistics import mean, median

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
XLSX = ROOT / "tests" / "fixtures" / "historical_drawdowns.xlsx"
OUT = ROOT / "trend" / "drawdowns" / "drawdowns.json"
PAGE = ROOT / "trend" / "drawdowns" / "index.html"
HOME = ROOT / "home" / "index.html"
SCALE = ROOT / "trend" / "scale-in" / "index.html"
SHEET_IDS = {"NDX": "NDX", "SPX": "SPX", "SOX": "SOX", "台灣加權": "TWII"}
AVG_LABELS = ("下跌段平均", "下跌段平均跌幅(%)")
MED_LABELS = ("下跌段中位數", "下跌段跌幅中位數(%)")


def excel_period(start, end) -> str:
    def fmt(d):
        if isinstance(d, datetime):
            d = d.date()
        return f"{d.year}/{d.month}/{d.day}"

    return f"{fmt(start)}~{fmt(end)}"


def calendar_days(start, end) -> int:
    if isinstance(start, datetime):
        start = start.date()
    if isinstance(end, datetime):
        end = end.date()
    return (end - start).days


def is_current_row(ws, r: int) -> bool:
    a, c = ws.cell(r, 1).value, ws.cell(r, 3).value
    return a == "現在" or c == "現在本波下跌"


def parse_leg(ws, r: int, seq):
    start, end = ws.cell(r, 8).value, ws.cell(r, 9).value
    j, k = float(ws.cell(r, 10).value), float(ws.cell(r, 11).value)
    return {
        "seq": seq,
        "period": excel_period(start, end),
        "name": str(ws.cell(r, 3).value),
        "calendar_days": calendar_days(start, end),
        "trading_days": int(ws.cell(r, 5).value),
        "change_pts": k - j,
        "change_pct": k / j - 1,
        "start_px": j,
        "end_px": k,
        "severe": (k / j - 1) <= -0.20,
        "_row": r,
    }


def recompute():
    wb = openpyxl.load_workbook(XLSX, data_only=False)
    out = {}
    for name in wb.sheetnames:
        ws = wb[name]
        events, current, notes = [], None, []
        for r in range(3, ws.max_row + 1):
            a = ws.cell(r, 1).value
            if is_current_row(ws, r):
                current = parse_leg(ws, r, "現在")
                current.pop("severe", None)
                continue
            if isinstance(a, int) or (isinstance(a, float) and a == int(a)):
                events.append(parse_leg(ws, r, int(a)))
                continue
            if isinstance(a, str) and a.strip() and a not in ("事件序列",):
                notes.append(a)
        pcts = [e["change_pct"] for e in events]
        days = [e["calendar_days"] for e in events]
        tdays = [e["trading_days"] for e in events]
        out[name] = dict(
            events=events,
            current=current,
            avg=mean(pcts),
            med=median(pcts),
            avg_days=mean(days),
            med_days=median(days),
            avg_td=mean(tdays),
            med_td=median(tdays),
            footnotes=notes,
            title=ws.cell(1, 1).value,
        )
    return out


@pytest.fixture(scope="module")
def computed():
    return recompute()


@pytest.fixture(scope="module")
def cached():
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    out = {}
    for name in wb.sheetnames:
        ws = wb[name]
        events, current = [], None
        avg = med = avg_days = med_days = avg_td = med_td = None
        for r in range(3, ws.max_row + 1):
            a, c = ws.cell(r, 1).value, ws.cell(r, 3).value
            if is_current_row(ws, r):
                current = {
                    "period": ws.cell(r, 2).value,
                    "calendar_days": ws.cell(r, 4).value,
                    "trading_days": ws.cell(r, 5).value,
                    "change_pts": ws.cell(r, 6).value,
                    "change_pct": ws.cell(r, 7).value,
                }
                continue
            if isinstance(a, int) or (isinstance(a, float) and a == int(a)):
                events.append({
                    "period": ws.cell(r, 2).value,
                    "calendar_days": ws.cell(r, 4).value,
                    "trading_days": ws.cell(r, 5).value,
                    "change_pts": ws.cell(r, 6).value,
                    "change_pct": ws.cell(r, 7).value,
                })
                continue
            if c in AVG_LABELS:
                avg, avg_days, avg_td = ws.cell(r, 7).value, ws.cell(r, 4).value, ws.cell(r, 5).value
            elif c in MED_LABELS:
                med, med_days, med_td = ws.cell(r, 7).value, ws.cell(r, 4).value, ws.cell(r, 5).value
        out[name] = dict(
            events=events, current=current,
            avg=avg, med=med, avg_days=avg_days, med_days=med_days, avg_td=avg_td, med_td=med_td,
        )
    return out


@pytest.fixture(scope="module")
def payload():
    assert OUT.exists(), "請先跑 python3 scripts/build_drawdowns.py"
    return json.loads(OUT.read_text(encoding="utf-8"))


def test_recomputed_matches_excel_cache(computed, cached):
    for name, comp in computed.items():
        ch = cached[name]
        assert len(comp["events"]) == len(ch["events"]), name
        for i, (a, b) in enumerate(zip(comp["events"], ch["events"])):
            assert a["period"] == b["period"], (name, i)
            assert a["calendar_days"] == b["calendar_days"], (name, i)
            assert a["trading_days"] == b["trading_days"], (name, i)
            assert a["change_pts"] == pytest.approx(b["change_pts"], rel=0, abs=1e-8)
            assert a["change_pct"] == pytest.approx(b["change_pct"], rel=0, abs=1e-12)
        assert comp["current"] is not None, name
        assert ch["current"] is not None, name
        assert comp["current"]["period"] == ch["current"]["period"], name
        assert comp["current"]["calendar_days"] == ch["current"]["calendar_days"], name
        assert comp["current"]["trading_days"] == ch["current"]["trading_days"], name
        assert comp["current"]["change_pts"] == pytest.approx(ch["current"]["change_pts"], rel=0, abs=1e-8)
        assert comp["current"]["change_pct"] == pytest.approx(ch["current"]["change_pct"], rel=0, abs=1e-12)
        assert comp["avg"] == pytest.approx(ch["avg"], rel=0, abs=1e-12)
        assert comp["med"] == pytest.approx(ch["med"], rel=0, abs=1e-12)
        assert comp["avg_days"] == pytest.approx(ch["avg_days"], rel=0, abs=1e-12)
        assert comp["med_days"] == pytest.approx(ch["med_days"], rel=0, abs=1e-12)
        assert comp["avg_td"] == pytest.approx(ch["avg_td"], rel=0, abs=1e-12)
        assert comp["med_td"] == pytest.approx(ch["med_td"], rel=0, abs=1e-12)


def test_json_matches_recompute(computed, payload):
    by_sheet = {x["sheet"]: x for x in payload["indices"]}
    assert set(by_sheet) == set(SHEET_IDS)
    assert payload.get("current_is_snapshot") is True
    for name, comp in computed.items():
        ix = by_sheet[name]
        assert ix["id"] == SHEET_IDS[name]
        assert ix["title"] == comp["title"]
        assert "下跌區段" in ix["title"]
        assert len(ix["events"]) == len(comp["events"])
        for a, b in zip(ix["events"], comp["events"]):
            assert a["seq"] == b["seq"]
            assert a["period"] == b["period"]
            assert a["name"] == b["name"]
            assert a["calendar_days"] == b["calendar_days"]
            assert a["trading_days"] == b["trading_days"]
            assert a["change_pts"] == pytest.approx(b["change_pts"], rel=0, abs=1e-12)
            assert a["change_pct"] == pytest.approx(b["change_pct"], rel=0, abs=1e-12)
            assert a["severe"] is b["severe"]
        cur, want = ix["current"], comp["current"]
        assert cur is not None
        assert cur["seq"] == "現在"
        assert cur["name"] == "現在本波下跌"
        assert cur["period"] == want["period"]
        assert cur["calendar_days"] == want["calendar_days"]
        assert cur["trading_days"] == want["trading_days"]
        assert cur["change_pts"] == pytest.approx(want["change_pts"], rel=0, abs=1e-12)
        assert cur["change_pct"] == pytest.approx(want["change_pct"], rel=0, abs=1e-12)
        assert "severe" not in cur
        assert ix["avg_pct"] == pytest.approx(comp["avg"], rel=0, abs=1e-12)
        assert ix["median_pct"] == pytest.approx(comp["med"], rel=0, abs=1e-12)
        assert ix["avg_calendar_days"] == pytest.approx(comp["avg_days"], rel=0, abs=1e-12)
        assert ix["median_calendar_days"] == pytest.approx(comp["med_days"], rel=0, abs=1e-12)
        assert ix["avg_trading_days"] == pytest.approx(comp["avg_td"], rel=0, abs=1e-12)
        assert ix["median_trading_days"] == pytest.approx(comp["med_td"], rel=0, abs=1e-12)
        assert ix["footnotes"] == comp["footnotes"]


def test_current_row_not_in_averages(computed):
    for name, comp in computed.items():
        events = comp["events"]
        cur = comp["current"]["change_pct"]
        pcts = [e["change_pct"] for e in events]
        days = [e["calendar_days"] for e in events]
        tdays = [e["trading_days"] for e in events]
        assert comp["avg"] == pytest.approx(mean(pcts), rel=0, abs=1e-15)
        assert comp["med"] == pytest.approx(median(pcts), rel=0, abs=1e-15)
        assert comp["avg_days"] == pytest.approx(mean(days), rel=0, abs=1e-15)
        assert comp["avg_td"] == pytest.approx(mean(tdays), rel=0, abs=1e-15)
        # 本波列沒有被算進去：把本波加進去會改變平均（這四個指數的本波與歷史平均都不同）
        assert abs(mean(pcts + [cur]) - mean(pcts)) > 1e-12, name
        assert abs(mean(days + [comp["current"]["calendar_days"]]) - mean(days)) > 1e-12, name


def test_ndx_typo_footnote(computed, payload):
    note = "事件7 漲跌幅原表寫 -37.22%，經您確認為筆誤，已更正為 -37.72%。"
    assert note in computed["NDX"]["footnotes"]
    ndx = [x for x in payload["indices"] if x["id"] == "NDX"][0]
    assert note in ndx["footnotes"]
    ev = [e for e in ndx["events"] if e["seq"] == 7][0]
    assert ev["change_pct"] == pytest.approx(-0.37723070756332, rel=0, abs=1e-12)


def test_current_wave_values(computed):
    expect = {
        "NDX": ("2026/10/6~2026/11/17", 42, 30, 31361.37, 26580.86),
        "SPX": ("2026/10/6~2026/11/17", 42, 30, 7844.52, 7288.34),
        "SOX": ("2026/6/22~2026/11/5", 136, 96, 14655.29, 8997.39),
        "台灣加權": ("2026/10/6~2026/11/6", 31, 20, 49968.92, 37837.05),
    }
    for name, (period, days, td, start, end) in expect.items():
        cur = computed[name]["current"]
        assert cur["period"] == period
        assert cur["calendar_days"] == days
        assert cur["trading_days"] == td
        assert cur["start_px"] == pytest.approx(start)
        assert cur["end_px"] == pytest.approx(end)
        assert cur["change_pct"] == pytest.approx(end / start - 1, rel=0, abs=1e-12)


def test_page_has_content_and_notes(computed):
    html = PAGE.read_text(encoding="utf-8")
    assert "<h1>歷史重大下跌段</h1>" in html
    assert "過去的下跌段不能預測未來" in html
    assert "非投資建議" in html
    assert "drawdowns.js" in html
    assert "../data.js" in html
    assert "依跌幅" in html and "依時間" in html
    assert "現在本波下跌" in html
    assert "黃底" in html
    assert "紅底" in html
    assert "靜態快照" in html
    assert "不是自動更新" in html or "不會自動更新" in html
    assert 'id="compare"' in html
    assert 'id="bars"' in html and 'id="timeline"' in html
    assert "class='current'" in html or 'class="current"' in html or '"current"' in html
    assert "下跌段平均" in html
    assert "下跌段中位數" in html
    js = (ROOT / "trend" / "drawdowns" / "drawdowns.js").read_text(encoding="utf-8")
    for name, comp in computed.items():
        for fn in comp["footnotes"]:
            assert fn in js, fn
        assert comp["current"]["period"] in js
    assert "沿用完整表的編號" not in html


def test_home_and_scale_in_links():
    home = HOME.read_text(encoding="utf-8")
    assert '<a href="./trend/drawdowns/">歷史跌幅</a>' in home
    nav = re.search(r'<nav class="book-nav" aria-label="看現況">.*?</nav>', home, re.S)
    assert nav and "drawdowns/" in nav.group(0)
    # 極簡：主圖區（分類導覽之前）不要出現下跌段正文
    head = home.split('<div class="home-nav"', 1)[0]
    assert "歷史重大下跌段" not in head
    scale = SCALE.read_text(encoding="utf-8")
    assert 'href="../drawdowns/"' in scale
    assert "歷史重大下跌段" in scale or "歷史跌幅" in scale

def test_bar_title_includes_period():
    """橫條圖把期間寫在標題列事件名後的全形括號裡；副標只留跌幅與多久。兩種排序共用。"""
    html = PAGE.read_text(encoding="utf-8")
    assert "function titleLines" in html
    assert 'titleLines(e.name, e.period, "500")' in html
    assert 'titleLines("現在本波下跌", now.period, "700")' in html
    # 全形括號包期間，例如 中美貿易戰加劇（2018/10/1~2018/12/24）
    assert '"（" + period + "）"' in html
    # 日期略小略淡，跟事件名同一行
    assert '"font-size":"11"' in html
    assert "a3aec0" in html
    # 副標回到只有跌幅與跌了多久，不再附期間
    assert 'pctSigned(e.change_pct) + " · " + dur(e.calendar_days)' in html
    assert 'pctSigned(now.change_pct) + " · " + dur(now.calendar_days)' in html
    assert "function subLines" not in html
    # 標題太長時自然換行
    assert "function wrapText" in html
    # 兩種排序都經過 ordered() 再畫同一套 items
    assert "var ev = ordered(ix)" in html
