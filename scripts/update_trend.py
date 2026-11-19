#!/usr/bin/env python3
"""更新 SOX／NDX／SPX 與台指（^TWII）走勢資料（首頁與 /trend/ 用）。與 11 項燈號規則無關。

用法（repo 根目錄）：
    TZ=Asia/Taipei python scripts/update_trend.py            # 美股從 Yahoo 抓；台指優先證交所，失敗再退 Yahoo
    TZ=Asia/Taipei python scripts/update_trend.py --offline  # 不連網，用 data/cache/trend/ 的上次行情重算

輸出：
    trend/data.json    {analysis, charts}（成功才覆寫）
    trend/data.js      window.SITE_DATA = …;（由同一份 JSON 產生，成功才覆寫）
    trend/drawdowns/drawdowns.json／.js  data.json 成功寫出後，刷新「現在本波下跌」（scripts/drawdowns_current.py）
    trend/status.json  每次都寫：{ok, stale, fetched_at_taipei, price_asof, error,
                                  last_good_asof, last_good_fetched_at}

抓不到或算不出來：status.json 標 ok=false／stale=true，data.json／data.js 保留上次成功的版本，
結束碼 0（讓燈號更新與 Pages 部署照常進行）。只有程式本身出錯才會非零結束。
行情原始檔放 data/cache/trend/（.gitignore，不進版控）。

台指（TWII）另外處理：台股與美股的交易日、收盤時間不同，不跟美股三檔比「最新交易日要同一天」，
price_asof 與過期檢查仍只看美股。台指優先用證交所（開高低收 MI_5MINS_HIST、量用 FMTQIK 成交金額），
失敗再退 Yahoo ^TWII，再不行沿用舊快取；沒有快取就這一輪不出台指圖卡，不影響美股三檔的更新。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trend_analyze as T  # noqa: E402
import twse_taiex as TWSE  # noqa: E402

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
TW_CLOSE_HM = (13, 30)  # 台股 13:30 收盤；之後（含 15:00 排程）當日K 視為已完成
TW_TZ = ZoneInfo("Asia/Taipei")
# 市場別：(判斷「今天」與收盤的時區, 收盤時分, 週K 星期一時間戳用的時區)
MARKETS = {"US": (T.NY, US_CLOSE_HM, "America/New_York"), "TW": (TW_TZ, TW_CLOSE_HM, "Asia/Taipei")}
TW_VOLUME_PROXY = "0050.TW"  # 台指籌碼視窗沒有量時的代理（做法比照 SOX 用 SOXX；證交所成交金額為主時通常用不到）
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


def rebuild_week(frames: dict, ref_date: str, week_tz: str):
    """把 ref_date 那一週的週K 改用已收盤的日K 重組（frames 沒有週K 就不動）。"""
    d = pd.Timestamp(ref_date)
    monday = (d - pd.Timedelta(days=d.weekday())).strftime("%Y-%m-%d")
    done = frames["daily_2y"]
    done = done[done["date"] >= monday]
    for key in ("weekly_2y", "weekly_5y"):
        if key not in frames:
            continue
        w = frames[key]
        w = w[w["date"] < monday].reset_index(drop=True)
        if len(done):
            row = {
                "date": monday,
                "timestamp": int(pd.Timestamp(monday, tz=week_tz).timestamp()),
                "open": done["open"].iloc[0],
                "high": done["high"].max(),
                "low": done["low"].min(),
                "close": done["close"].iloc[-1],
                "adjclose": done["adjclose"].iloc[-1],
                "volume": int(done["volume"].sum()),
            }
            w = pd.concat([w, pd.DataFrame([row])], ignore_index=True)
        frames[key] = w


def drop_unfinished_session(frames: dict, market: str = "US", now: datetime | None = None) -> str | None:
    """還沒收盤時，Yahoo 會給一根進行中的當日K。拿掉它，並把含它的那根週K 改用已收盤的日K 重組。

    market="US"：紐約 16:15 前算未完成（現狀）。
    market="TW"：台北 13:30 前算未完成；13:30 之後（例如 15:00 排程）當日K 已完成，要留著。
    now 只給測試用（有時區的 datetime）。frames 可以只有 daily_2y（代理量用）。
    回傳被拿掉的日期（沒有就 None）。這是資料整理，不是分析規則。
    """
    tz, close_hm, week_tz = MARKETS[market]
    now_local = (now or datetime.now(tz)).astimezone(tz)
    today = now_local.strftime("%Y-%m-%d")
    session_open = (now_local.hour, now_local.minute) < close_hm
    last = frames["daily_2y"]["date"].iloc[-1]
    if not (last > today or (last == today and session_open)):
        return None
    for key in ("daily_1y", "daily_2y"):
        if key not in frames:
            continue
        df = frames[key]
        frames[key] = df[df["date"] < last].reset_index(drop=True)
    rebuild_week(frames, last, week_tz)
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


def write_tw_source(source: str, asof: str, extra: dict | None = None):
    payload = {"price_source": source, "asof": asof}
    if source == "TWSE":
        payload["volume_field"] = TWSE.VOLUME_FIELD
        payload["volume_note"] = TWSE.VOLUME_NOTE
        payload["ohlc_api"] = TWSE.OHLC_URL
        payload["volume_api"] = TWSE.VOL_URL
    else:
        payload["volume_field"] = "yahoo_volume"
        payload["volume_note"] = "Yahoo ^TWII volume（指數股數；收盤後當天常是 0）"
    if extra:
        payload.update(extra)
    path = CACHE / "TWII_source.json"
    write_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _yahoo_tw_frames(name: str) -> tuple[dict, str | None]:
    tk = T.SOURCE_TICKERS[name]
    frames = {key: fetch_one(tk, rng, itv) for key, rng, itv in DATASETS}
    dropped = drop_unfinished_session(frames, market="TW")
    # Yahoo 的 ^TWII 週線在收盤後常常還沒有當週那一根（或收盤價落後），
    # 一律用已完成的日K 重組最新交易日所在的那一週。
    rebuild_week(frames, frames["daily_2y"]["date"].iloc[-1], MARKETS["TW"][2])
    validate(name, frames)
    return frames, dropped


def _crosscheck_twse_yahoo(twse_daily: pd.DataFrame) -> dict | None:
    """對重疊區間的收盤價；失敗只記錄，不讓台指更新失敗。"""
    try:
        yahoo = fetch_one(T.SOURCE_TICKERS["TWII"], "2y", "1d")
        frames = {"daily_2y": yahoo}
        drop_unfinished_session(frames, market="TW")
        return TWSE.crosscheck_closes(twse_daily, frames["daily_2y"])
    except Exception as e:
        return {"error": short_reason(e)}


def fetch_tw(market_times: dict, dropped: dict) -> list:
    """抓台指：證交所優先，失敗改 Yahoo，再不行沿用舊快取。失敗不擋美股。"""
    notes = []
    for name in T.TW_IDS:
        frames = None
        source = None
        report = None
        try:
            daily, report = TWSE.fetch_history(CACHE)
            frames = TWSE.build_frames(daily, MARKETS["TW"][2])
            dropped[name] = drop_unfinished_session(frames, market="TW")
            rebuild_week(frames, frames["daily_2y"]["date"].iloc[-1], MARKETS["TW"][2])
            validate(name, frames)
            source = "TWSE"
        except Exception as e:
            notes.append(f"{name} 證交所抓取失敗（{short_reason(e)}），改試 Yahoo ^TWII")
            try:
                frames, dropped[name] = _yahoo_tw_frames(name)
                source = "Yahoo"
            except SourceError as e2:
                has_cache = all((CACHE / f"{name}_{key}.csv").exists() for key, _, _ in DATASETS)
                notes.append(
                    f"{name} Yahoo 也失敗（{short_reason(e2)}），"
                    + ("沿用舊的快取" if has_cache else "沒有快取，這一輪不出台指圖卡")
                )
                continue
        asof = frames["daily_2y"]["date"].iloc[-1]
        extra = {}
        if source == "TWSE":
            chk = _crosscheck_twse_yahoo(frames["daily_2y"])
            if chk:
                extra["crosscheck"] = chk
                if chk.get("error"):
                    notes.append(f"{name} 與 Yahoo 收盤對照失敗：{chk['error']}")
                else:
                    notes.append(
                        f"{name} 與 Yahoo 收盤對照：重疊 {chk['overlap']} 天（{chk['first']}～{chk['last']}），"
                        f"平均差 {chk['mean_abs_diff']:.4f}、最大差 {chk['max_abs_diff']:.4f}，"
                        f"超過 0.05 點 {chk['mismatch_count']} 天"
                    )
            if report:
                extra["fetch_report"] = {
                    "months_requested": report.get("months_requested"),
                    "months_ok": report.get("months_ok"),
                    "rows": report.get("rows"),
                    "first": report.get("first"),
                    "current_month_skipped": report.get("current_month_skipped"),
                    "mismatches": report.get("mismatches") or [],
                }
        write_tw_source(source, asof, extra)
        for key, df in frames.items():
            df[COLS].to_csv(CACHE / f"{name}_{key}.csv", index=False)
        if source == "TWSE":
            market_times[name] = f"證交所 {asof} 收盤"
        else:
            market_times[name] = market_time_taipei(T.SOURCE_TICKERS[name])
        n2 = len(frames["daily_2y"])
        notes.append(f"{name} 來源={source} 日線 {n2} 筆，截至 {asof}"
                     + (f"（量=證交所成交金額，單位新台幣）" if source == "TWSE" else ""))
    return notes


def fetch_all() -> dict:
    """抓美股三個指數 → 檢查 → 全部通過才寫進快取；再抓台指（失敗不擋美股）。回傳 fetch_meta。"""
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
    tw_notes = fetch_tw(market_times, dropped)
    fetch_meta = {"fetched_at_taipei": fetched_at, "market_times": market_times, "dropped_unfinished_session": dropped,
                  "tw_notes": tw_notes}
    write_atomic(CACHE / "fetch_meta.json", json.dumps(fetch_meta, ensure_ascii=False, indent=2))
    return fetch_meta



def fetch_optional_proxies(price_asof: str | None = None):
    """選用代理資料：SOXX 成交量給 SOX 用；0050.TW 成交量給台指備用；HOM Forward P/E 給估值位置頁。

    失敗只記錄，不讓整次更新失敗。"""
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
    try:
        # 台指：只有籌碼視窗總量為 0 時才會用到（證交所用成交金額時通常有量；判斷在 trend_analyze.build）
        df = fetch_one(TW_VOLUME_PROXY, "2y", "1d")
        frames = {"daily_2y": df}
        drop_unfinished_session(frames, market="TW")
        df = frames["daily_2y"]
        if len(df) >= 200:
            df[COLS].to_csv(CACHE / T.TW_PROXY_CSV, index=False)
            notes.append(f"0050.TW 日線 {len(df)} 筆，截至 {df['date'].iloc[-1]}")
        else:
            notes.append(f"0050.TW 筆數不足（{len(df)}），略過")
    except Exception as e:
        notes.append(f"0050.TW 抓取失敗（{type(e).__name__}: {e}），台指若無量則籌碼分布從缺")
    try:
        import valuation_pe as VP
        notes.extend(VP.fetch_hom(CACHE))
    except Exception as e:
        notes.append(f"HOM Forward P/E 抓取失敗（{type(e).__name__}: {e}），估值位置未更新")
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
    ap = argparse.ArgumentParser(description="更新 SOX／NDX／SPX 與台指走勢資料")
    ap.add_argument("--offline", action="store_true", help="不連網，用 data/cache/trend/ 的上次行情重算")
    args = ap.parse_args()

    started = iso(now_taipei())
    prev = last_good()
    TREND.mkdir(parents=True, exist_ok=True)

    try:
        fetch_meta = load_cached_meta() if args.offline else fetch_all()
        if not args.offline:
            for note in fetch_meta.get("tw_notes") or []:
                print(f"[trend] 台指：{note}")
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
    for name, why in (analysis["meta"].get("tw_skipped") or {}).items():
        print(f"[trend] 這一輪沒有 {name}：{why}")
    for name in T.IDS + [n for n in T.TW_IDS if n in analysis["indices"]]:
        ix = analysis["indices"][name]
        hp = ix.get("home_pattern")
        tl = ix["trendlines"].get("rising_support_major") or {}
        m200 = ix["moving_averages"]["sma200"]
        m20 = ix["moving_averages"]["sma20"]
        tri = ix.get("descending_right_triangle")
        label = f"{hp['rule']} {hp['name']} {hp['badge_label']}" if hp else "（無）"
        print(f"  {name} 截至={ix['last']['date']} close={ix['last']['close']}　home_pattern={label}")
        if tri:
            print(f"      下降直角三角 status={tri['status']} 頸線={tri['support']} "
                  f"vs={tri['close_vs_support_pct']}% confirmed={tri['confirmed']} target={tri.get('target')}")
        print(f"      升勢線 close_vs_line_pct={tl.get('close_vs_line_pct')} close_is={tl.get('close_is')} "
              f"broken_by_3pct_filter={tl.get('broken_by_3pct_filter')}；sma200 pct={m200['pct']} above={m200['above']}；"
              f"sma20 below_streak={m20.get('below_streak')}")
        if name in T.TW_IDS:  # 台指不算牛熊階段（沒有台指的恐慌門檻）
            print(f"      籌碼量來源={(ix.get('volume_by_price') or {}).get('source_label')}")
            continue
        st = ix.get("market_stage") or {}
        print(f"      階段={st.get('headline')}  code={st.get('stage_code')}")
    import valuation_pe as VP
    for line in VP.summarize(analysis.get("valuation") or {}):
        print(f"  估值 {line}")
    print(f"[trend] 已寫 trend/data.json、trend/data.js、trend/status.json")
    refresh_drawdowns()
    return 0


def refresh_drawdowns():
    """data.json 成功寫出後（快取日線也是這一輪的新資料），刷新 /trend/drawdowns/ 的「現在本波下跌」。

    更新失敗、沿用舊 data.json 時 main() 已提早 return，不會走到這裡。刷新本身失敗只記錄，不讓走勢更新失敗。"""
    try:
        import drawdowns_current as DC

        report = DC.refresh_files(ROOT)
        for name, line in report.items():
            print(f"[trend] 本波下跌 {name}：{line}")
        print("[trend] 已寫 trend/drawdowns/drawdowns.json、trend/drawdowns/drawdowns.js")
    except Exception as e:
        print(f"[trend] 本波下跌刷新失敗（{short_reason(e)}），drawdowns 沿用上次")


if __name__ == "__main__":
    sys.exit(main())
