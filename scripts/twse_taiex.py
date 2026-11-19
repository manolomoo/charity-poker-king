#!/usr/bin/env python3
"""從臺灣證券交易所抓發行量加權股價指數（台指）日線。

- 開高低收：MI_5MINS_HIST（每月一包）
  https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date=YYYYMMDD&response=json
- 成交量：FMTQIK 的「成交金額」（新台幣，全市場，每月一包）
  https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=YYYYMMDD&response=json
  選成交金額而不是成交股數：籌碼分布看的是相對堆積，用金額較能代表資金在各價位的比重。
  這與 Yahoo ^TWII 的 volume（指數「股數」，收盤後當天常是 0）單位不同，不能直接比大小。

日線快取在 data/cache/trend/twse/（不進版控）。第一次會往回抓足夠讓 weekly_5y
通過既有筆數門檻的月份（約五年）；之後每天只重抓前一個月與當月，並補上缺的月份。
請求之間間隔約 3 秒，避免證交所限流。與 11 項燈號規則無關。
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

OHLC_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST"
VOL_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
PAUSE_SEC = 3.0
TRIES = 3
LOOKBACK_YEARS = 5  # 讓 weekly_5y（門檻 150 週）有足夠的日K 可重組
UA = "charity-poker-king/1.0 (https://github.com/manolomoo/charity-poker-king; trend data)"
COLS = ["date", "timestamp", "open", "high", "low", "close", "adjclose", "volume"]
VOLUME_FIELD = "turnover_twd"
VOLUME_NOTE = "FMTQIK 成交金額（新台幣，全市場；含一般、零股、盤後定價，不含鉅額、拍賣、標購）"


class TwseError(Exception):
    """證交所來源或資料內容的問題。"""


class TwseEmpty(TwseError):
    """該月尚無資料（常見於月初、還沒有交易日）。"""


def roc_to_iso(s: str) -> str:
    """民國年日期（115/11/19 或 115年11月19日）→ YYYY-MM-DD。"""
    t = str(s).strip().replace("年", "/").replace("月", "/").replace("日", "")
    t = t.replace("-", "/")
    parts = [p for p in t.split("/") if p]
    if len(parts) != 3:
        raise TwseError(f"無法解析證交所日期：{s!r}")
    y, m, d = (int(p) for p in parts)
    if y < 1911:
        y += 1911
    return f"{y:04d}-{m:02d}-{d:02d}"


def parse_num(s) -> float:
    t = str(s).strip().replace(",", "").replace("+", "").replace("−", "-")
    if t in ("", "--", "---", "null", "None"):
        raise TwseError(f"無法解析數字：{s!r}")
    return float(t)


def month_starts(start: date, end: date) -> list[date]:
    d = start.replace(day=1)
    last = end.replace(day=1)
    out = []
    while d <= last:
        out.append(d)
        d = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return out


def prev_month(d: date) -> date:
    first = d.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def lookback_start(today: date, years: int = LOOKBACK_YEARS) -> date:
    try:
        start = today.replace(year=today.year - years, day=1)
    except ValueError:
        start = today.replace(year=today.year - years, month=2, day=1)
    return prev_month(start)


def months_to_fetch(history: pd.DataFrame | None, today: date, years: int = LOOKBACK_YEARS) -> list[date]:
    """要向證交所重抓的月份：快取沒有的、加上前一個月與當月（可能有更正或新交易日）。"""
    all_months = month_starts(lookback_start(today, years), today)
    refresh = {today.replace(day=1), prev_month(today)}
    if history is None or history.empty:
        return all_months
    have = set(pd.to_datetime(history["date"]).dt.to_period("M").astype(str))
    needed = set(refresh)
    for m in all_months:
        if m.strftime("%Y-%m") not in have:
            needed.add(m)
    return sorted(needed)


def _get_json(url: str, date_yyyymmdd: str, session: requests.Session, pause: float) -> dict:
    last_err = None
    params = {"date": date_yyyymmdd, "response": "json"}
    for k in range(TRIES):
        try:
            time.sleep(pause if k == 0 else pause * (k + 1))
            r = session.get(url, params=params, timeout=25)
            r.raise_for_status()
            data = r.json()
            stat = str(data.get("stat") or "")
            if stat != "OK":
                if "沒有符合" in stat or "無符合" in stat or "沒有資料" in stat:
                    raise TwseEmpty(f"stat={stat!r}")
                raise TwseError(f"stat={stat!r}")
            if not isinstance(data.get("data"), list):
                raise TwseError("沒有 data 陣列")
            return data
        except TwseError as e:
            last_err = e
        except Exception as e:
            last_err = TwseError(f"{type(e).__name__}: {e}")
    raise TwseError(f"{url} date={date_yyyymmdd} 失敗：{last_err}")


def parse_ohlc_rows(payload: dict) -> pd.DataFrame:
    rows = []
    for rec in payload.get("data") or []:
        if not rec or len(rec) < 5:
            continue
        rows.append(
            {
                "date": roc_to_iso(rec[0]),
                "open": parse_num(rec[1]),
                "high": parse_num(rec[2]),
                "low": parse_num(rec[3]),
                "close": parse_num(rec[4]),
            }
        )
    return pd.DataFrame(rows)


def parse_volume_rows(payload: dict) -> pd.DataFrame:
    """FMTQIK：日期, 成交股數, 成交金額, 成交筆數, 收盤指數, 漲跌點數。量用成交金額。"""
    rows = []
    for rec in payload.get("data") or []:
        if not rec or len(rec) < 5:
            continue
        rows.append({"date": roc_to_iso(rec[0]), "volume": int(round(parse_num(rec[2])))})
    return pd.DataFrame(rows)


def combine_month(ohlc: pd.DataFrame, vol: pd.DataFrame) -> pd.DataFrame:
    if ohlc.empty or vol.empty:
        raise TwseError("該月開高低收或成交金額為空")
    out = ohlc.merge(vol, on="date", how="inner")
    missing = sorted(set(ohlc["date"]) ^ set(vol["date"]))
    if missing:
        # 內連接後若有落差，記在屬性裡給呼叫端提示；不把對不上的日期硬拼進去
        out.attrs["date_mismatch"] = missing
    if out.empty:
        raise TwseError("開高低收與成交金額對不上（沒有共同日期）")
    return out


def attach_bars(df: pd.DataFrame, week_tz: str = "Asia/Taipei") -> pd.DataFrame:
    out = df.copy()
    out = out.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    out = out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    ts = pd.to_datetime(out["date"]).dt.tz_localize(week_tz)
    out["timestamp"] = ts.map(lambda x: int(x.timestamp()))
    out["adjclose"] = out["close"]
    out["volume"] = out["volume"].astype("int64")
    if not (out[["open", "high", "low", "close"]] > 0).all().all():
        raise TwseError("有非正數價格")
    if (out["volume"] < 0).any():
        raise TwseError("有負的成交金額")
    return out[COLS].reset_index(drop=True)


def weeks_from_daily(daily: pd.DataFrame, week_tz: str = "Asia/Taipei") -> pd.DataFrame:
    """用已完成的日K 重組週K（週一起始，與 rebuild_week 同一算法）。"""
    if daily is None or daily.empty:
        return pd.DataFrame(columns=COLS)
    df = daily.copy()
    df["d"] = pd.to_datetime(df["date"])
    monday = df["d"] - pd.to_timedelta(df["d"].dt.day_of_week, unit="D")
    rows = []
    for mon, g in df.groupby(monday, sort=True):
        g = g.sort_values("date")
        mon_s = pd.Timestamp(mon).strftime("%Y-%m-%d")
        rows.append(
            {
                "date": mon_s,
                "timestamp": int(pd.Timestamp(mon_s, tz=week_tz).timestamp()),
                "open": float(g["open"].iloc[0]),
                "high": float(g["high"].max()),
                "low": float(g["low"].min()),
                "close": float(g["close"].iloc[-1]),
                "adjclose": float(g["adjclose"].iloc[-1] if "adjclose" in g.columns else g["close"].iloc[-1]),
                "volume": int(g["volume"].sum()),
            }
        )
    return pd.DataFrame(rows)[COLS]


def slice_daily(daily: pd.DataFrame, calendar_days: int) -> pd.DataFrame:
    last = pd.Timestamp(daily["date"].iloc[-1])
    cut = (last - pd.Timedelta(days=calendar_days)).strftime("%Y-%m-%d")
    return daily[daily["date"] >= cut].reset_index(drop=True)



def _history_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "twse" / "taiex_daily.csv"


def load_history(cache_dir: Path) -> pd.DataFrame | None:
    p = _history_path(cache_dir)
    if not p.exists():
        return None
    df = pd.read_csv(p)
    if "date" not in df.columns or df.empty:
        return None
    return df


def save_history(cache_dir: Path, df: pd.DataFrame):
    p = _history_path(cache_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    attach_bars(df).to_csv(tmp, index=False)
    tmp.replace(p)


def write_source(cache_dir: Path, asof: str, extra: dict | None = None):
    payload = {
        "price_source": "TWSE",
        "volume_field": VOLUME_FIELD,
        "volume_note": VOLUME_NOTE,
        "asof": asof,
        "ohlc_api": OHLC_URL,
        "volume_api": VOL_URL,
    }
    if extra:
        payload.update(extra)
    p = Path(cache_dir) / "TWII_source.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def load_source(cache_dir: Path) -> dict:
    p = Path(cache_dir) / "TWII_source.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def merge_months(base: pd.DataFrame | None, monthly: list[pd.DataFrame]) -> pd.DataFrame:
    parts = [m for m in monthly if m is not None and not m.empty]
    if base is not None and not base.empty:
        if monthly:
            touched = set()
            for m in monthly:
                touched.update(pd.to_datetime(m["date"]).dt.to_period("M").astype(str))
            keep = base[~pd.to_datetime(base["date"]).dt.to_period("M").astype(str).isin(touched)]
            parts = [keep] + parts
        else:
            parts = [base] + parts
    if not parts:
        raise TwseError("沒有可合併的日線")
    out = pd.concat(parts, ignore_index=True)
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_month(session: requests.Session, month: date, pause: float = PAUSE_SEC) -> pd.DataFrame:
    ymd = month.strftime("%Y%m%d")
    ohlc = parse_ohlc_rows(_get_json(OHLC_URL, ymd, session, pause))
    vol = parse_volume_rows(_get_json(VOL_URL, ymd, session, pause))
    return combine_month(ohlc, vol)


def fetch_history(
    cache_dir: Path,
    today: date | None = None,
    pause: float = PAUSE_SEC,
    session: requests.Session | None = None,
    years: int = LOOKBACK_YEARS,
) -> tuple[pd.DataFrame, dict]:
    """抓／更新證交所台指日線。回傳 (完整日線, 抓取報告)。"""
    today = today or datetime.now().date()
    history = load_history(cache_dir)
    months = months_to_fetch(history, today, years)
    report = {"months_requested": [m.strftime("%Y-%m") for m in months], "months_ok": [], "mismatches": []}
    own = session or requests.Session()
    own.headers.update({"User-Agent": UA, "Accept": "application/json"})
    monthly = []
    try:
        for m in months:
            try:
                df = fetch_month(own, m, pause=pause)
                monthly.append(df)
                report["months_ok"].append(m.strftime("%Y-%m"))
                if df.attrs.get("date_mismatch"):
                    report["mismatches"].append({"month": m.strftime("%Y-%m"), "dates": df.attrs["date_mismatch"]})
            except TwseEmpty as e:
                # 當月還沒有交易日：略過，不讓整次更新失敗（即使還沒有快取）。
                if m == today.replace(day=1):
                    report["current_month_skipped"] = str(e)
                    continue
                raise
            except TwseError as e:
                raise
        daily = merge_months(history, monthly)
        daily = attach_bars(daily)
        save_history(cache_dir, daily)
        report["rows"] = int(len(daily))
        report["asof"] = str(daily["date"].iloc[-1])
        report["first"] = str(daily["date"].iloc[0])
        return daily, report
    finally:
        if session is None:
            own.close()


def build_frames(daily: pd.DataFrame, week_tz: str = "Asia/Taipei") -> dict:
    """由完整日線切出 update_trend.validate 要的四份資料。"""
    daily = attach_bars(daily, week_tz)
    weekly = weeks_from_daily(daily, week_tz)
    d2 = slice_daily(daily, 800)
    d1 = slice_daily(daily, 400)
    start_2y = (pd.Timestamp(d2["date"].iloc[0]) - pd.Timedelta(days=6)).strftime("%Y-%m-%d") if len(d2) else daily["date"].iloc[0]
    start_5y = (pd.Timestamp(daily["date"].iloc[-1]) - pd.Timedelta(days=365 * 5 + 14)).strftime("%Y-%m-%d") if len(daily) else daily["date"].iloc[0]
    w2 = weekly[weekly["date"] >= start_2y].reset_index(drop=True) if len(weekly) else weekly
    w5 = weekly[weekly["date"] >= start_5y].reset_index(drop=True) if len(weekly) else weekly
    return {
        "daily_1y": d1,
        "daily_2y": d2,
        "weekly_2y": w2,
        "weekly_5y": w5,
    }


def crosscheck_closes(twse: pd.DataFrame, yahoo: pd.DataFrame, tol: float = 0.05) -> dict:
    """對同一天的收盤價：回傳重疊天數、最大差距、以及差距超過 tol 的日期。"""
    a = twse[["date", "close"]].rename(columns={"close": "twse"})
    b = yahoo[["date", "close"]].rename(columns={"close": "yahoo"})
    m = a.merge(b, on="date", how="inner")
    if m.empty:
        return {"overlap": 0, "mismatches": [], "max_abs_diff": None, "mean_abs_diff": None}
    m["diff"] = m["twse"] - m["yahoo"]
    m["abs_diff"] = m["diff"].abs()
    bad = m[m["abs_diff"] > tol].copy()
    return {
        "overlap": int(len(m)),
        "first": str(m["date"].iloc[0]),
        "last": str(m["date"].iloc[-1]),
        "max_abs_diff": float(m["abs_diff"].max()),
        "mean_abs_diff": float(m["abs_diff"].mean()),
        "mismatch_count": int(len(bad)),
        "mismatches": [
            {"date": str(r.date), "twse": float(r.twse), "yahoo": float(r.yahoo), "diff": float(r.diff)}
            for r in bad.sort_values("abs_diff", ascending=False).itertuples(index=False)
        ],
    }
