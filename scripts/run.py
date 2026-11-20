#!/usr/bin/env python3
"""台股與美股抄底訊號追蹤表 — 每次執行重新抓取資料並依 rules.json 判定。
用法：.venv/bin/python scripts/run.py   （輸出 data/YYYY-MM-DD.json、data/latest.json）
"""
import io, json, os, re, sys, time, zipfile, traceback, datetime as dt
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import signals_v2 as v2
from hold import apply_three_day_hold
from episode import build_episode

RULES = json.load(open(os.path.join(ROOT, "rules.json")))
P = lambda k: RULES["signals"][k]["params"]
RUN_TS = ts()
RUN_DATE = now_tpe().strftime("%Y-%m-%d")

def row(signal, target, value, threshold, status, data_date, sources, note="", extra=None):
    d = dict(signal=signal, target=target, value=value, threshold=threshold, status=status,
             status_label=STATUS_LABEL[status], data_date=data_date, sources=sources,
             queried_at=RUN_TS, note=note, asof=data_date)
    if extra: d["extra"] = extra
    return d

# ---------------- 行情（yfinance） ----------------
_px = {}
def daily(tk, start="2009-01-01"):
    if tk not in _px:
        import yfinance as yf
        d = yf.Ticker(tk).history(start=start, auto_adjust=False, actions=False)
        d.index = d.index.tz_localize(None).normalize()
        d = d.replace([np.inf, -np.inf], np.nan).dropna(subset=["Close"])
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
ETF_CACHE = os.path.join(DATA, "cache", "etf")
INVESCO_HEADERS = {
    "User-Agent": "vgi-etf-invesco/0.2.2",
    "Referer": "https://www.invesco.com/",
}
SOXX_JSON_URL = (
    "https://www.ishares.com/varnish-api/blk-one01-product-data/product-data/api/v2/get-product-data"
    "?appSubType=ISHARES&appType=PRODUCT_PAGE&component=fundDownload&locale=en_US"
    "&portfolioId=239705&targetSite=us-ishares&userType=individual&excludeContent=true"
)
SOXX_XLS_URL = (
    "https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/get-fund-document"
    "?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=us-ishares&locale=en_US"
    "&portfolioId=239705&component=fundDownload&userType=individual"
)
SOXX_PAGE_URL = "https://www.ishares.com/us/products/239705/ishares-semiconductor-etf"
QQQ_NAVS_URL = "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/navs?idType=ticker&productType=ETF"
QQQ_DETAILS_URL = "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ?idType=ticker&productType=ETF&variationType=fundDetails"
QQQ_PAGE_URL = "https://www.invesco.com/qqq-etf/en/about.html"
SMH_XLSX_URL = "https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/downloads/fundhistoprices/"
SMH_PAGE_URL = "https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/"


class EtfDataError(Exception):
    """官方檔抓不到、內容不是預期格式，或歷史不足以計算 5／21 日窗口。"""


def looks_like_html(raw):
    if raw is None:
        return False
    b = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8", "replace")
    head = b.lstrip()[:200].lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or head.startswith(b"<head")


def round_shares(val):
    """正數四捨五入到整數股（不用銀行家捨入）。缺值保持 NA。"""
    if isinstance(val, pd.Series):
        out = pd.Series(np.floor(pd.to_numeric(val, errors="coerce") + 0.5), index=val.index)
        return out
    if val is None or (isinstance(val, float) and (val != val)):
        return val
    return int(np.floor(float(val) + 0.5))


def drop_unchanged_rows(x, cols=("NAV", "SO")):
    """去掉 NAV 與流通股數都與上一列完全相同的重複列（週末／假日補列）。

    真實交易日即使沒有申贖，NAV 通常仍會變，會被保留，零流量日仍計入 5／21 日窗口。
    不把缺列補 0。
    """
    y = x.dropna(subset=list(cols)).sort_index()
    y = y[~y.index.duplicated(keep="last")]
    if y.empty:
        return y
    keep = [True]
    prev = y.iloc[0]
    for i in range(1, len(y)):
        cur = y.iloc[i]
        same = all(cur[c] == prev[c] for c in cols if c in y.columns)
        keep.append(not same)
        if not same:
            prev = cur
    return y.loc[y.index[np.asarray(keep, dtype=bool)]]


def etf_flow_frame(x, p):
    """x：DatetimeIndex，欄位 NAV、SO、TNA。不足 month_days+1 筆則失敗，絕不把缺漏當 0。"""
    y = drop_unchanged_rows(x, ("NAV", "SO"))
    need = int(p["month_days"]) + 1
    if len(y) < need:
        raise EtfDataError(f"交易日觀測只有 {len(y)} 筆，不足 {need} 筆（近一月 {p['month_days']} 日 + 期初）")
    f = y["SO"].diff() * y["NAV"]
    def win(n):
        fl = f.iloc[-n:].sum()
        aum0 = y["TNA"].iloc[-n - 1]
        if aum0 == 0 or pd.isna(aum0):
            raise EtfDataError("期初 AUM 為空或 0，不計算流量百分比")
        return float(fl), float(fl / aum0 * 100)
    wf, wp = win(p["week_days"])
    mf, mp = win(p["month_days"])
    last = y.index[-1].strftime("%Y-%m-%d")
    red_dates = []
    for i in range(p["month_days"] + 1, len(y)):
        wflow = f.iloc[i - p["week_days"] + 1:i + 1].sum()
        wbase = y["TNA"].iloc[i - p["week_days"]]
        mflow = f.iloc[i - p["month_days"] + 1:i + 1].sum()
        mbase = y["TNA"].iloc[i - p["month_days"]]
        if not wbase or not mbase or pd.isna(wbase) or pd.isna(mbase):
            continue
        if mflow / mbase * 100 <= p["m_trigger"] or wflow / wbase * 100 <= p["w_trigger"]:
            red_dates.append(y.index[i].strftime("%Y-%m-%d"))
    aum_chg = None
    if len(y) > p["month_days"]:
        base = y["TNA"].iloc[-p["month_days"] - 1]
        if base:
            aum_chg = (y["TNA"].iloc[-1] / base - 1) * 100
    return dict(week_flow_usd=wf, week_pct=wp, month_flow_usd=mf, month_pct=mp,
                aum=float(y["TNA"].iloc[-1]), shares=float(y["SO"].iloc[-1]),
                aum_change_1m_pct=aum_chg, obs_date=last, red_dates=red_dates, n_obs=int(len(y)),
                frame=y)


def etf_cache_path(name):
    return os.path.join(ETF_CACHE, name)


def write_etf_cache(name, content, binary=False):
    os.makedirs(ETF_CACHE, exist_ok=True)
    path = etf_cache_path(name)
    tmp = path + ".tmp"
    if binary:
        with open(tmp, "wb") as fh:
            fh.write(content)
    else:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
    os.replace(tmp, path)
    return path


def read_etf_cache(name, binary=False):
    path = etf_cache_path(name)
    if not os.path.exists(path):
        return None
    if binary:
        with open(path, "rb") as fh:
            return fh.read()
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def fetch_bytes(url, headers=None, retries=4, timeout=60, sleep=1.0):
    """帶重試的 GET。回傳 (content_bytes, content_type, status_code)。"""
    last = None
    hdrs = dict(headers or {})
    for i in range(retries):
        try:
            r = get(url, headers=hdrs, timeout=timeout)
            return r.content, (r.headers.get("Content-Type") or ""), r.status_code
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(sleep * (2 ** i))
    raise EtfDataError(f"抓取失敗（{url}）：{last}")


def parse_ishares_funddownload_json(raw):
    if looks_like_html(raw):
        raise EtfDataError("iShares fundDownload JSON 實際是 HTML，拒絕解析")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    try:
        j = json.loads(raw)
    except Exception as e:
        raise EtfDataError(f"iShares fundDownload 不是合法 JSON：{e}") from e
    try:
        dp = j["componentsByNameMap"]["fundDownload"]["containersByNameMap"]["historical"]["dataPointsByNameMap"]
        asof, nav, so = dp["asof"]["value"], dp["nav"]["value"], dp["sharesOutstanding"]["value"]
    except Exception as e:
        raise EtfDataError(f"iShares fundDownload JSON 缺少 historical 流通股數／NAV：{e}") from e
    if not asof or not nav or not so or len(asof) != len(nav) or len(asof) != len(so):
        raise EtfDataError("iShares fundDownload 欄位長度不一致或為空")
    rows = []
    for a, n, s in zip(asof, nav, so):
        if a is None or n is None or s is None:
            continue
        try:
            d = pd.Timestamp(dt.datetime.strptime(str(int(a)), "%Y%m%d"))
            rows.append((d, float(n), float(s)))
        except Exception:
            continue
    if not rows:
        raise EtfDataError("iShares fundDownload 沒有可解析的日期列")
    x = pd.DataFrame(rows, columns=["Date", "NAV", "SO"]).drop_duplicates("Date").set_index("Date").sort_index()
    x["TNA"] = x["SO"] * x["NAV"]
    return x


def parse_ishares_fund_xls(raw):
    """SpreadsheetML（.xls）備援。若 body 其實是 HTML，直接拒絕。"""
    if looks_like_html(raw):
        raise EtfDataError("iShares 歷史 xls 實際是 HTML，拒絕當成試算表")
    b = raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8")
    head = b.lstrip()[:80]
    if not (head.startswith(b"<?xml") or b"ss:Workbook" in b[:400] or b"<Workbook" in b[:400]):
        raise EtfDataError("iShares 歷史檔不是 SpreadsheetML")
    import xml.etree.ElementTree as ET
    ns = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}
    try:
        root = ET.fromstring(b)
    except Exception as e:
        raise EtfDataError(f"iShares xls XML 解析失敗：{e}") from e
    target = None
    for ws in root.findall("ss:Worksheet", ns):
        name = ws.get("{urn:schemas-microsoft-com:office:spreadsheet}Name") or ""
        if name.lower() in ("historical", "historical navs"):
            target = ws
            break
    if target is None:
        sheets = root.findall("ss:Worksheet", ns)
        target = sheets[0] if sheets else None
    if target is None:
        raise EtfDataError("iShares xls 沒有工作表")
    rows = []
    for row in target.findall(".//ss:Row", ns):
        vals = []
        for cell in row.findall("ss:Cell", ns):
            data = cell.find("ss:Data", ns)
            vals.append("" if data is None or data.text is None else data.text)
        rows.append(vals)
    if not rows:
        raise EtfDataError("iShares xls 沒有列")
    header_i, mapping = None, {}
    for i, vals in enumerate(rows[:20]):
        low = [v.strip().lower() for v in vals]
        def col(*names):
            for n in names:
                if n in low:
                    return low.index(n)
            return None
        di, ni, si = col("as of", "asof", "date"), col("nav per share", "nav"), col("shares outstanding", "sharesoutstanding")
        if di is not None and ni is not None and si is not None:
            header_i, mapping = i, dict(date=di, nav=ni, so=si)
            break
    if header_i is None:
        raise EtfDataError("iShares xls 找不到 As Of／NAV／Shares Outstanding 欄")
    out = []
    for vals in rows[header_i + 1:]:
        if max(mapping.values(), default=0) >= len(vals):
            continue
        ds, ns, ss = vals[mapping["date"]], vals[mapping["nav"]], vals[mapping["so"]]
        d = pd.to_datetime(ds, errors="coerce")
        try:
            n, s = float(str(ns).replace(",", "")), float(str(ss).replace(",", ""))
        except Exception:
            continue
        if pd.isna(d) or not n:
            continue
        out.append((d, n, s))
    if not out:
        raise EtfDataError("iShares xls Historical 沒有可解析的列")
    x = pd.DataFrame(out, columns=["Date", "NAV", "SO"]).drop_duplicates("Date").set_index("Date").sort_index()
    x["TNA"] = x["SO"] * x["NAV"]
    return x


def parse_invesco_navs(raw):
    if looks_like_html(raw):
        raise EtfDataError("Invesco navs 實際是 HTML，拒絕解析")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    try:
        data = json.loads(raw)
    except Exception as e:
        raise EtfDataError(f"Invesco navs 不是合法 JSON：{e}") from e
    if not isinstance(data, list) or not data:
        raise EtfDataError("Invesco navs 不是非空陣列")
    rows = []
    for rec in data:
        d = pd.to_datetime(rec.get("effectiveDate"), errors="coerce")
        nav = rec.get("netAssetValue")
        tna = rec.get("shareclassTotalNetAssets")
        if tna is None:
            tna = rec.get("totalNetAssets")
        if pd.isna(d) or nav is None or tna is None:
            continue
        try:
            nav, tna = float(nav), float(tna)
        except Exception:
            continue
        if not nav:
            continue
        rows.append((d, nav, tna))
    if not rows:
        raise EtfDataError("Invesco navs 沒有可解析的日期列")
    x = pd.DataFrame(rows, columns=["Date", "NAV", "TNA"]).drop_duplicates("Date").set_index("Date").sort_index()
    x["SO"] = round_shares(x["TNA"] / x["NAV"])
    return x


def parse_invesco_fund_details(raw):
    if looks_like_html(raw):
        raise EtfDataError("Invesco fundDetails 實際是 HTML，拒絕解析")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    try:
        j = json.loads(raw)
    except Exception as e:
        raise EtfDataError(f"Invesco fundDetails 不是合法 JSON：{e}") from e
    so, asof = j.get("sharesOutstanding"), j.get("effectiveDate") or j.get("effectiveBusinessDate")
    if so is None:
        raise EtfDataError("Invesco fundDetails 沒有 sharesOutstanding")
    return dict(shares=int(round_shares(so)), asof=str(asof) if asof else None, nav=j.get("nav"), tna=j.get("shareclassTotalNetAssets"))


def parse_vaneck_histoprices(raw):
    if looks_like_html(raw):
        raise EtfDataError("VanEck fundhistoprices 實際是 HTML，拒絕當成 xlsx")
    b = raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8")
    if not b.startswith(b"PK"):
        raise EtfDataError("VanEck fundhistoprices 不是 xlsx（缺少 ZIP 檔頭）")
    try:
        df = pd.read_excel(io.BytesIO(b), header=1)
    except Exception as e:
        raise EtfDataError(f"VanEck xlsx 解析失敗：{e}") from e
    cols = {str(c).strip().lower(): c for c in df.columns}
    if "date" not in cols or "nav" not in cols or "aum" not in cols:
        raise EtfDataError(f"VanEck xlsx 缺少 Date／NAV／AUM 欄，實際欄位：{list(df.columns)}")
    x = pd.DataFrame({
        "Date": pd.to_datetime(df[cols["date"]], errors="coerce"),
        "NAV": pd.to_numeric(df[cols["nav"]].astype(str).str.replace(",", ""), errors="coerce"),
        "TNA": pd.to_numeric(df[cols["aum"]].astype(str).str.replace(",", ""), errors="coerce"),
    }).dropna(subset=["Date", "NAV", "TNA"])
    x = x[x["NAV"] != 0].drop_duplicates("Date").set_index("Date").sort_index()
    if x.empty:
        raise EtfDataError("VanEck xlsx 沒有可解析的列")
    x["SO"] = round_shares(x["TNA"] / x["NAV"])
    return x


def _sig1_status(wp, mp, p):
    if mp <= p["m_trigger"] or wp <= p["w_trigger"]:
        return RED
    if mp <= p["m_near"] or wp <= p["w_near"]:
        return YEL
    return WHT


def _sig1_value_text(fl):
    return (f"近1週 {fl['week_flow_usd']/1e9:+.2f}B 美元（{fl['week_pct']:+.2f}% AUM）；"
            f"近1月 {fl['month_flow_usd']/1e9:+.2f}B（{fl['month_pct']:+.2f}% AUM）；"
            f"AUM {fl['aum']/1e9:.1f}B；流通股數 {fl['shares']/1e6:.2f}M")


def _sig1_row(target, fl, sources, note, stale=False, stale_reason=""):
    p = P("1")
    extra = dict(week_flow_usd=fl["week_flow_usd"], week_pct=fl["week_pct"],
                 month_flow_usd=fl["month_flow_usd"], month_pct=fl["month_pct"],
                 aum=fl["aum"], shares=fl["shares"], aum_change_1m_pct=fl["aum_change_1m_pct"],
                 obs_date=fl["obs_date"], red_dates=fl["red_dates"], n_obs=fl["n_obs"])
    r = row("1", target, _sig1_value_text(fl), RULES["signals"]["1"]["trigger"],
            _sig1_status(fl["week_pct"], fl["month_pct"], p), fl["obs_date"], sources, note, extra)
    if stale:
        r["stale"] = True
        r["stale_since"] = fl["obs_date"]
        r["stale_reason"] = stale_reason
    return r


def _load_soxx_history():
    """回傳 (frame, sources, note_src, stale, err_detail)。優先官方 JSON，xls 備援，再退快取。"""
    errors = []
    sources = [SOXX_JSON_URL, SOXX_XLS_URL, SOXX_PAGE_URL]
    # JSON
    try:
        raw, ctype, _ = fetch_bytes(SOXX_JSON_URL)
        if looks_like_html(raw):
            raise EtfDataError(f"JSON 端點回了 HTML（Content-Type={ctype}）")
        x = parse_ishares_funddownload_json(raw)
        write_etf_cache("soxx_funddownload.json", raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8"), True)
        return x, sources, "以 iShares 官方 fundDownload JSON 的每日流通股數 × NAV 推算", False, ""
    except Exception as e:
        errors.append(f"JSON：{e}")
    # xls 備援
    try:
        raw, ctype, _ = fetch_bytes(SOXX_XLS_URL)
        if looks_like_html(raw):
            raise EtfDataError(f"xls 端點回了 HTML（Content-Type={ctype}）")
        x = parse_ishares_fund_xls(raw)
        write_etf_cache("soxx_fund.xls", raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8"), True)
        return x, sources, "以 iShares 官方 fundDownload xls（JSON 失敗後的備援）每日流通股數 × NAV 推算", False, ""
    except Exception as e:
        errors.append(f"xls：{e}")
    # 快取
    cached = read_etf_cache("soxx_funddownload.json", True)
    if cached:
        try:
            x = parse_ishares_funddownload_json(cached)
            why = "；".join(errors)
            return x, sources, "以 iShares 官方 fundDownload JSON 快取的每日流通股數 × NAV 推算", True, why
        except Exception as e:
            errors.append(f"JSON快取：{e}")
    cached = read_etf_cache("soxx_fund.xls", True)
    if cached:
        try:
            x = parse_ishares_fund_xls(cached)
            why = "；".join(errors)
            return x, sources, "以 iShares 官方 fundDownload xls 快取的每日流通股數 × NAV 推算", True, why
        except Exception as e:
            errors.append(f"xls快取：{e}")
    raise EtfDataError("；".join(errors) or "SOXX 無資料")


def _load_qqq_history():
    sources = [QQQ_NAVS_URL, QQQ_DETAILS_URL, QQQ_PAGE_URL]
    errors = []
    x = None
    stale = False
    note_src = "以 Invesco 官方每日 NAV 與總資產（TNA÷NAV，四捨五入為整數股）推算流通股數再 × NAV"
    try:
        raw, ctype, _ = fetch_bytes(QQQ_NAVS_URL, headers=INVESCO_HEADERS, retries=4, sleep=1.0)
        if looks_like_html(raw):
            raise EtfDataError(f"navs 端點回了 HTML（Content-Type={ctype}）")
        x = parse_invesco_navs(raw)
        write_etf_cache("qqq_navs.json", raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8"), True)
    except Exception as e:
        errors.append(f"navs：{e}")
        cached = read_etf_cache("qqq_navs.json", True)
        if cached:
            try:
                x = parse_invesco_navs(cached)
            except Exception as e2:
                errors.append(f"navs快取：{e2}")
                raise EtfDataError("；".join(errors)) from e2
            stale = True
            note_src = "以 Invesco 官方 navs 快取的每日 NAV 與總資產推算流通股數再 × NAV"
        else:
            raise EtfDataError("；".join(errors) or "QQQ 無資料") from e
    # fundDetails 交叉核對（失敗只加註，不擋計算、不把缺漏當 0）
    cross = ""
    try:
        draw, _, _ = fetch_bytes(QQQ_DETAILS_URL, headers=INVESCO_HEADERS, retries=3, sleep=1.0)
        det = parse_invesco_fund_details(draw)
        last_so = int(x["SO"].iloc[-1])
        official = det["shares"]
        asof = det.get("asof") or ""
        if official is not None:
            diff = abs(last_so - official)
            if diff <= 1:
                cross = f"fundDetails 當日官方流通股數 {official:,.0f}（{asof or '日期未標'}）與 TNA÷NAV 推算值一致。"
            else:
                cross = (f"fundDetails 當日官方流通股數 {official:,.0f}（{asof or '日期未標'}）"
                         f"與序列末日推算值 {last_so:,.0f} 相差 {diff:,.0f} 股；流量仍用 TNA÷NAV 序列，未改寫。")
    except Exception as e:
        cross = f"fundDetails 交叉核對本次未成功（{e}），不影響以 navs 序列計算的流量。"
    return x, sources, note_src, stale, "；".join(errors), cross


def _load_smh_history():
    sources = [SMH_XLSX_URL, SMH_PAGE_URL]
    errors = []
    try:
        raw, ctype, _ = fetch_bytes(SMH_XLSX_URL)
        if looks_like_html(raw):
            raise EtfDataError(f"xlsx 端點回了 HTML（Content-Type={ctype}）")
        x = parse_vaneck_histoprices(raw)
        write_etf_cache("smh_fundhistoprices.xlsx", raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8"), True)
        return x, sources, "以 VanEck 官方 fundhistoprices 每日 NAV 與 AUM 推算流通股數（四捨五入為整數股）再 × NAV", False, ""
    except Exception as e:
        errors.append(str(e))
    cached = read_etf_cache("smh_fundhistoprices.xlsx", True)
    if cached:
        try:
            x = parse_vaneck_histoprices(cached)
        except Exception as e2:
            errors.append(f"xlsx快取：{e2}")
            raise EtfDataError("；".join(errors)) from e2
        return x, sources, "以 VanEck 官方 fundhistoprices 快取的每日 NAV 與 AUM 推算流通股數（四捨五入為整數股）再 × NAV", True, "；".join(errors)
    raise EtfDataError("；".join(errors) or "SMH 無資料")


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
        red_dates = []
        for i in range(p["month_days"] + 1, len(x)):
            wflow = f.iloc[i - p["week_days"] + 1:i + 1].sum(); wbase = x["TNA"].iloc[i - p["week_days"]]
            mflow = f.iloc[i - p["month_days"] + 1:i + 1].sum(); mbase = x["TNA"].iloc[i - p["month_days"]]
            if not wbase or not mbase: continue
            if mflow / mbase * 100 <= p["m_trigger"] or wflow / wbase * 100 <= p["w_trigger"]:
                red_dates.append(x.index[i].strftime("%Y-%m-%d"))
        rows.append(row("1", "SPY", f"近1週 {wf/1e9:+.2f}B 美元（{wp:+.2f}% AUM）；近1月 {mf/1e9:+.2f}B（{mp:+.2f}% AUM）；AUM {x['TNA'].iloc[-1]/1e9:.1f}B；流通股數 {x['SO'].iloc[-1]/1e6:.1f}M",
            RULES["signals"]["1"]["trigger"], st, last, [url],
            "以官方每日流通股數變動×NAV 推算；SSGA 檔案約延遲 1 個交易日。",
            dict(week_flow_usd=wf, week_pct=wp, month_flow_usd=mf, month_pct=mp, aum=x["TNA"].iloc[-1], shares=x["SO"].iloc[-1],
                 aum_change_1m_pct=(x["TNA"].iloc[-1]/x["TNA"].iloc[-22]-1)*100, obs_date=last, red_dates=red_dates)))
    except Exception as e:
        rows.append(row("1", "SPY", None, RULES["signals"]["1"]["trigger"], UNK, None, [url], f"抓取失敗：{e}"))

    # SOXX：iShares 官方 fundDownload 歷史（JSON 為主、xls 備援；失敗用快取）
    try:
        x, sources, note_src, stale, err = _load_soxx_history()
        fl = etf_flow_frame(x, p)
        note = f"{note_src}；資料截至 {fl['obs_date']}。"
        stale_reason = ""
        if stale:
            note += f"資料過期：本次抓取失敗（{err[:160]}），沿用 data/cache/etf 舊序列，未把缺漏當 0。"
            stale_reason = f"{STALE_PREFIX}：本次查詢（{RUN_TS}）抓取失敗（{err[:160]}），沿用快取序列，資料截至 {fl['obs_date']}。"
        rows.append(_sig1_row("SOXX", fl, sources, note, stale=stale, stale_reason=stale_reason))
    except Exception as e:
        rows.append(row("1", "SOXX", None, RULES["signals"]["1"]["trigger"], UNK, None,
                        [SOXX_JSON_URL, SOXX_XLS_URL, SOXX_PAGE_URL],
                        f"抓取失敗：{e}；不以資產規模縮水代替資金外流，也不把缺漏當 0。"))

    # QQQ：Invesco navs（TNA÷NAV），fundDetails 交叉核對
    try:
        x, sources, note_src, stale, err, cross = _load_qqq_history()
        fl = etf_flow_frame(x, p)
        note = f"{note_src}；資料截至 {fl['obs_date']}。"
        if cross:
            note += cross
        stale_reason = ""
        if stale:
            note += f"資料過期：本次抓取失敗（{err[:160]}），沿用 data/cache/etf 舊序列，未把缺漏當 0。"
            stale_reason = f"{STALE_PREFIX}：本次查詢（{RUN_TS}）抓取失敗（{err[:160]}），沿用快取序列，資料截至 {fl['obs_date']}。"
        rows.append(_sig1_row("QQQ", fl, sources, note, stale=stale, stale_reason=stale_reason))
    except Exception as e:
        rows.append(row("1", "QQQ", None, RULES["signals"]["1"]["trigger"], UNK, None,
                        [QQQ_NAVS_URL, QQQ_DETAILS_URL, QQQ_PAGE_URL],
                        f"抓取失敗：{e}；不以資產規模縮水代替資金外流，也不把缺漏當 0。"))

    # SMH：VanEck 官方 fundhistoprices
    try:
        x, sources, note_src, stale, err = _load_smh_history()
        fl = etf_flow_frame(x, p)
        note = f"{note_src}；資料截至 {fl['obs_date']}。"
        stale_reason = ""
        if stale:
            note += f"資料過期：本次抓取失敗（{err[:160]}），沿用 data/cache/etf 舊序列，未把缺漏當 0。"
            stale_reason = f"{STALE_PREFIX}：本次查詢（{RUN_TS}）抓取失敗（{err[:160]}），沿用快取序列，資料截至 {fl['obs_date']}。"
        rows.append(_sig1_row("SMH", fl, sources, note, stale=stale, stale_reason=stale_reason))
    except Exception as e:
        rows.append(row("1", "SMH", None, RULES["signals"]["1"]["trigger"], UNK, None,
                        [SMH_XLSX_URL, SMH_PAGE_URL],
                        f"抓取失敗：{e}；不以資產規模縮水代替資金外流，也不把缺漏當 0。"))
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
            obs = w.index[-1].strftime("%Y-%m-%d")
            red_dates = [i.strftime("%Y-%m-%d") for i, v in k["J"].dropna().items() if v <= p["trigger"]]
            rows.append(row("3", name, f"J={j:.1f}（K={k['K'].iloc[-1]:.1f}, D={k['D'].iloc[-1]:.1f}；前一週 J={prev:.1f}）",
                "週 J ≤ 0", st, f"週線截至 {obs}（已完成週）", [yf_src(tk)],
                "週K由 Yahoo 日線重組，KDJ(9,3,3)。觸發日＝該週週五。", dict(J=j, K=k['K'].iloc[-1], D=k['D'].iloc[-1], J_prev=prev,
                     obs_date=obs, red_dates=red_dates,
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
            today = pd.Timestamp(now_tpe().date())
            end = min(cur_date, today - pd.Timedelta(days=1))
            red_dates = []
            fri_pct = None
            last_fri = None
            if end >= ow.index[0]:
                for fri in pd.date_range(end=end, periods=8, freq="W-FRI"):
                    hist_own = ow.loc[:fri]
                    if hist_own.empty: continue
                    pe_f = hist_own.iloc[-1]
                    hist = ser[(ser.index <= fri) & (ser.index >= fri - pd.DateOffset(years=p["window_years"]))]
                    if len(hist) < 10: continue
                    pct_f = (hist < pe_f).mean() * 100
                    last_fri, fri_pct = fri, pct_f
                    if pct_f <= p["pct_trigger"]:
                        red_dates.append(fri.strftime("%Y-%m-%d"))
            # 週線訊號：燈號看最近一根已完成週五，不看週中的日線讀數
            judge = fri_pct if fri_pct is not None else p10
            st = RED if judge <= p["pct_trigger"] else YEL if judge <= p["pct_near"] else WHT
            obs_date = last_fri.strftime("%Y-%m-%d") if last_fri is not None else cur_date.strftime("%Y-%m-%d")
            px = daily(tk)["Close"]
            eps = (px.reindex(ow.index, method="ffill") / ow).dropna()
            e1 = (eps.iloc[-1] / eps.iloc[-21] - 1) * 100 if len(eps) > 21 else None
            e3 = (eps.iloc[-1] / eps.iloc[0] - 1) * 100
            flag = " ⚠️EPS 下修中，可能為假便宜" if e3 < p["eps_down_flag"] else ""
            rows.append(row("4", name, f"NTM P/E {cur:.2f}×（{cur_date.strftime('%Y-%m-%d')}）；10年分位 {p10:.0f}%（5年 {p5:.0f}%）；最近週五分位 {judge:.0f}%；隱含 NTM EPS 約1個月 {e1:+.1f}%、自 {eps.index[0].strftime('%m/%d')} 起 {e3:+.1f}%{flag}",
                "10 年分位 ≤ 20%", st, obs_date, [page, url],
                f"單一資料商 History of Market；歷史分位以終端機週頻序列（{s10.strftime('%Y-%m')}–{ser.index[-1].strftime('%Y-%m-%d')}，{n10} 筆）計算，最新值為同口徑自算日線 forwardOwn（官方說明與終端機序列差距 <3%）。EPS 由指數收盤÷本益比推得，非資料商直接公布。",
                dict(pe=cur, pct10=p10, pct5=p5, eps_chg_1m=e1, eps_chg_since=e3, eps_since=eps.index[0].strftime('%Y-%m-%d'), terminal_last=ser.index[-1].strftime('%Y-%m-%d'),
                     obs_date=obs_date, red_dates=red_dates, friday_pct10=fri_pct)))
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
        red_dates = []
        twa_all = tw["marginAmt"].astype(float)
        for dt_i in comb.index[-20:]:
            window = comb.loc[:dt_i].iloc[-p["lookback_days"]:]
            if len(window) < 20 or dt_i not in twa_all.index or dt_i not in tw.index: continue
            dd_i = (window.iloc[-1] / window.max() - 1) * 100
            mv = tw["marketValue"].loc[dt_i]
            if not mv: continue
            ratio_i = twa_all.loc[dt_i] / 1e5 / mv * 100
            if dd_i <= p["dd_trigger"] and ratio_i <= base:
                red_dates.append(dt_i.strftime("%Y-%m-%d"))
        src = ["https://www.twse.com.tw/zh/trading/margin/mi-margn.html", TW + "MI_MARGN_TREND?response=json&days=60", "https://www.tpex.org.tw/zh-tw/mainboard/trading/margin-trading/transactions.html", TW + "MI_MARGN_HISTORY?response=json"]
        rows.append(row("7", "上市＋上櫃融資餘額合計", f"{comb.iloc[-1]/1e5:,.0f} 億元；較 {len(tw)} 日高點（{hi_c.strftime('%Y-%m-%d')}）{dd_c:+.1f}%；上市融資／上市市值 {ratio:.3f}%（近{p['ratio_years']}年年底中位數 {base:.2f}%，{'≤' if ratio_ok else '>'}基準）",
            "回落 ≥20% 且 比重 ≤ 基準", st, ld.strftime("%Y-%m-%d"), src,
            f"上櫃日資料涵蓋 {len(ot)}/{len(tw)} 日；比重僅計上市（櫃買總市值未納入）。",
            dict(total_100m=comb.iloc[-1]/1e5, dd_pct=dd_c, high_date=hi_c.strftime('%Y-%m-%d'), ratio_pct=ratio, ratio_base=base, coverage=cover,
                 obs_date=ld.strftime("%Y-%m-%d"), red_dates=red_dates)))
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
        pub_m = re.search(r"(\d{4}-\d{2}-\d{2})", release_note or "")
        pub = pub_m.group(1) if pub_m else None
        def month_end(yyyymm):
            y, mth = int(yyyymm[:4]), int(yyyymm[4:6])
            nxt = dt.date(y + 1, 1, 1) if mth == 12 else dt.date(y, mth + 1, 1)
            return (nxt - dt.timedelta(days=1)).isoformat()
        red10 = []
        for i in range(len(vals)):
            streak = 0
            for _, vv in reversed(vals[:i + 1]):
                if vv < p["level"]: streak += 1
                else: break
            if streak >= 3:
                red10.append(pub if (i == len(vals) - 1 and pub) else month_end(vals[i][0]))
        obs10 = pub or month_end(vals[-1][0])
        mm = lambda s: f"{s[:4]}/{s[4:]}"
        revtxt = ("；與上次存檔相比之修正：" + "；".join(rev)) if rev else "；首次建立，尚無前一版本可比對修正（每月發布會回溯修正歷史值）"
        rows.append(row("10", "領先指標不含趨勢指數", "；".join(f"{mm(a)} {v:.2f}" for a, v in vals[-4:]) + f"（連續低於100：{below} 個月）",
            "連續3個月皆 <100", st, f"{mm(vals[-1][0])}（{release_note}）", [NDC_DS, "https://index.ndc.gov.tw/n/zh_tw/data/eco", url],
            f"資料檔更新時間 {mt}" + revtxt, dict(values=vals, below_streak=below, revisions=rev, obs_date=obs10, red_dates=red10)))
        sc = df[["Date", "景氣對策信號綜合分數", "景氣對策信號"]].tail(6)
        sv = [(r_["Date"], int(r_["景氣對策信號綜合分數"]), r_["景氣對策信號"]) for _, r_ in sc.iterrows()]
        s = sv[-1][1]; p = P("11")
        st = RED if s <= p["blue_max"] else YEL if s <= p["yb_max"] else WHT
        red11 = []
        for i in range(len(sv)):
            if sv[i][1] <= p["blue_max"]:
                red11.append(pub if (i == len(sv) - 1 and pub) else month_end(sv[i][0]))
        obs11 = pub or month_end(sv[-1][0])
        rows.append(row("11", "景氣對策信號", f"{mm(sv[-1][0])} {s} 分 {sv[-1][2]}燈；近月：" + "、".join(f"{mm(a)} {b}({c})" for a, b, c in sv[-6:-1]),
            "藍燈（9–16分）", st, f"{mm(sv[-1][0])}（{release_note}）", [NDC_DS, "https://www.ndc.gov.tw/nc_332_40618", url],
            "歷史修正：國發會每月回溯修正；本表以最新一版資料檔為準" + (revtxt if rev else "") ,
            dict(series=sv, obs_date=obs11, red_dates=red11)))
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
        elif k == "6": sts = [x["status"] for x in rs if x["target"].startswith("大盤融資維持率（扣除ETF")]
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
            # 來源改走快取、列上已標 stale 時，也列入本次 stale_rows，讓頁面顯示「資料過期」
            if r_.get("stale"):
                key = f"{r_['signal']}｜{r_['target']}"
                if key not in stale:
                    stale.append(key)
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
    # v2（2026-11-10 依 COBB 指正）：第 2、5、6、9 項改用 signals_v2；v1 函式保留供對照
    s2 = lambda: v2.sig2_v2(row, completed_daily, P)
    s5 = lambda: v2.sig5_v2(row, completed_daily, P)
    s6 = lambda: v2.sig6_v2(row, P)
    s8 = lambda: v2.sig8_v2(row, completed_daily, weekly, yf_src, P)
    s9 = lambda: v2.sig9_v2(row, completed_daily, weekly, MKT, yf_src, P)
    for k, f in (("1", sig1), ("2", s2), ("3", sig3), ("4", sig4), ("5", s5), ("6", s6), ("7", sig7), ("8", s8), ("9", s9)):
        print("running signal", k, flush=True)
        try: rows += f()
        except Exception as e:
            traceback.print_exc(); failed[k] = e
    try: rows += sig10_11(release_note)
    except Exception as e:
        traceback.print_exc(); failed["10"] = e; failed["11"] = e
    rows, stale = apply_stale_fallback(rows, prev, failed)
    rows = apply_three_day_hold(rows, dt.date.fromisoformat(RUN_DATE))
    summ = summarize(rows)
    try:
        ndx_px = completed_daily("^NDX", NY, (16, 15))["Close"]
        episode = build_episode(rows, ndx_px, [(k, cfg["name"]) for k, cfg in RULES["signals"].items()])
    except Exception as e:
        traceback.print_exc()
        episode = {"error": str(e), "rows": []}
    changes = []
    has_prev = bool(prev and prev.get("queried_at") and prev.get("queried_at") != RUN_TS)
    if has_prev:
        for k, v in summ.items():
            o = prev["summary"].get(k, {}).get("status")
            if o and o != v["status"]: changes.append(f"第{k}項 {v['name']}：{o} → {v['status']}")
    out = dict(schema="bottom-signals/v1", site=SITE_NAME, rules_version=RULES["version"], run_date=RUN_DATE,
               queried_at=RUN_TS, previous_run=prev.get("queried_at") if has_prev else None,
               changes_vs_previous=(changes or "與上次相比，整項燈號沒有變化") if has_prev else "首次建立，無上次紀錄",
               stale_rows=stale, summary=summ, episode=episode, rows=rows)
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
