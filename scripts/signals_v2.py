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

# ---------- 5. VIX / VXN（v2：絕對值，回檔區間內收盤曾達門檻）----------
def sig5_v2(row, completed_daily, P):
    p = P("5"); rows = []
    try:
        ndx, peak, dd = us_episode(completed_daily, p)
    except Exception as e:
        ndx = peak = dd = None; ep_err = e
    for s, fred in (("VIX", "VIXCLS"), ("VXN", "VXNCLS")):
        url = f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{s}_History.csv"
        trig, near = p[f"{s.lower()}_trigger"], p[f"{s.lower()}_near"]
        thr = f"回檔區間內任一收盤 ≥ {trig}（接近：≥ {near}）"
        try:
            if peak is None: raise RuntimeError(f"NDX 回檔區間計算失敗：{ep_err}")
            c = get(url).text; save_raw(f"cboe_{s}.csv", c)
            x = pd.read_csv(io.StringIO(c)); x["DATE"] = pd.to_datetime(x["DATE"]); x = x.set_index("DATE")
            cl = x["CLOSE"].dropna(); last, ld = cl.iloc[-1], cl.index[-1]
            w, ok, hits, nears = latch_eval(cl, dd, peak, lambda v: v >= trig, lambda v: v >= near, p["dd_min"])
            st = RED if len(hits) else YEL if len(nears) else WHT
            mx = w.idxmax() if len(w) else None
            h10 = cl[cl.index >= ld - pd.DateOffset(years=10)]; pct = (h10 < last).mean() * 100
            hi_intraday = x["HIGH"][x.index > peak].max() if len(w) else None
            val = (f"最新收盤 {last:.2f}（{ld.strftime('%m/%d')}）；回檔區間（自 {peak.strftime('%Y-%m-%d')}）最高收盤 {w.max():.2f}（{mx.strftime('%m/%d')}），盤中最高 {hi_intraday:.2f}；"
                   f"參考：最新值 10 年分位 {pct:.0f}%（不參與判定）")
            rows.append(row("5", s, val, thr, st, ld.strftime("%Y-%m-%d"), [url, f"https://fred.stlouisfed.org/series/{fred}"],
                "v2：改用絕對值（依 COBB 指正：不同年代分位會有落差）；只看收盤，盤中值僅列示。觸發後維持到 NDX 再創 52 週收盤新高。",
                dict(close=last, window_max=float(w.max()), window_max_date=mx.strftime("%Y-%m-%d"), intraday_max=float(hi_intraday),
                     episode_start=peak.strftime("%Y-%m-%d"), pct10=pct, hit_dates=[i.strftime("%Y-%m-%d") for i in hits.index])))
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
    src = ["https://www.twse.com.tw/zh/trading/margin/mi-margn.html", "https://www.twse.com.tw/zh/trading/historical/mi-index.html",
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
        rows.append(row("6", "大盤融資維持率（上市，不含ETF，自算）", f"{k:.2f}%；近5日 {recent}；近{len(got)}日最低 {mn[key]:.2f}%（{fmt(mn['date'])}）；另一口徑（{'只排ETF' if key.endswith('narrow') else '窄口徑'}）{last['twse_ex_etf' if key.endswith('narrow') else 'twse_narrow']:.2f}%",
            thr, st, ld, src,
            ("口徑＝窄口徑：分子只計上市本國普通股（排除 ETF／ETN／受益憑證／特別股、-KY／DR、當日停資 X）" if key.endswith("narrow") else "口徑＝只排 ETF：分子排除代號非 4 位數字者（ETF／ETN／受益憑證／特別股）") + "；公式 Σ(融資餘額張數×1000×收盤價) ÷ 證交所公布上市融資金額 ×100%；分母為官方總額（含 ETF 借款，官方不公布個別證券融資金額），所以比『含ETF』版本低。窄口徑與論壇流傳的『扣除ETF 融資維持率』（10/30–11/06）相差約 0.5–3.3 個百分點，是目前最接近 COBB 表上數字的版本，但算法屬第三方逆向推估、未經官方證實。",
            dict(variant=p["primary_variant"], value=k, ex_etf_only=last["twse_ex_etf"], narrow=last["twse_narrow"], recent=[(x["date"], x[key]) for x in got], min=mn[key], min_date=mn["date"])))
        rows.append(row("6", "上市櫃融資維持率（不含ETF，自算，參考）", f"{last['both_ex_etf']:.2f}%（上櫃 {last['tpex_ex_etf']:.2f}%）" if "both_ex_etf" in last else None,
            "不參與判定", WHT if "both_ex_etf" in last else UNK, ld, src, "上市＋上櫃分子、分母各自加總後相除。"))
        rows.append(row("6", "上市融資維持率（含ETF，參考）", f"{last['twse_all']:.2f}%；上市櫃含ETF {last.get('both_all', float('nan')):.2f}%",
            "不參與判定", WHT, ld, src + ["https://www.istock.tw/post/twmarginrequirement", "https://www.macromicro.me/charts/53117/taiwan-taiex-maintenance-margin"],
            "市場常見口徑（愛玩股、財經M平方現行版）；本表計算值已與兩者 2026-11-06／11-09 公布值逐位核對一致。"))
    
    except Exception as e:
        rows.append(row("6", "大盤融資維持率（上市，不含ETF，自算）", None, thr, UNK, None, src, f"失敗：{e}"))
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

def sig9_v2(row, completed_daily, MKT, yf_src, P):
    p = P("9"); rows = []
    for tk, (name, tz, hm) in MKT.items():
        rev = p["reversal_pct"][name] / 100
        thr = f"道氏：L2>L1（底底高）後，收盤突破其間反彈高點 H（頭頭高）；波段反轉門檻 {rev*100:.0f}%（收盤）"
        try:
            c = completed_daily(tk, tz, hm)["Close"]; c = c[c.index >= "2018-01-01"]
            pv = zigzag(c, rev); D = lambda i: c.index[i].strftime("%Y-%m-%d")
            lows = [x for x in pv if x[0] == "L"]; highs = [x for x in pv if x[0] == "H"]
            roll = c.rolling(252, min_periods=60).max()
            last = c.iloc[-1]; st = WHT; H = None; L1 = L2 = None; broke = False; invalid = False; brk_date = None
            if len(lows) >= 2:
                L2 = lows[-1]; L1 = lows[-2]
                hs = [h for h in highs if L1[1] < h[1] < L2[1]]
                H = max(hs, key=lambda t: t[2]) if hs else None
                after = c.iloc[L2[3]:]  # 自 L2 確認日起
                if H is not None:
                    b = after[after > H[2]]
                    broke = len(b) > 0; brk_date = b.index[0] if broke else None
                invalid = (c.iloc[L2[1] + 1:] < L2[2]).any()
                L1dd = (L1[2] / roll.iloc[L1[1]] - 1) * 100
                prior_down = L1dd <= p["min_drawdown"]
                if L2[2] > L1[2] and prior_down and not invalid:
                    st = RED if broke else YEL
            # 尚未確認的走勢（最後一個確認點之後）
            lp = pv[-1] if pv else None
            pend = ""
            if lp:
                seg = c.iloc[lp[1]:]
                if lp[0] == "H": pend = f"目前自高點 {lp[2]:,.0f}（{D(lp[1])}）回落中，區間最低收盤 {seg.min():,.0f}（{seg.idxmin().strftime('%m/%d')}），尚未出現 {rev*100:.0f}% 反彈確認新低點"
                else: pend = f"目前自低點 {lp[2]:,.0f}（{D(lp[1])}）反彈中，區間最高收盤 {seg.max():,.0f}（{seg.idxmax().strftime('%m/%d')}）"
            if L1 and L2:
                val = (f"L1={L1[2]:,.0f}（{D(L1[1])}，距 52 週高 {L1dd:+.1f}%）→ L2={L2[2]:,.0f}（{D(L2[1])}，{D(L2[3])} 確認）{'↑底底高' if L2[2] > L1[2] else '↓底底低'}；"
                       f"其間反彈高 H={'%s（%s）' % (format(H[2], ',.0f'), D(H[1])) if H else '無'}；{('已於 ' + brk_date.strftime('%m/%d') + ' 收盤突破') if broke else '未突破'}"
                       + ("；⚠️L2 之後已收破 L2，結構失效" if invalid else "") + f"；最新收盤 {last:,.0f}；{pend}")
            else:
                val = f"確認低點不足；{pend}"
            rows.append(row("9", name, val, thr, st, c.index[-1].strftime("%Y-%m-%d"), [yf_src(tk)],
                f"日收盤 zigzag：自極值反向 ≥{rev*100:.0f}% 才確認波段點，確認前不使用（無前視）。前提：L1 距 52 週最高收盤回落 ≥{abs(p['min_drawdown'])}%。",
                dict(L1=L1[2] if L1 else None, L1_date=D(L1[1]) if L1 else None, L2=L2[2] if L2 else None, L2_date=D(L2[1]) if L2 else None,
                     H=H[2] if H else None, broke=bool(broke), invalid=bool(invalid), pivots_last6=[(t, D(i), round(float(v), 1), D(j)) for t, i, v, j in pv[-6:]])))
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
