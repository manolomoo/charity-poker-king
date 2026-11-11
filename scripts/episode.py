"""本波觸發紀錄：自 NDX 最近一次 252 日收盤新高之後。"""
import pandas as pd

NOTE = "參考 2022：第一次 4 盞是 1 月，之後一年 NDX 又跌 15%；燈號是分批布局理由，不是一次重押。"

def _skip(row):
    thr = row.get("threshold") or ""
    if "不參與" in thr or "不納入" in thr: return True
    if "參考" in (row.get("target") or ""): return True
    return False

def _as_ts(value):
    text = str(value)[:10]
    try: return pd.Timestamp(text)
    except Exception: return None

def build_episode(rows, ndx_close, names):
    """ndx_close: 已去掉 NaN 的 NDX 收盤。names: [(signal_id, name), ...]。"""
    c = ndx_close.dropna().sort_index()
    roll = c.rolling(252, min_periods=60).max()
    is_high = c >= roll
    hits = is_high.fillna(False).to_numpy().nonzero()[0]
    if len(hits) == 0:
        return {"error": "NDX 沒有 252 日收盤新高", "note": NOTE, "rows": []}
    peak = c.index[hits[-1]]
    peak_px = float(c.loc[peak])
    last_px = float(c.iloc[-1])
    out_rows = []
    for sid, name in names:
        dates = []
        for row in rows:
            if str(row.get("signal")) != str(sid) or _skip(row): continue
            extra = row.get("extra") or {}
            for raw in extra.get("trigger_dates") or []:
                ts = _as_ts(raw)
                if ts is not None and ts > peak:
                    dates.append(ts)
        if not dates:
            out_rows.append({"signal": str(sid), "name": name, "first": None, "latest": None, "ndx_dd": None})
            continue
        first, latest = min(dates), max(dates)
        px = float(c.loc[:first].iloc[-1])
        out_rows.append({
            "signal": str(sid),
            "name": name,
            "first": first.strftime("%Y-%m-%d"),
            "latest": latest.strftime("%Y-%m-%d"),
            "ndx_dd": round((px / peak_px - 1) * 100, 1),
        })
    return {
        "peak_date": peak.strftime("%Y-%m-%d"),
        "peak_close": round(peak_px, 2),
        "ndx_last": c.index[-1].strftime("%Y-%m-%d"),
        "ndx_dd_now": round((last_px / peak_px - 1) * 100, 1),
        "note": NOTE,
        "rows": out_rows,
    }
