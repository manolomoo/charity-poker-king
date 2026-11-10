"""規則 v2（2026-11-10，依 COBB 指正）：第 2、5、6、9 項的新判定。
由 run.py 匯入；共用 run.py 的 row()、daily()、completed_daily()、P()。
"""
import io, json, os, datetime as dt
import numpy as np, pandas as pd
from common import *

# ---------- 共用：美股「回檔區間」（第 2、5 項的『對的位置』）----------
def us_episode(completed_daily, p):
    """回檔區間＝NDX 最近一次 252 日收盤新高之後的交易日（直到再創新高才結束）。
    回傳 (ndx_close, peak_date, dd_series)；dd_series 為每天相對『當時 252 日最高收盤』的回落 %。"""
    c = completed_daily("^NDX", NY, (16, 15))["Close"]
    roll = c.rolling(252, min_periods=60).max()
    dd = (c / roll - 1) * 100
    is_high = c >= roll
    peak = c.index[is_high.values.nonzero()[0][-1]]
    return c, peak, dd

def latch_eval(series, dd, peak, hit, near, dd_min):
    """series: 指標日值（index=日期）；hit/near: 函數 v->bool。
    只計算回檔區間（peak 之後）且當天 NDX 回落 ≥ dd_min% 的日子（『對的位置』）。"""
    s = series[(series.index > peak)]
    ddx = dd.reindex(s.index, method="ffill")
    ok = s[ddx <= dd_min]
    hits = ok[ok.apply(hit)]
    nears = ok[ok.apply(near)]
    return s, ok, hits, nears

# ---------- 2. CNN F&G（v2：回檔區間內曾 <25 即觸發）----------
def sig2_v2(row, completed_daily, P):
    p = P("2"); start = (now_tpe() - dt.timedelta(days=400)).strftime("%Y-%m-%d")
    url = f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}"
    src = ["https://edition.cnn.com/markets/fear-and-greed", url]
    thr = f"NDX 回檔區間內（自 52 週收盤新高起、當天 NDX 回落 ≥{abs(p['dd_min'])}%）任一已收盤日 < {p['trigger']}"
    try:
        d = get(url, headers={"Referer": "https://edition.cnn.com/", "Origin": "https://edition.cnn.com"}).json()
        save_raw("cnn_fng.json", json.dumps(d))
        fg = d["fear_and_greed"]; sc = fg["score"]
        pts = [(dt.datetime.fromtimestamp(x["x"] / 1000, dt.timezone.utc).date(), float(x["y"])) for x in d["fear_and_greed_historical"]["data"]]
        ser = pd.Series({pd.Timestamp(k): v for k, v in pts}).groupby(level=0).last().sort_index()
        # 只用已收盤日：美東今天未過 16:15 則捨棄今天（含盤中值）
        now = dt.datetime.now(NY); cut = pd.Timestamp(now.date())
        ser = ser[ser.index < cut] if (now.hour, now.minute) < (16, 15) else ser[ser.index <= cut]
        ndx, peak, dd = us_episode(completed_daily, p)
        ser = ser[ser.index <= ndx.index[-1]]
        s, ok, hits, nears = latch_eval(ser, dd, peak, lambda v: v < p["trigger"], lambda v: v < p["near"], p["dd_min"])
        st = RED if len(hits) else YEL if len(nears) else WHT
        last_d, last_v = ser.index[-1], ser.iloc[-1]
        mn = s.idxmin() if len(s) else None
        hit_txt = "、".join(f"{i.strftime('%m/%d')} {v:.1f}" for i, v in hits.items()) or "無"
        val = (f"最新收盤日 {last_v:.1f}（{last_d.strftime('%m/%d')}；盤中 {sc:.1f}）；回檔區間自 {peak.strftime('%Y-%m-%d')}（NDX 52 週收盤高點）起，"
               f"區間最低 {s.min():.1f}（{mn.strftime('%m/%d')}）；符合位置且 <{p['trigger']} 的日子：{hit_txt}；NDX 目前回落 {dd.iloc[-1]:.1f}%")
        return [row("2", "CNN 恐懼貪婪指數", val, thr, st, last_d.strftime("%Y-%m-%d"), src,
                    "v2：觸發後維持到 NDX 再創 52 週收盤新高為止（回檔區間結束）。CNN 未公開 API，結構可能變動；日值取每日最後一筆，已收盤日才計入。",
                    dict(score_live=sc, last_close=last_v, last_date=last_d.strftime("%Y-%m-%d"), episode_start=peak.strftime("%Y-%m-%d"),
                         window_min=float(s.min()), window_min_date=mn.strftime("%Y-%m-%d"), hit_dates=[i.strftime("%Y-%m-%d") for i in hits.index],
                         ndx_dd_now=float(dd.iloc[-1]), current_reading_status=(RED if last_v < p["trigger"] else YEL if last_v < p["near"] else WHT)))]
    except Exception as e:
        return [row("2", "CNN 恐懼貪婪指數", None, thr, UNK, None, src, f"抓取失敗：{e}")]

# ---------- 5. VIX / VXN（v2：COBB 絕對門檻，回檔區間內盤中最高）----------
def sig5_v2(row, completed_daily, P):
    p = P("5"); rows = []
    try:
        ndx, peak, dd = us_episode(completed_daily, p); ep_err = None
    except Exception as e:
        peak = None; ep_err = e
    for s, fred in (("VIX", "VIXCLS"), ("VXN", "VXNCLS")):
        url = f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{s}_History.csv"
        panic, extreme, near = p[f"{s.lower()}_panic"], p[f"{s.lower()}_extreme"], p[f"{s.lower()}_panic"] - p["near_gap"]
        thr = f"回檔區間內盤中最高 > {panic} 恐慌（> {extreme} 極度恐慌）；接近：{near:g}–{panic}"
        try:
            if peak is None: raise RuntimeError(f"NDX 回檔區間計算失敗：{ep_err}")
            c = get(url).text; save_raw(f"cboe_{s}.csv", c)
            x = pd.read_csv(io.StringIO(c)); x["DATE"] = pd.to_datetime(x["DATE"]); x = x.set_index("DATE")
            hi = x["HIGH"].dropna(); cl = x["CLOSE"].dropna(); last, ld = cl.iloc[-1], cl.index[-1]
            w, ok, hits, nears = latch_eval(hi, dd, peak, lambda v: v > panic, lambda v: v >= near, p["dd_min"])
            if len(ok) == 0:
                st = WHT; mx_v, mx_d = (w.max(), w.idxmax()) if len(w) else (None, None)
            else:
                mx_d = ok.idxmax(); mx_v = ok.max()
                st = RED if len(hits) else YEL if len(nears) else WHT
            tag = "極度恐慌" if (mx_v is not None and mx_v > extreme) else "恐慌" if (mx_v is not None and mx_v > panic) else "未達恐慌"
            whole_max = w.max() if len(w) else None
            val = (f"本波（自 {peak.strftime('%Y-%m-%d')} NDX 52 週收盤高點起、NDX 回落 ≥{abs(p['dd_min'])}% 的日子）盤中最高 {mx_v:.2f}（{mx_d.strftime('%m/%d')}）→ {tag}；"
                   f"最新收盤 {last:.2f}（{ld.strftime('%m/%d')}）" if mx_v is not None else f"本波尚無符合位置的交易日；最新收盤 {last:.2f}")
            rows.append(row("5", s, val, thr, st, ld.strftime("%Y-%m-%d"), [url, f"https://fred.stlouisfed.org/series/{fred}"],
                "門檻為 COBB 標準（VIX >30 恐慌、>40 極度恐慌；VXN >35 恐慌、>45 極度恐慌），看本波盤中最高。COBB 提醒：恐慌指數低不代表高點；高檔只是抄底分批布局的訊號之一。觸發後維持到 NDX 再創 52 週收盤新高。",
                dict(window_high=float(mx_v) if mx_v is not None else None, window_high_date=mx_d.strftime("%Y-%m-%d") if mx_d is not None else None, label=tag,
                     close=float(last), episode_start=peak.strftime("%Y-%m-%d"), hit_dates=[i.strftime("%Y-%m-%d") for i in hits.index])))
        except Exception as e:
            rows.append(row("5", s, None, thr, UNK, None, [url], f"失敗：{e}"))
    return rows

# ---------- 6. 大盤融資維持率（不含 ETF，自算）----------
def _mr_cache(date):
    from margin_ratio import compute
    cdir = os.path.join(DATA, "cache", "margin_ratio"); os.makedirs(cdir, exist_ok=True)
    fp = os.path.join(cdir, date + ".json")
    if os.path.exists(fp): return json.load(open(fp))
    r = compute(date)
    if r and (date < now_tpe().strftime("%Y%m%d") or now_tpe().hour >= 23):  # 當天資料晚上才定案，23 點前不快取
        json.dump(r, open(fp, "w"))
    return r

def sig6_v2(row, P):
    p = P("6"); rows = []
    TWM = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d}&selectType=ALL&response=json"
    src = ["https://www.wantgoo.com/stock/margin-trading/exclude-etf/taiex", "https://www.twse.com.tw/zh/trading/margin/mi-margn.html", "https://www.twse.com.tw/zh/trading/historical/mi-index.html",
           "https://www.tpex.org.tw/zh-tw/mainboard/trading/margin-trading/transactions.html"]
    thr = f"< {p['trigger']}%（接近：{p['trigger']}–{p['near']}%）"
    try:
        days = [d for d in pd.bdate_range(end=now_tpe().date(), periods=p["lookback_days"] + 8)][::-1]
        got = []
        for d in days:
            r = _mr_cache(d.strftime("%Y%m%d"))
            if r: got.append(r)
            if len(got) >= p["lookback_days"]: break
        if not got: raise RuntimeError("近期查無證交所融資資料")
        got = got[::-1]; last = got[-1]; key = "twse_" + p["primary_variant"]; k = last[key]
        st = RED if k < p["trigger"] else YEL if k < p["near"] else WHT
        mn = min(got, key=lambda x: x[key])
        fmt = lambda s: f"{s[4:6]}/{s[6:]}"
        recent = "、".join(f"{fmt(x['date'])} {x[key]:.2f}%" for x in got[-5:])
        ld = f"{last['date'][:4]}-{last['date'][4:6]}-{last['date'][6:]}"
        rows.append(row("6", "大盤融資維持率（扣除ETF，自算對照值）", f"{k:.2f}%；近5日 {recent}；近{len(got)}日最低 {mn[key]:.2f}%（{fmt(mn['date'])}）；另一口徑（{'只排ETF' if key.endswith('narrow') else '窄口徑'}）{last['twse_ex_etf' if key.endswith('narrow') else 'twse_narrow']:.2f}%",
            thr, st, ld, src,
            "自算對照值，標準以玩股網「扣除ETF資券進出行情」為準（玩股網頁面有 Cloudflare 驗證，程式無法讀取）。" + ("口徑＝窄口徑：分子只計上市本國普通股（排除 ETF／ETN／受益憑證／特別股、-KY／DR、當日停資 X）" if key.endswith("narrow") else "口徑＝只排 ETF：分子排除代號非 4 位數字者（ETF／ETN／受益憑證／特別股）") + "；公式 Σ(融資餘額張數×1000×收盤價) ÷ 證交所公布上市融資金額 ×100%；分母為官方總額（含 ETF 借款，官方不公布個別證券融資金額），所以比『含ETF』版本低。窄口徑與論壇流傳的『扣除ETF 融資維持率』（10/30–11/06）相差約 0.5–3.3 個百分點，是目前最接近玩股網數字的版本（通常略高於玩股網 0.5–3.3 個百分點），但算法屬第三方逆向推估、未經官方證實。",
            dict(variant=p["primary_variant"], value=k, ex_etf_only=last["twse_ex_etf"], narrow=last["twse_narrow"], recent=[(x["date"], x[key]) for x in got], min=mn[key], min_date=mn["date"])))
        rows.append(row("6", "上市櫃融資維持率（不含ETF，自算，參考）", f"{last['both_ex_etf']:.2f}%（上櫃 {last['tpex_ex_etf']:.2f}%）" if "both_ex_etf" in last else None,
            "不參與判定", WHT if "both_ex_etf" in last else UNK, ld, src, "上市＋上櫃分子、分母各自加總後相除。"))
        rows.append(row("6", "上市融資維持率（含ETF，參考）", f"{last['twse_all']:.2f}%；上市櫃含ETF {last.get('both_all', float('nan')):.2f}%",
            "不參與判定", WHT, ld, src + ["https://www.istock.tw/post/twmarginrequirement", "https://www.macromicro.me/charts/53117/taiwan-taiex-maintenance-margin"],
            "市場常見口徑（愛玩股、財經M平方現行版）；本表計算值已與兩者 2026-11-06／11-09 公布值逐位核對一致。"))
    
    except Exception as e:
        rows.append(row("6", "大盤融資維持率（扣除ETF，自算對照值）", None, thr, UNK, None, src, f"失敗：{e}"))
    # 官方整戶擔保維持率：保留作背景
    TW = "https://www.twse.com.tw/rwd/zh/marginTrading/"
    try:
        d = get(TW + "BFIJ3U_TREND?response=json&days=60", sleep=2).json()
        x = d["data"][-1]; ld = f"{x['date'][:4]}-{x['date'][4:6]}-{x['date'][6:]}"
        rows.append(row("6", "全市場整戶擔保維持率（證交所官方，參考）", f"{x['keepRate']:.2f}%（含融券與 ETF，口徑不同）", "不參與判定", WHT, ld,
            ["https://www.twse.com.tw/dashboard/zh/credit/margin.html"], "v1 判定依據；v2 依 COBB 指正改為不含 ETF 的融資維持率，此列僅作背景。"))
    except Exception as e:
        rows.append(row("6", "全市場整戶擔保維持率（證交所官方，參考）", None, "不參與判定", UNK, None, ["https://www.twse.com.tw/dashboard/zh/credit/margin.html"], f"失敗：{e}"))
    return rows

# ---------- 9. 道氏理論：底底高＋頭頭高（日收盤）----------
def zigzag(c, rev):
    """收盤價波段（無前視）：從極值反向達 rev 才確認該極值。回傳 [(type, idx_extreme, value, idx_confirm)]"""
    v = c.values; piv = []; hi = lo = 0; trend = 0
    for i in range(1, len(v)):
        if trend == 0:
            if v[i] > v[hi]: hi = i
            if v[i] < v[lo]: lo = i
            if v[i] >= v[lo] * (1 + rev) and lo < i: piv.append(("L", lo, v[lo], i)); trend = 1; hi = i
            elif v[i] <= v[hi] * (1 - rev) and hi < i: piv.append(("H", hi, v[hi], i)); trend = -1; lo = i
        elif trend == 1:
            if v[i] > v[hi]: hi = i
            elif v[i] <= v[hi] * (1 - rev): piv.append(("H", hi, v[hi], i)); trend = -1; lo = i
        else:
            if v[i] < v[lo]: lo = i
            elif v[i] >= v[lo] * (1 + rev): piv.append(("L", lo, v[lo], i)); trend = 1; hi = i
    return piv

def dow_pattern(c, rev, min_dd):
    """依已確認波段點描述道氏型態。回傳 (code, 文字, 細節 dict)。code: confirmed / uptrend_pullback / hl_pending / lower_low_rebound / falling_below / pullback / unknown"""
    pv = zigzag(c, rev); D = lambda i: c.index[i].strftime("%Y-%m-%d")
    roll = c.rolling(52 if len(c) < 600 else 252, min_periods=10).max()
    last = c.iloc[-1]
    lows = [x for x in pv if x[0] == "L"]; highs = [x for x in pv if x[0] == "H"]
    det = dict(pivots=[(t, D(i), round(float(v), 1), D(j)) for t, i, v, j in pv[-8:]])
    if len(lows) < 2 or not pv: return "unknown", "波段點不足", det
    lp = pv[-1]; L2, L1 = lows[-1], lows[-2]
    hs = [h for h in highs if L1[1] < h[1] < L2[1]]; H = max(hs, key=lambda t: t[2]) if hs else None
    invalid = (c.iloc[L2[1] + 1:] < L2[2]).any()
    det.update(L1=float(L1[2]), L1_date=D(L1[1]), L2=float(L2[2]), L2_date=D(L2[1]), H=float(H[2]) if H else None, H_date=D(H[1]) if H else None)
    if lp[0] == "L":   # 最後確認的是低點，目前在反彈段
        if L2[2] > L1[2] and not invalid:
            if H is not None and (c.iloc[L2[3]:] > H[2]).any():
                return "confirmed", f"底底高＋頭頭高確認（L1 {L1[2]:,.0f} → L2 {L2[2]:,.0f}，收盤已突破反彈高 {H[2]:,.0f}）", det
            return "hl_pending", f"底底高（L1 {L1[2]:,.0f} → L2 {L2[2]:,.0f}），頭頭高待確認：需收盤站上 {H[2]:,.0f}" if H else "底底高，頭頭高待確認", det
        return "lower_low_rebound", f"破前低（{L2[2]:,.0f}＜前低 {L1[2]:,.0f}，{D(L2[1])}）後反彈中，底底高待確認：下一個回檔低點需高於 {L2[2]:,.0f}", det
    # 最後確認的是高點，目前在回檔段
    seg_min = c.iloc[lp[1]:].min()
    if last < L2[2] or seg_min < L2[2]:
        return "falling_below", f"破前低：自高點 {lp[2]:,.0f}（{D(lp[1])}）回落，已收破前低 {L2[2]:,.0f}，下跌中", det
    if L2[2] > L1[2] and H is not None and lp[2] > H[2]:
        return "uptrend_pullback", f"上升趨勢中回檔：底底高＋頭頭高已成立（L2 {L2[2]:,.0f}＞L1 {L1[2]:,.0f}，高點 {lp[2]:,.0f}＞{H[2]:,.0f}），目前回檔中、守 {L2[2]:,.0f}", det
    return "pullback", f"自高點 {lp[2]:,.0f}（{D(lp[1])}）回檔中，尚未破前低 {L2[2]:,.0f}", det

def sig9_daily_light(code):
    """日K 機械燈號（自訂規則／尚未回測）。週K 不參與。"""
    if code == "confirmed": return RED   # 底底高＋頭頭高確認
    if code == "hl_pending": return YEL  # 底底高（頭頭高待確認）
    return WHT

def sig9_v2(row, completed_daily, weekly, MKT, yf_src, P):
    p = P("9"); rows = []
    note = ("COBB：看 4小時／日K／週K 哪個高低點軌跡最清楚（例如 2022 明顯看週K）；本站預設用日K 判燈，週K 僅供參考。4 小時K 暫無穩定免費資料，未納入。"
            "燈號門檻為自訂規則／尚未回測：🔴＝日K『底底高＋頭頭高確認』；🟡＝僅日K『底底高（頭頭高待確認）』；其餘日K 型態（含破前低後反彈）為⚪。"
            "只用收盤；波段點要反向達門檻才確認，確認前不使用（無前視）。")
    for tk, (name, tz, hm) in MKT.items():
        rd, rw = p["reversal_pct_daily"][name] / 100, p["reversal_pct_weekly"][name] / 100
        thr = (f"🔴 日K『底底高＋頭頭高確認』；🟡 僅日K『底底高（頭頭高待確認）』；其餘⚪（自訂規則／尚未回測）。"
               f"波段確認門檻：日K {rd*100:.0f}%、週K 參考 {rw*100:.0f}%（收盤）")
        try:
            dly = completed_daily(tk, tz, hm); c = dly["Close"][dly.index >= "2018-01-01"]
            wc = weekly(dly, tz)["Close"]; wc = wc[wc.index >= "2015-01-01"]
            cd, td, dd_ = dow_pattern(c, rd, p["min_drawdown"]); cw, tw, dw = dow_pattern(wc, rw, p["min_drawdown"])
            st = sig9_daily_light(cd)
            val = f"日K（判燈）：{td}｜週K（參考）：{tw}｜最新收盤 {c.iloc[-1]:,.0f}（{c.index[-1].strftime('%m/%d')}）"
            chart = dict(daily=[(i.strftime("%Y-%m-%d"), round(float(v), 1)) for i, v in c.iloc[-130:].items()],
                         weekly=[(i.strftime("%Y-%m-%d"), round(float(v), 1)) for i, v in wc.iloc[-104:].items()],
                         daily_pivots=dd_.get("pivots", []), weekly_pivots=dw.get("pivots", []))
            rows.append(row("9", name, val, thr, st, f"日K {c.index[-1].strftime('%Y-%m-%d')}；週K截至 {wc.index[-1].strftime('%Y-%m-%d')}（已完成週，參考）", [yf_src(tk)],
                note, dict(daily_code=cd, weekly_code=cw, daily=dd_, weekly=dw, chart=chart)))
        except Exception as e:
            rows.append(row("9", name, None, thr, UNK, None, [yf_src(tk)], f"失敗：{e}"))
    return rows

# ---------- 8. 槓桿 ETF 爆量（v2：COBB 的 HMV 週量 Z-Score）----------
def hmv_zone(z, p):
    if z >= p["extra_high"]: return "Extra High"
    if z >= p["high"]: return "High"
    if z >= p["elevated"]: return "Elevated"
    if z <= p["low"]: return "Low"
    return "Normal"

def sig8_v2(row, completed_daily, weekly, yf_src, P):
    """stdbar = (週量 − SMA(週量,200)) ÷ stdev(週量,200)，stdev 為母體標準差（同 TradingView ta.stdev），只用已完成週。"""
    import yfinance as yf
    p = P("8"); n = p["length"]; rows = []
    thr = f"週量 Z ≥ {p['extra_high']}σ（Extra High）；接近：≥ {p['high']}σ（High）"
    for tk in ("QLD", "TQQQ", "SOXL"):
        try:
            d = completed_daily(tk, NY, (16, 15)); w = weekly(d, NY)["Volume"].astype(float)
            w = w[w > 0]
            if len(w) < n:
                rows.append(row("8", tk, f"已完成週僅 {len(w)} 根，不足 {n} 根，無法計算", thr, UNK, w.index[-1].strftime("%Y-%m-%d") if len(w) else None, [yf_src(tk)], "週數不足 200。")); continue
            m = w.rolling(n).mean(); sd = w.rolling(n).std(ddof=0); z = (w - m) / sd
            zl = z.iloc[-1]; last4 = z.iloc[-4:]; zmax, zmax_d = last4.max(), last4.idxmax()
            st = RED if zl >= p["extra_high"] else YEL if zl >= p["high"] else WHT
            spl = yf.Ticker(tk).splits
            if len(spl): spl.index = spl.index.tz_localize(None)
            win_start = w.index[-n] if len(w) >= n else w.index[0]
            sp_in = spl[spl.index >= win_start] if len(spl) else spl
            note = ("量為 Yahoo 拆股調整後股數（拆股前的量已按比例換算）。" + (f"⚠️200 週視窗內有拆／合股：{ {k.strftime('%Y-%m-%d'): float(v) for k, v in sp_in.items()} }，調整後股數可比，但拆股前後流動性結構可能不同。" if len(sp_in) else "200 週視窗內無拆／合股。")
                    + f"共 {len(w)} 根已完成週。")
            rows.append(row("8", tk, f"上週（{w.index[-1].strftime('%m/%d')} 當週）量 {w.iloc[-1]/1e6:,.0f}M 股；Z = {zl:+.2f}σ（{hmv_zone(zl, p)}）；200 週均量 {m.iloc[-1]/1e6:,.0f}M、σ {sd.iloc[-1]/1e6:,.0f}M；近 4 週最高 Z {zmax:+.2f}σ（{zmax_d.strftime('%m/%d')} 當週，{hmv_zone(zmax, p)}）",
                thr, st, f"週線截至 {w.index[-1].strftime('%Y-%m-%d')}（已完成週）", [yf_src(tk)], note,
                dict(z=zl, zone=hmv_zone(zl, p), z_max4=zmax, z_max4_week=zmax_d.strftime("%Y-%m-%d"), sma200=m.iloc[-1], sd200=sd.iloc[-1], weeks=len(w),
                     last4=[(i.strftime("%Y-%m-%d"), round(float(v), 2)) for i, v in last4.items()])))
        except Exception as e:
            rows.append(row("8", tk, None, thr, UNK, None, [yf_src(tk)], f"失敗：{e}"))
    return rows
