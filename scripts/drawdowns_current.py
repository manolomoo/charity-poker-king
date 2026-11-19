#!/usr/bin/env python3
"""「現在本波下跌」自動計算（/trend/drawdowns/ 的黃底列）。不碰燈號規則。

口徑與 Excel 歷史列相同：
- 取盤中最高價與最低價（不是收盤價），漲跌幅 = end_px / start_px - 1。
- 起點：上一筆歷史下跌段結束日（events 最後一列 end_date）之後，盤中 high 最大的那根日K。
- 終點：從起點當根起算，盤中 low 最小的那根日K（結束日是低點日，不是今天）。
- 歷經交易日 = 低點 K 棒索引 − 高點 K 棒索引（TradingView 粉色量測框的根數差）。
- 日曆天與期間格式沿用 build_drawdowns.calendar_days / excel_period。
- 若高點之後還沒走出比高點當根更低的低點（例如高點就是最後一根），視為已創新高、沒有進行中的下跌段
  （recovered=True），不拿當天振幅充當本波下跌。
- 平均與中位數只算歷史 events，本波不計入（這裡不動那些欄位）。

日線來源：data/cache/trend/{ID}_daily_5y.csv → 2y → 1y（取第一個存在的），
再用 trend/data.json 的 charts.indices.{ID}.chart_daily_1y 覆蓋重疊日期並補上較新的交易日。
資料一律截在 data.json 該指數最後一根日K（網站實際顯示的資料），快取比 data.json 新的部分不用，
免得更新失敗、沿用舊 data.json 時用到沒通過檢查的新行情。

用法（repo 根目錄）：python3 scripts/drawdowns_current.py   # 只刷新本波，寫回 drawdowns.json／.js
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_drawdowns import calendar_days, excel_period  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "trend"
DATA_JSON = ROOT / "trend" / "data.json"
OUT_DIR = ROOT / "trend" / "drawdowns"
CURRENT_NAME = "現在本波下跌"
FOOTNOTE_PREFIX = "「現在本波下跌」"
CACHE_ORDER = ("daily_5y", "daily_2y", "daily_1y")
SOURCE_LABELS = {"TWSE": "證交所", "Yahoo": "Yahoo"}


def _d(s) -> date:
    return s if isinstance(s, date) else date.fromisoformat(str(s)[:10])


def ymd(s) -> str:
    d = _d(s)
    return f"{d.year}/{d.month}/{d.day}"


# ---------------- 純計算 ----------------
def compute_current(bars, after_date) -> dict | None:
    """bars：依日期排序的 [(date_iso, high, low), ...]（連續交易日）。after_date：上一段歷史下跌的結束日。

    回傳：
      None                       after_date 之後沒有任何日K
      {"recovered": True, ...}   高點之後沒有更低的低點（已創新高，沒有進行中的下跌段）
      {"recovered": False, ...}  本波下跌（欄位同歷史列）
    """
    after = _d(after_date).isoformat()
    seg = [b for b in bars if str(b[0])[:10] > after]
    if not seg:
        return None
    asof = str(seg[-1][0])[:10]
    pi = 0
    for i, b in enumerate(seg):  # 同價取最早那根
        if float(b[1]) > float(seg[pi][1]):
            pi = i
    ti = pi
    for i in range(pi, len(seg)):
        if float(seg[i][2]) < float(seg[ti][2]):
            ti = i
    start, start_px = str(seg[pi][0])[:10], float(seg[pi][1])
    if ti == pi:
        return {
            "seq": "現在",
            "name": CURRENT_NAME,
            "recovered": True,
            "asof": asof,
            "high_date": start,
            "high_px": start_px,
        }
    end, end_px = str(seg[ti][0])[:10], float(seg[ti][2])
    sd, ed = _d(start), _d(end)
    return {
        "seq": "現在",
        "period": excel_period(sd, ed),
        "name": CURRENT_NAME,
        "calendar_days": calendar_days(sd, ed),
        "trading_days": ti - pi,
        "change_pts": end_px - start_px,
        "change_pct": end_px / start_px - 1,
        "start_date": start,
        "end_date": end,
        "start_px": start_px,
        "end_px": end_px,
        "recovered": False,
        "asof": asof,
    }


def merge_bars(cache_bars, recent_bars, cap_date=None):
    """合併兩組 (date, high, low)：重疊日期以 recent_bars 為準；cap_date 之後的丟掉。"""
    by = {str(b[0])[:10]: (str(b[0])[:10], float(b[1]), float(b[2])) for b in cache_bars or []}
    for b in recent_bars or []:
        by[str(b[0])[:10]] = (str(b[0])[:10], float(b[1]), float(b[2]))
    out = [by[k] for k in sorted(by)]
    if cap_date:
        cap = str(cap_date)[:10]
        out = [b for b in out if b[0] <= cap]
    return out


def footnote_text(cur: dict | None, after_date, source_label: str) -> str:
    low = ymd(after_date)
    base = f"{FOOTNOTE_PREFIX}（黃底列）：從上一段歷史下跌的低點（{low}）之後的盤中最高點，算到該高點以來的盤中最低點；結束日是低點日。"
    tail = f"由本站日線自動計算（來源：{source_label}），隨每日資料自動更新。此列不計入上方的平均與中位數。"
    if cur is None:
        return base + "目前沒有可用的日線資料。" + tail
    asof = ymd(cur["asof"])
    if cur.get("recovered"):
        return base + f"資料截至 {asof}：已創新高，目前沒有進行中的下跌段。" + tail
    return base + f"資料截至 {asof}；若之後再創新低，數字會變動。" + tail


# ---------------- 讀資料 ----------------
def read_cache_bars(cache_dir: Path, idx_id: str):
    for key in CACHE_ORDER:
        p = Path(cache_dir) / f"{idx_id}_{key}.csv"
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            rows = [(r["date"], round(float(r["high"]), 2), round(float(r["low"]), 2)) for r in csv.DictReader(f)
                    if r.get("high") not in (None, "") and r.get("low") not in (None, "")]
        if rows:
            return rows, key
    return [], None


def site_bars(site: dict, idx_id: str):
    c = (((site or {}).get("charts") or {}).get("indices") or {}).get(idx_id) or {}
    d = c.get("chart_daily_1y") or {}
    dates, highs, lows = d.get("dates") or [], d.get("high") or [], d.get("low") or []
    return [(dt, h, lo) for dt, h, lo in zip(dates, highs, lows) if h is not None and lo is not None]


def source_info(site: dict, idx_id: str) -> tuple[str, str]:
    meta = ((site or {}).get("analysis") or {}).get("meta") or {}
    ticker = (meta.get("tickers") or {}).get(idx_id)
    if idx_id == "TWII":
        src = ((meta.get("tw_source") or {}).get("TWII") or {}).get("price_source") or "Yahoo"
    else:
        src = "Yahoo"
    label = SOURCE_LABELS.get(src, src)
    if src == "Yahoo" and ticker:
        label = f"Yahoo {ticker} "
    return src, label + "日線盤中高低點"


def cache_source(cache_dir: Path, idx_id: str) -> str | None:
    """台指快取的來源（TWII_source.json）；美股快取一律 Yahoo。"""
    if idx_id != "TWII":
        return "Yahoo"
    p = Path(cache_dir) / "TWII_source.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("price_source")
    except Exception:
        return None


def bars_for(idx_id: str, site: dict, cache_dir: Path = CACHE):
    recent = site_bars(site, idx_id)
    src, _ = source_info(site, idx_id)
    cached, key = read_cache_bars(cache_dir, idx_id)
    if cached and cache_source(cache_dir, idx_id) != src:
        # 來源不同（例如台指快取還是 Yahoo、data.json 已是證交所）：不混用，只用 data.json
        cached, key = [], None
    cap = recent[-1][0] if recent else None
    return merge_bars(cached, recent, cap), key


# ---------------- 套到 drawdowns 資料 ----------------
def apply_current(payload: dict, site: dict, cache_dir: Path = CACHE) -> dict:
    """就地覆寫各指數 current 與「現在本波下跌」註腳；回傳 {ID: 說明}。"""
    report = {}
    all_auto = True
    for ix in payload.get("indices", []):
        idx_id = ix["id"]
        events = ix.get("events") or []
        if not events:
            all_auto = False
            report[idx_id] = "沒有歷史 events，沿用原本的本波"
            continue
        after = events[-1]["end_date"]
        bars, key = bars_for(idx_id, site, cache_dir)
        cur = compute_current(bars, after) if bars else None
        if cur is None:
            all_auto = False
            report[idx_id] = "找不到日線，沿用原本的本波"
            continue
        src, label = source_info(site, idx_id)
        cur["source"] = src
        cur["source_label"] = label
        cur["after_date"] = after
        cur["history_from"] = bars[0][0]
        cur["history_covers_after_date"] = bars[0][0] <= after
        cur["cache_file"] = f"{idx_id}_{key}.csv" if key else None
        ix["current"] = cur
        note = footnote_text(cur, after, label)
        fns = [f for f in ix.get("footnotes", []) if not f.startswith(FOOTNOTE_PREFIX)]
        fns.append(note)
        ix["footnotes"] = fns
        if cur["recovered"]:
            report[idx_id] = f"已創新高（高點 {cur['high_date']}），資料截至 {cur['asof']}，{label}"
        else:
            report[idx_id] = (f"{cur['period']} {cur['change_pct']*100:.2f}% / {cur['calendar_days']}天 / "
                              f"{cur['trading_days']}交易日，截至 {cur['asof']}，{label}")
    payload["current_is_snapshot"] = not all_auto
    payload["current_method"] = (
        "本波由本站日線自動計算：上一段歷史下跌結束日之後的盤中最高點，到該高點以來的盤中最低點；"
        "交易日＝K 棒索引差；台指證交所、美股 Yahoo；不計入平均與中位數。"
    )
    return report


def load_site(path: Path = DATA_JSON) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def write_payload(payload: dict, out_dir: Path = OUT_DIR) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    for name, text in (("drawdowns.json", raw + "\n"), ("drawdowns.js", "window.DRAWDOWNS_DATA = " + raw + ";\n")):
        p = out_dir / name
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)


def refresh_files(root: Path = ROOT) -> dict:
    """讀現有 drawdowns.json（歷史列不動），用 data.json + 快取刷新本波後寫回 .json／.js。"""
    root = Path(root)
    out_dir = root / "trend" / "drawdowns"
    payload = json.loads((out_dir / "drawdowns.json").read_text(encoding="utf-8"))
    site = load_site(root / "trend" / "data.json")
    if not site:
        raise RuntimeError("讀不到 trend/data.json")
    report = apply_current(payload, site, root / "data" / "cache" / "trend")
    write_payload(payload, out_dir)
    return report


def main() -> None:
    for k, v in refresh_files().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
