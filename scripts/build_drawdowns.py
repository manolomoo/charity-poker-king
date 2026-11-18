#!/usr/bin/env python3
"""從 tests/fixtures/historical_drawdowns.xlsx 重算公式，寫出 trend/drawdowns/ 的資料檔。不碰燈號規則。"""
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


def parse_sheet(ws) -> dict:
    title = ws.cell(1, 1).value
    events = []
    r = 3
    while ws.cell(r, 1).value is not None:
        start, end = ws.cell(r, 8).value, ws.cell(r, 9).value
        start_px, end_px = float(ws.cell(r, 10).value), float(ws.cell(r, 11).value)
        change_pts = end_px - start_px
        change_pct = end_px / start_px - 1
        events.append({
            "seq": int(ws.cell(r, 1).value),
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
        })
        r += 1
    # skip blank, read avg/median labels then footnotes in column A
    while r <= ws.max_row and ws.cell(r, 1).value is None and ws.cell(r, 3).value is None:
        r += 1
    while r <= ws.max_row and ws.cell(r, 3).value in ("下跌段平均跌幅(%)", "下跌段跌幅中位數(%)"):
        r += 1
    while r <= ws.max_row and ws.cell(r, 1).value is None:
        r += 1
    footnotes = []
    while r <= ws.max_row:
        val = ws.cell(r, 1).value
        if isinstance(val, str) and val.strip():
            footnotes.append(val)
        r += 1
    pcts = [e["change_pct"] for e in events]
    return {
        "title": title,
        "events": events,
        "avg_pct": mean(pcts) if pcts else None,
        "median_pct": median(pcts) if pcts else None,
        "footnotes": footnotes,
    }


def check_cache(wb_cached, parsed: dict, sheet_name: str, tol: float = 1e-9) -> None:
    ws = wb_cached[sheet_name]
    for i, ev in enumerate(parsed["events"]):
        r = 3 + i
        cached = {
            "period": ws.cell(r, 2).value,
            "calendar_days": ws.cell(r, 4).value,
            "change_pts": ws.cell(r, 6).value,
            "change_pct": ws.cell(r, 7).value,
        }
        assert ev["period"] == cached["period"], (sheet_name, r, ev["period"], cached["period"])
        assert ev["calendar_days"] == cached["calendar_days"], (sheet_name, r, ev["calendar_days"], cached["calendar_days"])
        assert abs(ev["change_pts"] - cached["change_pts"]) < 1e-8, (sheet_name, r, ev["change_pts"], cached["change_pts"])
        assert abs(ev["change_pct"] - cached["change_pct"]) < tol, (sheet_name, r, ev["change_pct"], cached["change_pct"])
    # avg / median rows
    for r in range(3 + len(parsed["events"]), ws.max_row + 1):
        label = ws.cell(r, 3).value
        val = ws.cell(r, 7).value
        if label == "下跌段平均跌幅(%)":
            assert abs(parsed["avg_pct"] - val) < tol, (sheet_name, "avg", parsed["avg_pct"], val)
        elif label == "下跌段跌幅中位數(%)":
            assert abs(parsed["median_pct"] - val) < tol, (sheet_name, "median", parsed["median_pct"], val)


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
        indices.append({
            "id": idx_id,
            "sheet": sheet_name,
            "title": parsed["title"],
            "events": parsed["events"],
            "avg_pct": parsed["avg_pct"],
            "median_pct": parsed["median_pct"],
            "footnotes": parsed["footnotes"],
        })
    return {
        "source": "使用者提供的 Excel 歷史下跌段表",
        "generated_by": "scripts/build_drawdowns.py",
        "indices": indices,
    }


def main() -> None:
    data = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    (OUT_DIR / "drawdowns.json").write_text(raw + "\n", encoding="utf-8")
    (OUT_DIR / "drawdowns.js").write_text(
        "window.DRAWDOWNS_DATA = " + raw + ";\n", encoding="utf-8"
    )
    print("wrote", OUT_DIR / "drawdowns.json", "indices", [i["id"] for i in data["indices"]])
    for i in data["indices"]:
        n = len(i["events"])
        sev = sum(1 for e in i["events"] if e["severe"])
        print(f"  {i['id']}: {n} events, {sev} ≥20%, avg={i['avg_pct']*100:.2f}%, med={i['median_pct']*100:.2f}%")


if __name__ == "__main__":
    main()
