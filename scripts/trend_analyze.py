#!/usr/bin/env python3
"""SOX／NDX／SPX 走勢觀察的計算（客觀、可重現；不產生任何手填數字）。

這份是 poker-king-trend/analyze.py 的移植：擺動點、均線、趨勢線、3% 濾網、道氏週線
結構、形態檢查、三指數印證的算法與參數都原樣保留，只改了三件事：
  1. 行情檔位置改成呼叫端傳入的資料夾（data/cache/trend/，不進版控）。
  2. 抓資料時間、價格截至日、時差說明都由資料算出，不再寫死。
  3. 燈號摘要改讀本 repo 的 data/latest.json 與 rules.json。
另外補上 home_pattern（首頁型態挑選）與下降直角三角形（v2／COBB 主高點讀法）。

與 11 項燈號規則無關，不會改動燈號或門檻。由 scripts/update_trend.py 呼叫。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import market_stage as MS  # noqa: E402

IDS = ["SOX", "NDX", "SPX"]
SOURCE = "Yahoo Finance 圖表 API（query1.finance.yahoo.com/v8/finance/chart）"
SOURCE_TICKERS = {"SOX": "^SOX", "NDX": "^NDX", "SPX": "^GSPC"}
NAMES = {"SOX": "費城半導體指數（SOX, ^SOX）", "NDX": "那斯達克 100（NDX, ^NDX）", "SPX": "標普 500（SPX, ^GSPC）"}
TZ = timezone(timedelta(hours=8))
NY = ZoneInfo("America/New_York")
WEEKDAY_ZH = "一二三四五六日"


def r2(x, n=2):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), n)


def r1(x):
    return r2(x, 1)


def pct(a, b):
    if b == 0 or b is None or a is None:
        return None
    return (float(a) / float(b) - 1.0) * 100.0


def load_csv(path):
    df = pd.read_csv(path)
    for c in ["open", "high", "low", "close", "adjclose", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["close", "high", "low", "open"]).reset_index(drop=True)
    return df


def drop_duplicate_last_week(weekly: pd.DataFrame) -> pd.DataFrame:
    """Yahoo sometimes appends a Friday-dated bar that duplicates the Monday-dated current week."""
    if len(weekly) < 2:
        return weekly
    last = weekly.iloc[-1]
    prev = weekly.iloc[-2]
    # If last is mid-week date and close matches previous week's close (or last is just one daily bar)
    if last["date"].weekday() != 0:  # not Monday
        # drop if it looks like a single-day or duplicate of prev week
        return weekly.iloc[:-1].reset_index(drop=True)
    return weekly


def sma(series, n):
    return series.rolling(n, min_periods=n).mean()


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def swings(df, left=5, right=5, use="high_low"):
    """Local extrema. Swing high: high is max in [i-left, i+right]. Same for low."""
    highs, lows = [], []
    h, l, c, d = df["high"].values, df["low"].values, df["close"].values, df["date"]
    n = len(df)
    for i in range(left, n - right):
        window_h = h[i - left : i + right + 1]
        window_l = l[i - left : i + right + 1]
        if h[i] >= window_h.max() and (window_h == h[i]).sum() == 1 or (
            h[i] >= window_h.max() and i == i - left + int(np.argmax(window_h))
        ):
            # first occurrence if ties
            if np.argmax(window_h) == left:
                highs.append(
                    {
                        "i": int(i),
                        "date": str(d.iloc[i].date()),
                        "price": float(h[i]),
                        "close": float(c[i]),
                        "kind": "high",
                    }
                )
        if l[i] <= window_l.min() and np.argmin(window_l) == left:
            lows.append(
                {
                    "i": int(i),
                    "date": str(d.iloc[i].date()),
                    "price": float(l[i]),
                    "close": float(c[i]),
                    "kind": "low",
                }
            )
    return highs, lows


def swings_close(df, left=3, right=3):
    """Swing highs/lows on close (Dow uses closes)."""
    highs, lows = [], []
    c, d = df["close"].values, df["date"]
    n = len(df)
    for i in range(left, n - right):
        window = c[i - left : i + right + 1]
        if np.argmax(window) == left:
            highs.append({"i": int(i), "date": str(d.iloc[i].date()), "price": float(c[i]), "kind": "high"})
        if np.argmin(window) == left:
            lows.append({"i": int(i), "date": str(d.iloc[i].date()), "price": float(c[i]), "kind": "low"})
    return highs, lows


def last_n_swings(items, n=8):
    return items[-n:]


def trendline_from_two(p1, p2, x_now):
    x1, y1 = p1["i"], p1["price"]
    x2, y2 = p2["i"], p2["price"]
    if x2 == x1:
        return None
    slope = (y2 - y1) / (x2 - x1)
    y_now = y1 + slope * (x_now - x1)
    return {
        "p1": {k: p1[k] for k in ("date", "price", "i") if k in p1},
        "p2": {k: p2[k] for k in ("date", "price", "i") if k in p2},
        "slope_per_bar": slope,
        "value_at_last": y_now,
        "rising": slope > 0,
    }


def find_gaps(daily, lookback=252):
    d = daily.tail(lookback).reset_index(drop=True)
    gaps = []
    for i in range(1, len(d)):
        prev, cur = d.iloc[i - 1], d.iloc[i]
        # up gap
        if cur["low"] > prev["high"]:
            gap_low, gap_high = float(prev["high"]), float(cur["low"])
            kind = "up"
        elif cur["high"] < prev["low"]:
            gap_low, gap_high = float(cur["high"]), float(prev["low"])
            kind = "down"
        else:
            continue
        size_pct = (gap_high - gap_low) / ((gap_high + gap_low) / 2) * 100
        # filled? any subsequent bar traded into the gap
        filled = False
        filled_date = None
        rest = d.iloc[i:]
        if kind == "up":
            hit = rest[rest["low"] <= gap_low]
        else:
            hit = rest[rest["high"] >= gap_high]
        if len(hit):
            filled = True
            filled_date = str(hit.iloc[0]["date"].date())
        gaps.append(
            {
                "date": str(cur["date"].date()),
                "kind": kind,
                "gap_low": r2(gap_low),
                "gap_high": r2(gap_high),
                "size_pct": r2(size_pct, 2),
                "filled": filled,
                "filled_date": filled_date,
                "open": r2(cur["open"]),
                "close": r2(cur["close"]),
            }
        )
    return gaps


def cluster_levels(levels, tol=0.015):
    """Cluster prices within tol (fraction). levels: list of {price, date, kind, strength?}"""
    if not levels:
        return []
    items = sorted(levels, key=lambda x: x["price"])
    clusters = []
    cur = [items[0]]
    for x in items[1:]:
        anchor = np.mean([c["price"] for c in cur])
        if abs(x["price"] - anchor) / anchor <= tol:
            cur.append(x)
        else:
            clusters.append(cur)
            cur = [x]
    clusters.append(cur)
    out = []
    for cl in clusters:
        prices = [c["price"] for c in cl]
        out.append(
            {
                "price": r2(float(np.mean(prices))),
                "lo": r2(min(prices)),
                "hi": r2(max(prices)),
                "touches": len(cl),
                "dates": [c["date"] for c in cl][-6:],
                "kinds": sorted({c["kind"] for c in cl}),  # 排序只為了輸出穩定（原本 set 順序每次不同）
            }
        )
    out.sort(key=lambda x: -x["touches"])
    return out


def ma_cross_events(df, fast=50, slow=200):
    a = sma(df["close"], fast)
    b = sma(df["close"], slow)
    events = []
    sig = (a - b).dropna()
    idx = sig.index
    for j in range(1, len(idx)):
        i0, i1 = idx[j - 1], idx[j]
        if sig.loc[i0] <= 0 < sig.loc[i1]:
            events.append(
                {
                    "date": str(df.loc[i1, "date"].date()),
                    "kind": "golden_cross",
                    "fast": fast,
                    "slow": slow,
                    "price": r2(df.loc[i1, "close"]),
                }
            )
        elif sig.loc[i0] >= 0 > sig.loc[i1]:
            events.append(
                {
                    "date": str(df.loc[i1, "date"].date()),
                    "kind": "death_cross",
                    "fast": fast,
                    "slow": slow,
                    "price": r2(df.loc[i1, "close"]),
                }
            )
    return events


def retrace_fraction(peak, trough, now):
    """If decline from peak to trough, how much of that decline has been retraced."""
    move = peak - trough
    if move == 0:
        return None
    return (now - trough) / move



# 下降直角三角形（v2，依 COBB 主高點讀法；2026-11-14）
# 主波段用已確認的 10／10 擺動高／低；中間高點只要不向上刺破降勢線超過 1% 就不作廢。
# 水平支撐 S＝H1 之後、H2 附近之前最低的 10／10 主低點，且之後至少再有一次測試（日低點落入 S 的 2% 內）。
# 確認：單一交易日收盤跌破 S 達 3%（原書）。測算目標：S−(H1−S)。
# 狀態：形成中／可能跌破（未確認）／已確認跌破／已確認跌破（反抽中）／失效。
# 20 日均連續未站回只當附註，不參與命名或確認。
TRI_STATUS_ACTIVE = ("形成中", "可能跌破（未確認）", "已確認跌破", "已確認跌破（反抽中）")


def _line_at(i1, p1, i2, p2, i):
    return p1 + (p2 - p1) * (i - i1) / (i2 - i1)


def detect_descending_right_triangle(
    daily: pd.DataFrame,
    highs10: list,
    lows10: list,
    *,
    flat_tol: float = 0.02,
    lower_high_min: float = 0.01,
    pierce_tol: float = 0.01,
    break_pct: float = 0.03,
    min_span: int = 20,
    min_height: float = 0.08,
    min_touch_gap: int = 8,
):
    """在日線上找目前仍適用的下降直角三角形；沒有則回 None。"""
    if daily is None or len(daily) < 80 or not highs10:
        return None
    end = len(daily) - 1
    close = float(daily["close"].iloc[end])
    hits = []
    for a, h1 in enumerate(highs10):
        i1, p1 = int(h1["i"]), float(h1["price"])
        for h2 in highs10[a + 1 :]:
            i2, p2 = int(h2["i"]), float(h2["price"])
            if p2 >= p1 * (1 - lower_high_min) or i2 - i1 < min_span:
                continue
            pierced = False
            inter = []
            post = []
            for hk in highs10:
                ik = int(hk["i"])
                if ik <= i1:
                    continue
                lv = _line_at(i1, p1, i2, p2, ik)
                pk = float(hk["price"])
                if pk > lv * (1 + pierce_tol):
                    pierced = True
                    break
                rec = {"date": hk["date"], "high": r2(pk), "line": r2(lv)}
                if ik < i2:
                    inter.append(rec)
                elif ik > i2:
                    post.append(rec)
            if pierced:
                continue
            invalid_close = False
            for i in range(i2 + 1, end + 1):
                lv = _line_at(i1, p1, i2, p2, i)
                if float(daily["close"].iloc[i]) > lv * (1 + break_pct):
                    invalid_close = True
                    break
            if invalid_close:
                continue
            cands = [lw for lw in lows10 if i1 + 3 < int(lw["i"]) <= i2 + 5]
            if not cands:
                continue
            low1 = min(cands, key=lambda x: float(x["price"]))
            iL, pL = int(low1["i"]), float(low1["price"])
            S = pL
            if (p1 - S) / S < min_height or p2 <= S * 1.05:
                continue
            first_break_i = None
            first_unconf_i = None
            for i in range(iL + 1, end + 1):
                c = float(daily["close"].iloc[i])
                if first_unconf_i is None and c < S:
                    first_unconf_i = i
                if c <= S * (1 - break_pct):
                    first_break_i = i
                    break
            scan_to = first_break_i if first_break_i is not None else end
            tests = []
            for i in range(iL + min_touch_gap, scan_to + 1):
                lo = float(daily["low"].iloc[i])
                if abs(lo - S) / S <= flat_tol:
                    tests.append({
                        "date": str(daily["date"].iloc[i].date()),
                        "low": r2(lo),
                        "pct": r2((lo / S - 1) * 100, 2),
                    })
            if not tests:
                continue
            slope = (p2 - p1) / (i2 - i1)
            if slope >= 0:
                continue
            i_apex = i1 + (S - p1) / slope
            if i_apex <= i2:
                continue
            ref_i = first_break_i if first_break_i is not None else end
            frac = (ref_i - i1) / (i_apex - i1)
            recaptured = False
            recapture_date = None
            if first_break_i is not None:
                for i in range(first_break_i + 1, end + 1):
                    if float(daily["close"].iloc[i]) >= S * (1 + break_pct):
                        recaptured = True
                        recapture_date = str(daily["date"].iloc[i].date())
                        break
            if recaptured:
                status = "失效"
            elif first_break_i is not None:
                status = "已確認跌破（反抽中）" if close >= S else "已確認跌破"
            elif close < S:
                status = "可能跌破（未確認）"
            else:
                status = "形成中"
            target = 2 * S - p1
            vs = (close / S - 1.0) * 100
            hits.append({
                "status": status,
                "active": status in TRI_STATUS_ACTIVE,
                "h1": {"date": h1["date"], "price": r2(p1)},
                "h2": {"date": h2["date"], "price": r2(p2)},
                "support": r2(S),
                "support_first": {"date": low1["date"], "low": r2(pL)},
                "support_tests": tests[:6],
                "n_tests": len(tests),
                "close": r2(close),
                "close_vs_support_pct": r2(vs, 2),
                "confirmed": first_break_i is not None and not recaptured,
                "first_unconf_date": (str(daily["date"].iloc[first_unconf_i].date()) if first_unconf_i is not None else None),
                "first_3pct_date": (str(daily["date"].iloc[first_break_i].date()) if first_break_i is not None else None),
                "first_3pct_close": (r2(float(daily["close"].iloc[first_break_i])) if first_break_i is not None else None),
                "recapture_date": recapture_date,
                "target": r2(target),
                "height_pct": r2((p1 - S) / S * 100, 2),
                "apex_frac_at_ref": r2(frac, 2),
                "intermediate_highs": inter,
                "later_highs_below_line": post,
                "rule": "v2-cobb",
            })
    if not hits:
        return None
    # 同一組 H1+S 取最晚的 H2（較完整的降勢線）；再取仍有效、高度較大者
    best = {}
    for h in hits:
        k = (h["h1"]["date"], round(h["support"]))
        if k not in best or h["h2"]["date"] > best[k]["h2"]["date"]:
            best[k] = h
    cands = list(best.values())
    active = [h for h in cands if h["active"]]
    pool = active or cands
    pool.sort(key=lambda h: (h["active"], h["height_pct"], h["h2"]["date"]), reverse=True)
    return pool[0]


def sma20_below_streak(close: pd.Series) -> int:
    """最新收盤起算，連續多少根收在 20 日均之下。"""
    ma = sma(close, 20)
    streak = 0
    for c, m in zip(close.iloc[::-1], ma.iloc[::-1]):
        if pd.isna(m):
            break
        if float(c) < float(m):
            streak += 1
        else:
            break
    return int(streak)


def analyze_one(name: str, data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    daily = load_csv(data_dir / f"{name}_daily_2y.csv")
    daily_1y = load_csv(data_dir / f"{name}_daily_1y.csv")
    weekly = drop_duplicate_last_week(load_csv(data_dir / f"{name}_weekly_2y.csv"))
    weekly5 = drop_duplicate_last_week(load_csv(data_dir / f"{name}_weekly_5y.csv"))

    last = daily.iloc[-1]
    last_date = str(last["date"].date())
    last_close = float(last["close"])
    last_open = float(last["open"])
    last_high = float(last["high"])
    last_low = float(last["low"])

    def close_n_ago(n):
        if len(daily) < n + 1:
            return None
        return float(daily.iloc[-(n + 1)]["close"])

    # 52-week from last 252 trading days (incl last)
    win = daily.tail(252)
    hi_idx = win["high"].idxmax()
    lo_idx = win["low"].idxmin()
    close_hi_idx = win["close"].idxmax()
    close_lo_idx = win["close"].idxmin()
    w52_high = float(win.loc[hi_idx, "high"])
    w52_high_date = str(win.loc[hi_idx, "date"].date())
    w52_low = float(win.loc[lo_idx, "low"])
    w52_low_date = str(win.loc[lo_idx, "date"].date())
    w52_close_high = float(win.loc[close_hi_idx, "close"])
    w52_close_high_date = str(win.loc[close_hi_idx, "date"].date())
    w52_close_low = float(win.loc[close_lo_idx, "close"])
    w52_close_low_date = str(win.loc[close_lo_idx, "date"].date())

    # returns vs prior closes
    rets = {}
    for label, n in [("1d", 1), ("5d", 5), ("21d", 21), ("63d", 63), ("126d", 126), ("252d", 252)]:
        prev = close_n_ago(n)
        rets[label] = {
            "bars": n,
            "from_date": str(daily.iloc[-(n + 1)]["date"].date()) if len(daily) >= n + 1 else None,
            "from_close": r2(prev),
            "pct": r2(pct(last_close, prev), 2) if prev else None,
        }

    sma20 = float(sma(daily["close"], 20).iloc[-1])
    sma50 = float(sma(daily["close"], 50).iloc[-1])
    sma200 = float(sma(daily["close"], 200).iloc[-1])
    sma20_streak = sma20_below_streak(daily["close"])
    # weekly SMAs
    wsma10 = float(sma(weekly["close"], 10).iloc[-1]) if len(weekly) >= 10 else None
    wsma40 = float(sma(weekly["close"], 40).iloc[-1]) if len(weekly) >= 40 else None

    # price vs MA
    vs_ma = {
        "sma20": {
            "value": r2(sma20),
            "pct": r2(pct(last_close, sma20), 2),
            "above": last_close > sma20,
            "below_streak": sma20_streak,
        },
        "sma50": {"value": r2(sma50), "pct": r2(pct(last_close, sma50), 2), "above": last_close > sma50},
        "sma200": {"value": r2(sma200), "pct": r2(pct(last_close, sma200), 2), "above": last_close > sma200},
        "wsma10": {"value": r2(wsma10) if wsma10 else None, "pct": r2(pct(last_close, wsma10), 2) if wsma10 else None, "above": (last_close > wsma10) if wsma10 else None},
        "wsma40": {"value": r2(wsma40) if wsma40 else None, "pct": r2(pct(last_close, wsma40), 2) if wsma40 else None, "above": (last_close > wsma40) if wsma40 else None},
        "sma20_vs_sma50": {"above": sma20 > sma50, "pct": r2(pct(sma20, sma50), 2)},
        "sma50_vs_sma200": {"above": sma50 > sma200, "pct": r2(pct(sma50, sma200), 2)},
    }

    crosses_50_200 = ma_cross_events(daily, 50, 200)
    crosses_20_50 = ma_cross_events(daily, 20, 50)

    # streak of down/up closes
    chg = daily["close"].diff()
    streak = 0
    direction = None
    for v in chg.iloc[::-1]:
        if pd.isna(v) or v == 0:
            break
        s = 1 if v > 0 else -1
        if direction is None:
            direction = s
            streak = 1
        elif s == direction:
            streak += 1
        else:
            break

    # daily change last day
    prev_close = float(daily.iloc[-2]["close"])
    last_day_pct = pct(last_close, prev_close)

    # ATR
    atr14 = float(atr(daily, 14).iloc[-1])
    atr_pct = atr14 / last_close * 100

    # swings
    d_highs, d_lows = swings(daily, left=5, right=5)
    # major swings wider
    d_highs_m, d_lows_m = swings(daily, left=10, right=10)
    w_highs, w_lows = swings(weekly, left=3, right=3)
    w_ch, w_cl = swings_close(weekly, left=3, right=3)
    w5_ch, w5_cl = swings_close(weekly5, left=4, right=4)

    # Dow on weekly closes (significant, left=3 weeks ~ secondary)
    # Also a coarser weekly close swing left=8 for primary-ish
    w_ch_p, w_cl_p = swings_close(weekly5, left=8, right=8)

    def seq_desc(highs, lows, n=6):
        items = [("H", x) for x in highs] + [("L", x) for x in lows]
        items.sort(key=lambda t: t[1]["i"])
        return [
            {"kind": k, "date": x["date"], "price": r2(x["price"])}
            for k, x in items[-n:]
        ]

    # Determine HH/HL or LH/LL from last two swing highs and two swing lows (weekly close, medium)
    def structure(highs, lows, last_close, last_date, right_bars):
        if len(highs) < 2 or len(lows) < 2:
            return {"label": "資料不足以判斷", "detail": None}
        h1, h2 = highs[-2], highs[-1]
        l1, l2 = lows[-2], lows[-1]
        hh = h2["price"] > h1["price"]
        hl = l2["price"] > l1["price"]
        lh = h2["price"] < h1["price"]
        ll = l2["price"] < l1["price"]
        if hh and hl:
            base_label = "已確認的擺動：高點創新高、回檔低點墊高（多頭結構）"
            bias = "bull"
        elif lh and ll:
            base_label = "已確認的擺動：高點不過前高、回檔低點下破（空頭結構）"
            bias = "bear"
        elif hh and ll:
            base_label = "已確認的擺動：高點創新高但回檔低點下破（結構分歧）"
            bias = "mixed"
        elif lh and hl:
            base_label = "已確認的擺動：高點不過前高但低點墊高（結構分歧）"
            bias = "mixed"
        else:
            base_label = "結構不明"
            bias = "unclear"
        last_sl = l2["price"]
        last_sh = h2["price"]
        vs_low_pct = r2(pct(last_close, last_sl), 2)
        vs_high_pct = r2(pct(last_close, last_sh), 2)
        broke_low = last_close < last_sl
        broke_low_3 = last_close < last_sl * 0.97
        broke_high = last_close > last_sh
        broke_high_3 = last_close > last_sh * 1.03
        integrity_note = (
            f"擺動點需左右各 {right_bars} 根週K 才能確認，因此最近 {right_bars} 週的新高／新低還不算已確認擺動。"
            "但若最新收盤已穿越最近一個已確認擺動低點或高點，應一併記下，避免只看過時結構。"
        )
        extra = ""
        if bias == "bull" and broke_low_3:
            extra = (
                f"但最新收盤已較最近一個墊高的回檔低點（{l2['date']} 的 {r2(last_sl)}）"
                f"下跌超過 3%（相對該低點 {vs_low_pct}%），這段多頭的「低點墊高」條件目前已被收盤破壞；"
                "是否形成更低的已確認擺動低點，還要等右側週K走完。"
            )
            bias = "bull_low_broken"
        elif bias == "bull" and broke_low:
            extra = (
                f"最新收盤已低於最近回檔低點（{l2['date']} 的 {r2(last_sl)}），"
                f"相對該低點 {vs_low_pct}%，但尚未達到 3% 濾網。"
            )
            bias = "bull_low_broken_lt3"
        elif bias == "bear" and broke_high_3:
            extra = (
                f"但最新收盤已較最近一個擺動高點（{h2['date']} 的 {r2(last_sh)}）上漲超過 3%，"
                "這段空頭的「高點不過前高」條件目前已被收盤破壞。"
            )
            bias = "bear_high_broken"
        elif bias == "mixed" and broke_low_3:
            extra = f"最新收盤相對最近擺動低點 {vs_low_pct}%，已超過 3% 跌破。"
        label = base_label if not extra else (base_label + "。" + extra)
        return {
            "label": label,
            "bias": bias,
            "base_label": base_label,
            "last_two_highs": [
                {"date": h1["date"], "price": r2(h1["price"])},
                {"date": h2["date"], "price": r2(h2["price"])},
            ],
            "last_two_lows": [
                {"date": l1["date"], "price": r2(l1["price"])},
                {"date": l2["date"], "price": r2(l2["price"])},
            ],
            "hh": bool(hh),
            "hl": bool(hl),
            "lh": bool(lh),
            "ll": bool(ll),
            "close_vs_last_swing_low_pct": vs_low_pct,
            "close_vs_last_swing_high_pct": vs_high_pct,
            "close_broke_last_swing_low": broke_low,
            "close_broke_last_swing_low_by_3pct": broke_low_3,
            "close_broke_last_swing_high": broke_high,
            "close_broke_last_swing_high_by_3pct": broke_high_3,
            "confirmation_lag_weeks": right_bars,
            "integrity_note": integrity_note,
        }

    struct_weekly_med = structure(w_ch, w_cl, last_close, last_date, 3)
    struct_weekly_primary = structure(w_ch_p, w_cl_p, last_close, last_date, 8)

    # Last major peak (52w close high) and subsequent trough (lowest close since that peak)
    peak_i = int(close_hi_idx)
    after = daily.loc[peak_i:]
    trough_i = int(after["close"].idxmin())
    trough_close = float(daily.loc[trough_i, "close"])
    trough_date = str(daily.loc[trough_i, "date"].date())
    trough_low = float(daily.loc[trough_i, "low"])  # may not be the absolute low
    abs_low_i = int(after["low"].idxmin())
    abs_low = float(daily.loc[abs_low_i, "low"])
    abs_low_date = str(daily.loc[abs_low_i, "date"].date())
    dd_from_close_high = pct(last_close, w52_close_high)
    dd_from_high = pct(last_close, w52_high)
    bounce_from_trough = pct(last_close, trough_close)
    bounce_from_abs_low = pct(last_close, abs_low)

    # Prior advance into the 52w close high: from the significant weekly close low before that high
    # Find last weekly close swing low before close-high date
    prior_lows = [x for x in w_cl_p + w_cl if x["date"] < w52_close_high_date]
    if prior_lows:
        # take the lowest among the last 3 prior primary/med lows, or the most recent primary low
        # Use the most recent coarse weekly close low before the peak
        coarse_before = [x for x in w_cl_p if x["date"] < w52_close_high_date]
        if coarse_before:
            adv_from = coarse_before[-1]
        else:
            adv_from = min(prior_lows, key=lambda x: x["price"])
        prior_advance_pct = pct(w52_close_high, adv_from["price"])
        def retrace_pts(end_px, start_px, peak_px):
            den = peak_px - start_px
            if den == 0:
                return None
            return (peak_px - end_px) / den * 100.0
        retracement_of_advance = r2(retrace_pts(trough_close, adv_from["price"], w52_close_high), 1)
        retrace_now = r2(retrace_pts(last_close, adv_from["price"], w52_close_high), 1)
        longer = None
        if coarse_before:
            maj = min(coarse_before, key=lambda x: x["price"])
            if maj["date"] != adv_from["date"]:
                longer = {
                    "from_date": maj["date"],
                    "from_price": r2(maj["price"]),
                    "advance_pct": r2(pct(w52_close_high, maj["price"]), 2),
                    "trough_retrace_pct": r2(retrace_pts(trough_close, maj["price"], w52_close_high), 1),
                    "last_retrace_pct": r2(retrace_pts(last_close, maj["price"], w52_close_high), 1),
                }
        prior_leg = {
            "from_date": adv_from["date"],
            "from_price": r2(adv_from["price"]),
            "to_date": w52_close_high_date,
            "to_price": r2(w52_close_high),
            "advance_pct": r2(prior_advance_pct, 2),
            "close_trough_after_peak": {"date": trough_date, "close": r2(trough_close)},
            "decline_peak_to_trough_pct": r2(pct(trough_close, w52_close_high), 2),
            "trough_retrace_of_prior_leg_pct": retracement_of_advance,
            "decline_peak_to_last_pct": r2(dd_from_close_high, 2),
            "last_retrace_of_prior_leg_pct": retrace_now,
            "longer_primary_if_different": longer,
            "book_secondary_range_note": "原書：次級折返一般持續 3 週到數月，回吐前一段漲幅的 1/3～2/3。回吐比例用點數計算＝（高點−後續低點或最新收盤）÷（高點−前低），不是把兩段漲跌幅相除。這是觀察，不是預測。",
            # 高點前每一個粗週線收盤低點各算一次（同一套點數公式），/trend/ 並陳用。
            "all_coarse_lows_before_peak": [
                {
                    "from_date": x["date"],
                    "from_price": r2(x["price"]),
                    "advance_pct": r2(pct(w52_close_high, x["price"]), 2),
                    "trough_retrace_pct": r2(retrace_pts(trough_close, x["price"], w52_close_high), 1),
                    "last_retrace_pct": r2(retrace_pts(last_close, x["price"], w52_close_high), 1),
                }
                for x in coarse_before
            ],
        }
    else:
        prior_leg = None

    # Days since peak
    days_since_peak = int((daily.iloc[-1]["date"] - daily.loc[peak_i, "date"]).days)
    bars_since_peak = int(daily.index[-1] - peak_i)

    # Trendlines: last two major swing lows (daily, left=10) after or around the last year
    # Use swings on last 252+20 days but indices from full daily
    # Rising support: last two swing lows (major) where the later is not far above last
    def last_valid_line(points, x_now, rising=True, min_sep=8):
        pts = points[:]
        # try last two, then earlier combinations from the end
        for j in range(len(pts) - 1, 0, -1):
            for i in range(j - 1, -1, -1):
                if pts[j]["i"] - pts[i]["i"] < min_sep:
                    continue
                line = trendline_from_two(pts[i], pts[j], x_now)
                if line is None:
                    continue
                if rising and line["rising"]:
                    return line
                if (not rising) and (not line["rising"]):
                    return line
        return None

    x_now = int(daily.index[-1])
    # filter major lows/highs in last ~18 months
    cut_date = daily.iloc[-1]["date"] - pd.Timedelta(days=550)
    maj_lows = [x for x in d_lows_m if pd.Timestamp(x["date"]) >= cut_date]
    maj_highs = [x for x in d_highs_m if pd.Timestamp(x["date"]) >= cut_date]
    up_line = last_valid_line(maj_lows, x_now, rising=True, min_sep=15)
    down_line = last_valid_line(maj_highs, x_now, rising=False, min_sep=15)

    def enrich_line(line, last_close):
        if not line:
            return None
        val = line["value_at_last"]
        dist = pct(last_close, val)
        broken_3 = False
        side = "above" if last_close > val else "below"
        # For rising support, close 3% below line = breakdown
        # For falling resistance, close 3% above line = breakout
        if line["rising"]:
            broken_3 = last_close < val * 0.97
            relation = "升勢支撐線"
        else:
            broken_3 = last_close > val * 1.03
            relation = "降勢壓力線"
        line_out = {
            "type": relation,
            "p1": {"date": line["p1"]["date"], "price": r2(line["p1"]["price"])},
            "p2": {"date": line["p2"]["date"], "price": r2(line["p2"]["price"])},
            "value_at_last": r2(val),
            "close_vs_line_pct": r2(dist, 2),
            "close_is": side,
            "broken_by_3pct_filter": broken_3,
            "note": "趨勢線由最近兩處明顯波段低點／高點（日線、左右各 10 根K的擺動點）連成並延伸到最新收盤；3% 是原書對『有效突破』的收盤價濾網，這裡只報告有沒有達到，不是交易訊號。",
        }
        return line_out

    # Also a shorter-term line from last two medium swing lows after the peak
    post_peak_lows = [x for x in d_lows if x["i"] >= peak_i]
    post_peak_highs = [x for x in d_highs if x["i"] >= peak_i]
    rebound_line = last_valid_line(post_peak_lows, x_now, rising=True, min_sep=5) if len(post_peak_lows) >= 2 else None
    post_high_line = last_valid_line(post_peak_highs, x_now, rising=False, min_sep=5) if len(post_peak_highs) >= 2 else None

    # Support / resistance candidates
    level_pts = []
    for x in d_highs_m[-12:]:
        level_pts.append({"price": x["price"], "date": x["date"], "kind": "swing_high"})
    for x in d_lows_m[-12:]:
        level_pts.append({"price": x["price"], "date": x["date"], "kind": "swing_low"})
    level_pts.append({"price": w52_high, "date": w52_high_date, "kind": "52w_high"})
    level_pts.append({"price": w52_low, "date": w52_low_date, "kind": "52w_low"})
    level_pts.append({"price": w52_close_high, "date": w52_close_high_date, "kind": "52w_close_high"})
    level_pts.append({"price": abs_low, "date": abs_low_date, "kind": "post_peak_intraday_low"})
    clusters = cluster_levels(level_pts, tol=0.012)
    # keep those reasonably near last close (within 25%) plus the closest few
    nearby = [c for c in clusters if abs(c["price"] - last_close) / last_close <= 0.25]
    nearby.sort(key=lambda c: abs(c["price"] - last_close))
    # classify as support (below) or resistance (above)
    sr = []
    for c in nearby[:10]:
        role = "壓力" if c["price"] > last_close * 1.003 else ("支撐" if c["price"] < last_close * 0.997 else "近現價")
        distp = pct(last_close, c["price"])
        # 3% filter relative to last close? better: how far is close from level
        beyond_3 = abs(c["price"] - last_close) / c["price"] >= 0.03
        sr.append(
            {
                "role": role,
                "price": c["price"],
                "range": [c["lo"], c["hi"]],
                "touches": c["touches"],
                "dates": c["dates"],
                "kinds": c["kinds"],
                "close_vs_level_pct": r2(distp, 2),
                "at_least_3pct_away": beyond_3,
            }
        )

    # Gaps in last 1y, highlight unfilled and large
    gaps = find_gaps(daily_1y if len(daily_1y) else daily.tail(260), lookback=260)
    unfilled = [g for g in gaps if not g["filled"]]
    large_gaps = [g for g in gaps if g["size_pct"] >= 0.8]
    recent_gaps = gaps[-8:]

    # Pattern observations (conservative, objective checks)
    patterns = []
    # Double top check vs 52w high: is there a second swing high within 3% of the peak,
    # at least ~20 trading days later, with an intervening decline of >=15% (book: 20% typ, 15% possible)
    peak_price = w52_high
    later_highs = [x for x in d_highs_m if x["i"] > hi_idx + 15]
    for h in later_highs:
        diff = abs(h["price"] - peak_price) / peak_price * 100
        mid = daily.loc[hi_idx : h["i"]]
        mid_low = float(mid["low"].min())
        mid_dd = (mid_low / peak_price - 1) * 100
        mid_date = str(mid.loc[mid["low"].idxmin(), "date"].date())
        bars_apart = h["i"] - hi_idx
        if diff <= 3.0 and bars_apart >= 15:
            # valley broken by 3%?
            valley = mid_low
            broken = last_close < valley * 0.97
            meets_depth = mid_dd <= -15
            patterns.append(
                {
                    "name": "雙重頂候選（尚未當確認）" if not (meets_depth and broken) else "雙重頂（谷底被收盤跌破 3%）",
                    "confirmed": bool(meets_depth and broken),
                    "facts": {
                        "peak1": {"date": w52_high_date, "high": r2(peak_price)},
                        "peak2": {"date": h["date"], "high": r2(h["price"])},
                        "peaks_diff_pct": r2(diff, 2),
                        "bars_apart": int(bars_apart),
                        "intervening_low": {"date": mid_date, "low": r2(mid_low), "dd_pct": r2(mid_dd, 2)},
                        "book_mid_decline_15_to_20": meets_depth,
                        "close_broke_valley_by_3pct": broken,
                        "last_close": r2(last_close),
                    },
                    "opinion": "兩頂相差在原書容許的 3% 以內；"
                    + (
                        "中間回檔達到 15% 門檻。"
                        if meets_depth
                        else f"中間回檔只有 {r2(mid_dd,1)}%，未達原書常用的 15%～20%，因此不能稱為已完成的雙重頂。"
                    )
                    + (
                        "收盤已跌破兩頂之間低點的 3%。"
                        if broken
                        else "收盤尚未跌破兩頂之間低點的 3%，形態未確立。"
                    ),
                }
            )
            break  # only first candidate after peak

    # Double bottom after the post-peak low
    # two lows within 3%, >=15 bars apart, intervening rally >=15%, close broke midpoint high by 3%
    post = daily.loc[abs_low_i:]
    # find a later major swing low near abs_low
    later_lows = [x for x in d_lows_m if x["i"] > abs_low_i + 15]
    for lw in later_lows:
        diff = abs(lw["price"] - abs_low) / abs_low * 100
        mid = daily.loc[abs_low_i : lw["i"]]
        mid_high = float(mid["high"].max())
        mid_rally = (mid_high / abs_low - 1) * 100
        mid_date = str(mid.loc[mid["high"].idxmax(), "date"].date())
        bars_apart = lw["i"] - abs_low_i
        if diff <= 3.0 and bars_apart >= 15:
            broken = last_close > mid_high * 1.03
            meets = mid_rally >= 15
            patterns.append(
                {
                    "name": "雙重底候選（尚未當確認）" if not (meets and broken) else "雙重底（中間高點被收盤突破 3%）",
                    "confirmed": bool(meets and broken),
                    "facts": {
                        "low1": {"date": abs_low_date, "low": r2(abs_low)},
                        "low2": {"date": lw["date"], "low": r2(lw["price"])},
                        "lows_diff_pct": r2(diff, 2),
                        "bars_apart": int(bars_apart),
                        "intervening_high": {"date": mid_date, "high": r2(mid_high), "rally_pct": r2(mid_rally, 2)},
                        "book_mid_rally_15_to_20": meets,
                        "close_broke_mid_high_by_3pct": broken,
                    },
                    "opinion": "僅就擺動點距離與 3% 容差做檢查；未同時滿足深度與 3% 突破就不得稱為完成形態。",
                }
            )
            break

    descending_tri = detect_descending_right_triangle(daily, d_highs_m, d_lows_m)

    # Rectangle / range since peak: if last ~15-40 sessions are range-bound
    # Check last 20 trading days high-low range vs prior
    recent20 = daily.tail(20)
    range20 = (recent20["high"].max() - recent20["low"].min()) / last_close * 100
    # last 60 day range
    recent60 = daily.tail(60)
    range60 = (recent60["high"].max() - recent60["low"].min()) / last_close * 100

    # 3% break of 52w high or of post-peak low
    broke_52w_high_3 = last_close > w52_high * 1.03  # unlikely
    broke_abs_low_3 = last_close < abs_low * 0.97
    held_abs_low = last_close > abs_low * 1.03  # bounced more than 3% above the low (not a break)

    # Notable large daily moves (abs >= 3%) in last 1y
    daily["ret"] = daily["close"].pct_change() * 100
    big = daily[daily["ret"].abs() >= 3].tail(12)
    big_moves = [
        {
            "date": str(row["date"].date()),
            "close": r2(row["close"]),
            "pct": r2(row["ret"], 2),
            "low": r2(row["low"]),
            "high": r2(row["high"]),
        }
        for _, row in big.iterrows()
    ]

    # MA alignment
    if last_close > sma20 > sma50 > sma200:
        ma_align = "收盤 > 20日 > 50日 > 200日（多頭排列）"
    elif last_close < sma20 < sma50 < sma200:
        ma_align = "收盤 < 20日 < 50日 < 200日（空頭排列）"
    else:
        ma_align = "均線未呈單向排列（糾纏或交叉後重整）"

    # Volume note
    vol_note = None
    if name == "SOX":
        vol_note = "Yahoo 此指數成交量欄為 0，本頁不引用 SOX 成交量。"
    else:
        # last day vs 20d avg, warn if last day much lower
        v20 = float(daily["volume"].iloc[-21:-1].mean())
        vlast = float(last["volume"])
        vol_note = {
            "last": int(vlast),
            "avg20_prev": int(v20),
            "last_vs_avg20_pct": r2(pct(vlast, v20), 1) if v20 else None,
            "caution": "最新一日成交量若明顯低於均量，可能尚未完全入帳，解讀需保留。" if v20 and vlast < 0.4 * v20 else None,
        }

    # Chart series: last 252 daily + weekly 2y
    def series_daily(df, n=252):
        sl = df.tail(n)
        sma20s = sma(df["close"], 20).tail(n)
        sma50s = sma(df["close"], 50).tail(n)
        sma200s = sma(df["close"], 200).tail(n)
        return {
            "dates": [str(x.date()) for x in sl["date"]],
            "open": [r2(x) for x in sl["open"]],
            "high": [r2(x) for x in sl["high"]],
            "low": [r2(x) for x in sl["low"]],
            "close": [r2(x) for x in sl["close"]],
            "volume": [int(x) if not pd.isna(x) else 0 for x in sl["volume"]],
            "sma20": [r2(x) if pd.notna(x) else None for x in sma20s],
            "sma50": [r2(x) if pd.notna(x) else None for x in sma50s],
            "sma200": [r2(x) if pd.notna(x) else None for x in sma200s],
        }

    def series_weekly(df):
        sma10s = sma(df["close"], 10)
        sma40s = sma(df["close"], 40)
        return {
            "dates": [str(x.date()) for x in df["date"]],
            "open": [r2(x) for x in df["open"]],
            "high": [r2(x) for x in df["high"]],
            "low": [r2(x) for x in df["low"]],
            "close": [r2(x) for x in df["close"]],
            "sma10": [r2(x) if pd.notna(x) else None for x in sma10s],
            "sma40": [r2(x) if pd.notna(x) else None for x in sma40s],
        }

    # annotation marks for charts
    annotations = []
    annotations.append({"date": w52_close_high_date, "price": r2(w52_close_high), "label": "52週收盤高點", "kind": "peak"})
    annotations.append({"date": w52_high_date, "price": r2(w52_high), "label": "52週盤中高點", "kind": "peak_intra"})
    annotations.append({"date": abs_low_date, "price": r2(abs_low), "label": "高點後盤中低點", "kind": "trough_intra"})
    annotations.append({"date": trough_date, "price": r2(trough_close), "label": "高點後收盤低點", "kind": "trough"})
    if up_line:
        annotations.append(
            {
                "kind": "trendline_up",
                "p1": enrich_line(up_line, last_close)["p1"] if enrich_line(up_line, last_close) else None,
                "p2": enrich_line(up_line, last_close)["p2"] if enrich_line(up_line, last_close) else None,
                "value_at_last": r2(up_line["value_at_last"]),
            }
        )
    if down_line:
        annotations.append(
            {
                "kind": "trendline_down",
                "p1": enrich_line(down_line, last_close)["p1"] if enrich_line(down_line, last_close) else None,
                "p2": enrich_line(down_line, last_close)["p2"] if enrich_line(down_line, last_close) else None,
                "value_at_last": r2(down_line["value_at_last"]),
            }
        )

    # weekly last bar date (Monday start of last completed week)
    last_week_start = str(weekly.iloc[-1]["date"].date())
    last_week_close = r2(weekly.iloc[-1]["close"])

    result = {
        "id": name,
        "name": NAMES[name],
        "ticker": SOURCE_TICKERS[name],
        "asof_session": last_date,
        "last": {
            "date": last_date,
            "open": r2(last_open),
            "high": r2(last_high),
            "low": r2(last_low),
            "close": r2(last_close),
            "prev_close": r2(prev_close),
            "day_change_pct": r2(last_day_pct, 2),
            "day_change": r2(last_close - prev_close),
            "streak_closes": streak,
            "streak_dir": "上漲" if direction == 1 else ("下跌" if direction == -1 else "持平"),
        },
        "returns": rets,
        "range_52w": {
            "high": r2(w52_high),
            "high_date": w52_high_date,
            "low": r2(w52_low),
            "low_date": w52_low_date,
            "close_high": r2(w52_close_high),
            "close_high_date": w52_close_high_date,
            "close_low": r2(w52_close_low),
            "close_low_date": w52_close_low_date,
            "close_vs_high_pct": r2(dd_from_high, 2),
            "close_vs_close_high_pct": r2(dd_from_close_high, 2),
            "close_vs_low_pct": r2(pct(last_close, w52_low), 2),
            "close_vs_close_low_pct": r2(pct(last_close, w52_close_low), 2),
            "position_in_52w_high_low": r2((last_close - w52_low) / (w52_high - w52_low) * 100, 1),
        },
        "since_52w_close_high": {
            "peak_date": w52_close_high_date,
            "peak_close": r2(w52_close_high),
            "calendar_days": days_since_peak,
            "trading_days": bars_since_peak,
            "subsequent_close_low": {"date": trough_date, "close": r2(trough_close), "pct_from_peak": r2(pct(trough_close, w52_close_high), 2)},
            "subsequent_intraday_low": {"date": abs_low_date, "low": r2(abs_low), "pct_from_peak_high": r2(pct(abs_low, w52_high), 2)},
            "bounce_from_close_low_pct": r2(bounce_from_trough, 2),
            "bounce_from_intraday_low_pct": r2(bounce_from_abs_low, 2),
            "broke_intraday_low_by_3pct": broke_abs_low_3,
            "close_at_least_3pct_above_intraday_low": last_close >= abs_low * 1.03,
        },
        "prior_leg_and_retracement": prior_leg,
        "moving_averages": vs_ma,
        "ma_alignment": ma_align,
        "crosses_sma50_sma200": crosses_50_200[-4:],
        "crosses_sma20_sma50": crosses_20_50[-4:],
        "atr14": {"value": r2(atr14), "pct_of_close": r2(atr_pct, 2)},
        "dow_weekly_medium_swings": {
            "window": "週線收盤、左右各 3 根週K 的擺動點（偏次級）",
            "structure": struct_weekly_med,
            "recent_sequence": seq_desc(w_ch, w_cl, 8),
        },
        "dow_weekly_coarse_swings": {
            "window": "週線收盤、左右各 8 根週K 的擺動點（偏主要趨勢）",
            "structure": struct_weekly_primary,
            "recent_sequence": seq_desc(w_ch_p, w_cl_p, 8),
        },
        "trendlines": {
            "rising_support_major": enrich_line(up_line, last_close),
            "falling_resistance_major": enrich_line(down_line, last_close),
            "rising_support_since_peak": enrich_line(rebound_line, last_close) if rebound_line else None,
            "falling_resistance_since_peak": enrich_line(post_high_line, last_close) if post_high_line else None,
        },
        "support_resistance": sr,
        "gaps_1y": {
            "count": len(gaps),
            "unfilled_count": len(unfilled),
            "unfilled": unfilled[-8:],
            "large_or_recent": (large_gaps[-6:] + [g for g in recent_gaps if g not in large_gaps[-6:]])[:8],
        },
        "pattern_checks": patterns,
        "descending_right_triangle": descending_tri,
        "recent_range": {
            "last_20_sessions_range_pct": r2(range20, 2),
            "last_20_high": r2(float(recent20["high"].max())),
            "last_20_high_date": str(recent20.loc[recent20["high"].idxmax(), "date"].date()),
            "last_20_low": r2(float(recent20["low"].min())),
            "last_20_low_date": str(recent20.loc[recent20["low"].idxmin(), "date"].date()),
            "last_60_sessions_range_pct": r2(range60, 2),
        },
        "big_daily_moves_ge_3pct_recent": big_moves,
        "volume": vol_note,
        "weekly_last_completed": {
            "week_start_monday": last_week_start,
            "close": last_week_close,
            "note": "若 Yahoo 多給一根日期不是週一的重複週K（與當週週一起始的那根重複）會先去掉。最後一根週K 從該週一算到價格截至日；週中更新時這一週還沒走完。",
        },
        "daily_swings_major_recent": {
            "highs": [{"date": x["date"], "price": r2(x["price"])} for x in d_highs_m[-6:]],
            "lows": [{"date": x["date"], "price": r2(x["price"])} for x in d_lows_m[-6:]],
        },
        "chart_daily_1y": series_daily(daily, 252),
        "chart_weekly_2y": series_weekly(weekly),
        "chart_annotations": annotations,
    }
    return result


def build_confirmation(indices: dict) -> dict:
    """Dow: two indices should confirm. Compare SOX, NDX, SPX."""
    rows = []
    for k in ["SOX", "NDX", "SPX"]:
        ix = indices[k]
        rows.append(
            {
                "id": k,
                "close": ix["last"]["close"],
                "vs_close_high_pct": ix["range_52w"]["close_vs_close_high_pct"],
                "close_high_date": ix["range_52w"]["close_high_date"],
                "above_sma50": ix["moving_averages"]["sma50"]["above"],
                "above_sma200": ix["moving_averages"]["sma200"]["above"],
                "ma_alignment": ix["ma_alignment"],
                "weekly_med_bias": ix["dow_weekly_medium_swings"]["structure"].get("bias"),
                "weekly_med_label": ix["dow_weekly_medium_swings"]["structure"].get("label"),
                "weekly_coarse_bias": ix["dow_weekly_coarse_swings"]["structure"].get("bias"),
                "weekly_coarse_label": ix["dow_weekly_coarse_swings"]["structure"].get("label"),
                "ret_5d": ix["returns"]["5d"]["pct"],
                "ret_21d": ix["returns"]["21d"]["pct"],
                "ret_252d": ix["returns"]["252d"]["pct"],
            }
        )
    # agreement
    coarse = [r["weekly_coarse_bias"] for r in rows]
    med = [r["weekly_med_bias"] for r in rows]
    above200 = [r["above_sma200"] for r in rows]
    facts = {
        "all_above_sma200": all(above200),
        "all_below_sma200": not any(above200),
        "coarse_all_bull": all(b == "bull" for b in coarse),
        "coarse_all_bear": all(b == "bear" for b in coarse),
        "med_all_same": len(set(med)) == 1,
        "close_high_dates": {r["id"]: r["close_high_date"] for r in rows},
        "drawdowns_from_close_high": {r["id"]: r["vs_close_high_pct"] for r in rows},
        "one_year_returns": {r["id"]: r["ret_252d"] for r in rows},
    }
    opinion = (
        "原書道氏理論要求『兩個指數互相印證』才採信主要趨勢訊號。"
        "這裡把 SOX、NDX、SPX 並列觀察：若三者的粗週線結構或相對 200 日線位置不一致，就只記分歧，不把它寫成單一趨勢結論。"
    )
    return {"table": rows, "facts": facts, "opinion": opinion}


def session_close_taipei(price_asof: str) -> datetime:
    """美股該交易日 16:00（紐約時間）換成台北時間。"""
    d = datetime.strptime(price_asof, "%Y-%m-%d").date()
    return datetime.combine(d, dtime(16, 0), tzinfo=NY).astimezone(TZ)


def build_note_timing(queried_at, price_asof: str) -> str:
    """時差提醒：由燈號實際查詢時間與行情截至日組句，不寫死日期。"""
    q_txt = str(queried_at or "").strip()
    q_dt = None
    try:
        q_dt = datetime.strptime(q_txt[:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    except ValueError:
        pass
    head = f"燈號站最近一次查詢是 {q_txt[:16]}（台北時間）" if q_dt else "燈號站最近一次查詢時間不明"
    tail = f"本頁指數走勢用的是 Yahoo {price_asof} 收盤"
    if q_dt is None:
        return f"{head}。{tail}，兩邊的資料日可能不同，不要混著比。"
    if q_dt < session_close_taipei(price_asof):
        return (
            f"{head}，當時美股 {price_asof} 這一場還沒收盤，燈號用到的美股資料至少比本頁早一個交易日"
            f"（各燈號的資料日寫在每一項裡）。{tail}，兩邊不要混著比。"
        )
    return (
        f"{head}，在美股 {price_asof} 收盤之後。{tail}；"
        "各燈號仍以每一項自己列的資料日為準（週線、月線燈號會更早），兩邊日期可能不同，不要混著比。"
    )


def cobb_snapshot(latest_path: Path, rules_path: Path, price_asof: str):
    latest = json.loads(Path(latest_path).read_text(encoding="utf-8"))
    rules = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    groups = rules["presentation"]["groups"]
    signals = []
    for i in range(1, 12):
        sid = str(i)
        rule = rules["signals"][sid]
        summary = latest["summary"][sid]
        rows = [r for r in latest["rows"] if str(r["signal"]) == sid]
        slim_rows = []
        for r in rows:
            slim_rows.append(
                {
                    "target": r.get("target"),
                    "status": r.get("status"),
                    "status_label": r.get("status_label"),
                    "value": r.get("value"),
                    "threshold": r.get("threshold"),
                    "data_date": r.get("data_date"),
                    "asof": r.get("asof"),
                    "note": r.get("note"),
                }
            )
        signals.append(
            {
                "id": sid,
                "name": rule["name"],
                "aggregation": rule.get("aggregation"),
                "trigger": rule.get("trigger"),
                "status": summary.get("status"),
                "status_label": summary.get("status_label"),
                "by_market": summary.get("by_market"),
                "rows": slim_rows,
            }
        )
    # counts
    counts = {}
    for s in signals:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    return {
        "site": "COBB我大哥",
        "live_url": "https://manolomoo.github.io/charity-poker-king/",
        "repo": "https://github.com/manolomoo/charity-poker-king",
        "data_url": "https://manolomoo.github.io/charity-poker-king/data/latest.json",
        "rules_url": "https://manolomoo.github.io/charity-poker-king/rules.json",
        "rules_version": latest.get("rules_version"),
        "run_date": latest.get("run_date"),
        "queried_at": latest.get("queried_at"),
        "previous_run": latest.get("previous_run"),
        "changes_vs_previous": latest.get("changes_vs_previous"),
        "framing": rules.get("framing"),
        "status_legend": rules.get("status_legend"),
        "groups": groups,
        "hold": rules.get("hold"),
        "signals": signals,
        "status_counts": counts,
        "episode": {
            "peak_date": latest["episode"].get("peak_date"),
            "peak_close": latest["episode"].get("peak_close"),
            "ndx_last": latest["episode"].get("ndx_last"),
            "ndx_dd_now": latest["episode"].get("ndx_dd_now"),
            "note": latest["episode"].get("note"),
            "rows": latest["episode"].get("rows"),
        },
        "fetched_at_taipei": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "note_timing": build_note_timing(latest.get("queried_at"), price_asof),
    }


def write_markdown(payload: dict, path: Path):
    lines = []
    m = payload["meta"]
    lines.append("# 指數走勢觀察（資料與計算）")
    lines.append("")
    lines.append(f"- 來源：{m['source']}")
    lines.append(f"- 代號：SOX=`^SOX`、NDX=`^NDX`、SPX=`^GSPC`")
    lines.append(f"- 抓資料時間（台北）：{m['fetched_at_taipei']}")
    lines.append(f"- 價格截至：{m['price_asof']}（美股收盤）")
    lines.append(f"- 說明：{m['price_asof_note']}")
    lines.append("- 所有數字都來自這批 CSV／JSON，沒有手填或估算。")
    lines.append("- 「事實」是可核對的計算；「觀察」是套用原書定義後的解讀，不是預測，更不是獲利保證。")
    lines.append("")
    for k in ["SOX", "NDX", "SPX"]:
        ix = payload["indices"][k]
        last = ix["last"]
        r52 = ix["range_52w"]
        since = ix["since_52w_close_high"]
        lines.append(f"## {ix['name']}")
        lines.append("")
        lines.append("### 事實")
        lines.append(
            f"- 最新收盤 {last['close']}（{last['date']}），當日 {last['day_change']:+}（{last['day_change_pct']:+}%），相對前收 {last['prev_close']}。"
            f"連續 {last['streak_closes']} 根收盤{last['streak_dir']}。"
        )
        rets = ix["returns"]
        lines.append(
            f"- 漲跌：5 個交易日 {rets['5d']['pct']:+}%（自 {rets['5d']['from_date']} 收盤 {rets['5d']['from_close']}）；"
            f"21 日 {rets['21d']['pct']:+}%；63 日 {rets['63d']['pct']:+}%；126 日 {rets['126d']['pct']:+}%；"
            f"252 日 {rets['252d']['pct']:+}%（自 {rets['252d']['from_date']} 收盤 {rets['252d']['from_close']}）。"
        )
        lines.append(
            f"- 近 52 週（252 根日K）：盤中高 {r52['high']}（{r52['high_date']}），盤中低 {r52['low']}（{r52['low_date']}）；"
            f"收盤高 {r52['close_high']}（{r52['close_high_date']}），收盤低 {r52['close_low']}（{r52['close_low_date']}）。"
            f"最新收盤距收盤高點 {r52['close_vs_close_high_pct']}%，距盤中高點 {r52['close_vs_high_pct']}%，"
            f"距盤中低點 {r52['close_vs_low_pct']:+}%。在 52 週高低區間的位置約 {r52['position_in_52w_high_low']}%。"
        )
        lines.append(
            f"- 自 52 週收盤高點（{since['peak_date']}，{since['peak_close']}）至今 {since['calendar_days']} 日曆天、{since['trading_days']} 個交易日；"
            f"其後收盤低點 {since['subsequent_close_low']['close']}（{since['subsequent_close_low']['date']}，相對高點 {since['subsequent_close_low']['pct_from_peak']}%）；"
            f"其後盤中低點 {since['subsequent_intraday_low']['low']}（{since['subsequent_intraday_low']['date']}）。"
            f"自收盤低點反彈 {since['bounce_from_close_low_pct']}%，自盤中低點 {since['bounce_from_intraday_low_pct']}%。"
            f"收盤是否較盤中低點再跌破 3%：{since['broke_intraday_low_by_3pct']}；"
            f"收盤是否至少高於盤中低點 3%：{since['close_at_least_3pct_above_intraday_low']}。"
        )
        ma = ix["moving_averages"]
        lines.append(
            f"- 移動平均（日線收盤）：20 日 {ma['sma20']['value']}（相對 {ma['sma20']['pct']:+}%）；"
            f"50 日 {ma['sma50']['value']}（{ma['sma50']['pct']:+}%）；"
            f"200 日 {ma['sma200']['value']}（{ma['sma200']['pct']:+}%）。{ix['ma_alignment']}。"
            f"週線 10 週均 {ma['wsma10']['value']}（{ma['wsma10']['pct']:+}%），40 週均 {ma['wsma40']['value']}（{ma['wsma40']['pct']:+}%）。"
        )
        if ix["crosses_sma50_sma200"]:
            xs = "；".join(
                f"{x['date']} {'黃金交叉' if x['kind']=='golden_cross' else '死亡交叉'}（收盤 {x['price']}）"
                for x in ix["crosses_sma50_sma200"]
            )
            lines.append(f"- 近兩年 50／200 日交叉：{xs}。")
        lines.append(f"- 14 日 ATR {ix['atr14']['value']}（約為收盤價的 {ix['atr14']['pct_of_close']}%）。")
        rr = ix["recent_range"]
        lines.append(
            f"- 近 20 個交易日高低區間 {rr['last_20_low']}（{rr['last_20_low_date']}）～{rr['last_20_high']}（{rr['last_20_high_date']}），幅度約 {rr['last_20_sessions_range_pct']}%；"
            f"近 60 日區間幅度約 {rr['last_60_sessions_range_pct']}%。"
        )
        if ix["big_daily_moves_ge_3pct_recent"]:
            bm = "；".join(f"{b['date']} {b['pct']:+}%（收 {b['close']}）" for b in ix["big_daily_moves_ge_3pct_recent"])
            lines.append(f"- 近一年收盤漲跌幅 ≥3% 的日子（最近數筆）：{bm}。")
        if ix["gaps_1y"]["unfilled"]:
            ug = "；".join(
                f"{g['date']} {'向上' if g['kind']=='up' else '向下'}缺口 {g['gap_low']}–{g['gap_high']}（{g['size_pct']}%）"
                for g in ix["gaps_1y"]["unfilled"]
            )
            lines.append(f"- 近一年尚未回補的缺口：{ug}。共 {ix['gaps_1y']['count']} 個缺口，未回補 {ix['gaps_1y']['unfilled_count']} 個。")
        else:
            lines.append(f"- 近一年偵測到 {ix['gaps_1y']['count']} 個缺口，目前清單內沒有未回補的。")
        if isinstance(ix["volume"], str):
            lines.append(f"- 成交量：{ix['volume']}")
        elif ix["volume"]:
            v = ix["volume"]
            extra = f" 注意：{v['caution']}" if v.get("caution") else ""
            lines.append(f"- 成交量：最新一日 {v['last']:,}，前 20 日均量 {v['avg20_prev']:,}，相對均量 {v['last_vs_avg20_pct']}%。{extra}")
        lines.append("")
        lines.append("### 觀察（套用原書定義，不是預測）")
        lines.append(
            f"- 偏主要趨勢的週線收盤擺動（左右各 8 根週K）：{ix['dow_weekly_coarse_swings']['structure']['label']}。"
        )
        st = ix["dow_weekly_coarse_swings"]["structure"]
        if st.get("last_two_highs"):
            lines.append(
                f"  最近兩個週線收盤擺動高點：{st['last_two_highs'][0]['date']} 的 {st['last_two_highs'][0]['price']}、"
                f"{st['last_two_highs'][1]['date']} 的 {st['last_two_highs'][1]['price']}；"
                f"低點：{st['last_two_lows'][0]['date']} 的 {st['last_two_lows'][0]['price']}、"
                f"{st['last_two_lows'][1]['date']} 的 {st['last_two_lows'][1]['price']}。"
            )
        lines.append(
            f"- 偏次級的週線收盤擺動（左右各 3 根週K）：{ix['dow_weekly_medium_swings']['structure']['label']}。"
        )
        pl = ix["prior_leg_and_retracement"]
        if pl:
            lines.append(
                f"- 高點前一波（粗週線低點 {pl['from_date']} 的 {pl['from_price']} → 收盤高點 {pl['to_date']} 的 {pl['to_price']}）漲幅 {pl['advance_pct']}%。"
                f"高點到後續收盤低點回落 {pl['decline_peak_to_trough_pct']}%，以點數計回吐前一波的 {pl['trough_retrace_of_prior_leg_pct']}%；"
                f"高點到最新收盤回落 {pl['decline_peak_to_last_pct']}%，回吐前一波的 {pl['last_retrace_of_prior_leg_pct']}%。"
                + (("另，自更早的主要低點 " + pl["longer_primary_if_different"]["from_date"] + " 的 " + str(pl["longer_primary_if_different"]["from_price"]) + " 起算，這波漲幅 " + str(pl["longer_primary_if_different"]["advance_pct"]) + "%，高點到收盤低點回吐 " + str(pl["longer_primary_if_different"]["trough_retrace_pct"]) + "%，到最新收盤回吐 " + str(pl["longer_primary_if_different"]["last_retrace_pct"]) + "%。") if pl.get("longer_primary_if_different") else "")
                + pl["book_secondary_range_note"]
            )
        for key, title in [
            ("rising_support_major", "較長升勢支撐線"),
            ("falling_resistance_major", "較長降勢壓力線"),
            ("rising_support_since_peak", "高點之後的反彈支撐線"),
            ("falling_resistance_since_peak", "高點之後的下降壓力線"),
        ]:
            ln = ix["trendlines"].get(key)
            if not ln:
                continue
            lines.append(
                f"- {title}：{ln['p1']['date']} 的 {ln['p1']['price']} 連 {ln['p2']['date']} 的 {ln['p2']['price']}，"
                f"延伸到最新交易日約 {ln['value_at_last']}；收盤在線{ '上' if ln['close_is']=='above' else '下' } {abs(ln['close_vs_line_pct'])}%。"
                f"是否達到 3% 有效突破濾網：{ln['broken_by_3pct_filter']}。"
            )
        if ix["support_resistance"]:
            bits = []
            for s in ix["support_resistance"][:6]:
                bits.append(f"{s['role']} {s['price']}（距收盤 {s['close_vs_level_pct']:+}%，觸及 {s['touches']} 次）")
            lines.append("- 靠近現價的擺動密集區（約 1.2% 容差歸類）：" + "；".join(bits) + "。這是價位觀察，不是保證守得住或過得了。")
        tri = ix.get("descending_right_triangle")
        if tri:
            bits = (
                f"- 下降直角三角形（v2／COBB 主高點讀法）：狀態「{tri['status']}」；"
                f"降勢線 {tri['h1']['date']} 的 {tri['h1']['price']} 連 {tri['h2']['date']} 的 {tri['h2']['price']}；"
                f"頸線（水平支撐）{tri['support']}（首觸 {tri['support_first']['date']}，其後測試 {tri['n_tests']} 次）；"
                f"收盤相對頸線 {tri['close_vs_support_pct']:+}%。"
            )
            if tri.get("first_3pct_date"):
                bits += f"3% 確認日 {tri['first_3pct_date']} 收盤 {tri['first_3pct_close']}。"
            if tri.get("target") is not None:
                bits += f"最小測算目標 {tri['target']}。"
            if tri.get("apex_frac_at_ref") is not None:
                bits += f"確認或截至日時約在起點至頂點路程的 {tri['apex_frac_at_ref']}。"
            lines.append(bits)
        m20 = (ix.get("moving_averages") or {}).get("sma20") or {}
        if isinstance(m20.get("below_streak"), int) and m20["below_streak"] >= 1:
            lines.append(f"- 附註：連續 {m20['below_streak']} 日未站回 20 日線（不作為型態命名或確認條件）。")
        if ix["pattern_checks"]:
            for p in ix["pattern_checks"]:
                lines.append(f"- 形態檢查「{p['name']}」：已確認={p['confirmed']}。{p['opinion']} 要點：{json.dumps(p['facts'], ensure_ascii=False)}。")
        elif not (tri and tri.get("active")):
            lines.append("- 形態檢查：依原書雙重頂／底的間隔、中間回檔 15%–20%、以及收盤突破 3% 濾網，目前沒有達到「已確認」的項目。未達標準的形狀不當成形態。")
        lines.append("")
    conf = payload["confirmation"]
    lines.append("## 三者對照（印證）")
    lines.append("")
    lines.append("### 事實")
    for r in conf["table"]:
        lines.append(
            f"- {r['id']}：距 52 週收盤高點 {r['vs_close_high_pct']}%（高點日 {r['close_high_date']}）；"
            f"收盤在 50 日均線{'之上' if r['above_sma50'] else '之下'}、200 日均線{'之上' if r['above_sma200'] else '之下'}；"
            f"一年 {r['ret_252d']:+}%、21 日 {r['ret_21d']:+}%、5 日 {r['ret_5d']:+}%；"
            f"粗週線結構：{r['weekly_coarse_label']}；次級週線結構：{r['weekly_med_label']}。"
        )
    lines.append(f"- 三者都在 200 日均線之上：{conf['facts']['all_above_sma200']}；都在之下：{conf['facts']['all_below_sma200']}。")
    lines.append(f"- 粗週線結構是否同為多頭：{conf['facts']['coarse_all_bull']}；同為空頭：{conf['facts']['coarse_all_bear']}。")
    lines.append("")
    lines.append("### 觀察")
    lines.append(f"- {conf['opinion']}")
    lines.append("")
    c = payload["cobb"]
    lines.append("## COBB我大哥燈號（摘要）")
    lines.append("")
    lines.append(f"- 來源：{c['data_url']}；規則 {c['rules_version']}（{c['rules_url']}）。")
    lines.append(f"- 燈號查詢時間：{c['queried_at']}；相對上次（{c['previous_run']}）：{c['changes_vs_previous']}。")
    lines.append(f"- 官網說明：{c['framing']}")
    lines.append(f"- 時差提醒：{c['note_timing']}")
    lines.append(f"- 燈號計數：{json.dumps(c['status_counts'], ensure_ascii=False)}")
    for g in c["groups"]:
        ids = "、".join(f"{sid}.{next(s['name'] for s in c['signals'] if s['id']==sid)} {next(s['status'] for s in c['signals'] if s['id']==sid)}" for sid in g["ids"])
        lines.append(f"- {g['title']}：{ids}")
    lines.append("")
    lines.append("## 免責")
    lines.append("")
    lines.append("本頁只整理公開行情與既有燈號狀態，不是投資建議，也不保證任何報酬。技術形態與趨勢判斷可能失敗，市場有虧損本金的風險。")
    path.write_text("\n".join(lines), encoding="utf-8")


def home_pattern(ix: dict):
    """首頁型態挑選。已確認或形成中的下降直角三角形優先；否則仍是 A → B → C（升勢線）。

    頭肩、雙重頂、楔形沒有嚴格判定式，一律不給。
    """
    labels = {"down": "看跌", "up": "看漲", "mid": "中性"}
    tl = (ix.get("trendlines") or {}).get("rising_support_major")
    m = (ix.get("moving_averages") or {}).get("sma200")
    m20 = (ix.get("moving_averages") or {}).get("sma20")
    s = (ix.get("dow_weekly_coarse_swings") or {}).get("structure")
    tri = ix.get("descending_right_triangle")
    below = bool(tl) and (
        tl.get("close_is") == "below"
        or (tl.get("close_is") is None and isinstance(tl.get("close_vs_line_pct"), (int, float)) and tl["close_vs_line_pct"] < 0)
    )

    ev = []
    if tri and tri.get("active"):
        ev.append(
            f"下降直角三角形，狀態「{tri['status']}」；降勢線 {tri['h1']['date']} 的 {tri['h1']['price']} 連 "
            f"{tri['h2']['date']} 的 {tri['h2']['price']}；頸線（水平支撐）{tri['support']}，"
            f"收盤相對頸線 {tri['close_vs_support_pct']:+}%"
        )
        if tri.get("first_3pct_date"):
            ev.append(f"3% 確認日 {tri['first_3pct_date']} 收盤 {tri['first_3pct_close']}")
        if tri.get("target") is not None:
            ev.append(f"最小測算目標 {tri['target']}")
    if tl:
        side = "下" if below else "上"
        flt = "已達 3% 濾網" if tl.get("broken_by_3pct_filter") is True else "未達 3% 濾網"
        ev.append(f"收盤 {ix['last']['close']} 在升勢支撐線（約 {tl.get('value_at_last')}）{side}方 {abs(tl.get('close_vs_line_pct') or 0)}%，{flt}")
    else:
        ev.append("沒有可用的較長升勢支撐線")
    if m and isinstance(m.get("above"), bool):
        ev.append(f"相對 200 日線 {m.get('pct'):+}%（線{'上' if m['above'] else '下'}）")
    evidence = "；".join(ev) + f"。資料截至 {ix['last']['date']}。"
    sma20_note = None
    if m20 and isinstance(m20.get("below_streak"), int) and m20["below_streak"] >= 1:
        sma20_note = f"連續 {m20['below_streak']} 日未站回 20 日線"

    def out(rule, name, badge, icon, slug, confirmed, confidence, **extra):
        rec = {
            "rule": rule,
            "name": name,
            "badge": badge,
            "badge_label": labels[badge],
            "icon": icon,
            "slug": slug,
            "confirmed": confirmed,
            "confidence": confidence,
            "evidence": evidence,
        }
        if sma20_note:
            rec["sma20_note"] = sma20_note
        rec.update(extra)
        return rec

    # T. 已確認或形成中的下降直角三角形，優先於升勢線跌破標籤（2026-11-14，依 COBB 主高點讀法）
    if tri and tri.get("active"):
        st = tri["status"]
        confirmed = st in ("已確認跌破", "已確認跌破（反抽中）")
        return out(
            "T",
            f"下降直角三角形（{st}）",
            "down",
            "descTri",
            "desc-triangle",
            confirmed,
            "high" if confirmed else "medium",
            neckline=tri["support"],
            close_vs_neckline_pct=tri["close_vs_support_pct"],
            target=tri.get("target"),
            status=st,
        )

    # A. 收盤跌破升勢線且過 3% 濾網：書上的「有效跌破」
    if tl and tl.get("broken_by_3pct_filter") is True:
        return out("A", "上升趨勢線（跌破）", "down", "tlBreak", "rising-tl-break", True, "high")
    # B. 收在線下但沒到 3%：只是候選，不算完成
    if tl and below and tl.get("broken_by_3pct_filter") is False:
        return out("B", "可能跌破升勢線（未確認）", "mid", "tlBreak", "rising-tl-break", False, "medium")
    # C. 通用示意
    if m and m.get("above") is True and s and s.get("close_broke_last_swing_low") is False:
        return out("C", "上升趨勢中", "up", "rising", "rising-tl-break", False, "low")
    if m and m.get("above") is False:
        if tl and below:
            return out("C", "跌破升勢線", "down", "tlBreak", "rising-tl-break", False, "low")
        return out("C", "200日線下", "down", "below200", "moving-avg", False, "low")
    return None


def build(data_dir: Path, latest_path: Path, rules_path: Path, fetched_at_taipei: str, market_times: dict | None = None):
    """由 data_dir 內的行情 CSV 算出 (analysis, charts)。任何資料問題都以例外丟出，由呼叫端決定怎麼處理。"""
    data_dir = Path(data_dir)
    indices = {name: analyze_one(name, data_dir) for name in IDS}

    asofs = {k: ix["last"]["date"] for k, ix in indices.items()}
    if len(set(asofs.values())) != 1:
        raise ValueError(f"三個指數的最新交易日不一致：{asofs}")
    price_asof = asofs["SOX"]
    for k, ix in indices.items():
        ix["home_pattern"] = home_pattern(ix)

    # 牛熊三階段（價量代理，不影響燈號）。SOX 若有 SOXX 日線快取，用它的成交量當近似。
    soxx_path = data_dir / "SOXX_daily_2y.csv"
    soxx_vol = None
    if soxx_path.exists():
        soxx_df = load_csv(soxx_path)
        soxx_vol = soxx_df.set_index(soxx_df["date"].dt.strftime("%Y-%m-%d"))["volume"]
    for k, ix in indices.items():
        daily = load_csv(data_dir / f"{k}_daily_2y.csv")
        vol = None
        vol_src = None
        if k == "SOX":
            if soxx_vol is not None:
                vol = daily["date"].dt.strftime("%Y-%m-%d").map(soxx_vol)
                vol.index = daily.index
                vol_src = "SOXX ETF 日線成交量（近似；Yahoo ^SOX 成交量為 0）"
            else:
                vol_src = "Yahoo ^SOX 成交量為 0，且沒有 SOXX 快取，未驗量"
        else:
            vol = daily["volume"]
            vol_src = "指數日線成交量（Yahoo）"
        med = (ix.get("dow_weekly_medium_swings") or {}).get("structure")
        coarse = (ix.get("dow_weekly_coarse_swings") or {}).get("structure")
        ix["market_stage"] = MS.detect_market_stage(daily, k, med, coarse, volume=vol, volume_source=vol_src)
        vbp_vol = vol if vol is not None else daily["volume"]
        ix["volume_by_price"] = MS.volume_by_price(
            daily, volume=vbp_vol,
            source_label=vol_src or "每日收盤價×成交量",
        )
    stages = MS.summarize_stages({k: ix["market_stage"] for k, ix in indices.items()})

    confirmation = build_confirmation(indices)
    cobb = cobb_snapshot(latest_path, rules_path, price_asof)

    d = datetime.strptime(price_asof, "%Y-%m-%d").date()
    wk = indices["SOX"]["weekly_last_completed"]["week_start_monday"]
    market_times = {k: v for k, v in (market_times or {}).items() if v}
    if market_times:
        mt = "Yahoo meta.regularMarketTime（台北時間）：" + "、".join(f"{k} {market_times[k]}" for k in IDS if k in market_times) + "。"
    else:
        mt = ""
    price_asof_note = (
        f"美股 {price_asof}（週{WEEKDAY_ZH[d.weekday()]}）收盤價；還沒收盤的當日K不採用。{mt}"
        f"最後一根週K 起始日為 {wk}（涵蓋至 {price_asof[5:]}）；Yahoo 若多給一根非週一的重複週K 會先去掉。"
    )

    meta = {
        "source": SOURCE,
        "source_api": "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=...&interval=...",
        "tickers": SOURCE_TICKERS,
        "fetched_at_taipei": fetched_at_taipei,
        "fetched_at_taipei_daily2y": fetched_at_taipei,
        "price_asof": price_asof,
        "price_asof_weekday": "週" + WEEKDAY_ZH[d.weekday()],
        "price_asof_note": price_asof_note,
        "regular_market_time_taipei": market_times,
        "generated_by": "scripts/update_trend.py（GitHub Actions 自動更新）",
        "files": {
            "daily_1y": [f"{n}_daily_1y.csv" for n in IDS],
            "daily_2y": [f"{n}_daily_2y.csv" for n in IDS],
            "weekly_2y": [f"{n}_weekly_2y.csv" for n in IDS],
            "weekly_5y": [f"{n}_weekly_5y.csv" for n in IDS],
        },
        "method_notes": {
            "closes_only_for_dow": "道氏結構只用週線收盤價的擺動點。",
            "breakout_filter": "有效突破採用原書常見的收盤價超出界線約 3%。",
            "sma": "日線 SMA 20／50／200；週線 SMA 10／40（約對應兩個月與一年）。",
            "swings_daily_major": "日線明顯擺動：左右各 10 根 K 的局部高低。",
            "descending_right_triangle": "下降直角三角形（v2／COBB 主高點讀法，2026-11-14）：10／10 主高點連成降勢上界，中間高點不向上刺破該線超過 1% 即不算破壞；水平支撐取 H1 之後最低的 10／10 主低點，且之後至少再有一次日低點落入 2% 內的測試；收盤跌破支撐 3% 為確認；最小目標為 S−(H1−S)。已確認或形成中的下降直角三角形在首頁優先於「升勢線跌破」標籤。20 日均連續未站回只當附註，不參與命名。",
            "market_stage": "牛熊三階段參考指標（v1／2026-11-14，COBB Telegram 筆記＋Claude 規格）：以第3章精神做成價量代理，不是真正的道氏階段。空頭須同時滿足回撤≥20%、收盤低於200日線3%或連續10日在其下、週線擺動低點被收盤跌破≥3%；多頭須回撤<10%、收盤大於仍上揚的200日線、週線結構未被有效跌破。站回200日線則先降為無法判定。恐慌為10日收盤跌幅達 NDX／SPX 12%、SOX 18%。SOX 量用 SOXX 近似，沒有則未驗量。不影響燈號 v3.0。",
            "swings_weekly_medium": "週線收盤、左右各 3 根。",
            "swings_weekly_coarse": "週線收盤、左右各 8 根，用來觀察較長的主要結構。",
            "gaps": "當日低點高於前一日高點＝向上缺口；當日高點低於前一日低點＝向下缺口。後續曾交易進缺口區間視為已回補。",
        },
    }
    payload = {"meta": meta, "indices": indices, "confirmation": confirmation, "cobb": cobb, "stages": stages}

    # 圖表序列另外放（跟原本 analysis.json／chart_data.json 的切法相同）
    chart = {
        "meta": {k: meta[k] for k in ["source", "tickers", "fetched_at_taipei", "price_asof"]},
        "indices": {},
    }
    slim_indices = {}
    for k, ix in indices.items():
        chart["indices"][k] = {
            "chart_daily_1y": ix.pop("chart_daily_1y"),
            "chart_weekly_2y": ix.pop("chart_weekly_2y"),
            "chart_annotations": ix.pop("chart_annotations"),
            "trendlines": ix["trendlines"],
            "support_resistance": ix["support_resistance"],
            "last": ix["last"],
            "moving_averages": ix["moving_averages"],
            "range_52w": ix["range_52w"],
            "descending_right_triangle": ix.get("descending_right_triangle"),
        }
        slim_indices[k] = ix
    payload["indices"] = slim_indices
    return payload, chart
