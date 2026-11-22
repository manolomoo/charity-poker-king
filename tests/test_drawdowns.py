"""歷史跌幅（與反彈）（/trend/drawdowns/）：重算 Excel 公式（含 L~Q 反彈段），對照快取與產出 JSON；本波由日線自動計算、不推算反彈。不碰燈號規則。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev

import sys

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import drawdowns_current as DC  # noqa: E402

BARS_FIXTURE = ROOT / "tests" / "fixtures" / "drawdowns_current_bars_20261119.json"
SITE_JSON = ROOT / "trend" / "data.json"
WORKFLOW = ROOT / ".github" / "workflows" / "update-and-deploy.yml"
UPDATE_TREND = ROOT / "scripts" / "update_trend.py"
XLSX = ROOT / "tests" / "fixtures" / "historical_drawdowns.xlsx"
OUT = ROOT / "trend" / "drawdowns" / "drawdowns.json"
PAGE = ROOT / "trend" / "drawdowns" / "index.html"
HOME = ROOT / "home" / "index.html"
SCALE = ROOT / "trend" / "scale-in" / "index.html"
SHEET_IDS = {"NDX": "NDX", "SPX": "SPX", "SOX": "SOX", "台灣加權": "TWII"}
AVG_LABELS = ("下跌段平均", "下跌段平均跌幅(%)")
MED_LABELS = ("下跌段中位數", "下跌段跌幅中位數(%)")
REBOUND_KEYS = ("turn_date", "turn_px", "calendar_days", "trading_days", "change_pts", "change_pct",
                "retrace_pct", "gap_to_high_pct")


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


def parse_rebound(ws, r: int):
    """L~Q：起點＝K 欄低點（盤中最低價），終點＝M 欄轉多日收盤價；反彈交易日（O 欄）直接讀。"""
    low_date, low = ws.cell(r, 9).value, float(ws.cell(r, 11).value)
    turn, close, td = ws.cell(r, 12).value, ws.cell(r, 13).value, ws.cell(r, 15).value
    if turn is None and close is None and td is None:
        return None
    close = float(close)
    return {
        "turn_date": turn.date().isoformat(),
        "turn_px": close,
        "calendar_days": calendar_days(low_date, turn),
        "trading_days": int(td),
        "change_pts": close - low,
        "change_pct": close / low - 1,
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
                current["rebound"] = parse_rebound(ws, r)
                continue
            if isinstance(a, int) or (isinstance(a, float) and a == int(a)):
                ev = parse_leg(ws, r, int(a))
                ev["rebound"] = parse_rebound(ws, r)
                events.append(ev)
                continue
            if isinstance(a, str) and a.strip() and a not in ("事件序列",):
                notes.append(a)
        pcts = [e["change_pct"] for e in events]
        days = [e["calendar_days"] for e in events]
        tdays = [e["trading_days"] for e in events]
        rebs = [e["rebound"] for e in events if e["rebound"]]
        out[name] = dict(
            reb_avg=mean(x["change_pct"] for x in rebs),
            reb_med=median(x["change_pct"] for x in rebs),
            reb_avg_days=mean(x["calendar_days"] for x in rebs),
            reb_med_days=median(x["calendar_days"] for x in rebs),
            reb_avg_td=mean(x["trading_days"] for x in rebs),
            reb_med_td=median(x["trading_days"] for x in rebs),
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
        reb = {}
        for r in range(3, ws.max_row + 1):
            a, c = ws.cell(r, 1).value, ws.cell(r, 3).value
            m = ws.cell(r, 13).value
            if m in ("反彈平均", "反彈中位數"):
                k = "avg" if m == "反彈平均" else "med"
                reb[k] = (ws.cell(r, 17).value, ws.cell(r, 14).value, ws.cell(r, 15).value)
            if is_current_row(ws, r):
                current = {
                    "period": ws.cell(r, 2).value,
                    "calendar_days": ws.cell(r, 4).value,
                    "trading_days": ws.cell(r, 5).value,
                    "change_pts": ws.cell(r, 6).value,
                    "change_pct": ws.cell(r, 7).value,
                    "rebound_cells": [ws.cell(r, c).value for c in range(12, 18)],
                }
                continue
            if isinstance(a, int) or (isinstance(a, float) and a == int(a)):
                events.append({
                    "reb_calendar_days": ws.cell(r, 14).value,
                    "reb_trading_days": ws.cell(r, 15).value,
                    "reb_change_pts": ws.cell(r, 16).value,
                    "reb_change_pct": ws.cell(r, 17).value,
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
            reb=reb,
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


def test_rebound_recomputed_matches_excel_cache(computed, cached):
    """L~Q 反彈段：公式重算（N＝L−I、P＝M−K、Q＝M/K−1）對 data_only 快取；反彈平均／中位數只算歷史列。"""
    for name, comp in computed.items():
        ch = cached[name]
        for i, (a, b) in enumerate(zip(comp["events"], ch["events"])):
            r = a["rebound"]
            assert r is not None, (name, i, "每段歷史下跌都要有反彈資料")
            assert r["calendar_days"] == b["reb_calendar_days"], (name, i)
            assert r["trading_days"] == b["reb_trading_days"], (name, i)
            assert r["change_pts"] == pytest.approx(b["reb_change_pts"], rel=0, abs=1e-8)
            assert r["change_pct"] == pytest.approx(b["reb_change_pct"], rel=0, abs=1e-12)
            assert r["calendar_days"] > 0 and r["trading_days"] > 0, (name, i, "轉多日要在低點之後")
        # 本波列沒有轉多日／反彈數字
        assert comp["current"]["rebound"] is None, name
        assert all(v is None for v in ch["current"]["rebound_cells"]), name
        for k, (cp, cd, ct) in ch["reb"].items():
            assert comp[f"reb_{k}"] == pytest.approx(cp, rel=0, abs=1e-12), (name, k)
            assert comp[f"reb_{k}_days"] == pytest.approx(cd, rel=0, abs=1e-12), (name, k)
            assert comp[f"reb_{k}_td"] == pytest.approx(ct, rel=0, abs=1e-12), (name, k)
        assert set(ch["reb"]) == {"avg", "med"}, name


def test_rebound_summary_values(computed):
    """使用者給的四指數反彈摘要（cache 與公式已對過）。"""
    expect = {
        # 平均%、平均天、平均交易日、中位數%、中位數天、中位數交易日、筆數
        "NDX": (0.1894, 29.4, 20.4, 0.1699, 25.5, 18, 8),
        "SPX": (0.1550, 43.1, 29.7, 0.1344, 17, 12, 9),
        "SOX": (0.2451, 26.9, 18.9, 0.2073, 25, 17, 15),
        "台灣加權": (0.1614, 30.9, 19.3, 0.1393, 27, 15.5, 12),
    }
    for name, (ap, ad, at, mp, md, mt, n) in expect.items():
        c = computed[name]
        assert len(c["events"]) == n
        assert c["reb_avg"] == pytest.approx(ap, abs=5e-5)
        assert c["reb_avg_days"] == pytest.approx(ad, abs=0.051)
        assert c["reb_avg_td"] == pytest.approx(at, abs=0.051)
        assert c["reb_med"] == pytest.approx(mp, abs=5e-5)
        assert c["reb_med_days"] == pytest.approx(md, abs=1e-9)
        assert c["reb_med_td"] == pytest.approx(mt, abs=1e-9)
    ndx = {e["name"]: e["rebound"] for e in computed["NDX"]["events"]}
    assert ndx[".COM泡沫破裂+911事件"]["change_pct"] == pytest.approx(0.4060, abs=5e-5)
    assert ndx[".COM泡沫破裂+911事件"]["calendar_days"] == 44
    maga = ndx["川普MAGA新政，關稅風暴席捲全球"]
    assert maga["turn_date"] == "2025-04-24" and maga["calendar_days"] == 17
    assert maga["change_pct"] == pytest.approx(0.1615, abs=5e-5)
    spx = max(computed["SPX"]["events"], key=lambda e: e["rebound"]["change_pct"])
    assert spx["name"] == "2008金融海嘯" and spx["rebound"]["calendar_days"] == 59
    assert spx["rebound"]["change_pct"] == pytest.approx(0.3606, abs=5e-5)
    sox = max(computed["SOX"]["events"], key=lambda e: e["rebound"]["change_pct"])
    assert sox["name"] == ".COM泡沫破裂+911事件" and sox["rebound"]["calendar_days"] == 44
    assert sox["rebound"]["change_pct"] == pytest.approx(0.7449, abs=5e-5)
    tw = {e["name"]: e["rebound"] for e in computed["台灣加權"]["events"]}
    top = max(tw.items(), key=lambda kv: kv[1]["change_pct"])
    assert top[0] == ".COM泡沫破裂+911事件" and top[1]["calendar_days"] == 69
    assert top[1]["change_pct"] == pytest.approx(0.3971, abs=5e-5)
    assert tw["川普MAGA新政，關稅風暴席捲全球"]["turn_date"] == "2025-04-29"
    assert tw["川普MAGA新政，關稅風暴席捲全球"]["calendar_days"] == 20
    assert tw["川普MAGA新政，關稅風暴席捲全球"]["change_pct"] == pytest.approx(0.1690, abs=5e-5)


def test_json_rebound_matches_recompute(computed, payload):
    by_sheet = {x["sheet"]: x for x in payload["indices"]}
    for name, comp in computed.items():
        ix = by_sheet[name]
        for a, b in zip(ix["events"], comp["events"]):
            r, rb = a["rebound"], b["rebound"]
            assert set(r) == set(REBOUND_KEYS)
            assert r["turn_date"] == rb["turn_date"]
            assert r["turn_px"] == pytest.approx(rb["turn_px"], rel=0, abs=1e-12)
            assert r["calendar_days"] == rb["calendar_days"]
            assert r["trading_days"] == rb["trading_days"]
            assert r["change_pts"] == pytest.approx(rb["change_pts"], rel=0, abs=1e-12)
            assert r["change_pct"] == pytest.approx(rb["change_pct"], rel=0, abs=1e-12)
            # 起點＝該次下跌的低點（盤中最低價）
            assert r["change_pct"] == pytest.approx(r["turn_px"] / a["end_px"] - 1, rel=0, abs=1e-12)
        # 指數層反彈平均／中位數：只用歷史 events
        rebs = [e["rebound"] for e in ix["events"]]
        for k, fn in (("avg", mean), ("median", median)):
            assert ix[f"rebound_{k}_pct"] == pytest.approx(fn(x["change_pct"] for x in rebs), rel=0, abs=1e-15)
            assert ix[f"rebound_{k}_calendar_days"] == pytest.approx(fn(x["calendar_days"] for x in rebs), rel=0, abs=1e-12)
            assert ix[f"rebound_{k}_trading_days"] == pytest.approx(fn(x["trading_days"] for x in rebs), rel=0, abs=1e-12)
        assert ix["rebound_avg_pct"] == pytest.approx(comp["reb_avg"], rel=0, abs=1e-12)
        assert ix["rebound_median_pct"] == pytest.approx(comp["reb_med"], rel=0, abs=1e-12)
        # 本波沒有轉多確認：不得帶 rebound
        assert not ix["current"].get("rebound"), name
        # 反彈註腳：仍在 footnotes（Excel 全文保留），另以 rebound_footnotes 標出
        assert len(ix["rebound_footnotes"]) == 2
        assert all(f in ix["footnotes"] for f in ix["rebound_footnotes"])
        assert any("「現在本波下跌」尚無反彈資料" in f for f in ix["rebound_footnotes"])
        assert any("轉多日的日K收盤價" in f for f in ix["rebound_footnotes"])
    assert "主觀判斷" in payload["rebound_method"] and "COBB我大哥" in payload["rebound_method"]


def test_apply_current_keeps_rebound_and_never_adds_to_current(payload, tmp_path):
    """本波自動覆寫（DC.apply_current／refresh_files 的核心）只動本波，歷史反彈原樣保留，本波不會長出 rebound。"""
    import copy
    data = copy.deepcopy(payload)
    site = json.loads(SITE_JSON.read_text(encoding="utf-8"))
    DC.apply_current(data, site, tmp_path)  # 空快取目錄：只用 data.json
    for a, b in zip(data["indices"], payload["indices"]):
        assert [e["rebound"] for e in a["events"]] == [e["rebound"] for e in b["events"]]
        assert a["rebound_avg_pct"] == b["rebound_avg_pct"]
        assert a["rebound_footnotes"] == b["rebound_footnotes"]
        assert all(f in a["footnotes"] for f in a["rebound_footnotes"])
        assert "rebound" not in a["current"]
        for k in RETRACE_INDEX_KEYS:
            assert a[k] == b[k], k


def test_build_rejects_rebound_on_current_row(tmp_path, monkeypatch):
    """Excel 本波列若出現轉多日，build 要停下來，不默默帶入。"""
    import build_drawdowns as BD
    wb = openpyxl.load_workbook(XLSX)
    ws = wb["NDX"]
    r = [i for i in range(3, ws.max_row + 1) if is_current_row(ws, i)][0]
    ws.cell(r, 12).value = datetime(2026, 11, 20)
    ws.cell(r, 13).value = 27000.0
    ws.cell(r, 15).value = 2
    with pytest.raises(ValueError, match="現在本波下跌"):
        BD.parse_sheet(ws)


def test_json_matches_recompute(computed, payload):
    by_sheet = {x["sheet"]: x for x in payload["indices"]}
    assert set(by_sheet) == set(SHEET_IDS)
    assert payload.get("current_is_snapshot") is False
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
        cur = ix["current"]
        assert cur is not None
        assert cur["seq"] == "現在"
        assert cur["name"] == "現在本波下跌"
        assert "severe" not in cur
        assert ix["avg_pct"] == pytest.approx(comp["avg"], rel=0, abs=1e-12)
        assert ix["median_pct"] == pytest.approx(comp["med"], rel=0, abs=1e-12)
        assert ix["avg_calendar_days"] == pytest.approx(comp["avg_days"], rel=0, abs=1e-12)
        assert ix["median_calendar_days"] == pytest.approx(comp["med_days"], rel=0, abs=1e-12)
        assert ix["avg_trading_days"] == pytest.approx(comp["avg_td"], rel=0, abs=1e-12)
        assert ix["median_trading_days"] == pytest.approx(comp["med_td"], rel=0, abs=1e-12)
        # 註腳：Excel 的其他註腳原樣保留；「現在本波下跌」那句改成自動更新版本
        excel_rest = [f for f in comp["footnotes"] if not f.startswith(DC.FOOTNOTE_PREFIX)]
        json_rest = [f for f in ix["footnotes"] if not f.startswith(DC.FOOTNOTE_PREFIX)]
        assert json_rest == excel_rest
        now_notes = [f for f in ix["footnotes"] if f.startswith(DC.FOOTNOTE_PREFIX)]
        assert len(now_notes) == 1
        assert "自動更新" in now_notes[0]
        assert "不計入上方的平均與中位數" in now_notes[0]
        assert "資料截至 " + DC.ymd(cur["asof"]) in now_notes[0]
        assert cur["source_label"] in now_notes[0]


def test_current_row_not_in_averages(computed, payload):
    by_sheet = {x["sheet"]: x for x in payload["indices"]}
    for name, comp in computed.items():
        ix = by_sheet[name]
        assert ix["current"] and not ix["current"].get("recovered"), name
        events = comp["events"]
        cur = ix["current"]["change_pct"]
        pcts = [e["change_pct"] for e in events]
        days = [e["calendar_days"] for e in events]
        tdays = [e["trading_days"] for e in events]
        assert comp["avg"] == pytest.approx(mean(pcts), rel=0, abs=1e-15)
        assert comp["med"] == pytest.approx(median(pcts), rel=0, abs=1e-15)
        assert comp["avg_days"] == pytest.approx(mean(days), rel=0, abs=1e-15)
        assert comp["avg_td"] == pytest.approx(mean(tdays), rel=0, abs=1e-15)
        # 本波列沒有被算進去：把本波加進去會改變平均（這四個指數的本波與歷史平均都不同）
        assert abs(mean(pcts + [cur]) - mean(pcts)) > 1e-12, name
        assert abs(mean(days + [ix["current"]["calendar_days"]]) - mean(days)) > 1e-12, name
        # JSON 的平均與中位數＝只用歷史 events
        assert ix["avg_pct"] == pytest.approx(mean(e["change_pct"] for e in ix["events"]), rel=0, abs=1e-15)
        assert ix["median_pct"] == pytest.approx(median(e["change_pct"] for e in ix["events"]), rel=0, abs=1e-15)
        assert ix["avg_calendar_days"] == pytest.approx(mean(e["calendar_days"] for e in ix["events"]), rel=0, abs=1e-12)
        assert ix["avg_trading_days"] == pytest.approx(mean(e["trading_days"] for e in ix["events"]), rel=0, abs=1e-12)


def test_ndx_typo_footnote(computed, payload):
    note = "事件7 漲跌幅原表寫 -37.22%，經您確認為筆誤，已更正為 -37.72%。"
    assert note in computed["NDX"]["footnotes"]
    ndx = [x for x in payload["indices"] if x["id"] == "NDX"][0]
    assert note in ndx["footnotes"]
    ev = [e for e in ndx["events"] if e["seq"] == 7][0]
    assert ev["change_pct"] == pytest.approx(-0.37723070756332, rel=0, abs=1e-12)


def test_excel_snapshot_values(computed):
    """Excel 表上的黃底列（舊快照，只用來確認 Excel 解析沒壞；網頁已改用自動計算）。"""
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
    assert "<title>慈善撲克王｜歷史跌幅（與反彈）</title>" in html
    assert "過去的下跌段不能預測未來" in html
    assert "非投資建議" in html
    assert "drawdowns.js" in html
    assert "../data.js" in html
    assert "依跌幅" in html and "依時間" in html
    assert "現在本波下跌" in html
    assert "黃底" in html
    assert "紅底" in html
    assert "靜態快照" not in html
    assert "不是自動更新" not in html and "不會自動更新" not in html
    assert "自動計算" in html
    assert "證交所" in html and "Yahoo" in html
    assert "資料截至" in html
    assert "結束日是低點" in html
    assert "不計入平均與中位數" in html
    # 已創新高的優雅處理
    assert "function waveOf" in html and "recovered" in html
    assert "已創新高，目前沒有進行中的下跌段" in html
    # 對照行不再說是 Yahoo 收盤（台指已是證交所），並註明口徑不同
    assert "本站 Yahoo 收盤" not in html
    assert "本站收盤相對 52 週高點" in html and "口徑不同" in html
    assert 'id="compare"' in html
    assert 'id="bars"' in html and 'id="timeline"' in html
    assert "class='current'" in html or 'class="current"' in html or '"current"' in html
    assert "下跌段平均" in html
    assert "下跌段中位數" in html
    js = (ROOT / "trend" / "drawdowns" / "drawdowns.js").read_text(encoding="utf-8")
    for name, comp in computed.items():
        for fn in comp["footnotes"]:
            if fn.startswith(DC.FOOTNOTE_PREFIX):
                continue
            assert fn in js, fn
    data = json.loads(OUT.read_text(encoding="utf-8"))
    for ix in data["indices"]:
        for fn in ix["footnotes"]:
            assert fn in js
        assert ix["current"]["period"] in js
    assert "沿用完整表的編號" not in html


def test_page_rebound_section_below_old_block():
    html = PAGE.read_text(encoding="utf-8")
    # 舊區塊仍在：h1、原 lede、舊圖 id、明細表與舊註腳
    lede = "依使用者提供的 Excel 下跌段表做成網頁。表內高低點是 TradingView 日線的盤中最高價與最低價，不是收盤價。"
    assert "<h1>歷史重大下跌段</h1>" in html and lede in html
    assert '<p class="sub">過去每次大跌，跌了多少、跌了多久</p>' in html
    for i in ('id="bars"', 'id="timeline"', 'id="tbody"', 'id="footnotes"', 'id="sum"', 'id="compare"', 'id="sort"'):
        assert i in html, i
    # 新區塊在明細表之後、頁底免責框之前
    sec = html.index('<section id="rebound"')
    assert html.index('<section id="table-sec">') < html.index('id="footnotes"') < sec < html.index('<div class="box-o">')
    body = html[sec:html.index("</section>", sec)]
    assert "<h2 id=\"rb-h\">跌段與反彈對照</h2>" in body
    # 主觀判斷註記與方法來源、口徑
    assert "轉多確認點是 Cobb 的主觀判斷" in body and "方法來源：COBB我大哥" in body
    assert "盤中最低價" in body and "轉多日的日K收盤價" in body and "口徑不同" in body
    assert "不能預測未來" in body and "非投資建議" in body
    assert "不含「現在本波下跌」" in body
    for i in ('id="rb-tabs"', 'id="rb-list"', 'id="rb-ref"', 'id="rb-mode"', 'id="rb-sort"', 'id="rb-footnotes"'):
        assert i in body, i
    # 本波：只畫跌段，不推算反彈
    assert "尚無轉多確認（不推算反彈）" in html
    assert "function paintRebound" in html and "paintRebound(ix, wave)" in html
    js = html[html.index("function paintRebound"):html.index("function paint()")]
    assert "wave.rebound" not in js and "cur.rebound" not in js
    # 反彈註腳只顯示在新區塊；舊註腳清單排除它們
    assert "ix.rebound_footnotes" in html
    assert "rbNotes.indexOf(t)<0" in html
    assert "#00838f" in html.lower()


def test_home_and_scale_in_links():
    home = HOME.read_text(encoding="utf-8")
    assert '<a href="./trend/drawdowns/">歷史跌幅（與反彈）</a>' in home
    nav = re.search(r'<nav class="book-nav" aria-label="看現況">.*?</nav>', home, re.S)
    assert nav and "drawdowns/" in nav.group(0)
    # 極簡：主圖區（分類導覽之前）不要出現下跌段正文
    head = home.split('<div class="home-nav"', 1)[0]
    assert "歷史重大下跌段" not in head
    scale = SCALE.read_text(encoding="utf-8")
    assert 'href="../drawdowns/"' in scale
    assert '<a href="../drawdowns/">歷史跌幅（與反彈）</a>' in scale
    lsf = (ROOT / "trend" / "left-side-fix" / "index.html").read_text(encoding="utf-8")
    assert lsf.count('<a href="../drawdowns/">歷史跌幅（與反彈）</a>') == 2
    assert "歷史重大下跌段" in scale or "歷史跌幅" in scale

def test_bar_title_includes_period():
    """橫條圖把期間寫在標題列事件名後的全形括號裡；副標只留跌幅與多久（本波另加名次）。兩種排序共用。"""
    html = PAGE.read_text(encoding="utf-8")
    assert "function titleLines" in html
    assert 'titleLines(isNow ? "現在本波下跌" : e.name, e.period, isNow ? "700" : "500")' in html
    # 全形括號包期間，例如 中美貿易戰加劇（2018/10/1~2018/12/24）
    assert '"（" + period + "）"' in html
    # 日期略小略淡，跟事件名同一行
    assert '"font-size":"11"' in html
    assert "a3aec0" in html
    # 副標回到只有跌幅與跌了多久，不再附期間；本波在前面加上名次
    assert 'pctSigned(e.change_pct) + " · " + dur(e.calendar_days)' in html
    assert '"第 " + info.rank + "／" + info.total + " 名 · " + base' in html
    assert "function subLines" not in html
    # 標題太長時自然換行
    assert "function wrapText" in html
    # 兩種排序都經過 ordered()（含本波插入）再畫同一套 items
    assert "var ev = ordered(ix)" in html
    assert "items.push" not in html


# ---------------- 本波自動計算 ----------------
def fixture_bars():
    raw = json.loads(BARS_FIXTURE.read_text(encoding="utf-8"))
    return {k: list(zip(v["dates"], v["high"], v["low"])) for k, v in raw["indices"].items()}


def last_event_end(payload, idx_id):
    ix = [x for x in payload["indices"] if x["id"] == idx_id][0]
    return ix["events"][-1]["end_date"]


EXPECT_20261119 = {
    # id: (asof, start_date, start_px, end_date, end_px, period, calendar_days, trading_days, pct)
    "TWII": ("2026-11-19", "2026-10-06", 49968.92, "2026-11-06", 37837.05, "2026/10/6~2026/11/6", 31, 20, -0.2428),
    "SOX": ("2026-11-18", "2026-06-22", 14655.29, "2026-11-05", 8997.39, "2026/6/22~2026/11/5", 136, 96, -0.3861),
    "NDX": ("2026-11-18", "2026-10-06", 31361.37, "2026-11-17", 26580.86, "2026/10/6~2026/11/17", 42, 30, -0.1524),
    "SPX": ("2026-11-18", "2026-10-06", 7844.52, "2026-11-18", 7285.76, "2026/10/6~2026/11/18", 43, 31, -0.0712),
}


def test_compute_current_on_20261119_bars(payload):
    """盤中高 → 之後盤中低、交易日＝K 棒索引差；用 2026-11-19 的日線快照鎖定四個指數。"""
    bars = fixture_bars()
    for idx_id, (asof, sd, spx, ed, epx, period, cd, td, pct) in EXPECT_20261119.items():
        after = last_event_end(payload, idx_id)
        assert after.startswith("2025-04"), (idx_id, after)
        cur = DC.compute_current(bars[idx_id], after)
        assert cur["recovered"] is False, idx_id
        assert cur["asof"] == asof, idx_id
        assert cur["start_date"] == sd and cur["end_date"] == ed, idx_id
        assert cur["start_px"] == pytest.approx(spx, abs=1e-9)
        assert cur["end_px"] == pytest.approx(epx, abs=1e-9)
        assert cur["period"] == period
        assert cur["calendar_days"] == cd
        assert cur["trading_days"] == td
        assert cur["change_pct"] == pytest.approx(epx / spx - 1, rel=0, abs=1e-12)
        assert round(cur["change_pct"], 4) == pytest.approx(pct, abs=1e-9)
        assert cur["change_pts"] == pytest.approx(epx - spx, abs=1e-9)
        assert cur["name"] == "現在本波下跌" and cur["seq"] == "現在"


def test_twii_1119_low_does_not_extend_period():
    """台指 11/19 低點沒跌破 11/6，期間仍停在低點日，但截至日是 11/19。"""
    bars = fixture_bars()["TWII"]
    last = bars[-1]
    assert last[0] == "2026-11-19" and last[2] == pytest.approx(38259.70)
    cur = DC.compute_current(bars, "2025-04-09")
    assert cur["end_date"] == "2026-11-06" and cur["asof"] == "2026-11-19"


def test_compute_current_uses_intraday_and_index_diff():
    bars = [
        ("2025-04-07", 200.0, 50.0),   # 上一段低點當天（不算）
        ("2025-04-08", 100.0, 90.0),
        ("2025-04-09", 120.0, 110.0),  # 高點（high 最大）
        ("2025-04-10", 118.0, 95.0),
        ("2025-04-11", 115.0, 80.0),   # 低點（之後 low 最小）
        ("2025-04-14", 112.0, 85.0),
    ]
    cur = DC.compute_current(bars, "2025-04-07")
    assert cur["start_date"] == "2025-04-09" and cur["start_px"] == 120.0
    assert cur["end_date"] == "2025-04-11" and cur["end_px"] == 80.0
    assert cur["trading_days"] == 2  # 索引差，不含兩端
    assert cur["calendar_days"] == 2
    assert cur["period"] == "2025/4/9~2025/4/11"
    assert cur["change_pct"] == pytest.approx(80 / 120 - 1)
    assert cur["asof"] == "2025-04-14"
    # 高點前的低點（4/8 的 90 比 4/9 之後都高，但 4/7 的 50 在上一段）不算
    assert DC.compute_current(bars, "2025-04-14") is None


def test_compute_current_new_high_is_recovered():
    """創新高：高點就是最後一根，不拿當天振幅當本波下跌。"""
    bars = [
        ("2026-01-02", 100.0, 95.0),
        ("2026-01-05", 104.0, 98.0),
        ("2026-01-06", 110.0, 101.0),  # 最後一根創新高，當天振幅 −8%
    ]
    cur = DC.compute_current(bars, "2025-12-31")
    assert cur["recovered"] is True
    assert cur["asof"] == "2026-01-06"
    assert cur["high_date"] == "2026-01-06" and cur["high_px"] == 110.0
    assert "period" not in cur and "change_pct" not in cur
    # 高點後還有日K、但沒有比高點當根更低的低點：也算沒有進行中的下跌段
    bars2 = bars + [("2026-01-07", 109.0, 102.0)]
    cur2 = DC.compute_current(bars2, "2025-12-31")
    assert cur2["recovered"] is True and cur2["asof"] == "2026-01-07"


def test_merge_bars_prefers_recent_and_caps():
    cache = [("2026-11-14", 10.0, 9.0), ("2026-11-17", 11.0, 9.5), ("2026-11-18", 99.0, 1.0)]
    recent = [("2026-11-17", 11.5, 9.4)]
    out = DC.merge_bars(cache, recent, cap_date="2026-11-17")
    assert out == [("2026-11-14", 10.0, 9.0), ("2026-11-17", 11.5, 9.4)]
    out2 = DC.merge_bars(cache[:2], [("2026-11-17", 11.5, 9.4), ("2026-11-19", 12.0, 10.0)])
    assert [b[0] for b in out2] == ["2026-11-14", "2026-11-17", "2026-11-19"]


def _fake_site(idx_bars, tw_source="TWSE"):
    charts = {k: {"chart_daily_1y": {"dates": [b[0] for b in v], "high": [b[1] for b in v], "low": [b[2] for b in v]}}
              for k, v in idx_bars.items()}
    meta = {"tickers": {"SPX": "^GSPC", "TWII": "^TWII"}, "tw_source": {"TWII": {"price_source": tw_source}},
            "price_asof": "2026-01-05"}
    return {"analysis": {"meta": meta}, "charts": {"indices": charts}}


def test_apply_current_on_fake_data(tmp_path):
    old_note = "「現在本波下跌」（黃底列）：舊快照，資料截至 2026/11/17 收盤。"
    payload = {
        "current_is_snapshot": True,
        "indices": [
            {"id": "SPX", "events": [{"end_date": "2025-12-31", "change_pct": -0.3, "calendar_days": 10}],
             "avg_pct": -0.3, "current": {"period": "舊"}, "footnotes": ["其他註腳", old_note]},
            {"id": "TWII", "events": [{"end_date": "2025-12-31", "change_pct": -0.25, "calendar_days": 5}],
             "avg_pct": -0.25, "current": {"period": "舊"}, "footnotes": [old_note]},
        ],
    }
    site = _fake_site({
        "SPX": [("2026-01-02", 100.0, 95.0), ("2026-01-05", 110.0, 101.0)],              # 創新高
        "TWII": [("2026-01-02", 100.0, 95.0), ("2026-01-05", 99.0, 90.0), ("2026-01-06", 98.0, 92.0)],
    })
    DC.apply_current(payload, site, tmp_path)  # tmp_path：沒有快取
    spx, tw = payload["indices"]
    assert payload["current_is_snapshot"] is False
    assert spx["current"]["recovered"] is True and spx["current"]["source"] == "Yahoo"
    assert spx["current"]["source_label"].startswith("Yahoo ^GSPC")
    assert spx["avg_pct"] == -0.3  # 平均不動
    assert spx["footnotes"][0] == "其他註腳" and len(spx["footnotes"]) == 2
    assert "已創新高，目前沒有進行中的下跌段" in spx["footnotes"][-1]
    assert "資料截至 2026/1/5" in spx["footnotes"][-1]
    assert old_note not in spx["footnotes"]
    c = tw["current"]
    assert c["recovered"] is False and c["source"] == "TWSE" and "證交所" in c["source_label"]
    assert c["period"] == "2026/1/2~2026/1/5" and c["trading_days"] == 1 and c["asof"] == "2026-01-06"
    assert "資料截至 2026/1/6" in tw["footnotes"][-1]
    # 重跑不會累積註腳
    DC.apply_current(payload, site, tmp_path)
    assert len([f for f in tw["footnotes"] if f.startswith(DC.FOOTNOTE_PREFIX)]) == 1


def test_bars_for_uses_cache_and_skips_other_source(tmp_path):
    site = _fake_site({"TWII": [("2026-01-05", 101.0, 99.0)]}, tw_source="TWSE")
    csv_text = "date,timestamp,open,high,low,close,adjclose,volume\n" \
               "2026-01-02,0,1,150.004,90,1,1,0\n2026-01-05,0,1,999,1,1,1,0\n2026-01-06,0,1,5,4,4,4,0\n"
    (tmp_path / "TWII_daily_2y.csv").write_text(csv_text, encoding="utf-8")
    # 快取沒有來源檔 → 不確定是不是證交所，不混用
    bars, key = DC.bars_for("TWII", site, tmp_path)
    assert key is None and bars == [("2026-01-05", 101.0, 99.0)]
    (tmp_path / "TWII_source.json").write_text('{"price_source": "TWSE"}', encoding="utf-8")
    bars, key = DC.bars_for("TWII", site, tmp_path)
    # 同來源：快取補舊日期、重疊日以 data.json 為準、比 data.json 新的快取丟掉
    assert key == "daily_2y"
    assert bars == [("2026-01-02", 150.0, 90.0), ("2026-01-05", 101.0, 99.0)]


def test_json_current_matches_site_data(payload):
    """drawdowns.json 的本波＝用目前 trend/data.json（＋快取）重算的結果；截至日用各指數自己的最後一根日K。"""
    site = json.loads(SITE_JSON.read_text(encoding="utf-8"))
    for ix in payload["indices"]:
        cur = ix["current"]
        bars, _ = DC.bars_for(ix["id"], site, DC.CACHE)
        want = DC.compute_current(bars, ix["events"][-1]["end_date"])
        site_last = site["charts"]["indices"][ix["id"]]["chart_daily_1y"]["dates"][-1]
        assert cur["asof"] == want["asof"] == site_last, ix["id"]
        assert cur["recovered"] is want["recovered"]
        for k in ("period", "calendar_days", "trading_days", "start_date", "end_date"):
            assert cur.get(k) == want.get(k), (ix["id"], k)
        for k in ("start_px", "end_px", "change_pct"):
            if k in want:
                assert cur[k] == pytest.approx(want[k], rel=0, abs=1e-9), (ix["id"], k)
        assert cur["after_date"] == ix["events"][-1]["end_date"]
        want_src = site["analysis"]["meta"]["tw_source"]["TWII"]["price_source"] if ix["id"] == "TWII" else "Yahoo"
        assert cur["source"] == want_src
    tw = [x for x in payload["indices"] if x["id"] == "TWII"][0]
    us = [x for x in payload["indices"] if x["id"] == "SPX"][0]
    # 台指截至日不跟美股的 price_asof 綁在一起
    assert tw["current"]["asof"] == site["analysis"]["meta"]["tw_asof"]["TWII"]
    assert us["current"]["asof"] == site["analysis"]["meta"]["price_asof"]


def test_update_trend_refreshes_drawdowns_after_success():
    src = UPDATE_TREND.read_text(encoding="utf-8")
    assert "def refresh_drawdowns" in src and "DC.refresh_files(ROOT)" in src
    main = src[src.index("def main()"):src.index("def refresh_drawdowns")]
    # 失敗分支 return 0 在前，成功寫出 data.json 之後才刷新
    assert main.index("write_atomic(DATA_JSON") < main.index("refresh_drawdowns()")
    fail = main[main.index("except SourceError as e:"):main.index("write_atomic(DATA_JSON")]
    assert "return 0" in fail and "refresh_drawdowns" not in fail


def test_workflow_adds_drawdowns_generated_files():
    wf = WORKFLOW.read_text(encoding="utf-8")
    step = wf[wf.index("檢查是否有資料變更"):wf.index("Commit 並推送")]
    assert "git add -f trend/drawdowns/drawdowns.json trend/drawdowns/drawdowns.js" in step
    assert "trend/drawdowns/index.html" not in step


# ---------------- 點數回血比例與距前高（跌段與反彈對照） ----------------
RETRACE_INDEX_KEYS = (
    "retrace_avg_pct", "retrace_std_pct", "retrace_median_pct",
    "gap_to_high_avg_pct", "gap_to_high_std_pct", "gap_to_high_median_pct",
    "rebound_std_calendar_days", "rebound_count",
)


def test_rebound_retrace_and_gap_per_event(payload):
    """每個歷史 rebound：回血＝(轉多收盤−低點)/(高點−低點)，距前高＝轉多收盤/高點−1，用 Excel 的高低點重算。"""
    for ix in payload["indices"]:
        for e in ix["events"]:
            r = e["rebound"]
            hi, lo, turn = e["start_px"], e["end_px"], r["turn_px"]
            assert r["retrace_pct"] == pytest.approx((turn - lo) / (hi - lo), rel=0, abs=1e-12), (ix["id"], e["seq"])
            assert r["gap_to_high_pct"] == pytest.approx(turn / hi - 1, rel=0, abs=1e-12), (ix["id"], e["seq"])
            # 不是「反彈%÷|跌幅%|」（分母不同；跌得深時差很多）
            ratio = r["change_pct"] / abs(e["change_pct"])
            assert r["retrace_pct"] == pytest.approx(ratio * (1 + e["change_pct"]), rel=1e-9)


def test_rebound_retrace_index_stats_use_sample_stdev(payload):
    for ix in payload["indices"]:
        ev = ix["events"]
        retr = [e["rebound"]["retrace_pct"] for e in ev]
        gap = [e["rebound"]["gap_to_high_pct"] for e in ev]
        days = [e["rebound"]["calendar_days"] for e in ev]
        assert ix["rebound_count"] == len(ev) >= 2
        assert ix["retrace_avg_pct"] == pytest.approx(mean(retr), rel=0, abs=1e-15)
        assert ix["retrace_median_pct"] == pytest.approx(median(retr), rel=0, abs=1e-15)
        assert ix["retrace_std_pct"] == pytest.approx(stdev(retr), rel=0, abs=1e-15)  # 樣本（n−1）
        assert ix["gap_to_high_avg_pct"] == pytest.approx(mean(gap), rel=0, abs=1e-15)
        assert ix["gap_to_high_median_pct"] == pytest.approx(median(gap), rel=0, abs=1e-15)
        assert ix["gap_to_high_std_pct"] == pytest.approx(stdev(gap), rel=0, abs=1e-15)
        assert ix["rebound_std_calendar_days"] == pytest.approx(stdev(days), rel=0, abs=1e-12)
        # 明確是樣本標準差，不是母體 pstdev
        from statistics import pstdev
        assert ix["retrace_std_pct"] > pstdev(retr)


def test_sample_stdev_none_when_n_lt_2():
    import build_drawdowns as BD
    assert BD.sample_stdev([]) is None
    assert BD.sample_stdev([0.3]) is None
    assert BD.sample_stdev([0.1, 0.3]) == pytest.approx(stdev([0.1, 0.3]))


def test_rebound_retrace_known_values(payload):
    by = {x["id"]: x for x in payload["indices"]}
    tw = by["TWII"]
    assert tw["rebound_count"] == 12
    assert tw["retrace_avg_pct"] == pytest.approx(0.340, abs=5e-4)
    assert tw["retrace_std_pct"] == pytest.approx(0.111, abs=5e-4)
    assert tw["retrace_median_pct"] == pytest.approx(0.371, abs=5e-4)
    assert tw["gap_to_high_median_pct"] == pytest.approx(-0.176, abs=5e-4)
    assert tw["rebound_avg_calendar_days"] == pytest.approx(30.9, abs=0.05)
    assert tw["rebound_std_calendar_days"] == pytest.approx(15.8, abs=0.05)
    assert tw["rebound_median_calendar_days"] == 27
    ndx = {e["name"]: e["rebound"] for e in by["NDX"]["events"]}[".COM泡沫破裂+911事件"]
    assert ndx["retrace_pct"] == pytest.approx(0.080, abs=5e-4)
    assert ndx["gap_to_high_pct"] == pytest.approx(-0.768, abs=5e-4)
    sox = {e["name"]: e["rebound"] for e in by["SOX"]["events"]}[".COM泡沫破裂+911事件"]
    assert sox["change_pct"] == pytest.approx(0.745, abs=5e-4)
    assert sox["retrace_pct"] == pytest.approx(0.135, abs=5e-4)
    # SPX：平均天數被 .COM 194 天拉高，中位數只有 17 天
    spx = by["SPX"]
    assert max(e["rebound"]["calendar_days"] for e in spx["events"]) == 194
    assert spx["rebound_median_calendar_days"] == 17
    assert 194 >= 3 * spx["rebound_avg_calendar_days"]
    # 其他三個指數不會觸發「最長一段遠大於平均」的提醒
    for i in ("NDX", "SOX", "TWII"):
        assert max(e["rebound"]["calendar_days"] for e in by[i]["events"]) < 3 * by[i]["rebound_avg_calendar_days"]


def test_current_wave_has_no_retrace(payload):
    for ix in payload["indices"]:
        cur = ix["current"]
        assert "rebound" not in cur and "retrace_pct" not in cur and "gap_to_high_pct" not in cur
        assert not any("retrace" in k or "gap_to_high" in k for k in cur)


def test_page_rebound_retrace_section():
    html = PAGE.read_text(encoding="utf-8")
    sec = html.index('<section id="rebound"')
    body = html[sec:html.index("</section>", sec)]
    assert "收回跌幅（點數）＝(轉多日收盤−低點)÷(高點−低點)" in body
    assert "距前高＝轉多日收盤÷高點−1" in body
    assert "不用 %÷%" in body and "分母不同" in body
    assert "樣本標準差" in body and 'id="rb-ex"' in body
    js = html[html.index("function paintRebound"):html.index("function paint()")]
    helpers = html[html.index("function pct1"):html.index("function paintRebound")]
    assert "轉多確認時：" in helpers and "收回跌幅 " in helpers and "（點數）" in helpers and "距前高還差 " in helpers
    assert "r.retrace_pct" in helpers and "r.gap_to_high_pct" in helpers
    # 摘要句由 JSON 算：回血 mean±std、樣本標準差、天數 mean±std、中位數、距前高中位數
    for k in ("ix.retrace_avg_pct", "ix.retrace_std_pct", "ix.retrace_median_pct", "ix.rebound_std_calendar_days",
              "ix.rebound_median_calendar_days", "ix.gap_to_high_median_pct"):
        assert k in js, k
    assert "樣本標準差" in js and "中位數" in js and "較穩" in js
    assert "不是反彈終點" in js and "主觀判斷" in js
    # 數字不寫死
    for lit in ("34.0%", "11.1%", "30.9", "15.8", "17.6%", "194"):
        assert lit not in js, lit
    # 回血只出現在歷史事件卡；本波卡維持「尚無轉多確認」，不呼叫 retrLine、不用平均去填
    now = js[js.index("if(wave){"):js.index("list.innerHTML = h;")]
    assert "尚無轉多確認（不推算反彈）" in now
    assert "retrLine" not in now and "retrace" not in now and "gap_to_high" not in now
    assert "retrLine(r)" in js
    # 舊區塊文案未動
    assert "<h1>歷史重大下跌段</h1>" in html
    assert "依使用者提供的 Excel 下跌段表做成網頁。表內高低點是 TradingView 日線的盤中最高價與最低價，不是收盤價。" in html
    for i in ('id="bars"', 'id="timeline"', 'id="tbody"', 'id="sum"'):
        assert i in html
    old = html[:sec]
    assert "收回跌幅" not in old and "retrace" not in old
