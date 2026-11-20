#!/usr/bin/env python3
"""證交所「上市融資餘額」序列（/trend/ 走勢頁的圖表用）。與 11 項燈號規則無關。

燈號第 6、7 項自己有一套抓法（scripts/run.py、scripts/margin_ratio.py、scripts/signals_v2.py），
這裡完全不碰它們、也不被它們讀取；這個模組只產生走勢頁的圖表資料。

來源：信用交易統計 MI_MARGN，selectType=MS（市場合計三列）
  主要 https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?response=json&date=YYYYMMDD&selectType=MS
  備用 https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date=YYYYMMDD&selectType=MS
tables[0].fields = 項目、買進、賣出、現金(券)償還、前日餘額、今日餘額
  融資(交易單位)：融資張數　融券(交易單位)：融券張數　融資金額(仟元)：融資金額（仟元；億元＝仟元／100000）

口徑：證交所當晚公布的「今日餘額」是初值；次一交易日報表的「前日餘額」才是確定值（證交所附註：
請以『前日餘額』為準）。所以重建序列時：
  1. 收集每日原始報告（每份都有該日的今日餘額與前日餘額），依日期排序。
  2. 日期 D 若有「相鄰的下一個交易日」N 的報告：D 的值＝N 的前日餘額，provisional=False。
  3. 序列最後一日用該日的今日餘額，provisional=True（初值）。
  4. 不用第一日的前日餘額去發明更早的一天（不知道那天的日期）。
  5. 中間缺日時，不把 N 的前日餘額寫到不相鄰的更早那筆（維持那筆的今日餘額當占位）。
結果與報告套用順序無關。

快取（data/cache/trend/twse/ 底下，不進版控；CI 的 actions/cache 已涵蓋）：
  margin/raw/YYYYMMDD.json      MI_MARGN MS 原始 JSON（只存 stat=OK 的）
  margin/trend_history.json     MI_MARGN_TREND 回補的約五年「今日餘額」種子（本模組只讀不寫）
  margin/history.json           本模組重建後的序列（每次覆寫）
平常只抓「快取最後一日之後到今天（台北）」的工作日，外加重抓最後 1 個已有日；請求間隔 3 秒。
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

URLS = (
    "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
    "https://www.twse.com.tw/exchangeReport/MI_MARGN",
)
PAUSE_SEC = 3.0
TRIES = 3
UA = "charity-poker-king/1.0 (https://github.com/manolomoo/charity-poker-king; trend data)"
TW_TZ = ZoneInfo("Asia/Taipei")
K_PER_YI = 100_000  # 仟元 → 億元
REFETCH_LAST = 1  # 有既有序列時，再重抓最後幾個已有日（防日檔不完整）
MAX_NEW_DAYS = 20  # 一次最多補幾個工作日；更久沒更新就只補最近的，不做長回補
PUBLISH_HOUR = 20  # 台北 20:00 前當天一定還沒公布，不浪費請求
SOURCE = "臺灣證券交易所 信用交易統計（MI_MARGN，selectType=MS）"
SOURCE_NOTE = "資料：證交所（今日餘額為初值）"
SOURCE_DETAIL = (
    "上市融資金額（仟元）。證交所當晚公布的今日餘額是初值，次一交易日的前日餘額才是確定值；"
    "本站在拿到次一交易日報表後改用前日餘額。較早的歷史以 MI_MARGN_TREND 的今日餘額補位。"
)
UNPUBLISHED_HINTS = ("沒有符合", "無符合", "沒有資料", "查無資料")


class TwseMarginError(Exception):
    """融資來源或資料內容的問題；呼叫端應 fail-soft（沿用上次）。"""


# ---------------- 解析 ----------------
def _int(s) -> int:
    t = str(s).strip().replace(",", "")
    if not t or not t.lstrip("-").isdigit():
        raise TwseMarginError(f"無法解析數字：{s!r}")
    return int(t)


def _iso_from_payload(payload: dict) -> str:
    d = str(payload.get("date") or "").strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    title = str(((payload.get("tables") or [{}])[0] or {}).get("title") or "")
    try:
        import twse_taiex as TWSE  # 只借民國年日期轉換

        return TWSE.roc_to_iso(title.split()[0])
    except Exception as e:
        raise TwseMarginError(f"找不到報表日期：date={d!r} title={title!r}") from e


def is_unpublished(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    stat = str(payload.get("stat") or "")
    return stat != "OK" and any(h in stat for h in UNPUBLISHED_HINTS)


def parse_report(payload) -> dict | None:
    """MI_MARGN MS 的 JSON → 一日報告；尚未公布（沒有符合條件的資料）回傳 None。

    回傳 {date, margin_amt_k, margin_amt_prev_k, margin_shares, margin_shares_prev,
          short_shares, short_shares_prev}（今日＝初值，*_prev＝前一交易日的確定值）。
    格式不對丟 TwseMarginError。"""
    if not isinstance(payload, dict):
        raise TwseMarginError("回應不是 JSON 物件")
    if is_unpublished(payload):
        return None
    stat = str(payload.get("stat") or "")
    if stat != "OK":
        raise TwseMarginError(f"stat={stat!r}")
    tables = payload.get("tables") or []
    if not tables or not isinstance(tables[0], dict):
        if not tables:
            return None  # stat=OK 卻沒有表：當成尚未公布
        raise TwseMarginError("tables[0] 格式不對")
    t = tables[0]
    fields = [str(f).strip() for f in t.get("fields") or []]
    try:
        i_prev, i_today = fields.index("前日餘額"), fields.index("今日餘額")
    except ValueError as e:
        raise TwseMarginError(f"欄位不對：{fields}") from e
    amt = shares = short = None
    for rec in t.get("data") or []:
        if not rec or len(rec) <= max(i_prev, i_today):
            continue
        name = str(rec[0]).strip()
        pair = (_int(rec[i_today]), _int(rec[i_prev]))
        if "融資金額" in name:
            amt = pair
        elif name.startswith("融資(") or name.startswith("融資（"):
            shares = pair
        elif "融券" in name:
            short = pair
    if amt is None:
        if not t.get("data"):
            return None
        raise TwseMarginError("找不到「融資金額」列")
    out = {"date": _iso_from_payload(payload), "margin_amt_k": amt[0], "margin_amt_prev_k": amt[1]}
    out["margin_shares"], out["margin_shares_prev"] = shares if shares else (None, None)
    out["short_shares"], out["short_shares_prev"] = short if short else (None, None)
    return out


# ---------------- 換算 ----------------
def k_to_yi(k, digits: int | None = 4):
    if k is None:
        return None
    v = k / K_PER_YI
    return round(v, digits) if digits is not None else v


def short_to_margin_pct(short_shares, margin_shares, digits: int = 2):
    """券資比＝融券張數／融資張數×100。"""
    if not short_shares or not margin_shares:
        return None if short_shares is None or not margin_shares else 0.0
    return round(short_shares / margin_shares * 100, digits)


# ---------------- 重建序列 ----------------
def _d(s: str) -> date:
    return date.fromisoformat(s)


def _no_weekday_between(a: str, b: str) -> bool:
    d = _d(a) + timedelta(days=1)
    end = _d(b)
    while d < end:
        if d.weekday() < 5:
            return False
        d += timedelta(days=1)
    return True


def rebuild_series(reports, seed_rows=None) -> list[dict]:
    """MS 日報（list 或 {date: report}）＋種子（TREND／上次網站序列）→ 依日期排序的序列。

    每列 {date, margin_amt_k, margin_shares, short_shares, provisional, source}。
    source：MS（MS 今日餘額，初值）、MS_PREV（次一交易日 MS 的前日餘額，確定值）、TREND、SITE（種子）。
    與套用順序無關：先全部收齊、排序後才覆寫。"""
    reps = {}
    for r in (reports.values() if isinstance(reports, dict) else reports or []):
        if r:
            reps[r["date"]] = r
    rows: dict[str, dict] = {}
    seeds = sorted((s for s in seed_rows or [] if s and s.get("date") and s.get("margin_amt_k") is not None),
                   key=lambda s: s["date"])
    for s in seeds:
        if s["date"] in rows:
            continue  # 前面的種子優先（呼叫端先給 TREND 再給 SITE）
        rows[s["date"]] = {
            "date": s["date"],
            "margin_amt_k": int(s["margin_amt_k"]),
            "margin_shares": s.get("margin_shares"),
            "short_shares": s.get("short_shares"),
            "provisional": bool(s.get("provisional", True)),
            "source": s.get("source") or "SEED",
        }
    for d, r in reps.items():
        rows[d] = {
            "date": d,
            "margin_amt_k": r["margin_amt_k"],
            "margin_shares": r.get("margin_shares"),
            "short_shares": r.get("short_shares"),
            "provisional": True,
            "source": "MS",
        }
    dates = sorted(rows)
    # 種子連續涵蓋的日期區間：在這段裡，序列上的前一筆就是真正的前一交易日（日曆來自種子）
    seed_dates = {s["date"] for s in seeds}
    for k in range(1, len(dates)):
        n, d = dates[k], dates[k - 1]
        rep = reps.get(n)
        if rep is None:
            continue
        adjacent = _no_weekday_between(d, n) or (d in seed_dates and n in seed_dates)
        if not adjacent:
            continue  # 中間缺日：不把前日餘額寫到不相鄰的那筆
        row = rows[d]
        row["margin_amt_k"] = rep["margin_amt_prev_k"]
        if rep.get("margin_shares_prev") is not None:
            row["margin_shares"] = rep["margin_shares_prev"]
        if rep.get("short_shares_prev") is not None:
            row["short_shares"] = rep["short_shares_prev"]
        row["provisional"] = False
        row["source"] = "MS_PREV"
    out = [rows[d] for d in dates]
    if out:
        out[-1]["provisional"] = out[-1]["source"] != "MS_PREV"
    # 種子（TREND/SITE）的歷史列雖然是今日餘額，但不是最新一筆就不再標初值，避免整條線都是「初值」；
    # 真正口徑看 source。
    for r in out[:-1]:
        if r["source"] in ("TREND", "SITE", "SEED"):
            r["provisional"] = False
    return out


def latest_summary(series: list[dict], reports) -> dict | None:
    """最新一列＋日變化（對前一交易日的確定值）、張數、券資比。"""
    if not series:
        return None
    reps = reports if isinstance(reports, dict) else {r["date"]: r for r in reports or [] if r}
    last = series[-1]
    rep = reps.get(last["date"])
    prev_k = None
    if rep is not None:
        prev_k = rep.get("margin_amt_prev_k")
    elif len(series) >= 2:
        prev_k = series[-2]["margin_amt_k"]
    amt = last["margin_amt_k"]
    chg_k = amt - prev_k if prev_k is not None else None
    return {
        "date": last["date"],
        "margin_amt_k": amt,
        "margin_amt_yi": k_to_yi(amt),
        "margin_shares": last.get("margin_shares"),
        "short_shares": last.get("short_shares"),
        "short_to_margin_pct": short_to_margin_pct(last.get("short_shares"), last.get("margin_shares")),
        "provisional": bool(last.get("provisional")),
        "prev_amt_k": prev_k,
        "chg_k": chg_k,
        "chg_yi": k_to_yi(chg_k, 5) if chg_k is not None else None,
        "chg_pct": round(chg_k / prev_k * 100, 3) if chg_k is not None and prev_k else None,
        "source": last.get("source"),
    }


# ---------------- 快取 ----------------
def margin_dir(cache_dir: Path) -> Path:
    """cache_dir 是 data/cache/trend（與 twse_taiex 相同）。"""
    return Path(cache_dir) / "twse" / "margin"


def _atomic_write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def load_raw(cache_dir: Path) -> dict[str, dict]:
    """raw/*.json → {date: report}；壞檔或尚未公布的檔略過。"""
    out = {}
    raw = margin_dir(cache_dir) / "raw"
    if not raw.exists():
        return out
    for p in sorted(raw.glob("*.json")):
        try:
            rep = parse_report(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
        if rep:
            out[rep["date"]] = rep
    return out


def save_raw(cache_dir: Path, payload: dict) -> Path:
    rep = parse_report(payload)
    if not rep:
        raise TwseMarginError("尚未公布的回應不存檔")
    p = margin_dir(cache_dir) / "raw" / (rep["date"].replace("-", "") + ".json")
    _atomic_write(p, json.dumps(payload, ensure_ascii=False))
    return p


def load_trend_seed(cache_dir: Path) -> list[dict]:
    """trend_history.json（MI_MARGN_TREND 今日餘額）→ 種子列；格式不對回空。只讀，不改它。"""
    p = margin_dir(cache_dir) / "trend_history.json"
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = d.get("rows") if isinstance(d, dict) else d
    out = []
    for r in rows or []:
        try:
            amt = r.get("margin_amt_k", r.get("marginAmt"))
            dt = str(r["date"])
            if len(dt) == 8 and dt.isdigit():
                dt = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
            out.append({"date": dt[:10], "margin_amt_k": int(amt), "margin_shares": r.get("margin_shares"),
                        "short_shares": r.get("short_shares"), "source": "TREND"})
        except Exception:
            continue
    return out


def seed_from_site(site_margin: dict | None) -> list[dict]:
    """上次 trend/data.json 的 charts.margin（欄式）→ 種子列。快取全空時才用。"""
    if not site_margin:
        return []
    try:
        dates = site_margin["dates"]
        amt = site_margin["margin_amt_k"]
        ms = site_margin.get("margin_shares") or [None] * len(dates)
        ss = site_margin.get("short_shares") or [None] * len(dates)
        return [{"date": d, "margin_amt_k": a, "margin_shares": m, "short_shares": s, "source": "SITE"}
                for d, a, m, s in zip(dates, amt, ms, ss) if a is not None]
    except Exception:
        return []


def save_history(cache_dir: Path, series: list[dict]):
    _atomic_write(margin_dir(cache_dir) / "history.json",
                  json.dumps({"source": SOURCE, "rows": series}, ensure_ascii=False) + "\n")


# ---------------- 抓取 ----------------
def fetch_report(session: requests.Session, yyyymmdd: str, pause: float = PAUSE_SEC) -> dict | None:
    """抓一天 MS 報表 → {payload, report}；尚未公布回 None；重試完仍失敗丟 TwseMarginError。"""
    last_err = None
    params = {"response": "json", "date": yyyymmdd, "selectType": "MS"}
    for k in range(TRIES):
        url = URLS[k % len(URLS)]
        try:
            time.sleep(pause if k == 0 else pause * (k + 1))
            r = session.get(url, params=params, timeout=25)
            r.raise_for_status()
            payload = r.json()
            rep = parse_report(payload)
            if rep is None:
                return None
            if rep["date"].replace("-", "") != yyyymmdd:
                raise TwseMarginError(f"回傳日期 {rep['date']} 與要求的 {yyyymmdd} 不同")
            return {"payload": payload, "report": rep}
        except TwseMarginError as e:
            last_err = e
        except Exception as e:
            last_err = TwseMarginError(f"{type(e).__name__}: {e}")
    raise TwseMarginError(f"MI_MARGN date={yyyymmdd} 失敗：{last_err}")


def dates_to_fetch(known: list[str], now: datetime, refetch_last: int = REFETCH_LAST,
                   max_new: int = MAX_NEW_DAYS) -> list[str]:
    """要請求的日期（YYYY-MM-DD，遞增）：最後一日之後到今天（台北）的工作日，加重抓最後幾個已有日。

    known 是已有的日期（MS 日檔＋種子）。今天在 PUBLISH_HOUR 前不抓。快取全空時只看最近兩週。"""
    now = now.astimezone(TW_TZ)
    today = now.date()
    if now.hour < PUBLISH_HOUR:
        today -= timedelta(days=1)
    known = sorted(set(known))
    last = _d(known[-1]) if known else today - timedelta(days=14)
    new = []
    d = last + timedelta(days=1)
    while d <= today:
        if d.weekday() < 5:
            new.append(d.isoformat())
        d += timedelta(days=1)
    new = new[-max_new:]
    again = known[-refetch_last:] if (known and refetch_last > 0) else []
    return again + new


def update(cache_dir: Path, now: datetime | None = None, session: requests.Session | None = None,
           pause: float = PAUSE_SEC, prev_site_margin: dict | None = None, fetch: bool = True) -> dict:
    """增量更新並重建序列。回傳 {series, reports, latest, report}；完全沒有資料丟 TwseMarginError。

    - 有快取：只請求最後一日之後的工作日（＋重抓最後 1 個 MS 日）。
    - raw 與 trend_history 都空：用上次網站的 margin 序列當種子，只抓新的日子（不做五年逐日回補）。
    - 某日尚未公布（今天 22:00 還沒出來、週末、假日）：略過，不算失敗。
    - 網路或格式錯誤：停止後面的請求，用已有的快取重建（錯誤寫在 report.errors）。"""
    now = now or datetime.now(TW_TZ)
    reports = load_raw(cache_dir)
    seeds = load_trend_seed(cache_dir)
    seed_kind = "TREND" if seeds else None
    if not reports and not seeds:
        seeds = seed_from_site(prev_site_margin)
        seed_kind = "SITE" if seeds else None
    elif prev_site_margin:
        seeds = seeds + seed_from_site(prev_site_margin)  # 只補 TREND 沒有的日期
    report = {"requested": [], "ok": [], "unpublished": [], "errors": [], "seed": seed_kind}
    if fetch:
        # 最後一日以 MS 日檔與種子合起來算；重抓的是合併後最後一個已有日
        todo = dates_to_fetch([*reports, *(x["date"] for x in seeds)], now)
        own = session or requests.Session()
        own.headers.update({"User-Agent": UA, "Accept": "application/json"})
        for d in todo:
            ymd = d.replace("-", "")
            report["requested"].append(d)
            try:
                got = fetch_report(own, ymd, pause=pause)
            except TwseMarginError as e:
                report["errors"].append(f"{d}：{e}")
                break  # 證交所出問題時後面多半也一樣，留到下一班
            if got is None:
                report["unpublished"].append(d)
                continue  # 假日或還沒公布：略過（最後一天沒公布就自然停在這裡）
            save_raw(cache_dir, got["payload"])
            reports[got["report"]["date"]] = got["report"]
            report["ok"].append(d)
    series = rebuild_series(reports, seeds)
    if not series:
        raise TwseMarginError("沒有任何融資餘額資料（快取與上次網站資料都沒有）")
    save_history(cache_dir, series)
    return {"series": series, "reports": reports, "latest": latest_summary(series, reports), "report": report}


# ---------------- 網站 payload ----------------
def site_payload(result: dict, fetched_at: str | None = None, twii_closes: dict | None = None,
                 twii_source: str | None = None) -> tuple[dict, dict]:
    """→ (analysis.margin, charts.margin)。charts 用欄式陣列，和既有 chart_daily_1y 一致、檔案較小。

    twii_closes：{date: close}（可選，供右軸疊圖；對不到的日期為 null，沒有就省略）。"""
    series = result["series"]
    latest = result["latest"]
    confirmed = [r["date"] for r in series if r["source"] == "MS_PREV"]
    counts = {}
    for r in series:
        counts[r["source"]] = counts.get(r["source"], 0) + 1
    analysis = {
        "source": SOURCE,
        "source_note": SOURCE_NOTE,
        "source_detail": SOURCE_DETAIL,
        "source_api": URLS[0] + "?response=json&date=YYYYMMDD&selectType=MS",
        "unit_note": "融資金額單位：億元＝仟元／100000",
        "asof": latest["date"],
        "fetched_at_taipei": fetched_at,
        "first": series[0]["date"],
        "count": len(series),
        "confirmed_through": confirmed[-1] if confirmed else None,
        "source_counts": counts,
        "latest": latest,
        "not_a_signal": "走勢頁參考圖表，與 11 項燈號（規則 v3.0）無關。",
    }
    charts = {
        "dates": [r["date"] for r in series],
        "margin_amt_yi": [k_to_yi(r["margin_amt_k"]) for r in series],
        "margin_amt_k": [r["margin_amt_k"] for r in series],
        "margin_shares": [r.get("margin_shares") for r in series],
        "short_shares": [r.get("short_shares") for r in series],
        "short_to_margin_pct": [short_to_margin_pct(r.get("short_shares"), r.get("margin_shares")) for r in series],
        "provisional": [bool(r["provisional"]) for r in series],
    }
    if twii_closes:
        closes = [twii_closes.get(d) for d in charts["dates"]]
        if any(c is not None for c in closes):
            charts["twii_close"] = closes
            charts["twii_source"] = twii_source
    return analysis, charts
