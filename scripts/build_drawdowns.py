#!/usr/bin/env python3
"""從 tests/fixtures/historical_drawdowns.xlsx 重算公式，寫出 trend/drawdowns/ 的資料檔。不碰燈號規則。

歷史 events、平均與中位數來自 Excel；歷史 events 另帶 L~Q 欄的反彈段（rebound：下跌低點 → Cobb 主觀確認的
轉多日收盤價），並算反彈平均與中位數（只計歷史列）。「現在本波下跌」沒有轉多日，不推算反彈。
「現在本波下跌」若找得到本站走勢日線（trend/data.json 與
data/cache/trend/），改由 scripts/drawdowns_current.py 以同一口徑自動計算並覆寫（current_is_snapshot=false）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
XLSX = ROOT / "tests" / "fixtures" / "historical_drawdowns.xlsx"
OUT_DIR = ROOT / "trend" / "drawdowns"
SHEET_IDS = {"NDX": "NDX", "SPX": "SPX", "SOX": "SOX", "台灣加權": "TWII"}
AVG_LABELS = ("下跌段平均", "下跌段平均跌幅(%)")
MED_LABELS = ("下跌段中位數", "下跌段跌幅中位數(%)")
REB_AVG_LABEL = "反彈平均"
REB_MED_LABEL = "反彈中位數"
REBOUND_FOOTNOTE_PREFIXES = ("反彈區段", "轉多日收盤價")
# L~Q 欄：轉多日、轉多日收盤價、反彈歷時(日曆天)、反彈交易日、反彈幅度(點)、反彈幅度(%)
COL_TURN_DATE, COL_TURN_PX, COL_REB_DAYS, COL_REB_TD, COL_REB_PTS, COL_REB_PCT = 12, 13, 14, 15, 16, 17


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
    # 只看後 6 碼（RRGGBB），免得 FFFF0000（紅）之類被誤判
    return fill_rgb(cell).upper()[-6:] == "FFFF00"


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


def parse_rebound(ws, r: int, low_date, low_px: float, high_px: float) -> dict | None:
    """L~Q：起點＝該次下跌的低點（K 欄，盤中最低價），終點＝轉多日的日K收盤價（M 欄）。
    反彈交易日（O 欄）是 K 棒根數差，直接讀；其餘由公式重算。
    另算（點數口徑，同一段高低點）：
      retrace_pct     ＝(轉多收盤−低點)／(高點−低點)，轉多確認時收回了跌幅的幾成；
      gap_to_high_pct ＝轉多收盤／高點−1，轉多確認時距前高還差多少（負值＝還在前高之下）。
    不用「反彈%÷|跌幅%|」：兩個 % 分母不同（低點 vs 高點），深跌時會誇大。"""
    turn, turn_px, td = ws.cell(r, COL_TURN_DATE).value, ws.cell(r, COL_TURN_PX).value, ws.cell(r, COL_REB_TD).value
    if turn is None and turn_px is None and td is None:
        return None
    if turn is None or turn_px is None or td is None:
        raise ValueError(f"{ws.title} 第 {r} 列反彈資料不完整（L/M/O 欄）")
    turn_px = float(turn_px)
    return {
        "turn_date": as_date(turn),
        "turn_px": turn_px,
        "calendar_days": calendar_days(low_date, turn),
        "trading_days": int(td),
        "change_pts": turn_px - low_px,
        "change_pct": turn_px / low_px - 1,
        "retrace_pct": (turn_px - low_px) / (high_px - low_px),
        "gap_to_high_pct": turn_px / high_px - 1,
    }


def sample_stdev(xs: list) -> float | None:
    """樣本標準差（n−1，statistics.stdev）；n<2 時為 None。"""
    return stdev(xs) if len(xs) >= 2 else None


def is_rebound_footnote(text: str) -> bool:
    return isinstance(text, str) and text.startswith(REBOUND_FOOTNOTE_PREFIXES)


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
            if parse_rebound(ws, r, ws.cell(r, 9).value, cur["end_px"], cur["start_px"]) is not None:
                # 轉多日是 Cobb 的主觀判斷；Excel 沒給就不推算，給了也要先人工確認再接進來
                raise ValueError(f"{ws.title} 「現在本波下跌」列出現反彈資料，請先確認再改程式")
            current = cur
            continue
        if isinstance(a, int) or (isinstance(a, float) and a == int(a)):
            ev = parse_leg(ws, r, int(a))
            reb = parse_rebound(ws, r, ws.cell(r, 9).value, ev["end_px"], ev["start_px"])
            if reb is None:
                raise ValueError(f"{ws.title} 第 {r} 列（事件 {int(a)}）缺反彈資料")
            ev["rebound"] = reb
            events.append(ev)
            continue
        if isinstance(a, str) and a.strip() and a not in ("事件序列",):
            footnotes.append(a)
    pcts = [e["change_pct"] for e in events]
    days = [e["calendar_days"] for e in events]
    tdays = [e["trading_days"] for e in events]
    r_pcts = [e["rebound"]["change_pct"] for e in events]
    r_days = [e["rebound"]["calendar_days"] for e in events]
    r_tdays = [e["rebound"]["trading_days"] for e in events]
    r_retr = [e["rebound"]["retrace_pct"] for e in events]
    r_gap = [e["rebound"]["gap_to_high_pct"] for e in events]
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
        "rebound_avg_pct": mean(r_pcts) if r_pcts else None,
        "rebound_median_pct": median(r_pcts) if r_pcts else None,
        "rebound_avg_calendar_days": mean(r_days) if r_days else None,
        "rebound_median_calendar_days": median(r_days) if r_days else None,
        "rebound_avg_trading_days": mean(r_tdays) if r_tdays else None,
        "rebound_median_trading_days": median(r_tdays) if r_tdays else None,
        "rebound_std_calendar_days": sample_stdev(r_days),
        "rebound_count": len(events),
        "retrace_avg_pct": mean(r_retr) if r_retr else None,
        "retrace_std_pct": sample_stdev(r_retr),
        "retrace_median_pct": median(r_retr) if r_retr else None,
        "gap_to_high_avg_pct": mean(r_gap) if r_gap else None,
        "gap_to_high_std_pct": sample_stdev(r_gap),
        "gap_to_high_median_pct": median(r_gap) if r_gap else None,
        "footnotes": footnotes,
        "rebound_footnotes": [f for f in footnotes if is_rebound_footnote(f)],
    }


def check_cache(wb_cached, parsed: dict, sheet_name: str, tol: float = 1e-9) -> None:
    ws = wb_cached[sheet_name]
    for ev in parsed["events"]:
        r = ev["_row"]
        assert ev["period"] == ws.cell(r, 2).value, (sheet_name, r, ev["period"], ws.cell(r, 2).value)
        assert ev["calendar_days"] == ws.cell(r, 4).value, (sheet_name, r, ev["calendar_days"], ws.cell(r, 4).value)
        assert abs(ev["change_pts"] - ws.cell(r, 6).value) < 1e-8, (sheet_name, r, ev["change_pts"], ws.cell(r, 6).value)
        assert abs(ev["change_pct"] - ws.cell(r, 7).value) < tol, (sheet_name, r, ev["change_pct"], ws.cell(r, 7).value)
        reb = ev["rebound"]
        assert reb["calendar_days"] == ws.cell(r, COL_REB_DAYS).value, (sheet_name, r, "reb days", reb["calendar_days"], ws.cell(r, COL_REB_DAYS).value)
        assert reb["trading_days"] == ws.cell(r, COL_REB_TD).value, (sheet_name, r, "reb td")
        assert abs(reb["change_pts"] - ws.cell(r, COL_REB_PTS).value) < 1e-8, (sheet_name, r, "reb pts", reb["change_pts"], ws.cell(r, COL_REB_PTS).value)
        assert abs(reb["change_pct"] - ws.cell(r, COL_REB_PCT).value) < tol, (sheet_name, r, "reb pct", reb["change_pct"], ws.cell(r, COL_REB_PCT).value)
    if parsed["current"]:
        r = parsed["current"]["_row"]
        cur = parsed["current"]
        assert cur["period"] == ws.cell(r, 2).value, (sheet_name, "current period", cur["period"], ws.cell(r, 2).value)
        assert cur["calendar_days"] == ws.cell(r, 4).value
        assert cur["trading_days"] == int(ws.cell(r, 5).value)
        assert abs(cur["change_pts"] - ws.cell(r, 6).value) < 1e-8
        assert abs(cur["change_pct"] - ws.cell(r, 7).value) < tol
        for c in range(COL_TURN_DATE, COL_REB_PCT + 1):
            assert ws.cell(r, c).value is None, (sheet_name, "current has rebound cache", c)
    seen_reb = set()
    for r in range(3, ws.max_row + 1):
        reb_label = ws.cell(r, COL_TURN_PX).value
        if reb_label in (REB_AVG_LABEL, REB_MED_LABEL):
            key = "avg" if reb_label == REB_AVG_LABEL else "median"
            seen_reb.add(key)
            assert abs(parsed[f"rebound_{key}_calendar_days"] - ws.cell(r, COL_REB_DAYS).value) < 1e-9, (sheet_name, reb_label, "days")
            assert abs(parsed[f"rebound_{key}_trading_days"] - ws.cell(r, COL_REB_TD).value) < 1e-9, (sheet_name, reb_label, "td")
            assert abs(parsed[f"rebound_{key}_pct"] - ws.cell(r, COL_REB_PCT).value) < tol, (sheet_name, reb_label, "pct")
        label = ws.cell(r, 3).value
        if label in AVG_LABELS:
            assert abs(parsed["avg_pct"] - ws.cell(r, 7).value) < tol, (sheet_name, "avg_pct", parsed["avg_pct"], ws.cell(r, 7).value)
            assert abs(parsed["avg_calendar_days"] - ws.cell(r, 4).value) < 1e-9, (sheet_name, "avg_days")
            assert abs(parsed["avg_trading_days"] - ws.cell(r, 5).value) < 1e-9, (sheet_name, "avg_td")
        elif label in MED_LABELS:
            assert abs(parsed["median_pct"] - ws.cell(r, 7).value) < tol, (sheet_name, "med_pct")
            assert abs(parsed["median_calendar_days"] - ws.cell(r, 4).value) < 1e-9, (sheet_name, "med_days")
            assert abs(parsed["median_trading_days"] - ws.cell(r, 5).value) < 1e-9, (sheet_name, "med_td")
    assert seen_reb == {"avg", "median"}, (sheet_name, "反彈平均／中位數列", seen_reb)


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
            "rebound_avg_pct": parsed["rebound_avg_pct"],
            "rebound_median_pct": parsed["rebound_median_pct"],
            "rebound_avg_calendar_days": parsed["rebound_avg_calendar_days"],
            "rebound_median_calendar_days": parsed["rebound_median_calendar_days"],
            "rebound_avg_trading_days": parsed["rebound_avg_trading_days"],
            "rebound_median_trading_days": parsed["rebound_median_trading_days"],
            "rebound_std_calendar_days": parsed["rebound_std_calendar_days"],
            "rebound_count": parsed["rebound_count"],
            "retrace_avg_pct": parsed["retrace_avg_pct"],
            "retrace_std_pct": parsed["retrace_std_pct"],
            "retrace_median_pct": parsed["retrace_median_pct"],
            "gap_to_high_avg_pct": parsed["gap_to_high_avg_pct"],
            "gap_to_high_std_pct": parsed["gap_to_high_std_pct"],
            "gap_to_high_median_pct": parsed["gap_to_high_median_pct"],
            "footnotes": parsed["footnotes"],
            "rebound_footnotes": parsed["rebound_footnotes"],
        })
    return {
        "source": "使用者提供的 Excel 歷史下跌段表（2026-11-20 改版，含反彈段 L~Q 欄）",
        "rebound_method": (
            "反彈起點＝該次下跌的低點（盤中最低價），終點＝轉多日的日K收盤價；轉多日是 Cobb 在 TradingView 框選的"
            "主觀判斷（方法來源：COBB我大哥），不自動推算。反彈交易日＝K 棒根數差；反彈平均與中位數只計歷史下跌段。"
            "回血比例（retrace_pct）＝(轉多收盤−低點)／(高點−低點)，點數口徑；距前高（gap_to_high_pct）＝轉多收盤／高點−1。"
            "標準差一律是樣本標準差（n−1）；本波沒有轉多確認，不填回血數字。"
        ),
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
        print(
            f"      反彈 avg={i['rebound_avg_pct']*100:.2f}% {i['rebound_avg_calendar_days']:.1f}天 "
            f"{i['rebound_avg_trading_days']:.1f}交易日, med={i['rebound_median_pct']*100:.2f}% "
            f"{i['rebound_median_calendar_days']:.1f}天 {i['rebound_median_trading_days']:.1f}交易日"
        )
        std = lambda x, k=1: "—" if x is None else f"{x:.{k}f}"
        print(
            f"      回血 avg={i['retrace_avg_pct']*100:.1f}% ± {std(i['retrace_std_pct'] and i['retrace_std_pct']*100)}% "
            f"med={i['retrace_median_pct']*100:.1f}% | 天數 {i['rebound_avg_calendar_days']:.1f} ± "
            f"{std(i['rebound_std_calendar_days'])} med={i['rebound_median_calendar_days']:g} | "
            f"距前高 med={i['gap_to_high_median_pct']*100:.1f}% (n={i['rebound_count']})"
        )
        if i["id"] in report:
            print(f"      本波：{report[i['id']]}")


if __name__ == "__main__":
    main()
