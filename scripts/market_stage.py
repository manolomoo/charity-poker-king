"""牛熊三階段參考指標（2026-11-14，COBB Telegram 筆記＋Claude 規格）。

這是價格／量能的代理分類，不是書中用情緒與基本面界定的真正道氏階段。
不參與 11 項燈號 v3.0。條件不足時輸出「無法判定」。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

# ----- 自訂門檻（書中沒有寫死這些數字；3% 濾網是書的常用有效穿越） -----
BEAR_DD = 0.20          # 空頭：距 52 週收盤高回撤
BULL_DD = 0.10          # 多頭：回撤須小於這個
MA200_PCT = 0.03        # 空頭：收盤低於 200 日線的幅度
MA200_STREAK = 10       # 或連續在 200 日線下的交易日
MA200_SLOPE_BARS = 20   # 多頭：200 日線須高於 N 日前
WEEKLY_BREAK_PCT = 0.03 # 與原書有效跌破濾網相同
PANIC_LOOKBACK = 15     # 「近 15 日內觸發恐慌」
PANIC_WINDOW = 10       # 恐慌：10 日收盤跌幅
PANIC_TH = {"NDX": 0.12, "SPX": 0.12, "SOX": 0.18}
VOL_RATIO_BEAR = 1.3    # 近 20 日跌日均量／漲日均量
CLIMAX_MULT = 2.0       # 天量：單日量 ≥ 50 日均量的倍數
VOL_DAYS = 20
CLIMAX_MA = 50
POST_PANIC_MIN = 15     # 第二→第三階段？ 恐慌低點後
POST_PANIC_MAX = 40
STAGE3_DAYS = 40        # 第三階段：恐慌低點後
STAGE3_NO_PANIC_DAYS = 120  # 或全程無恐慌但峰值後
STAGE3_RANGE_FRAC = 0.60
STAGE3_RET20_LO = -0.10
STAGE3_RET20_HI = 0.0
VBP_BARS = 126
VBP_BINS = 20

VERSION = "v1-2026-11-14"
RULE_NOTE = (
    "依《股市趨勢技術分析》第3章牛熊三階段的精神，做成可計算的價量代理。"
    "回撤 20%／10%、200 日線、恐慌幅度與日數都是自訂門檻；3% 週線濾網是書中常用的有效穿越。"
    "這不是真正的道氏階段判定（書靠情緒與基本面），只供參考。"
)


def r2(x, n=2):
    if x is None or (isinstance(x, float) and (pd.isna(x) or not pd.notna(x))):
        return None
    return round(float(x), n)


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def weekly_low_broken_3(med: dict | None, coarse: dict | None) -> bool:
    """週線已確認擺動低點被收盤跌破 ≥ 3%，或已是空頭結構（低點創新低）。"""
    for s in (med, coarse):
        if not s:
            continue
        if s.get("close_broke_last_swing_low_by_3pct"):
            return True
        if s.get("bias") == "bear":
            return True
    return False


def weekly_hl_intact(med: dict | None, coarse: dict | None) -> bool:
    """多頭用：高低點墊高的結構尚未被 3% 濾網跌破，也還不是空頭結構。"""
    return not weekly_low_broken_3(med, coarse)


def _below_streak(close: pd.Series, ma: pd.Series) -> int:
    n = 0
    for c, m in zip(close.iloc[::-1], ma.iloc[::-1]):
        if pd.isna(m) or pd.isna(c):
            break
        if float(c) < float(m):
            n += 1
        else:
            break
    return n


def volume_side_stats(vol: pd.Series, close: pd.Series, n: int = VOL_DAYS) -> dict | None:
    """漲日／跌日均量。成交量全 0 或不足則回傳 None。"""
    if vol is None or close is None or len(vol) < n + 1:
        return None
    sl_c = close.iloc[-(n + 1):]
    sl_v = vol.reindex(sl_c.index)
    if sl_v.isna().all() or float(sl_v.fillna(0).sum()) <= 0:
        return None
    chg = sl_c.diff()
    # 對齊後去掉第一個 NaN
    mask_up = chg > 0
    mask_dn = chg < 0
    up = sl_v[mask_up].dropna()
    dn = sl_v[mask_dn].dropna()
    if len(up) == 0 or len(dn) == 0 or float(up.mean()) <= 0:
        return None
    ratio = float(dn.mean()) / float(up.mean())
    return {
        "days": n,
        "up_days": int(len(up)),
        "down_days": int(len(dn)),
        "vol_up": r2(float(up.mean()), 0),
        "vol_down": r2(float(dn.mean()), 0),
        "ratio_down_up": r2(ratio, 2),
        "bearish_volume": ratio >= VOL_RATIO_BEAR,
        "bullish_volume": ratio <= (1 / VOL_RATIO_BEAR),
    }


def climax_days(vol: pd.Series, multiple: float = CLIMAX_MULT, ma_n: int = CLIMAX_MA) -> list[dict]:
    if vol is None or len(vol) < ma_n + 1:
        return []
    avg = sma(vol, ma_n)
    out = []
    for i in range(len(vol)):
        v, a = vol.iloc[i], avg.iloc[i]
        if pd.isna(v) or pd.isna(a) or float(a) <= 0:
            continue
        if float(v) >= multiple * float(a):
            out.append({"index": int(i), "multiple": r2(float(v) / float(a), 2)})
    return out


def find_panic_events(close: pd.Series, threshold: float) -> list[dict]:
    """每一個結束日，若過去 10 根收盤相對其窗口最高收盤下跌達到門檻，記一次恐慌。"""
    events = []
    if close is None or len(close) < PANIC_WINDOW:
        return events
    values = close.astype(float)
    for i in range(PANIC_WINDOW - 1, len(values)):
        window = values.iloc[i - (PANIC_WINDOW - 1): i + 1]
        peak_i = int(window.idxmax())
        low_i = int(window.idxmin())
        peak = float(window.max())
        if peak <= 0:
            continue
        dd = float(values.iloc[i]) / peak - 1.0
        if dd <= -threshold:
            events.append({
                "end_index": i,
                "peak_index": values.index.get_loc(peak_i) if peak_i in values.index else i,
                "low_index": values.index.get_loc(low_i) if low_i in values.index else i,
                "peak_pos": int(window.values.argmax()),
                "low_pos": int(window.values.argmin()),
                "drop": dd,
                "peak_close": peak,
                "low_close": float(window.min()),
            })
    return events


def _iloc_date(df: pd.DataFrame, i: int) -> str | None:
    if i is None or i < 0 or i >= len(df):
        return None
    d = df.iloc[i]["date"]
    return str(d.date()) if hasattr(d, "date") else str(d)[:10]


def volume_by_price(
    daily: pd.DataFrame,
    volume: pd.Series | None = None,
    bars: int = VBP_BINS and VBP_BARS,
    n_bins: int = VBP_BINS,
    source_label: str = "該指數日線收盤價×成交量",
) -> dict:
    """用每日收盤價×成交量近似籌碼分布（不是盤中 volume profile）。"""
    sl = daily.tail(VBP_BARS).copy()
    if volume is not None:
        vol = volume.reindex(sl.index)
    else:
        vol = sl["volume"]
    vol = vol.fillna(0).astype(float)
    if float(vol.sum()) <= 0:
        return {
            "available": False,
            "reason": "這段期間成交量為 0 或沒有可用的量（Yahoo ^SOX 成交量欄是 0）。",
            "source_label": source_label,
        }
    px = sl["close"].astype(float)
    lo, hi = float(px.min()), float(px.max())
    if hi <= lo:
        return {"available": False, "reason": "價格沒有區間可分箱。", "source_label": source_label}
    # 右邊界包含最高價
    bins = [lo + (hi - lo) * k / n_bins for k in range(n_bins)] + [hi]
    amounts = [0.0] * n_bins
    for price, v in zip(px, vol):
        if v <= 0:
            continue
        # 最高價歸最後一箱
        k = min(n_bins - 1, int((price - lo) / (hi - lo) * n_bins)) if hi > lo else 0
        amounts[k] += float(v)
    total = sum(amounts) or 1.0
    last = float(daily["close"].iloc[-1])
    rows = []
    poc_i = max(range(n_bins), key=lambda i: amounts[i])
    for i in range(n_bins):
        rows.append({
            "low": r2(bins[i], 2),
            "high": r2(bins[i + 1], 2),
            "volume": int(round(amounts[i])),
            "share": r2(amounts[i] / total, 4),
            "is_poc": i == poc_i,
            "contains_last": bins[i] <= last <= bins[i + 1] or (i == n_bins - 1 and last == bins[i + 1]),
        })
    return {
        "available": True,
        "source_label": source_label,
        "bars": int(len(sl)),
        "from_date": _iloc_date(sl, 0),
        "to_date": _iloc_date(sl, len(sl) - 1),
        "bin_count": n_bins,
        "poc": {"low": rows[poc_i]["low"], "high": rows[poc_i]["high"], "share": rows[poc_i]["share"]},
        "bins": rows,
        "note": "用每日收盤價×當日成交量近似，不是盤中真實的成交量分布。指數或 ETF 的量只能當參考。",
    }


def detect_market_stage(
    daily: pd.DataFrame,
    name: str,
    weekly_med: dict | None = None,
    weekly_coarse: dict | None = None,
    volume: pd.Series | None = None,
    volume_source: str | None = None,
) -> dict:
    """對單一指數、只用截至最後一根已完成日K 的資料，輸出階段判定。"""
    daily = daily.reset_index(drop=True)
    close = daily["close"].astype(float)
    last = float(close.iloc[-1])
    last_date = _iloc_date(daily, len(daily) - 1)
    s20 = sma(close, 20)
    s50 = sma(close, 50)
    s200 = sma(close, 200)
    sma20 = float(s20.iloc[-1]) if pd.notna(s20.iloc[-1]) else None
    sma50 = float(s50.iloc[-1]) if pd.notna(s50.iloc[-1]) else None
    sma200 = float(s200.iloc[-1]) if pd.notna(s200.iloc[-1]) else None
    sma200_ago = None
    if len(s200) > MA200_SLOPE_BARS and pd.notna(s200.iloc[-(MA200_SLOPE_BARS + 1)]):
        sma200_ago = float(s200.iloc[-(MA200_SLOPE_BARS + 1)])
    below_200 = _below_streak(close, s200) if sma200 is not None else 0

    win = close.tail(min(252, len(close)))
    peak_close = float(win.max())
    peak_idx_label = win.idxmax()
    peak_pos = int(close.index.get_loc(peak_idx_label)) if peak_idx_label in close.index else int(len(close) - len(win) + win.values.argmax())
    # after reset_index, idxmax is the integer position in `win` which equals position in daily if win is a tail slice with original index
    # safer:
    peak_pos = int(len(daily) - len(win) + list(win.values).index(peak_close))
    peak_date = _iloc_date(daily, peak_pos)
    dd = last / peak_close - 1.0 if peak_close else 0.0
    days_from_peak = int(len(daily) - 1 - peak_pos)

    wk_broken = weekly_low_broken_3(weekly_med, weekly_coarse)
    wk_intact = weekly_hl_intact(weekly_med, weekly_coarse)

    below_200_enough = False
    if sma200:
        below_200_enough = (last <= sma200 * (1 - MA200_PCT)) or (below_200 >= MA200_STREAK)

    cond_bear = {
        "drawdown_ge_20": dd <= -BEAR_DD,
        "below_200_3pct_or_10d": bool(below_200_enough),
        "weekly_low_broken_3pct": bool(wk_broken),
    }
    slope_up = (sma200 is not None and sma200_ago is not None and sma200 > sma200_ago)
    cond_bull = {
        "drawdown_lt_10": dd > -BULL_DD,
        "above_200_and_rising": bool(sma200 is not None and last > sma200 and slope_up),
        "weekly_structure_intact": bool(wk_intact),
    }

    regime = "無法判定"
    if all(cond_bear.values()):
        regime = "空頭"
    elif all(cond_bull.values()):
        regime = "多頭"

    # 空頭成立後，只要收盤站回 200 日線，立刻降為無法判定（不直接翻多）
    if regime == "空頭" and sma200 is not None and last > sma200:
        regime = "無法判定"
        regime_note = "先前空頭條件曾可能成立，但收盤已站回 200 日線，先降為無法判定。"
    else:
        regime_note = None
    # 上面這段在「當天同時滿足空頭三條件又站上 200」時不會發生（互相矛盾）。
    # 真正的「解除」要靠歷史狀態；即時判定若已站上 200，below_200_enough 為假，不會標空頭。
    # 保留註記欄位給頁面說明規則。

    # 量
    vol_series = volume
    if vol_series is None and "volume" in daily.columns:
        vol_series = daily["volume"]
    vol_stats = volume_side_stats(vol_series, close) if vol_series is not None else None
    vol_available = vol_stats is not None
    climax = climax_days(vol_series) if vol_available else []

    # 恐慌
    th = PANIC_TH.get(name, 0.12)
    events = find_panic_events(close, th)
    last_i = len(close) - 1
    recent = [e for e in events if last_i - e["end_index"] <= PANIC_LOOKBACK - 1]
    last_event = events[-1] if events else None
    panic_now = bool(recent)
    panic_info = None
    if last_event:
        # 用最近一次恐慌（若近 15 日有，用最近一次近 15 日的）
        ev = recent[-1] if recent else last_event
        # 修正 index：find_panic 用的是 0..n-1 位置（reset 後 iloc）
        panic_info = {
            "threshold": th,
            "drop": r2(ev["drop"] * 100, 2),
            "end_date": _iloc_date(daily, ev["end_index"]),
            "peak_date": _iloc_date(daily, ev["end_index"] - (PANIC_WINDOW - 1) + ev["peak_pos"]),
            "low_date": _iloc_date(daily, ev["end_index"] - (PANIC_WINDOW - 1) + ev["low_pos"]),
            "days_since_end": int(last_i - ev["end_index"]),
            "days_since_low": int(last_i - (ev["end_index"] - (PANIC_WINDOW - 1) + ev["low_pos"])),
            "peak_close": r2(ev["peak_close"]),
            "low_close": r2(ev["low_close"]),
            "in_lookback": ev in recent if recent else (last_i - ev["end_index"] <= PANIC_LOOKBACK - 1),
        }
        # 天量是否出現在這段恐慌窗口或近 15 日
        if climax:
            lo = ev["end_index"] - (PANIC_WINDOW - 1)
            hi = last_i
            hits = [c for c in climax if lo <= c["index"] <= hi]
            panic_info["climax"] = bool(hits)
            if hits:
                panic_info["climax_date"] = _iloc_date(daily, hits[-1]["index"])
                panic_info["climax_multiple"] = hits[-1]["multiple"]
        else:
            panic_info["climax"] = None

    # 近 20 日振幅與報酬
    sl20 = daily.tail(20)
    range20 = (float(sl20["high"].max()) - float(sl20["low"].min())) / last if last else 0.0
    ret20 = float(close.iloc[-1]) / float(close.iloc[-21]) - 1.0 if len(close) >= 21 else None

    stage = "無法判定"
    stage_code = "undetermined"
    stage_detail = []
    annotations = []

    def panic_range(ev) -> float:
        return abs(ev["drop"])  # 已是比例

    if regime == "空頭":
        if panic_now and panic_info:
            days_low = panic_info["days_since_low"]
            ev = recent[-1]
            if days_low < POST_PANIC_MIN:
                stage = "第二階段（恐慌）"
                stage_code = "bear_2"
                stage_detail.append(f"近 {PANIC_LOOKBACK} 日內出現 {PANIC_WINDOW} 日收盤跌幅 {panic_info['drop']}%，達到門檻 {int(th*100)}%。")
                if panic_info.get("climax"):
                    stage_detail.append(f"期間出現天量（約 {panic_info.get('climax_multiple')} 倍 50 日均量）。")
                else:
                    if not vol_available:
                        annotations.append("未驗量")
            elif POST_PANIC_MIN <= days_low <= POST_PANIC_MAX and sma50 is not None and last < sma50:
                # 區間振幅 ≤ 恐慌段跌幅的一半
                if range20 <= 0.5 * panic_range(ev) and last < sma50:
                    stage = "第二→第三階段？"
                    stage_code = "bear_2_to_3"
                    stage_detail.append(f"恐慌低點（{panic_info['low_date']}）已過 {days_low} 個交易日，尚未站回 50 日線，近 20 日振幅相對恐慌段收斂。")
                else:
                    stage = "第二階段（恐慌）"
                    stage_code = "bear_2"
                    stage_detail.append("恐慌已過但尚未出現書中所說、足夠收斂的橫盤，仍視為恐慌期或其後的反彈。")
            else:
                # 可能第三階段
                ev = last_event
                days_low = panic_info["days_since_low"] if panic_info else 0
                slow = (
                    sma50 is not None and sma200 is not None
                    and last < sma50 < sma200
                    and range20 <= STAGE3_RANGE_FRAC * panic_range(ev)
                    and ret20 is not None and STAGE3_RET20_LO <= ret20 <= STAGE3_RET20_HI
                )
                if days_low >= STAGE3_DAYS and slow:
                    stage = "第三階段（灰心）"
                    stage_code = "bear_3"
                    stage_detail.append(f"恐慌低點已過 {days_low} 個交易日，均線空頭排列，近 20 日跌勢相對緩和。")
                elif sma50 is not None and last >= sma50 and sma200 is not None and last < sma200:
                    stage = "無法判定"
                    stage_code = "stage_undetermined"
                    stage_detail.append("已站回 50 日線但尚未站回 200 日線，次級反彈與新牛市第一階段此時無法區分。")
                else:
                    stage = "無法判定"
                    stage_code = "stage_undetermined"
                    stage_detail.append("空頭已成立，但不符合第三階段的緩跌條件，也已離開近 15 日恐慌窗口。")
        else:
            # 無近期恐慌：可能跳過第二階段
            no_panic_since_peak = True
            if events:
                no_panic_since_peak = all(e["end_index"] < peak_pos for e in events)
            slow = (
                sma50 is not None and sma200 is not None
                and last < sma50 < sma200
                and ret20 is not None and STAGE3_RET20_LO <= ret20 <= STAGE3_RET20_HI
            )
            if no_panic_since_peak and days_from_peak >= STAGE3_NO_PANIC_DAYS and slow:
                stage = "第三階段（灰心）"
                stage_code = "bear_3"
                stage_detail.append(f"峰值後已 {days_from_peak} 個交易日，全程沒有達到恐慌門檻，依規則允許跳過第二階段。")
            elif sma50 is not None and last >= sma50 and sma200 is not None and last < sma200:
                stage = "無法判定"
                stage_code = "stage_undetermined"
                stage_detail.append("已站回 50 日線但尚未站回 200 日線。")
            else:
                stage = "無法判定"
                stage_code = "stage_undetermined"
                stage_detail.append("空頭已成立，但沒有近 15 日的恐慌，也不符合「無恐慌卻走完 120 日緩跌」的第三階段條件。")
    elif regime == "多頭":
        # 第一階段不即時確認
        above_50_200 = sma50 is not None and sma200 is not None and last > sma50 > sma200
        up_vol = bool(vol_stats and (vol_stats["ratio_down_up"] is not None) and vol_stats["ratio_down_up"] < 1)
        # 跳水頻繁
        rets = close.pct_change().tail(60)
        air_pockets = int((rets <= -0.025).sum()) if len(rets) else 0
        vol_expanding = False
        if vol_available and len(vol_series) >= 60:
            v1 = float(vol_series.iloc[-60:-30].mean() or 0)
            v2 = float(vol_series.iloc[-30:].mean() or 0)
            vol_expanding = v1 > 0 and v2 > v1 * 1.1
        if air_pockets >= 4 and vol_expanding:
            stage = "第三階段？"
            stage_code = "bull_3"
            stage_detail.append(f"近 60 個交易日出現 {air_pockets} 次單日跌幅 ≥ 2.5%，且近期均量放大。民眾、IPO、垃圾股沒有資料，故永遠帶問號。")
        elif above_50_200 and (up_vol or not vol_available):
            stage = "第二階段（穩步向上）"
            stage_code = "bull_2"
            if not vol_available:
                annotations.append("未驗量")
                stage_detail.append("收盤在 50 日線與 200 日線之上，且 50 日高於 200 日。量能條件因無量或未驗量而省略。")
            else:
                stage_detail.append("收盤 > 50 日線 > 200 日線，且漲日均量大於跌日均量。")
        else:
            stage = "無法判定"
            stage_code = "stage_undetermined"
            stage_detail.append("多頭條件成立，但還不符合第二階段（收盤須大於 50 日且 50 日大於 200 日）或第三階段代理條件。第一階段無法即時確認。")
            if sma200 is not None and sma200_ago is not None and sma200 < sma200_ago:
                annotations.append("牛一？")
    else:
        stage = "無法判定"
        stage_code = "regime_undetermined"
        reasons = []
        if not cond_bear["drawdown_ge_20"]:
            reasons.append(f"回撤 {r2(dd*100, 2)}% 未達空頭門檻 20%")
        if not cond_bear["below_200_3pct_or_10d"]:
            pct200 = r2((last / sma200 - 1) * 100, 2) if sma200 else None
            reasons.append(f"相對 200 日線 {pct200}%（需低於 3% 或連續 10 日在其下，目前連續 {below_200} 日）")
        if not cond_bear["weekly_low_broken_3pct"]:
            reasons.append("週線擺動低點尚未被收盤有效跌破 3%")
        if not cond_bull["drawdown_lt_10"]:
            reasons.append(f"回撤 {r2(dd*100, 2)}% 已不小於 10%，不算多頭")
        if not cond_bull["above_200_and_rising"]:
            reasons.append("收盤未同時站上仍上揚的 200 日線")
        if not cond_bull["weekly_structure_intact"]:
            reasons.append("週線結構已被有效跌破，不算多頭")
        stage_detail.append("空頭與多頭的三項條件都未全部成立。" + "；".join(reasons[:4]) + "。")
        # 出貨疑慮：接近高點、漲日量相對跌日量偏弱
        if dd > -0.12 and vol_stats and vol_stats.get("bearish_volume"):
            annotations.append("出貨疑慮？")
            stage_detail.append("價格仍靠近高點，但近 20 日跌日均量已明顯大於漲日均量，只作出貨疑慮附註，不是已確認的第一階段。")

    # 標籤
    if regime == "空頭":
        headline = f"空頭・{stage}" if stage != "無法判定" else "空頭・階段無法判定"
    elif regime == "多頭":
        headline = f"多頭・{stage}" if stage != "無法判定" else "多頭・階段無法判定"
    else:
        headline = "無法判定"

    conditions_met = []
    conditions_unmet = []

    def add_cond(group, key, label, ok):
        (conditions_met if ok else conditions_unmet).append({"group": group, "key": key, "label": label})

    add_cond("空頭", "dd20", f"回撤 {r2(dd*100, 2)}% ≥ 20%", cond_bear["drawdown_ge_20"])
    pct200 = r2((last / sma200 - 1) * 100, 2) if sma200 else None
    add_cond("空頭", "ma200", f"低於 200 日線 3% 或連續 10 日（現 {pct200}%，連續 {below_200} 日）", cond_bear["below_200_3pct_or_10d"])
    add_cond("空頭", "weekly", "週線擺動低點被收盤跌破 ≥ 3%", cond_bear["weekly_low_broken_3pct"])
    add_cond("多頭", "dd10", f"回撤 {r2(dd*100, 2)}% < 10%", cond_bull["drawdown_lt_10"])
    add_cond("多頭", "ma200up", "收盤大於仍上揚的 200 日線", cond_bull["above_200_and_rising"])
    add_cond("多頭", "weekly", "週線高低點墊高未被有效跌破", cond_bull["weekly_structure_intact"])

    return {
        "version": VERSION,
        "asof": last_date,
        "name": name,
        "regime": regime,
        "stage": stage,
        "stage_code": stage_code,
        "headline": headline,
        "label": headline,  # 給首頁用
        "detail": stage_detail,
        "annotations": annotations,
        "regime_note": regime_note,
        "rule_note": RULE_NOTE,
        "evidence": {
            "close": r2(last),
            "peak_close": r2(peak_close),
            "peak_date": peak_date,
            "drawdown_pct": r2(dd * 100, 2),
            "days_from_peak": days_from_peak,
            "sma20": r2(sma20),
            "sma50": r2(sma50),
            "sma200": r2(sma200),
            "vs_sma200_pct": pct200,
            "below_sma200_streak": below_200,
            "sma200_slope_up": slope_up,
            "above_sma50": bool(sma50 is not None and last > sma50),
            "above_sma200": bool(sma200 is not None and last > sma200),
            "sma50_vs_sma200": (r2((sma50 / sma200 - 1) * 100, 2) if sma50 and sma200 else None),
            "weekly_low_broken_3pct": wk_broken,
            "range20_pct": r2(range20 * 100, 2),
            "return20_pct": r2(ret20 * 100, 2) if ret20 is not None else None,
            "volume": vol_stats,
            "volume_source": volume_source or ("指數日線成交量" if vol_available else None),
            "volume_available": vol_available,
            "panic": panic_info,
            "panic_in_lookback": panic_now,
            "panic_threshold": th,
        },
        "conditions_met": conditions_met,
        "conditions_unmet": conditions_unmet,
        "conditions_bear": cond_bear,
        "conditions_bull": cond_bull,
    }


def summarize_stages(per_index: dict[str, dict]) -> dict:
    """總覽：SOX／NDX 為主，SPX 對照；兩者不一致時加註未互相確認。"""
    sox = per_index.get("SOX") or {}
    ndx = per_index.get("NDX") or {}
    spx = per_index.get("SPX") or {}
    confirm_note = None
    if sox.get("regime") and ndx.get("regime") and sox["regime"] != ndx["regime"]:
        confirm_note = (
            f"SOX 判定為「{sox['regime']}」，NDX 判定為「{ndx['regime']}」，"
            "未獲另一指數確認（這是借用書中兩指數互證的原則，只當標籤）。"
        )
    parts = []
    for name in ("SOX", "NDX", "SPX"):
        s = per_index.get(name)
        if s:
            parts.append(f"{name} {s['headline']}")
    home_line = "；".join(parts) if parts else "無法判定"
    return {
        "version": VERSION,
        "rule_note": RULE_NOTE,
        "home_line": home_line,
        "confirmation_note": confirm_note,
        "cobb_view": "COBB 看法（2026-11-14 Telegram）：現在應該就是要進入熊市第三階段；巨頭、台積這類績優股確實緩跌，垃圾股早就死了。當代最有意義的指數是 NDX 與 SOX。",
        "primary": ["NDX", "SOX"],
        "reference": ["SPX"],
        "asof": sox.get("asof") or ndx.get("asof"),
    }


def ev_table(rewards=None, win_rates=None, risk=100):
    """盈虧比表：每格 = 勝率×報酬 − (1−勝率)×風險。報酬是贏的時候拿到的點數（含 100:100 的 100）。"""
    if rewards is None:
        rewards = list(range(100, 1001, 50))
    if win_rates is None:
        win_rates = [round(x, 2) for x in [i / 100 for i in range(20, 61, 5)]]
    rows = []
    for reward in rewards:
        cells = []
        for w in win_rates:
            ev = w * reward - (1 - w) * risk
            cells.append({"win": w, "ev": r2(ev, 2), "negative": ev < 0, "zero": abs(ev) < 1e-9})
        rows.append({"reward": reward, "ratio": f"{reward}:{risk}", "cells": cells})
    return {"risk": risk, "win_rates": win_rates, "rows": rows, "formula": "勝率×報酬 − (1−勝率)×風險"}
