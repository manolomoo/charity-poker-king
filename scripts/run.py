#!/usr/bin/env python3
"""台股與美股抄底訊號追蹤表 — 每次執行重新抓取資料並依 rules.json 判定。
用法：.venv/bin/python scripts/run.py   （輸出 data/YYYY-MM-DD.json、data/latest.json）
"""
import io, json, os, re, sys, time, zipfile, traceback, datetime as dt
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

RULES = json.load(open(os.path.join(ROOT, "rules.json")))
P = lambda k: RULES["signals"][k]["params"]
RUN_TS = ts()
RUN_DATE = now_tpe().strftime("%Y-%m-%d")

def row(signal, target, value, threshold, status, data_date, sources, note="", extra=None):
    d = dict(signal=signal, target=target, value=value, threshold=threshold, status=status,
             status_label=STATUS_LABEL[status], data_date=data_date, sources=sources,
             queried_at=RUN_TS, note=note)
    if extra: d["extra"] = extra
    return d

# ---------------- 行情（yfinance） ----------------
_px = {}
def daily(tk, start="2009-01-01"):
    if tk not in _px:
        import yfinance as yf
        d = yf.Ticker(tk).history(start=start, auto_adjust=False, actions=False)
        d.index = d.index.tz_localize(None).normalize()
        _px[tk] = d
    return _px[tk]

def completed_daily(tk, tz, close_hm):
    d = daily(tk)
    now = dt.datetime.now(tz)
    last = d.index[-1].date()
    if last > now.date() or (last == now.date() and (now.hour, now.minute) < close_hm):
        d = d.iloc[:-1]
    return d

def weekly(d, tz):
    """日線→週線（週五為週末），只保留已完成週：週五日期 < 市場當地今日。"""
    w = d.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna(subset=["Close"])
    today = dt.datetime.now(tz).date()
    w = w[[ix.date() < today for ix in w.index]]
    return w

def yf_src(tk):
    return f"https://finance.yahoo.com/quote/{tk.replace('^','%5E')}/history"

MKT = {"^TWII": ("台灣加權指數", TPE, (14, 30)), "^NDX": ("NDX", NY, (16, 15)), "^SOX": ("SOX", NY, (16, 15))}

# ---------------- 1. ETF 資金流 ----------------
def sig1():
    rows = []; p = P("1")
    # SPY: SSGA NAV history（含流通股數）
    url = "https://www.ssga.com/library-content/products/fund-data/etfs/us/navhist-us-en-spy.xlsx"
    try:
        b = get(url).content; save_raw("spy_navhist.xlsx", b, True)
        x = pd.read_excel(io.BytesIO(b), header=3).iloc[:, :4]
        x.columns = ["Date", "NAV", "SO", "TNA"]
        x["Date"] = pd.to_datetime(x["Date"], format="%d-%b-%Y", errors="coerce")
        for c in ("NAV", "SO", "TNA"): x[c] = pd.to_numeric(x[c], errors="coerce")
        x = x.dropna().sort_values("Date").set_index("Date")
        f = x["SO"].diff() * x["NAV"]
        def win(n):
            fl = f.iloc[-n:].sum(); aum0 = x["TNA"].iloc[-n-1]
            return fl, fl / aum0 * 100
        wf, wp = win(p["week_days"]); mf, mp = win(p["month_days"])
        st = RED if (mp <= p["m_trigger"] or wp <= p["w_trigger"]) else YEL if (mp <= p["m_near"] or wp <= p["w_near"]) else WHT
        last = x.index[-1].strftime("%Y-%m-%d")
        rows.append(row("1", "SPY", f"近1週 {wf/1e9:+.2f}B 美元（{wp:+.2f}% AUM）；近1月 {mf/1e9:+.2f}B（{mp:+.2f}% AUM）；AUM {x['TNA'].iloc[-1]/1e9:.1f}B；流通股數 {x['SO'].iloc[-1]/1e6:.1f}M",
            RULES["signals"]["1"]["trigger"], st, last, [url],
            "以官方每日流通股數變動×NAV 推算；SSGA 檔案約延遲 1 個交易日。",
            dict(week_flow_usd=wf, week_pct=wp, month_flow_usd=mf, month_pct=mp, aum=x["TNA"].iloc[-1], shares=x["SO"].iloc[-1],
                 aum_change_1m_pct=(x["TNA"].iloc[-1]/x["TNA"].iloc[-22]-1)*100)))
    except Exception as e:
        rows.append(row("1", "SPY", None, RULES["signals"]["1"]["trigger"], UNK, None, [url], f"抓取失敗：{e}"))
    # SOXX：iShares 頁面僅有當日流通股數（無歷史）→ 存快照，未來累積後可計算
    soxx_url = "https://www.ishares.com/us/products/239705/ishares-semiconductor-etf"
    snap = {}
    try:
        import html as _html; h = _html.unescape(get(soxx_url).text)
        m = re.search(r'Shares Outstanding","formattedValue":"([\d,]+)".{0,80}?"formattedAsOfDate":"([^"]+)"', h)
        a = re.search(r'"totalNetAssetsFundLevel":\{.{0,120}?"formattedValue":"([\d,]+)".{0,200}?"formattedAsOfDate":"([^"]+)"', h)
        if m: snap["SOXX"] = dict(shares=int(m.group(1).replace(",", "")), asof=m.group(2), aum=int(a.group(1).replace(",", "")) if a else None)
    except Exception as e:
        snap["SOXX_err"] = str(e)
    # 快照累積
    sp = os.path.join(DATA, "etf_shares_snapshots.csv")
    if "SOXX" in snap:
        try:
            s = snap["SOXX"]; iso = dt.datetime.strptime(s["asof"], "%b %d, %Y").strftime("%Y-%m-%d")
            new = not os.path.exists(sp)
            dup = (not new) and any(l.split(",")[1:3] == ["SOXX", iso] for l in open(sp).read().splitlines()[1:])
            if not dup:
                with open(sp, "a") as fh:
                    if new: fh.write("queried_at,ticker,asof,shares,aum\n")
                    fh.write(f"{RUN_TS},SOXX,{iso},{s['shares']},{s['aum']}\n")
        except Exception as e:
            snap.pop("SOXX", None); snap["SOXX_err"] = str(e)
    s = snap.get("SOXX")
    rows.append(row("1", "SOXX", (f"流通股數 {s['shares']/1e6:.2f}M、AUM {s['aum']/1e9:.2f}B（{s['asof']}）；近1週／近1月淨流量：無歷史資料" if s else None),
        RULES["signals"]["1"]["trigger"], UNK, s["asof"] if s else None, [soxx_url],
        "iShares 官網只公開當日流通股數，歷史下載檔以程式讀取失敗；已開始每日存快照（data/etf_shares_snapshots.csv），累積 22 個交易日後即可依規則計算。"))
    rows.append(row("1", "QQQ", None, RULES["signals"]["1"]["trigger"], UNK, None, ["https://www.invesco.com/qqq-etf/en/about.html"],
        "Invesco 資料 API 拒絕程式存取（HTTP 406），未取得每日流通股數；不以資產規模縮水代替資金外流。"))
    rows.append(row("1", "SMH", None, RULES["signals"]["1"]["trigger"], UNK, None, ["https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/"],
        "VanEck 官網需 Cookie／瀏覽器，程式讀取被導向錯誤頁；未取得每日流通股數。"))
    return rows

# ---------------- 2. CNN F&G ----------------
def sig2():
    p = P("2"); start = (now_tpe() - dt.timedelta(days=60)).strftime("%Y-%m-%d")
    url = f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}"
    try:
        d = get(url, headers={"Referer": "https://edition.cnn.com/", "Origin": "https://edition.cnn.com"}).json()
        save_raw("cnn_fng.json", json.dumps(d))
        fg = d["fear_and_greed"]; sc = fg["score"]
        st = RED if sc < p["trigger"] else YEL if sc < p["near"] else WHT
        pc_ = fg["previous_close"]; prev_st = RED if pc_ < p["trigger"] else YEL if pc_ < p["near"] else WHT
        t = dt.datetime.fromisoformat(fg["timestamp"]).astimezone(TPE)
        hist = [(dt.datetime.fromtimestamp(x["x"]/1000, TPE).strftime("%m-%d"), round(x["y"], 1)) for x in d["fear_and_greed_historical"]["data"][-10:]]
        return [row("2", "CNN 恐懼貪婪指數", f"{sc:.1f}（{fg['rating']}）；前一交易日 {fg['previous_close']:.1f}、1 週前 {fg['previous_1_week']:.1f}、1 個月前 {fg['previous_1_month']:.1f}",
            "< 25", st, t.strftime("%Y-%m-%d %H:%M 台北"), ["https://edition.cnn.com/markets/fear-and-greed", url],
            "CNN 未公開 API，結構可能變動。", dict(score=sc, prev_close=fg["previous_close"], prev_1w=fg["previous_1_week"], prev_1m=fg["previous_1_month"], recent=hist, prev_day_status=prev_st))]
    except Exception as e:
        return [row("2", "CNN 恐懼貪婪指數", None, "< 25", UNK, None, ["https://edition.cnn.com/markets/fear-and-greed"], f"抓取失敗：{e}")]

# ---------------- 3. 週 KDJ ----------------
def kdj(w, n=9, m1=3, m2=3):
    low = w["Low"].rolling(n, min_periods=n).min(); high = w["High"].rolling(n, min_periods=n).max()
    rsv = (w["Close"] - low) / (high - low) * 100
    K, D, out = 50.0, 50.0, []
    for v in rsv:
        if np.isnan(v): out.append((np.nan, np.nan, np.nan)); continue
        K = (m1 - 1) / m1 * K + v / m1; D = (m2 - 1) / m2 * D + K / m2
        out.append((K, D, 3 * K - 2 * D))
    return pd.DataFrame(out, index=w.index, columns=["K", "D", "J"])

def sig3():
    p = P("3"); rows = []
    for tk, (name, tz, hm) in MKT.items():
        try:
            d = completed_daily(tk, tz, hm); d = d[d.index >= p["seed_start"]]
            w = weekly(d, tz); k = kdj(w, p["n"], p["m1"], p["m2"])
            j = k["J"].iloc[-1]; prev = k["J"].iloc[-2]
            st = RED if j <= p["trigger"] else YEL if j <= p["near"] else WHT
            rows.append(row("3", name, f"J={j:.1f}（K={k['K'].iloc[-1]:.1f}, D={k['D'].iloc[-1]:.1f}；前一週 J={prev:.1f}）",
                "週 J ≤ 0", st, f"週線截至 {w.index[-1].strftime('%Y-%m-%d')}（已完成週）", [yf_src(tk)],
                "週K由 Yahoo 日線重組，KDJ(9,3,3)。", dict(J=j, K=k['K'].iloc[-1], D=k['D'].iloc[-1], J_prev=prev,
                     last5=[(i.strftime('%Y-%m-%d'), round(v, 1)) for i, v in k['J'].iloc[-5:].items()])))
        except Exception as e:
            rows.append(row("3", name, None, "週 J ≤ 0", UNK, None, [yf_src(tk)], f"失敗：{e}"))
    return rows

# ---------------- 4. 估值 ----------------
def sig4():
    p = P("4"); rows = []
    cfg = {"NDX": ("https://historyofmarket.com/api/ndx/forward-pe.json", None, "^NDX", "https://historyofmarket.com/nasdaq/ndx-forward-pe/"),
           "SOX": ("https://historyofmarket.com/api/sectors/forward-pe.json", "sox", "^SOX", "https://historyofmarket.com/zh-tw/semi/semi-fwdpe/")}
    for name, (url, key, tk, page) in cfg.items():
        try:
            d = get(url).json(); save_raw(f"hom_{name}.json", json.dumps(d))
            if key: d = d["sectors"][key]; series = d["series"]; own = d["forwardOwn"]
            else: series = d["forward"]; own = d["forwardOwn"]
            ser = pd.Series({pd.Timestamp(x["date"]): x["value"] for x in series}).sort_index()
            ow = pd.Series({pd.Timestamp(x["date"]): x["value"] for x in own}).sort_index()
            cur, cur_date = ow.iloc[-1], ow.index[-1]
            def pct(years):
                h = ser[ser.index >= cur_date - pd.DateOffset(years=years)]
                return (h < cur).mean() * 100, len(h), h.index[0]
            p10, n10, s10 = pct(p["window_years"]); p5, n5, _ = pct(5)
            st = RED if p10 <= p["pct_trigger"] else YEL if p10 <= p["pct_near"] else WHT
            px = daily(tk)["Close"]
            eps = (px.reindex(ow.index, method="ffill") / ow).dropna()
            e1 = (eps.iloc[-1] / eps.iloc[-21] - 1) * 100 if len(eps) > 21 else None
            e3 = (eps.iloc[-1] / eps.iloc[0] - 1) * 100
            flag = " ⚠️EPS 下修中，可能為假便宜" if e3 < p["eps_down_flag"] else ""
            rows.append(row("4", name, f"NTM P/E {cur:.2f}×；10年分位 {p10:.0f}%（5年 {p5:.0f}%）；隱含 NTM EPS 約1個月 {e1:+.1f}%、自 {eps.index[0].strftime('%m/%d')} 起 {e3:+.1f}%{flag}",
                "10 年分位 ≤ 20%", st, cur_date.strftime("%Y-%m-%d"), [page, url],
                f"單一資料商 History of Market；歷史分位以終端機週頻序列（{s10.strftime('%Y-%m')}–{ser.index[-1].strftime('%Y-%m-%d')}，{n10} 筆）計算，最新值為同口徑自算日線 forwardOwn（官方說明與終端機序列差距 <3%）。EPS 由指數收盤÷本益比推得，非資料商直接公布。",
                dict(pe=cur, pct10=p10, pct5=p5, eps_chg_1m=e1, eps_chg_since=e3, eps_since=eps.index[0].strftime('%Y-%m-%d'), terminal_last=ser.index[-1].strftime('%Y-%m-%d'))))
        except Exception as e:
            rows.append(row("4", name, None, "10 年分位 ≤ 20%", UNK, None, [url], f"失敗：{e}"))
    return rows

# ---------------- 5. VIX / VXN ----------------
def sig5():
    p = P("5"); rows = []
    for s, fred in (("VIX", "VIXCLS"), ("VXN", "VXNCLS")):
        url = f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{s}_History.csv"
        try:
            c = get(url).text; save_raw(f"cboe_{s}.csv", c)
            x = pd.read_csv(io.StringIO(c)); x["DATE"] = pd.to_datetime(x["DATE"]); x = x.set_index("DATE")["CLOSE"].dropna()
            last, ld = x.iloc[-1], x.index[-1]
            h = x[x.index >= ld - pd.DateOffset(years=p["window_years"])]
            pc = (h < last).mean() * 100; q90 = h.quantile(.9); q80 = h.quantile(.8)
            st = RED if last >= q90 else YEL if last >= q80 else WHT
            peak5 = x.iloc[-5:]
            pv = x.iloc[-2]; prev_st = RED if pv >= q90 else YEL if pv >= q80 else WHT
            fx = None
            try:
                f = pd.read_csv(io.StringIO(get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fred}&cosd=2026-01-01", timeout=90).text)).dropna()
                f = f[f.iloc[:, 1] != "."]; fx = f"FRED {fred} {f.iloc[-1,0]} = {float(f.iloc[-1,1]):.2f}"
            except Exception as e:
                fx = "FRED 讀取逾時／失敗，本次未交叉核對（以 Cboe 官方檔為準）"
            rows.append(row("5", s, f"收盤 {last:.2f}（10年分位 {pc:.0f}%）；本規則門檻 p90={q90:.2f}、p80={q80:.2f}；近5日最高收盤 {peak5.max():.2f}（{peak5.idxmax().strftime('%m/%d')}）",
                f"≥ 自身10年p90（{q90:.2f}）", st, ld.strftime("%Y-%m-%d"), [url, f"https://fred.stlouisfed.org/series/{fred}"], fx,
                dict(close=last, pct=pc, p90=q90, p80=q80, max5=peak5.max(), max5_date=peak5.idxmax().strftime('%Y-%m-%d'), prev_close=pv, prev_date=x.index[-2].strftime('%Y-%m-%d'), prev_day_status=prev_st)))
        except Exception as e:
            rows.append(row("5", s, None, "≥ 自身10年p90", UNK, None, [url], f"失敗：{e}"))
    return rows

# ---------------- 6/7. 台股信用 ----------------
TW = "https://www.twse.com.tw/rwd/zh/marginTrading/"
def sig6():
    p = P("6"); rows = []
    try:
        d = get(TW + "BFIJ3U_TREND?response=json&days=60", sleep=2).json(); save_raw("twse_BFIJ3U_TREND.json", json.dumps(d, ensure_ascii=False))
        data = d["data"]; last = data[-1]; k = last["keepRate"]
        st = RED if k < p["trigger"] else YEL if k < p["near"] else WHT
        mn = min(data, key=lambda x: x["keepRate"]); mx = max(data, key=lambda x: x["keepRate"])
        recent = [(x["date"][4:6] + "/" + x["date"][6:], x["keepRate"]) for x in data[-5:]]
        ld = f"{last['date'][:4]}-{last['date'][4:6]}-{last['date'][6:]}"
        rows.append(row("6", "全市場信用交易整戶擔保維持率（官方）", f"{k:.2f}%；近5日 " + "、".join(f"{a} {b:.2f}%" for a, b in recent) + f"；近{len(data)}個交易日（{data[0]['date']}起）最低 {mn['keepRate']:.2f}%（{mn['date']}）、最高 {mx['keepRate']:.2f}%；追繳戶 {last['belowAccNum']:,}",
            "< 160%（接近：160–166%）", st, ld, ["https://www.twse.com.tw/dashboard/zh/credit/margin.html", TW + "BFIJ3U_TREND?response=json&days=60"],
            "上市＋上櫃、融資＋融券合併計算；官方資料自 2026-08-03 起才有，歷史很短。", dict(keepRate=k, recent=recent, min=mn, max=mx, prev_day_status=(RED if data[-2]["keepRate"] < p["trigger"] else YEL if data[-2]["keepRate"] < p["near"] else WHT))))
    except Exception as e:
        rows.append(row("6", "全市場信用交易整戶擔保維持率（官方）", None, "< 160%", UNK, None, ["https://www.twse.com.tw/dashboard/zh/credit/margin.html"], f"失敗：{e}"))
    rows.append(row("6", "資料商推估融資維持率（僅參考）", None, "不納入判定", UNK, None, ["https://www.macromicro.me/charts/53117/taiwan-taiex-maintenance-margin"],
        "財經M平方等資料商頁面有 Cloudflare 驗證／付費牆，程式無法讀取；口徑（只算融資、自行估算）與官方整戶維持率不同，不可混用。"))
    return rows

def twse_trend(date=None):
    q = f"MI_MARGN_TREND?response=json&days=60" + (f"&date={date}" if date else "")
    return get(TW + q, sleep=3).json()["data"]

def tpex_margin(date):
    cache = os.path.join(DATA, "cache", "tpex_margin"); os.makedirs(cache, exist_ok=True)  # 進 git，CI 不必每次重抓
    fp = os.path.join(cache, date.strftime("%Y%m%d") + ".json")
    if os.path.exists(fp): return json.load(open(fp))
    u = f"https://www.tpex.org.tw/www/zh-tw/margin/balance?date={date.strftime('%Y/%m/%d')}&response=json"
    d = get(u, sleep=0.6).json(); out = None
    try:
        t = d["tables"][0]
        if t.get("summary"):
            for srow in t["summary"]:
                if "融資金" in srow[1]: out = int(srow[6].replace(",", ""))
            if out is not None and t.get("date"):
                y, m, dd = t["date"].split("/"); rd = f"{int(y)+1911}{m}{dd}"
                if rd != date.strftime("%Y%m%d"): out = None
    except Exception: out = None
    res = {"date": date.strftime("%Y%m%d"), "marginAmt": out}
    if date.date() < now_tpe().date(): json.dump(res, open(fp, "w"))
    return res

def sig7():
    p = P("7"); rows = []
    try:
        a = twse_trend(); first = a[0]["date"]
        b = twse_trend((pd.Timestamp(first) - pd.Timedelta(days=1)).strftime("%Y%m%d"))
        c = twse_trend((pd.Timestamp(b[0]["date"]) - pd.Timedelta(days=1)).strftime("%Y%m%d"))
        tw = pd.DataFrame(c + b + a).drop_duplicates("date"); tw["date"] = pd.to_datetime(tw["date"]); tw = tw.set_index("date").sort_index()
        save_raw("twse_margin_trend.csv", tw.to_csv())
        tw = tw.iloc[-p["lookback_days"]:]
        # TPEx：逐日抓（有快取）
        ot = {}
        for dte in tw.index:
            try:
                r_ = tpex_margin(dte)
                if r_["marginAmt"]: ot[dte] = r_["marginAmt"]
            except Exception: pass
        ot = pd.Series(ot, dtype=float)
        hist = get(TW + "MI_MARGN_HISTORY?response=json", sleep=2).json()["data"]
        yrs = [h for h in hist if h["periodEnd"].endswith("12")][-p["ratio_years"]:]
        base = float(np.median([h["marginRatio"] for h in yrs]))
        ld = tw.index[-1]
        def dd(s): return (s.iloc[-1] / s.max() - 1) * 100, s.idxmax()
        twa = tw["marginAmt"].astype(float); dd_tw, hi_tw = dd(twa)
        ratio = twa.iloc[-1] / 1e5 / tw["marketValue"].iloc[-1] * 100
        ratio_ok = ratio <= base
        comb = (twa + ot).dropna(); dd_c, hi_c = dd(comb) if len(comb) else (None, None)
        cover = len(ot) / len(tw)
        if dd_c is None or cover < 0.9:
            st = UNK
        elif dd_c <= p["dd_trigger"] and ratio_ok: st = RED
        elif (dd_c <= p["dd_near"] and ratio_ok) or dd_c <= p["dd_trigger"]: st = YEL
        else: st = WHT
        src = ["https://www.twse.com.tw/zh/trading/margin/mi-margn.html", TW + "MI_MARGN_TREND?response=json&days=60", "https://www.tpex.org.tw/zh-tw/mainboard/trading/margin-trading/transactions.html", TW + "MI_MARGN_HISTORY?response=json"]
        rows.append(row("7", "上市＋上櫃融資餘額合計", f"{comb.iloc[-1]/1e5:,.0f} 億元；較 {len(tw)} 日高點（{hi_c.strftime('%Y-%m-%d')}）{dd_c:+.1f}%；上市融資／上市市值 {ratio:.3f}%（近{p['ratio_years']}年年底中位數 {base:.2f}%，{'≤' if ratio_ok else '>'}基準）",
            "回落 ≥20% 且 比重 ≤ 基準", st, ld.strftime("%Y-%m-%d"), src,
            f"上櫃日資料涵蓋 {len(ot)}/{len(tw)} 日；比重僅計上市（櫃買總市值未納入）。",
            dict(total_100m=comb.iloc[-1]/1e5, dd_pct=dd_c, high_date=hi_c.strftime('%Y-%m-%d'), ratio_pct=ratio, ratio_base=base, coverage=cover)))
        rows.append(row("7", "上市融資餘額（參考）", f"{twa.iloc[-1]/1e5:,.0f} 億元；較高點（{hi_tw.strftime('%Y-%m-%d')}）{dd_tw:+.1f}%", "（整項以合計判定）", WHT if dd_tw > p["dd_near"] else YEL if dd_tw > p["dd_trigger"] else RED, ld.strftime("%Y-%m-%d"), src[:2], "分項燈號僅供參考，不參與彙總。"))
        if len(ot):
            dd_o, hi_o = dd(ot)
            rows.append(row("7", "上櫃融資餘額（參考）", f"{ot.iloc[-1]/1e5:,.0f} 億元；較高點（{hi_o.strftime('%Y-%m-%d')}）{dd_o:+.1f}%", "（整項以合計判定）", WHT if dd_o > p["dd_near"] else YEL if dd_o > p["dd_trigger"] else RED, ot.index[-1].strftime("%Y-%m-%d"), src[2:3], "分項燈號僅供參考，不參與彙總。"))
    except Exception as e:
        traceback.print_exc()
        rows.append(row("7", "上市＋上櫃融資餘額合計", None, "回落 ≥20% 且 比重 ≤ 基準", UNK, None, [TW], f"失敗：{e}"))
    return rows

# ---------------- 8. 槓桿 ETF 量 ----------------
def sig8():
    p = P("8"); rows = []
    import yfinance as yf
    for tk in ("QLD", "TQQQ", "SOXL"):
        try:
            d = completed_daily(tk, NY, (16, 15)); v = d["Volume"].astype(float)
            ratios = [(v.index[i], v.iloc[i] / v.iloc[i - p["avg_n"]:i].mean()) for i in range(len(v) - p["recent_days"], len(v))]
            dmax_d, dmax = max(ratios, key=lambda t: t[1]); dlast = ratios[-1][1]
            w = weekly(d, NY)["Volume"].astype(float)
            wr = w.iloc[-1] / w.iloc[-1 - p["avg_n"]:-1].mean()
            spl = yf.Ticker(tk).splits; spl.index = spl.index.tz_localize(None)
            win_start = w.index[-1 - p["avg_n"]] - pd.Timedelta(days=7)
            sp_in = spl[spl.index >= win_start]
            st = RED if (dmax >= p["d_trigger"] or wr >= p["w_trigger"]) else YEL if (dmax >= p["d_near"] or wr >= p["w_near"]) else WHT
            note = "量為拆股調整後股數。" + (f"⚠️視窗內有拆／合股：{sp_in.to_dict()}" if len(sp_in) else f"視窗內無拆／合股（最近一次 {spl.index[-1].strftime('%Y-%m-%d')} {spl.iloc[-1]:g}:1）。")
            rows.append(row("8", tk, f"最新日 {v.iloc[-1]/1e6:.1f}M 股＝{dlast:.2f}×；近5日最大 {dmax:.2f}×（{dmax_d.strftime('%m/%d')}）；上週 {w.iloc[-1]/1e6:.0f}M 股＝{wr:.2f}×（20週均）",
                "日 ≥2.0× 或 週 ≥1.5×", st, f"日線 {v.index[-1].strftime('%Y-%m-%d')}；週線截至 {w.index[-1].strftime('%Y-%m-%d')}", [yf_src(tk)], note,
                dict(d_last=dlast, d_max5=dmax, d_max5_date=dmax_d.strftime('%Y-%m-%d'), w_ratio=wr)))
        except Exception as e:
            rows.append(row("8", tk, None, "日 ≥2.0× 或 週 ≥1.5×", UNK, None, [yf_src(tk)], f"失敗：{e}"))
    return rows

# ---------------- 9. 波段結構 ----------------
def pivots(w, n):
    lows, highs = [], []
    for i in range(n, len(w) - n):
        lo = w["Low"].iloc[i]; hi = w["High"].iloc[i]
        if lo < w["Low"].iloc[i-n:i].min() and lo < w["Low"].iloc[i+1:i+n+1].min(): lows.append((i, lo))
        if hi > w["High"].iloc[i-n:i].max() and hi > w["High"].iloc[i+1:i+n+1].max(): highs.append((i, hi))
    return lows, highs  # 只到 len-n-1，自然排除未確認

def sig9():
    p = P("9"); n = p["pivot_n"]; rows = []
    for tk, (name, tz, hm) in MKT.items():
        try:
            w = weekly(completed_daily(tk, tz, hm), tz); w = w[w.index >= "2015-01-01"]
            lows, highs = pivots(w, n)
            hi52 = w["Close"].rolling(52, min_periods=10).max()
            L2i, L2 = lows[-1]; L1i, L1 = lows[-2]
            L1dd = (w["Low"].iloc[L1i] / hi52.iloc[L1i] - 1) * 100
            hs = [h for h in highs if L1i < h[0] < L2i]
            H = max(hs, key=lambda t: t[1]) if hs else None
            broke = H is not None and (w["Close"].iloc[L2i+1:] > H[1]).any()
            invalid = w["Low"].iloc[L2i+1:].min() < L2 if L2i + 1 < len(w) else False
            cond_dd = L1dd <= p["min_drawdown"]
            if L2 > L1 and cond_dd and not invalid and broke: st = RED
            elif L2 > L1 and cond_dd and not invalid: st = YEL
            else: st = WHT
            dstr = lambda i: w.index[i].strftime("%Y-%m-%d")
            lastw = w.iloc[-1]
            val = (f"確認低點 L1={L1:,.0f}（{dstr(L1i)}，距52週高 {L1dd:+.1f}%）→ L2={L2:,.0f}（{dstr(L2i)}）{'↑底底高' if L2 > L1 else '↓未墊高'}；"
                   f"其間波段高 H={'%s（%s）' % (format(H[1], ',.0f'), dstr(H[0])) if H else '無'}；{'已突破' if broke else '未突破'}；"
                   f"最新完成週 收 {lastw['Close']:,.0f}／低 {lastw['Low']:,.0f}" + ("；⚠️已跌破 L2，結構失效" if invalid else ""))
            rows.append(row("9", name, val, "L2>L1 且週收盤突破 H", st, f"週線截至 {dstr(len(w)-1)}", [yf_src(tk)],
                f"週線、左右各 {n} 週確認的波段點；最近 {n} 週的高低點尚未確認、不使用。",
                dict(L1=L1, L1_date=dstr(L1i), L2=L2, L2_date=dstr(L2i), H=H[1] if H else None, broke=bool(broke), invalid=bool(invalid), L1_dd=L1dd)))
        except Exception as e:
            rows.append(row("9", name, None, "L2>L1 且突破 H", UNK, None, [yf_src(tk)], f"失敗：{e}"))
    return rows

# ---------------- 10/11. 國發會 ----------------
NDC_DS = "https://data.gov.tw/dataset/6099"
def ndc():
    meta = get("https://data.gov.tw/api/v2/rest/dataset/6099").json()["result"]
    url = meta["distribution"][0]["resourceDownloadUrl"]
    b = get(url).content
    save_raw(f"ndc_{RUN_DATE}.zip", b, True)
    z = zipfile.ZipFile(io.BytesIO(b))
    for i in z.infolist():
        try: nm = i.filename.encode("cp437").decode("utf-8")
        except Exception: nm = i.filename
        if nm == "景氣指標與燈號.csv":
            df = pd.read_csv(io.BytesIO(z.read(i)), dtype=str, encoding="utf-8-sig")
            mt = dt.datetime(*i.date_time).strftime("%Y-%m-%d %H:%M")
            return df, url, mt
    raise RuntimeError("zip 內找不到 景氣指標與燈號.csv")

def revisions(df):
    """與上一次存檔比較，找出歷史修正。"""
    vp = os.path.join(DATA, "ndc_last_vintage.csv"); notes = []
    cols = ["Date", "領先指標不含趨勢指數", "景氣對策信號綜合分數", "景氣對策信號"]
    cur = df[cols].copy()
    if os.path.exists(vp):
        old = pd.read_csv(vp, dtype=str)
        m = cur.merge(old, on="Date", suffixes=("", "_old"))
        for c in cols[1:]:
            ch = m[m[c] != m[c + "_old"]].tail(6)
            for _, x in ch.iterrows():
                notes.append(f"{x['Date']} {c}: {x[c + '_old']} → {x[c]}")
    cur.to_csv(vp, index=False)
    return notes

def ndc_release():
    """從國發會最新消息列表找最近一則『景氣概況新聞稿』的發布日期（民國→西元）。"""
    try:
        h = get("https://www.ndc.gov.tw/News_TopNews.aspx?n=1CE324F4C0BA40AC&sms=2DA69AC70496E6D1", timeout=40).text
        txt = re.sub(r"<[^>]+>", " ", h); txt = re.sub(r"\s+", " ", txt)
        m = re.search(r"(\d{3})年(\d{1,2})月份景氣概況新聞稿\s*(\d{3})-(\d{2})-(\d{2})", txt)
        if m:
            return f"資料月份 {int(m.group(1))+1911}/{int(m.group(2)):02d}，國發會 {int(m.group(3))+1911}-{m.group(4)}-{m.group(5)} 發布"
    except Exception as e:
        err = str(e)[:60]
    else:
        err = "未解析到標題"
    try:
        c = json.load(open(os.path.join(DATA, "ndc_release.json")))
        m = c["data_month"]
        return f"資料月份 {m[:4]}/{m[4:]}，國發會 {c['released']} 發布（備援設定檔，來源 {c['source']}）；下次 {c['next_release']}"
    except Exception:
        return f"發布日期自動查詢失敗：{err}"

def sig10_11(release_note):
    rows = []
    try:
        df, url, mt = ndc(); df = df[df["景氣對策信號綜合分數"] != "-"]
        rev = revisions(df)
        li = df[["Date", "領先指標不含趨勢指數"]].tail(6)
        vals = [(r_["Date"], float(r_["領先指標不含趨勢指數"])) for _, r_ in li.iterrows()]
        last3 = [v for _, v in vals[-3:]]
        p = P("10"); below = 0
        for _, v in reversed(vals):
            if v < p["level"]: below += 1
            else: break
        st = RED if below >= 3 else YEL if below >= 1 else WHT
        mm = lambda s: f"{s[:4]}/{s[4:]}"
        revtxt = ("；與上次存檔相比之修正：" + "；".join(rev)) if rev else "；首次建立，尚無前一版本可比對修正（每月發布會回溯修正歷史值）"
        rows.append(row("10", "領先指標不含趨勢指數", "；".join(f"{mm(a)} {v:.2f}" for a, v in vals[-4:]) + f"（連續低於100：{below} 個月）",
            "連續3個月皆 <100", st, f"{mm(vals[-1][0])}（{release_note}）", [NDC_DS, "https://index.ndc.gov.tw/n/zh_tw/data/eco", url],
            f"資料檔更新時間 {mt}" + revtxt, dict(values=vals, below_streak=below, revisions=rev)))
        sc = df[["Date", "景氣對策信號綜合分數", "景氣對策信號"]].tail(6)
        sv = [(r_["Date"], int(r_["景氣對策信號綜合分數"]), r_["景氣對策信號"]) for _, r_ in sc.iterrows()]
        s = sv[-1][1]; p = P("11")
        st = RED if s <= p["blue_max"] else YEL if s <= p["yb_max"] else WHT
        rows.append(row("11", "景氣對策信號", f"{mm(sv[-1][0])} {s} 分 {sv[-1][2]}燈；近月：" + "、".join(f"{mm(a)} {b}({c})" for a, b, c in sv[-6:-1]),
            "藍燈（9–16分）", st, f"{mm(sv[-1][0])}（{release_note}）", [NDC_DS, "https://www.ndc.gov.tw/nc_332_40618", url],
            "歷史修正：國發會每月回溯修正；本表以最新一版資料檔為準" + (revtxt if rev else "") ,
            dict(series=sv)))
    except Exception as e:
        traceback.print_exc()
        rows.append(row("10", "領先指標不含趨勢指數", None, "連續3個月皆 <100", UNK, None, [NDC_DS], f"失敗：{e}"))
        rows.append(row("11", "景氣對策信號", None, "藍燈", UNK, None, [NDC_DS], f"失敗：{e}"))
    return rows

# ---------------- 彙總 ----------------
def summarize(rows):
    out = {}
    for k, cfg in RULES["signals"].items():
        rs = [x for x in rows if x["signal"] == k]
        if k == "7": sts = [x["status"] for x in rs if "合計" in x["target"]]
        elif k == "6": sts = [x["status"] for x in rs if "官方" in x["target"]]
        else: sts = [x["status"] for x in rs]
        st = agg_any(sts)
        extra = {}
        if k in ("3", "9"):
            extra = {"台股": agg_any([x["status"] for x in rs if x["target"] == "台灣加權指數"]),
                     "美股": agg_any([x["status"] for x in rs if x["target"] in ("NDX", "SOX")])}
        out[k] = dict(name=cfg["name"], status=st, status_label=STATUS_LABEL[st], aggregation=cfg["aggregation"], by_market=extra or None)
    return out

STALE_PREFIX = "資料過期"

def apply_stale_fallback(rows, prev, failed_signals):
    """來源失敗時沿用上一次的數值與舊資料日期，並標成『資料過期』；不編造數字。
    - 本次某列 value 為 None 且狀態 ❔，而上一次同一列（signal+target）有數值 → 沿用上一次那列。
    - 整個訊號函式拋出例外（failed_signals）→ 沿用上一次該訊號所有列。
    沿用列保留上一次的 status、data_date、queried_at（原查詢時間），另加 stale=True 與說明。"""
    if not prev: return rows, []
    prev_rows = {(x["signal"], x["target"]): x for x in prev.get("rows", [])}
    out, stale = [], []
    def mk(old, why):
        r_ = dict(old)
        r_["stale"] = True
        r_["stale_since"] = old.get("stale_since") or old.get("queried_at")
        r_["stale_reason"] = f"{STALE_PREFIX}：本次查詢（{RUN_TS}）抓取失敗（{str(why)[:160]}），沿用 {r_['stale_since']} 查得的數值與原資料日期。"
        stale.append(f"{old['signal']}｜{old['target']}")
        return r_
    for k, err in failed_signals.items():
        olds = [x for x in prev.get("rows", []) if x["signal"] == k]
        # 上次本來就沒有數值的列（例如一直讀不到的來源）原樣保留，不標過期
        if olds: out += [mk(o, err) if o.get("value") is not None else dict(o) for o in olds]
        else: out.append(row(k, RULES["signals"][k]["name"], None, RULES["signals"][k]["trigger"], UNK, None, [], f"失敗：{err}"))
    failed_here = {}
    for r_ in rows:
        old = prev_rows.get((r_["signal"], r_["target"]))
        if r_["value"] is None and r_["status"] == UNK and old and old.get("value") is not None:
            out.append(mk(old, r_.get("note") or "未知錯誤")); failed_here[r_["signal"]] = r_.get("note") or "未知錯誤"
        else:
            out.append(r_)
    # 同一訊號有列抓取失敗時，上次有、這次卻沒產生的分項列（例如上市／上櫃參考列）也沿用並標過期
    present = {(x["signal"], x["target"]) for x in out}
    for o in prev.get("rows", []):
        if o["signal"] in failed_here and (o["signal"], o["target"]) not in present and o.get("value") is not None:
            out.append(mk(o, failed_here[o["signal"]]))
    order = {k: i for i, k in enumerate(RULES["signals"].keys())}
    out.sort(key=lambda x: order.get(x["signal"], 99))  # 穩定排序，保留同訊號內原順序
    return out, stale

def main():
    prev_p = os.path.join(DATA, "latest.json")
    prev = None
    try:
        prev = json.load(open(prev_p)) if os.path.exists(prev_p) else None
    except Exception as e:
        print("讀取上一次 latest.json 失敗：", e)
    try:
        release_note = ndc_release()
    except Exception as e:
        release_note = f"發布日期查詢失敗：{e}"
    rows, failed = [], {}
    for k, f in (("1", sig1), ("2", sig2), ("3", sig3), ("4", sig4), ("5", sig5), ("6", sig6), ("7", sig7), ("8", sig8), ("9", sig9)):
        print("running", f.__name__, flush=True)
        try: rows += f()
        except Exception as e:
            traceback.print_exc(); failed[k] = e
    try: rows += sig10_11(release_note)
    except Exception as e:
        traceback.print_exc(); failed["10"] = e; failed["11"] = e
    rows, stale = apply_stale_fallback(rows, prev, failed)
    summ = summarize(rows)
    changes = []
    has_prev = bool(prev and prev.get("queried_at") and prev.get("queried_at") != RUN_TS)
    if has_prev:
        for k, v in summ.items():
            o = prev["summary"].get(k, {}).get("status")
            if o and o != v["status"]: changes.append(f"第{k}項 {v['name']}：{o} → {v['status']}")
    out = dict(schema="bottom-signals/v1", site="慈善撲克王大賽", rules_version=RULES["version"], run_date=RUN_DATE,
               queried_at=RUN_TS, previous_run=prev.get("queried_at") if has_prev else None,
               changes_vs_previous=(changes or "與上次相比，整項燈號沒有變化") if has_prev else "首次建立，無上次紀錄",
               stale_rows=stale, summary=summ, rows=rows)
    def conv(o):
        if isinstance(o, (np.integer,)): return int(o)
        if isinstance(o, (np.floating,)): return None if np.isnan(o) else float(o)
        if isinstance(o, (np.bool_,)): return bool(o)
        if isinstance(o, (pd.Timestamp, dt.date)): return str(o)
        return str(o)
    def clean(o):  # NaN/Inf 不是合法 JSON，網站 JSON.parse 會失敗 → 轉 null
        if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))): return None
        if isinstance(o, np.floating): return clean(float(o))
        if isinstance(o, dict): return {k: clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)): return [clean(v) for v in o]
        return o
    s = json.dumps(clean(out), ensure_ascii=False, indent=2, default=conv, allow_nan=False)
    open(os.path.join(DATA, f"{RUN_DATE}.json"), "w").write(s)
    open(prev_p, "w").write(s)
    hp = os.path.join(DATA, "history.jsonl")
    with open(hp, "a") as fh: fh.write(json.dumps({"run_date": RUN_DATE, "queried_at": RUN_TS, "summary": {k: v["status"] for k, v in summ.items()}, "stale": stale}, ensure_ascii=False) + "\n")
    for k, v in summ.items(): print(k, v["status"], v["name"], v["by_market"] or "")
    if stale: print("資料過期（沿用上次）：", "、".join(stale))

if __name__ == "__main__":
    main()
