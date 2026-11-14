#!/usr/bin/env python3
"""更新 SOX／NDX／SPX 走勢資料（首頁與 /trend/ 用）。與 11 項燈號規則無關。

用法（repo 根目錄）：
    TZ=Asia/Taipei python scripts/update_trend.py            # 從 Yahoo 抓，再計算
    TZ=Asia/Taipei python scripts/update_trend.py --offline  # 不連網，用 data/cache/trend/ 的上次行情重算

輸出：
    trend/data.json    {analysis, charts}（成功才覆寫）
    trend/data.js      window.SITE_DATA = …;（由同一份 JSON 產生，成功才覆寫）
    trend/status.json  每次都寫：{ok, stale, fetched_at_taipei, price_asof, error,
                                  last_good_asof, last_good_fetched_at}

抓不到或算不出來：status.json 標 ok=false／stale=true，data.json／data.js 保留上次成功的版本，
結束碼 0（讓燈號更新與 Pages 部署照常進行）。只有程式本身出錯才會非零結束。
行情原始檔放 data/cache/trend/（.gitignore，不進版控）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trend_analyze as T  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "trend"
TREND = ROOT / "trend"
DATA_JSON = TREND / "data.json"
DATA_JS = TREND / "data.js"
STATUS_JSON = TREND / "status.json"
LATEST = ROOT / "data" / "latest.json"
RULES = ROOT / "rules.json"

# (檔名後綴, Yahoo range, interval)
DATASETS = [("daily_1y", "1y", "1d"), ("daily_2y", "2y", "1d"), ("weekly_2y", "2y", "1wk"), ("weekly_5y", "5y", "1wk")]
US_CLOSE_HM = (16, 15)  # 與 scripts/run.py 的 completed_daily 相同：紐約 16:15 前當日K 視為未完成
MIN_ROWS = {"daily_1y": 200, "daily_2y": 400, "weekly_2y": 60, "weekly_5y": 150}
COLS = ["date", "timestamp", "open", "high", "low", "close", "adjclose", "volume"]


class SourceError(Exception):
    """行情來源或資料內容的問題（不是程式錯誤）：保留上次成功資料。"""


def now_taipei() -> datetime:
    return datetime.now(T.TZ)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def write_atomic(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------- 抓資料 ----------------
def to_frame(hist: pd.DataFrame) -> pd.DataFrame:
    """yfinance history → 與原本 CSV 相同的欄位（date,timestamp,open,high,low,close,adjclose,volume）。"""
    if hist is None or len(hist) == 0:
        raise SourceError("Yahoo 回傳空資料")
    idx = hist.index
    out = pd.DataFrame(
        {
            "date": [x.strftime("%Y-%m-%d") for x in idx],
            "timestamp": [int(x.timestamp()) for x in idx],
            "open": hist["Open"].values,
            "high": hist["High"].values,
            "low": hist["Low"].values,
            "close": hist["Close"].values,
            "adjclose": hist["Adj Close"].values if "Adj Close" in hist.columns else hist["Close"].values,
            "volume": hist["Volume"].values,
        }
    )
    out = out.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    out["volume"] = out["volume"].fillna(0).astype("int64")
    return out


def fetch_one(ticker: str, rng: str, interval: str, tries: int = 3) -> pd.DataFrame:
    import yfinance as yf

    last_err = None
    for k in range(tries):
        try:
            hist = yf.Ticker(ticker).history(period=rng, interval=interval, auto_adjust=False, actions=False)
            return to_frame(hist)
        except SourceError as e:
            last_err = e
        except Exception as e:  # 網路、限流、Yahoo 格式改變都算來源問題
            last_err = SourceError(f"{type(e).__name__}: {e}")
        time.sleep(2 * (k + 1))
    raise SourceError(f"{ticker} {rng}/{interval} 抓取失敗：{last_err}")


def market_time_taipei(ticker: str):
    """Yahoo meta.regularMarketTime 轉台北時間；拿不到就算了（只用在說明文字）。"""
    try:
        import yfinance as yf

        ts = yf.Ticker(ticker).history_metadata.get("regularMarketTime")
        if ts is None:
            return None
        ts = pd.Timestamp(ts, unit="s", tz="UTC") if isinstance(ts, (int, float)) else pd.Timestamp(ts)
        return ts.tz_convert("Asia/Taipei").strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def drop_unfinished_session(frames: dict) -> str | None:
    """美股還沒收盤時，Yahoo 會給一根進行中的當日K。拿掉它，並把含它的那根週K 改用已收盤的日K 重組。

    回傳被拿掉的日期（沒有就 None）。這是資料整理，不是分析規則。
    """
    now_ny = datetime.now(T.NY)
    today = now_ny.strftime("%Y-%m-%d")
    session_open = (now_ny.hour, now_ny.minute) < US_CLOSE_HM
    last = frames["daily_2y"]["date"].iloc[-1]
    if not (last > today or (last == today and session_open)):
        return None
    for key in ("daily_1y", "daily_2y"):
        df = frames[key]
        frames[key] = df[df["date"] < last].reset_index(drop=True)
    d = pd.Timestamp(last)
    monday = (d - pd.Timedelta(days=d.weekday())).strftime("%Y-%m-%d")
    done = frames["daily_2y"]
    done = done[done["date"] >= monday]
    for key in ("weekly_2y", "weekly_5y"):
        w = frames[key]
        w = w[w["date"] < monday].reset_index(drop=True)
        if len(done):
            row = {
                "date": monday,
                "timestamp": int(pd.Timestamp(monday, tz="America/New_York").timestamp()),
                "open": done["open"].iloc[0],
                "high": done["high"].max(),
                "low": done["low"].min(),
                "close": done["close"].iloc[-1],
                "adjclose": done["adjclose"].iloc[-1],
                "volume": int(done["volume"].sum()),
            }
            w = pd.concat([w, pd.DataFrame([row])], ignore_index=True)
        frames[key] = w
    return last


def validate(name: str, frames: dict):
    for key, df in frames.items():
        if len(df) < MIN_ROWS[key]:
            raise SourceError(f"{name} {key} 只有 {len(df)} 筆，資料不足")
        if not (df[["open", "high", "low", "close"]] > 0).all().all():
            raise SourceError(f"{name} {key} 有非正數價格")
    d2, d1 = frames["daily_2y"], frames["daily_1y"]
    if d1["date"].iloc[-1] != d2["date"].iloc[-1]:
        raise SourceError(f"{name} 日線 1 年與 2 年的最新日期不一致")
    last = pd.Timestamp(d2["date"].iloc[-1])
    for key in ("weekly_2y", "weekly_5y"):
        # 與分析端相同的去重之後，最後一根週K 必須涵蓋最新交易日，收盤也要對得上
        w = frames[key].copy()
        w["date"] = pd.to_datetime(w["date"])
        w = T.drop_duplicate_last_week(w)
        wk = w["date"].iloc[-1]
        if not (wk <= last < wk + pd.Timedelta(days=7)):
            raise SourceError(f"{name} {key} 最後一根週K（{wk.date()}）沒有涵蓋最新交易日 {last.date()}")
        wc, dc = float(w["close"].iloc[-1]), float(d2["close"].iloc[-1])
        if abs(wc / dc - 1) > 0.001:
            raise SourceError(f"{name} {key} 最後週K 收盤 {wc:.2f} 與日線收盤 {dc:.2f} 對不上")


def fetch_all() -> dict:
    """抓三個指數 → 檢查 → 全部通過才寫進快取。回傳 fetch_meta。"""
    fetched_at = iso(now_taipei())
    staged, dropped, market_times = {}, {}, {}
    for name in T.IDS:
        tk = T.SOURCE_TICKERS[name]
        frames = {key: fetch_one(tk, rng, itv) for key, rng, itv in DATASETS}
        dropped[name] = drop_unfinished_session(frames)
        validate(name, frames)
        staged[name] = frames
        market_times[name] = market_time_taipei(tk)
    CACHE.mkdir(parents=True, exist_ok=True)
    for name, frames in staged.items():
        for key, df in frames.items():
            df[COLS].to_csv(CACHE / f"{name}_{key}.csv", index=False)
    fetch_meta = {"fetched_at_taipei": fetched_at, "market_times": market_times, "dropped_unfinished_session": dropped}
    write_atomic(CACHE / "fetch_meta.json", json.dumps(fetch_meta, ensure_ascii=False, indent=2))
    return fetch_meta



def fetch_optional_proxies(price_asof: str | None = None):
    """選用代理資料：SOXX 成交量給 SOX 用。失敗只記錄，不讓整次更新失敗。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    notes = []
    try:
        df = fetch_one("SOXX", "2y", "1d")
        frames = {"daily_2y": df}
        drop_unfinished_session(frames)
        df = frames["daily_2y"]
        if price_asof:
            df = df[df["date"] <= price_asof].reset_index(drop=True)
        if len(df) >= 200:
            df[COLS].to_csv(CACHE / "SOXX_daily_2y.csv", index=False)
            notes.append(f"SOXX 日線 {len(df)} 筆，截至 {df['date'].iloc[-1]}")
        else:
            notes.append(f"SOXX 筆數不足（{len(df)}），略過")
    except Exception as e:
        notes.append(f"SOXX 抓取失敗（{type(e).__name__}: {e}），SOX 未驗量")
    return notes

def load_cached_meta() -> dict:
    p = CACHE / "fetch_meta.json"
    missing = [f"{n}_{k}.csv" for n in T.IDS for k, _, _ in DATASETS if not (CACHE / f"{n}_{k}.csv").exists()]
    if missing or not p.exists():
        raise SourceError(f"--offline 需要 {CACHE.relative_to(ROOT)}/ 內的行情快取，缺：{', '.join(missing) or p.name}")
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------- 上次成功的資料 ----------------
def last_good() -> dict:
    """上次成功資料的截至日與抓取時間：先看 data.json，再退到 data.js，最後退到 status.json。"""
    meta = None
    try:
        if DATA_JSON.exists():
            meta = json.loads(DATA_JSON.read_text(encoding="utf-8"))["analysis"]["meta"]
        elif DATA_JS.exists():
            s = DATA_JS.read_text(encoding="utf-8")
            meta = json.loads(s[s.index("{"):].rstrip().rstrip(";"))["analysis"]["meta"]
    except Exception:
        meta = None
    if meta:
        return {"asof": meta.get("price_asof"), "fetched_at": meta.get("fetched_at_taipei")}
    try:
        st = json.loads(STATUS_JSON.read_text(encoding="utf-8"))
        return {"asof": st.get("last_good_asof"), "fetched_at": st.get("last_good_fetched_at")}
    except Exception:
        return {"asof": None, "fetched_at": None}


def write_status(**kw):
    keys = ["ok", "stale", "fetched_at_taipei", "price_asof", "error", "last_good_asof", "last_good_fetched_at"]
    write_atomic(STATUS_JSON, json.dumps({k: kw[k] for k in keys}, ensure_ascii=False, indent=2) + "\n")


def short_reason(e: Exception) -> str:
    msg = " ".join(str(e).split()) or type(e).__name__
    return msg if len(msg) <= 200 else msg[:197] + "…"


# ---------------- 主流程 ----------------
def main() -> int:
    ap = argparse.ArgumentParser(description="更新 SOX／NDX／SPX 走勢資料")
    ap.add_argument("--offline", action="store_true", help="不連網，用 data/cache/trend/ 的上次行情重算")
    args = ap.parse_args()

    started = iso(now_taipei())
    prev = last_good()
    TREND.mkdir(parents=True, exist_ok=True)

    try:
        fetch_meta = load_cached_meta() if args.offline else fetch_all()
        if not args.offline:
            for note in fetch_optional_proxies():
                print(f"[trend] 選用資料：{note}")
        try:
            analysis, charts = T.build(CACHE, LATEST, RULES, fetch_meta["fetched_at_taipei"], fetch_meta.get("market_times"))
            site = {"analysis": analysis, "charts": charts}
            text = json.dumps(site, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except SourceError:
            raise
        except Exception as e:  # 資料形狀不合導致算不出來：一樣保留上次成功資料
            raise SourceError(f"分析失敗（{type(e).__name__}: {e}）")
        price_asof = analysis["meta"]["price_asof"]
        if prev["asof"] and price_asof < prev["asof"]:
            raise SourceError(f"抓到的最新交易日 {price_asof} 比上次成功的 {prev['asof']} 還舊")
        age = (now_taipei().date() - datetime.strptime(price_asof, "%Y-%m-%d").date()).days
        if age > 7 and not args.offline:
            raise SourceError(f"Yahoo 最新交易日停在 {price_asof}（{age} 天前）")
    except SourceError as e:
        reason = short_reason(e)
        write_status(ok=False, stale=True, fetched_at_taipei=started, price_asof=None, error=reason,
                     last_good_asof=prev["asof"], last_good_fetched_at=prev["fetched_at"])
        print(f"[trend] 更新失敗：{reason}")
        print(f"[trend] 保留上次成功資料（截至 {prev['asof'] or '無'}，抓取於 {prev['fetched_at'] or '無'}）；"
              f"已寫 {STATUS_JSON.relative_to(ROOT)}（stale=true）。")
        return 0

    write_atomic(DATA_JSON, text + "\n")
    write_atomic(DATA_JS, "window.SITE_DATA = " + text + ";\n")
    T.write_markdown(analysis, CACHE / "analysis.md")
    fetched = analysis["meta"]["fetched_at_taipei"]
    write_status(ok=True, stale=False, fetched_at_taipei=fetched, price_asof=price_asof, error=None,
                 last_good_asof=price_asof, last_good_fetched_at=fetched)

    print(f"[trend] 成功{'（離線重算）' if args.offline else ''}：price_asof={price_asof}　fetched_at={fetched}")
    for name in T.IDS:
        ix = analysis["indices"][name]
        hp = ix.get("home_pattern")
        tl = ix["trendlines"].get("rising_support_major") or {}
        m200 = ix["moving_averages"]["sma200"]
        m20 = ix["moving_averages"]["sma20"]
        tri = ix.get("descending_right_triangle")
        label = f"{hp['rule']} {hp['name']} {hp['badge_label']}" if hp else "（無）"
        print(f"  {name} close={ix['last']['close']}　home_pattern={label}")
        if tri:
            print(f"      下降直角三角 status={tri['status']} 頸線={tri['support']} "
                  f"vs={tri['close_vs_support_pct']}% confirmed={tri['confirmed']} target={tri.get('target')}")
        print(f"      升勢線 close_vs_line_pct={tl.get('close_vs_line_pct')} close_is={tl.get('close_is')} "
              f"broken_by_3pct_filter={tl.get('broken_by_3pct_filter')}；sma200 pct={m200['pct']} above={m200['above']}；"
              f"sma20 below_streak={m20.get('below_streak')}")
        st = ix.get("market_stage") or {}
        print(f"      階段={st.get('headline')}  code={st.get('stage_code')}")
    print(f"[trend] 已寫 trend/data.json、trend/data.js、trend/status.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
