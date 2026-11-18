"""歷史重大下跌段（/trend/drawdowns/）：重算 Excel 公式，對照快取與產出 JSON。不碰燈號規則。"""
from __future__ import annotations

import json
import math
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


def recompute():
    wb = openpyxl.load_workbook(XLSX, data_only=False)
    out = {}
    for name in wb.sheetnames:
        ws = wb[name]
        events, r = [], 3
        while ws.cell(r, 1).value is not None:
            start, end = ws.cell(r, 8).value, ws.cell(r, 9).value
            j, k = float(ws.cell(r, 10).value), float(ws.cell(r, 11).value)
            events.append({
                "seq": int(ws.cell(r, 1).value),
                "period": excel_period(start, end),
                "name": str(ws.cell(r, 3).value),
                "calendar_days": calendar_days(start, end),
                "trading_days": int(ws.cell(r, 5).value),
                "change_pts": k - j,
                "change_pct": k / j - 1,
                "severe": (k / j - 1) <= -0.20,
            })
            r += 1
        notes = []
        while r <= ws.max_row:
            val = ws.cell(r, 1).value
            if isinstance(val, str) and val.strip():
                notes.append(val)
            r += 1
        pcts = [e["change_pct"] for e in events]
        out[name] = dict(events=events, avg=mean(pcts), med=median(pcts), footnotes=notes)
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
        events, r = [], 3
        while ws.cell(r, 1).value is not None:
            events.append({
                "period": ws.cell(r, 2).value,
                "calendar_days": ws.cell(r, 4).value,
                "change_pts": ws.cell(r, 6).value,
                "change_pct": ws.cell(r, 7).value,
            })
            r += 1
        avg = med = None
        for rr in range(r, ws.max_row + 1):
            if ws.cell(rr, 3).value == "下跌段平均跌幅(%)":
                avg = ws.cell(rr, 7).value
            elif ws.cell(rr, 3).value == "下跌段跌幅中位數(%)":
                med = ws.cell(rr, 7).value
        out[name] = dict(events=events, avg=avg, med=med)
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
            assert a["change_pts"] == pytest.approx(b["change_pts"], rel=0, abs=1e-8)
            assert a["change_pct"] == pytest.approx(b["change_pct"], rel=0, abs=1e-12)
        assert comp["avg"] == pytest.approx(ch["avg"], rel=0, abs=1e-12)
        assert comp["med"] == pytest.approx(ch["med"], rel=0, abs=1e-12)


def test_json_matches_recompute(computed, payload):
    by_sheet = {x["sheet"]: x for x in payload["indices"]}
    assert set(by_sheet) == set(SHEET_IDS)
    for name, comp in computed.items():
        ix = by_sheet[name]
        assert ix["id"] == SHEET_IDS[name]
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
        assert ix["avg_pct"] == pytest.approx(comp["avg"], rel=0, abs=1e-12)
        assert ix["median_pct"] == pytest.approx(comp["med"], rel=0, abs=1e-12)
        assert ix["footnotes"] == comp["footnotes"]


def test_ndx_typo_footnote(computed, payload):
    note = "事件13 漲跌幅原表寫 -37.22%，經您確認為筆誤，已更正為 -37.72%。"
    assert note in computed["NDX"]["footnotes"]
    ndx = [x for x in payload["indices"] if x["id"] == "NDX"][0]
    assert note in ndx["footnotes"]
    ev = [e for e in ndx["events"] if e["seq"] == 13][0]
    assert ev["change_pct"] == pytest.approx(-0.37723070756332, rel=0, abs=1e-12)


def test_page_has_content_and_notes(computed):
    html = PAGE.read_text(encoding="utf-8")
    assert "<h1>歷史重大下跌段</h1>" in html
    assert "過去的下跌段不能預測未來" in html
    assert "非投資建議" in html
    assert "drawdowns.js" in html
    assert "../data.js" in html
    assert "依跌幅" in html and "依時間" in html
    for name, comp in computed.items():
        for fn in comp["footnotes"]:
            # footnotes are injected by JS from JSON, so they must be in the data file
            pass
        # titles appear in JSON / will be rendered; check JSON inclusion via data file
    js = (ROOT / "trend" / "drawdowns" / "drawdowns.js").read_text(encoding="utf-8")
    for name, comp in computed.items():
        for fn in comp["footnotes"]:
            assert fn in js, fn
    assert "現在" in html
    assert 'id="bars"' in html and 'id="timeline"' in html


def test_home_and_scale_in_links():
    home = HOME.read_text(encoding="utf-8")
    assert '<a href="./trend/drawdowns/">歷史跌幅</a>' in home
    nav = re.search(r'<nav class="book-nav" aria-label="內頁">.*?</nav>', home, re.S)
    assert nav and "drawdowns/" in nav.group(0)
    # 極簡：主圖區（內頁列之前）不要出現下跌段正文
    head = home.split('<div class="inner-nav"', 1)[0]
    assert "歷史重大下跌段" not in head
    scale = SCALE.read_text(encoding="utf-8")
    assert 'href="../drawdowns/"' in scale
    assert "歷史重大下跌段" in scale or "歷史跌幅" in scale
