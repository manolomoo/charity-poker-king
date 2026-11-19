#!/usr/bin/env python3
"""從 tests/fixtures/historical_drawdowns.xlsx 重算公式，寫出 trend/drawdowns/ 的資料檔。不碰燈號規則。

歷史 events、平均與中位數來自 Excel；「現在本波下跌」若找得到本站走勢日線（trend/data.json 與
data/cache/trend/），改由 scripts/drawdowns_current.py 以同一口徑自動計算並覆寫（current_is_snapshot=false）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean, median

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
XLSX = ROOT / "tests" / "fixtures" / "historical_drawdowns.xlsx"
OUT_DIR = ROOT / "trend" / "drawdowns"
SHEET_IDS = {"NDX": "NDX", "SPX": "SPX", "SOX": "SOX", "台灣加權": "TWII"}
AVG_LABELS = ("下跌段平均", "下跌段平均跌幅(%)")
MED_LABELS = ("下跌段中位數", "下跌段跌幅中位數(%)")


def excel_period(start, end) -> str:
    def fmt(d):
        if isinstance(d, datetime):
            d = d.date()
        return f"{d.year}/{d.month}/{d.day}"

    return f"{fmt(start)}~{fmt(end)}"


def as_date(d) -> str:
    if isinstance(d, datetime):
        return d.date().isoformat()
    return d.isoformat()


def calendar_days(start, end) -> int:
    if isinstance(start, datetime):
        start = start.date()
    if isinstance(end, datetime):
        end = end.date()
    return (end - start).days


def fill_rgb(cell) -> str:
    try:
        if cell.fill and cell.fill.patternType and cell.fill.fgColor:
            return str(getattr(cell.fill.fgColor, "rgb", None) or "")
    except Exception:
        return ""
    return ""


def is_yellow(cell) -> bool:
    return "FFFF00" in fill_rgb(cell)


def is_current_row(ws, r: int) -> bool:
    a, c = ws.cell(r, 1).value, ws.cell(r, 3).value
    return a == "現在" or c == "現在本波下跌" or is_yellow(ws.cell(r, 1))


def parse_leg(ws, r: int, seq) -> dict:
    start, end = ws.cell(r, 8).value, ws.cell(r, 9).value
    start_px, end_px = float(ws.cell(r, 10).value), float(ws.cell(r, 11).value)
    change_pts = end_px - start_px
    change_pct = end_px / start_px - 1
    return {
        "seq": seq,
        "period": excel_period(start, end),
        "name": str(ws.cell(r, 3).value),
        "calendar_days": calendar_days(start, end),
        "trading_days": int(ws.cell(r, 5).value),
        "change_pts": change_pts,
        "change_pct": change_pct,
        "start_date": as_date(start),
        "end_date": as_date(end),
        "start_px": start_px,
        "end_px": end_px,
        "severe": change_pct <= -0.20,
        "_row": r,
    }


def parse_sheet(ws) -> dict:
    title = ws.cell(1, 1).value
    events = []
    current = None
    footnotes = []
    for r in range(3, ws.max_row + 1):
        a, c = ws.cell(r, 1).value, ws.cell(r, 3).value
        if is_current_row(ws, r):
            cur = parse_leg(ws, r, "現在")
            cur.pop("severe", None)
            current = cur
            continue
        if isinstance(a, int) or (isinstance(a, float) and a == int(a)):
            ev = parse_leg(ws, r, int(a))
            events.append(ev)
            continue
        if isinstance(a, str) and a.strip() and a not in ("事件序列",):
            footnotes.append(a)
    pcts = [e["change_pct"] for e in events]
    days = [e["calendar_days"] for e in events]
    tdays = [e["trading_days"] for e in events]
    return {
        "title": title,
        "events": events,
        "current": current,
        "avg_pct": mean(pcts) if pcts else None,
        "median_pct": median(pcts) if pcts else None,
        "avg_calendar_days": mean(days) if days else None,
        "median_calendar_days": median(days) if days else None,
        "avg_trading_days": mean(tdays) if tdays else None,
        "median_trading_days": median(tdays) if tdays else None,
        "footnotes": footnotes,
    }


def check_cache(wb_cached, parsed: dict, sheet_name: str, tol: float = 1e-9) -> None:
    ws = wb_cached[sheet_name]
    for ev in parsed["events"]:
        r = ev["_row"]
        assert ev["period"] == ws.cell(r, 2).value, (sheet_name, r, ev["period"], ws.cell(r, 2).value)
        assert ev["calendar_days"] == ws.cell(r, 4).value, (sheet_name, r, ev["calendar_days"], ws.cell(r, 4).value)
        assert abs(ev["change_pts"] - ws.cell(r, 6).value) < 1e-8, (sheet_name, r, ev["change_pts"], ws.cell(r, 6).value)
        assert abs(ev["change_pct"] - ws.cell(r, 7).value) < tol, (sheet_name, r, ev["change_pct"], ws.cell(r, 7).value)
    if parsed["current"]:
        r = parsed["current"]["_row"]
        cur = parsed["current"]
        assert cur["period"] == ws.cell(r, 2).value, (sheet_name, "current period", cur["period"], ws.cell(r, 2).value)
        assert cur["calendar_days"] == ws.cell(r, 4).value
        assert cur["trading_days"] == int(ws.cell(r, 5).value)
        assert abs(cur["change_pts"] - ws.cell(r, 6).value) < 1e-8
        assert abs(cur["change_pct"] - ws.cell(r, 7).value) < tol
    for r in range(3, ws.max_row + 1):
        label = ws.cell(r, 3).value
        if label in AVG_LABELS:
            assert abs(parsed["avg_pct"] - ws.cell(r, 7).value) < tol, (sheet_name, "avg_pct", parsed["avg_pct"], ws.cell(r, 7).value)
            assert abs(parsed["avg_calendar_days"] - ws.cell(r, 4).value) < 1e-9, (sheet_name, "avg_days")
            assert abs(parsed["avg_trading_days"] - ws.cell(r, 5).value) < 1e-9, (sheet_name, "avg_td")
        elif label in MED_LABELS:
            assert abs(parsed["median_pct"] - ws.cell(r, 7).value) < tol, (sheet_name, "med_pct")
            assert abs(parsed["median_calendar_days"] - ws.cell(r, 4).value) < 1e-9, (sheet_name, "med_days")
            assert abs(parsed["median_trading_days"] - ws.cell(r, 5).value) < 1e-9, (sheet_name, "med_td")


def strip_row(d: dict) -> dict:
    out = {k: v for k, v in d.items() if k != "_row"}
    return out


def build() -> dict:
    if not XLSX.exists():
        raise SystemExit(f"找不到 Excel：{XLSX}")
    wb = openpyxl.load_workbook(XLSX, data_only=False)
    wb_cached = openpyxl.load_workbook(XLSX, data_only=True)
    indices = []
    for sheet_name in wb.sheetnames:
        idx_id = SHEET_IDS[sheet_name]
        parsed = parse_sheet(wb[sheet_name])
        check_cache(wb_cached, parsed, sheet_name)
        cur = strip_row(parsed["current"]) if parsed["current"] else None
        indices.append({
            "id": idx_id,
            "sheet": sheet_name,
            "title": parsed["title"],
            "events": [strip_row(e) for e in parsed["events"]],
            "current": cur,
            "avg_pct": parsed["avg_pct"],
            "median_pct": parsed["median_pct"],
            "avg_calendar_days": parsed["avg_calendar_days"],
            "median_calendar_days": parsed["median_calendar_days"],
            "avg_trading_days": parsed["avg_trading_days"],
            "median_trading_days": parsed["median_trading_days"],
            "footnotes": parsed["footnotes"],
        })
    return {
        "source": "使用者提供的 Excel 歷史下跌段表",
        "generated_by": "scripts/build_drawdowns.py",
        "current_is_snapshot": True,
        "indices": indices,
    }


def main() -> None:
    import drawdowns_current as DC  # 延後 import：drawdowns_current 會 import 本檔的 excel_period／calendar_days

    data = build()
    site = DC.load_site()
    if site:
        report = DC.apply_current(data, site)
    else:
        report = {}
        print("找不到 trend/data.json，本波沿用 Excel 快照")
    DC.write_payload(data, OUT_DIR)
    print("wrote", OUT_DIR / "drawdowns.json", "indices", [i["id"] for i in data["indices"]],
          "current_is_snapshot", data["current_is_snapshot"])
    for i in data["indices"]:
        n = len(i["events"])
        sev = sum(1 for e in i["events"] if e["severe"])
        cur = i.get("current")
        cur_s = ""
        if cur and cur.get("recovered"):
            cur_s = f" | 現在 已創新高（截至 {cur.get('asof')}）"
        elif cur:
            cur_s = (f" | 現在 {cur['period']} {cur['change_pct']*100:.2f}% / {cur['calendar_days']}天 / "
                     f"{cur['trading_days']}交易日（截至 {cur.get('asof', 'Excel 快照')}）")
        print(
            f"  {i['id']}: {n} events, {sev} ≥20%, "
            f"avg={i['avg_pct']*100:.2f}% {i['avg_calendar_days']:.1f}天 {i['avg_trading_days']:.1f}交易日, "
            f"med={i['median_pct']*100:.2f}% {i['median_calendar_days']:.1f}天 {i['median_trading_days']:.1f}交易日"
            + cur_s
        )
        if i["id"] in report:
            print(f"      本波：{report[i['id']]}")


if __name__ == "__main__":
    main()
