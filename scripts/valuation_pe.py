"""Forward P/E 估值位置（COBB MEMO 視圖＋History of Market 重算）。

不參與、也不修改 11 項燈號 v3.0 第 4 項規則。失敗時回傳 available=False，
不讓走勢更新整批失敗。
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

import pandas as pd

HOM_URLS = {
    "NDX": "https://historyofmarket.com/api/ndx/forward-pe.json",
    "SOX": "https://historyofmarket.com/api/sectors/forward-pe.json",
}
CACHE_NAME = {"NDX": "hom_ndx_forward-pe.json", "SOX": "hom_sox_forward-pe.json"}
HOM_PAGES = {
    "NDX": "https://historyofmarket.com/nasdaq/ndx-forward-pe/",
    "SOX": "https://historyofmarket.com/zh-tw/semi/semi-fwdpe/",
}

# COBB MEMO 2026-11 快照（凍結，不自動更新）。SOX 的月統計可本站以 HOM
# 「每月最後一筆、截至 2026-11-05」重現到小數點後兩位；NDX 資料商未確認。
COBB_MEMO = {
    "source": "COBB MEMO",
    "asof": "2026-11",
    "indices": {
        "SOX": {
            "latest": 12.72,
            "windows": {
                "2020": {"mean": 21.37, "median": 21.12, "q1": 19.26, "q3": 24.59,
                         "up05": 23.26, "dn05": 19.48, "up10": 25.14, "dn10": 17.60,
                         "up15": 27.03, "dn15": 15.71},
                "2016": {"mean": 19.08, "median": 18.35, "q1": 15.29, "q3": 22.36,
                         "up05": 21.26, "dn05": 16.90, "up10": 23.44, "dn10": 14.72,
                         "up15": 25.62, "dn15": 12.54},
                "2011": {"mean": 17.51, "median": 15.71, "q1": 14.42, "q3": 20.55,
                         "up05": 19.71, "dn05": 15.32, "up10": 21.90, "dn10": 13.12,
                         "up15": 24.10, "dn15": 10.92},
            },
        },
        "NDX": {
            "latest": 18.01,
            "windows": {
                "2020": {"mean": 25.35, "median": 25.53, "q1": 22.93, "q3": 27.11,
                         "up05": 27.04, "dn05": 23.66, "up10": 28.73, "dn10": 21.98,
                         "up15": 30.42, "dn15": 20.29},
                "2016": {"mean": 23.62, "median": 22.92, "q1": 20.91, "q3": 25.88,
                         "up05": 25.43, "dn05": 21.80, "up10": 27.24, "dn10": 19.99,
                         "up15": 29.06, "dn15": 18.17},
                "2011": {"mean": 21.47, "median": 21.10, "q1": 18.89, "q3": 24.73,
                         "up05": 23.76, "dn05": 19.18, "up10": 26.05, "dn10": 16.89,
                         "up15": 28.34, "dn15": 14.60},
                "2006": {"mean": 21.40, "median": 20.95, "q1": 18.52, "q3": 25.21},
                "2001": {"mean": 23.94, "median": 22.04, "q1": 19.00, "q3": 26.21},
            },
        },
    },
}

# 股價觸底月份與事件名稱來自 COBB MEMO 圖上標註；P/E 數字由本站用 HOM 計算。
EVENT_BOTTOMS = [
    {"ym": "2015-08", "label": "中國股災+美國預期升息", "short": "股災"},
    {"ym": "2016-02", "label": "美國升息+中國股災", "short": "美升息"},
    {"ym": "2018-12", "label": "中美貿易戰加劇", "short": "貿易戰"},
    {"ym": "2020-03", "label": "疫情風暴", "short": "疫情"},
    {"ym": "2022-10", "label": "暴力升息+庫存調整", "short": "升息"},
    {"ym": "2023-10", "label": "殖利率倒掛收斂+美債信評降評", "short": "信評"},
    {"ym": "2024-08", "label": "日圓套利平倉+薩姆法則觸發", "short": "日圓"},
    {"ym": "2025-04", "label": "對等關稅", "short": "關稅"},
    {"ym": "2026-03", "label": "美以伊戰爭+油價失控", "short": "戰爭"},
]

HOME_WINDOW = "2016"  # 與第 4 項燈號的約 10 年窗口較接近；不用 2020 起（偏短、含零利率高估值期）
WINDOWS_LIVE = ("2020", "2016", "2011")  # 本站只重算這三個；2006／2001 只引用 MEMO（HOM 有缺口）
MEMO_REPRO_END = "2026-11-05"  # SOX 可重現 MEMO 的截止日期（當日 forwardOwn＝12.72）
UA = "charity-poker-king/valuation (+https://manolomoo.github.io/charity-poker-king/)"


def r2(x):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), 2)


def r1(x):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), 1)


def window_stats(series: pd.Series) -> dict:
    s = series.dropna()
    if s.empty:
        return {}
    mu = float(s.mean())
    sd = float(s.std(ddof=1)) if len(s) >= 2 else 0.0
    return {
        "n": int(len(s)),
        "start": s.index[0].strftime("%Y-%m"),
        "end": s.index[-1].strftime("%Y-%m"),
        "mean": r2(mu),
        "median": r2(float(s.median())),
        "q1": r2(float(s.quantile(0.25))),
        "q3": r2(float(s.quantile(0.75))),
        "std": r2(sd),
        "up05": r2(mu + 0.5 * sd),
        "dn05": r2(mu - 0.5 * sd),
        "up10": r2(mu + 1.0 * sd),
        "dn10": r2(mu - 1.0 * sd),
        "up15": r2(mu + 1.5 * sd),
        "dn15": r2(mu - 1.5 * sd),
        "_mu": mu,
        "_sd": sd,
    }


def sigma_band(value: float, mu: float, sd: float) -> tuple[str, float | None]:
    if sd is None or sd <= 0 or value is None:
        return "無法判定", None
    z = (value - mu) / sd
    if z < -1.5:
        lab = "低於 μ−1.5σ"
    elif z < -1:
        lab = "μ−1.5σ～μ−1σ"
    elif z < -0.5:
        lab = "μ−1σ～μ−0.5σ"
    elif z < 0:
        lab = "μ−0.5σ～μ"
    elif z <= 0.5:
        lab = "μ～μ+0.5σ"
    elif z <= 1:
        lab = "μ+0.5σ～μ+1σ"
    elif z <= 1.5:
        lab = "μ+1σ～μ+1.5σ"
    else:
        lab = "高於 μ+1.5σ"
    return lab, r2(z)


def vs_quartile(value: float, q1: float, median: float, q3: float) -> str:
    if value < q1:
        return "低於 Q1"
    if value < median:
        return "Q1～中位數"
    if value <= q3:
        return "中位數～Q3"
    return "高於 Q3"


def locate(value: float, stats: dict) -> dict:
    mu, sd = stats.get("_mu"), stats.get("_sd")
    band, z = sigma_band(value, mu, sd) if mu is not None else ("無法判定", None)
    return {
        "value": r2(value),
        "band": band,
        "z": z,
        "percentile": r1(stats.get("_pct")) if "_pct" in stats else None,
        "vs_quartile": vs_quartile(value, stats["q1"], stats["median"], stats["q3"])
        if stats.get("q1") is not None else None,
    }


def attach_percentile(stats: dict, series: pd.Series, value: float) -> dict:
    s = series.dropna()
    out = dict(stats)
    if s.empty:
        out["_pct"] = None
    else:
        out["_pct"] = float((s < value).mean() * 100)
    return out


def parse_hom(name: str, payload: dict) -> tuple[pd.Series, pd.Series, dict]:
    if name == "NDX":
        weekly = pd.Series({pd.Timestamp(x["date"]): x["value"] for x in payload["forward"]}).sort_index()
        own = pd.Series({pd.Timestamp(x["date"]): x["value"] for x in payload["forwardOwn"]}).sort_index()
        meta = {
            "updated": payload.get("updated"),
            "history_starts": (payload.get("historyStarts") or {}).get("forward"),
            "terminal_last": weekly.index[-1].strftime("%Y-%m-%d") if len(weekly) else None,
        }
    else:
        block = payload["sectors"]["sox"]
        weekly = pd.Series({pd.Timestamp(x["date"]): x["value"] for x in block["series"]}).sort_index()
        own = pd.Series({pd.Timestamp(x["date"]): x["value"] for x in block["forwardOwn"]}).sort_index()
        meta = {
            "updated": payload.get("updated"),
            "history_starts": block.get("historyStarts"),
            "terminal_last": weekly.index[-1].strftime("%Y-%m-%d") if len(weekly) else None,
        }
    return weekly.astype(float), own.astype(float), meta


def stitch(weekly: pd.Series, own: pd.Series) -> pd.Series:
    if weekly.empty:
        return own.sort_index()
    extra = own[own.index > weekly.index[-1]]
    return pd.concat([weekly, extra]).sort_index()


def monthly_last(series: pd.Series) -> pd.Series:
    return series.resample("ME").last().dropna()


def last_completed_month_end(today: date | None = None) -> pd.Timestamp:
    today = today or date.today()
    first = date(today.year, today.month, 1)
    return pd.Timestamp(first) - pd.offsets.MonthEnd(1)


def series_to_points(monthly: pd.Series, incomplete: tuple[str, float] | None = None) -> list[dict]:
    pts = [{"date": ix.strftime("%Y-%m"), "value": r2(v), "complete": True} for ix, v in monthly.items()]
    if incomplete:
        pts.append({"date": incomplete[0], "value": r2(incomplete[1]), "complete": False})
    return pts


def load_cached_payload(cache_dir: Path, name: str) -> dict | None:
    path = Path(cache_dir) / CACHE_NAME[name]
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_hom(cache_dir: Path, timeout: int = 30) -> list[str]:
    """抓 HOM API 寫入 cache。失敗只記筆記，不丟例外。"""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    notes = []
    for name, url in HOM_URLS.items():
        path = cache_dir / CACHE_NAME[name]
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            payload = json.loads(raw.decode("utf-8"))
            parse_hom(name, payload)  # 形狀檢查
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(raw)
            tmp.replace(path)
            notes.append(f"{name} HOM {len(raw)} bytes")
        except (URLError, HTTPError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as e:
            notes.append(f"{name} HOM 抓取失敗（{type(e).__name__}: {e}）")
    return notes


def _index_daily(cache_dir: Path, name: str) -> pd.Series | None:
    path = Path(cache_dir) / f"{name}_daily_2y.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["date"])
    if "close" not in df.columns or df.empty:
        return None
    return df.set_index("date")["close"].sort_index().astype(float)


def _event_rows(name: str, stitched: pd.Series, prices: pd.Series | None) -> list[dict]:
    rows = []
    for ev in EVENT_BOTTOMS:
        ym = ev["ym"]
        sl = stitched[stitched.index.to_period("M") == ym]
        row = {
            "ym": ym,
            "label": ev["label"],
            "short": ev.get("short") or ev["label"],
            "source": "事件名稱來自 COBB MEMO 圖上標註；P/E 為 History of Market 同口徑（週頻接日線）",
        }
        if sl.empty:
            row["available"] = False
            rows.append(row)
            continue
        imin = sl.idxmin()
        row.update({
            "available": True,
            "month_min_pe": r2(float(sl.min())),
            "month_min_date": imin.strftime("%Y-%m-%d"),
            "month_last_pe": r2(float(sl.iloc[-1])),
            "month_last_date": sl.index[-1].strftime("%Y-%m-%d"),
            "sampling": "週頻（該月若已進入 forwardOwn 日線則為日線）",
        })
        if prices is not None:
            px = prices[prices.index.to_period("M") == ym]
            if not px.empty:
                pdate = px.idxmin()
                row["price_low_date"] = pdate.strftime("%Y-%m-%d")
                row["price_low_close"] = r2(float(px.min()))
                # 當日 P/E：同日；沒有則取最近一筆
                if pdate in stitched.index:
                    row["price_low_pe"] = r2(float(stitched.loc[pdate]))
                    row["price_low_pe_date"] = pdate.strftime("%Y-%m-%d")
                else:
                    loc = stitched.index.get_indexer([pdate], method="nearest")[0]
                    near = stitched.index[loc]
                    row["price_low_pe"] = r2(float(stitched.iloc[loc]))
                    row["price_low_pe_date"] = near.strftime("%Y-%m-%d")
                    row["price_low_pe_note"] = "指數低點當日無 P/E 點，取最接近的資料日"
        rows.append(row)
    return rows


def _signal4(latest_path: Path | None) -> dict | None:
    if not latest_path or not Path(latest_path).exists():
        return None
    try:
        latest = json.loads(Path(latest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    out = {
        "rules_version": latest.get("rules_version"),
        "run_date": latest.get("run_date"),
        "queried_at": latest.get("queried_at"),
        "rule": "第 4 項燈號規則未改（v3.0）：10 年分位 ≤ 20% 為紅燈；看已完成週K，維持到下一根週K。",
        "rows": {},
    }
    for r in latest.get("rows") or []:
        if str(r.get("signal")) != "4":
            continue
        extra = r.get("extra") or {}
        out["rows"][r.get("target")] = {
            "status": r.get("status"),
            "status_label": r.get("status_label"),
            "obs_date": extra.get("obs_date") or r.get("data_date"),
            "pe": extra.get("pe"),
            "pe_date": None,
            "pct10": r2(extra.get("pct10")) if extra.get("pct10") is not None else None,
            "pct5": r2(extra.get("pct5")) if extra.get("pct5") is not None else None,
            "friday_pct10": r2(extra.get("friday_pct10")) if extra.get("friday_pct10") is not None else None,
            "value_text": r.get("value"),
            "hold": (extra.get("hold") or {}).get("text"),
        }
        # 從「NTM P/E 15.06×（2026-11-12）」抽出日期
        text = r.get("value") or ""
        if "（" in text and "）" in text and "NTM P/E" in text:
            try:
                inside = text.split("NTM P/E", 1)[1]
                date_s = inside.split("（", 1)[1].split("）", 1)[0]
                if len(date_s) == 10:
                    out["rows"][r.get("target")]["pe_date"] = date_s
            except (IndexError, ValueError):
                pass
    return out


def _crash_check(name: str, own: pd.Series, prices: pd.Series | None) -> dict | None:
    """確認 2026-11-05 SOX 12.72 主要是價格變動，不是分母異常。"""
    day = pd.Timestamp("2026-11-05")
    prev = pd.Timestamp("2026-11-04")
    if day not in own.index or prev not in own.index:
        return None
    pe0, pe1 = float(own.loc[prev]), float(own.loc[day])
    out = {
        "prev_date": "2026-11-04",
        "date": "2026-11-05",
        "pe_prev": r2(pe0),
        "pe": r2(pe1),
        "pe_chg_pct": r2((pe1 / pe0 - 1) * 100),
    }
    if prices is not None and prev in prices.index and day in prices.index:
        px0, px1 = float(prices.loc[prev]), float(prices.loc[day])
        out.update({
            "close_prev": r2(px0),
            "close": r2(px1),
            "price_chg_pct": r2((px1 / px0 - 1) * 100),
            "implied_eps_prev": r2(px0 / pe0),
            "implied_eps": r2(px1 / pe1),
            "implied_eps_chg_pct": r2(((px1 / pe1) / (px0 / pe0) - 1) * 100),
        })
        # 價格跌幅與本益比跌幅同向且接近，即視為以價格為主
        if out["price_chg_pct"] is not None and out["pe_chg_pct"] is not None:
            out["conclusion"] = (
                "本益比跌幅與指數收盤跌幅接近，隱含 EPS 沒有大幅下修，12.72 主要是價格變動，不像資料異常。"
                if abs(out["pe_chg_pct"] - out["price_chg_pct"]) < 5
                else "價格與本益比變動幅度有差距，頁面上並列，不自動認定資料無誤。"
            )
    return out


def _public_stats(stats: dict) -> dict:
    return {k: v for k, v in stats.items() if not k.startswith("_")}


def _home_tag(name: str, value: float, value_date: str, stats: dict, band: str) -> dict:
    # 一位小數；日期用 M/D
    dt = pd.Timestamp(value_date)
    text = f"Fwd P/E {r1(value)}×｜{band}（2016起）｜HOM {dt.month}/{dt.day}"
    return {
        "text": text,
        "pe": r1(value),
        "pe_full": r2(value),
        "date": value_date,
        "band": band,
        "window": HOME_WINDOW,
        "source": "History of Market forwardOwn",
        "href": f"./trend/valuation/#{name.lower()}",
    }


def build_valuation(cache_dir: Path, latest_path: Path | None = None, today: date | None = None) -> dict:
    """由 cache 裡的 HOM JSON 組出估值位置。缺檔則 available=False。"""
    today = today or date.today()
    cache_dir = Path(cache_dir)
    missing = [n for n in ("SOX", "NDX") if load_cached_payload(cache_dir, n) is None]
    if missing:
        return {
            "available": False,
            "reason": f"沒有 History of Market 快取（缺 {', '.join(missing)}），本次未計算估值位置",
            "version": "v1-2026-11-15",
        }

    completed_end = last_completed_month_end(today)
    indices = {}
    events = {}
    home_tags = {}
    crash = {}
    metas = {}

    for name in ("SOX", "NDX"):
        weekly, own, meta = parse_hom(name, load_cached_payload(cache_dir, name))
        metas[name] = meta
        stitched = stitch(weekly, own)
        monthly_all = monthly_last(stitched)
        monthly = monthly_all[monthly_all.index <= completed_end]
        cur = float(own.iloc[-1])
        cur_date = own.index[-1].strftime("%Y-%m-%d")
        prices = _index_daily(cache_dir, name)

        windows = {}
        for start in WINDOWS_LIVE:
            h = monthly[monthly.index >= pd.Timestamp(f"{start}-01-01")]
            st = attach_percentile(window_stats(h), h, cur)
            loc = locate(cur, st)
            loc["date"] = cur_date
            windows[start] = {**_public_stats(st), "current_vs": loc}

        # 當月未完成點（供圖上單獨標）
        incomplete = None
        if own.index[-1].to_period("M") > completed_end.to_period("M"):
            incomplete = (own.index[-1].strftime("%Y-%m"), cur)

        # MEMO 重現：截至 2026-11-05 的月最後一筆
        repro_m = monthly_last(stitched[stitched.index <= pd.Timestamp(MEMO_REPRO_END)])
        cobb = COBB_MEMO["indices"][name]
        repro = {"through": MEMO_REPRO_END, "windows": {}, "exact_match": name == "SOX"}
        for start, cobb_w in cobb["windows"].items():
            if start not in WINDOWS_LIVE:
                continue  # 2006／2001 不重算
            h = repro_m[repro_m.index >= pd.Timestamp(f"{start}-01-01")]
            st = _public_stats(window_stats(h))
            diffs = {}
            for k, cv in cobb_w.items():
                if k in st and st[k] is not None:
                    diffs[k] = r2(st[k] - cv)
            repro["windows"][start] = {"hom": st, "cobb": cobb_w, "delta": diffs}
            if name == "SOX":
                # 兩位小數全相等才算完全重現
                if any(abs(v) > 0.0001 for v in diffs.values()):
                    repro["exact_match"] = False
            else:
                repro["exact_match"] = False

        # NDX 2006／2001 不重算（HOM 2005-01 至 2006-07 有缺口）
        gap = None
        if name == "NDX":
            diff = weekly.index.to_series().diff()
            holes = diff[diff > pd.Timedelta(days=14)]
            if not holes.empty:
                # 缺口起點是上一個點的隔天，終點是這個點前一天
                items = []
                for end, delta in holes.items():
                    start = end - delta + pd.Timedelta(days=1)
                    items.append({"from": start.strftime("%Y-%m-%d"), "to": (end - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                                  "days": int(delta.days)})
                gap = items

        indices[name] = {
            "latest": {"value": r2(cur), "date": cur_date, "basis": "forwardOwn 日線"},
            "windows": windows,
            "monthly": series_to_points(monthly[monthly.index >= pd.Timestamp("2011-01-01")], incomplete),
            "memo_repro": repro,
            "history_starts": meta.get("history_starts"),
            "terminal_last": meta.get("terminal_last"),
            "own_first": own.index[0].strftime("%Y-%m-%d") if len(own) else None,
            "gap": gap,
        }
        if MEMO_REPRO_END in set(own.index.strftime("%Y-%m-%d")):
            indices[name]["memo_print"] = {
                "date": MEMO_REPRO_END,
                "value": r2(float(own.loc[pd.Timestamp(MEMO_REPRO_END)])),
                "note": "當月尚未結束的單日讀數；MEMO 2026-11 月值與這一天相同（SOX 12.72；NDX 最接近 11/06 的 18.07）。",
            }

        events[name] = _event_rows(name, stitched, prices)
        chk = _crash_check(name, own, prices)
        if chk:
            crash[name] = chk

        tag_stats = windows[HOME_WINDOW]
        home_tags[name] = _home_tag(
            name, cur, cur_date, tag_stats, tag_stats["current_vs"]["band"]
        )

    # 12.72 與 14.40 並列說明
    sox_own_note = None
    if "SOX" in crash and indices["SOX"].get("memo_print"):
        sox_own_note = (
            f"COBB MEMO 的 SOX 2026-11＝12.72，與 History of Market 2026-11-05 日線相同，"
            f"是當月尚未結束的單日低點；同序列 2026-11-13 已回到 {indices['SOX']['latest']['value']}。"
            + ((" " + crash["SOX"]["conclusion"]) if crash["SOX"].get("conclusion") else "")
        )

    return {
        "available": True,
        "version": "v1-2026-11-15",
        "computed_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "completed_month_end": completed_end.strftime("%Y-%m-%d"),
        "home_window": HOME_WINDOW,
        "sources": {
            "cobb_memo": "COBB MEMO（2026-11）Forward P/E 圖表；快照凍結，不自動更新。",
            "hom": "History of Market NTM 12 個月 blended-forward 本益比：終端機週頻序列接 2026-08-05 之後的 forwardOwn 日線；本站視窗統計用已完成月份的月最後一筆。當月未完成值只當現值，不進入 σ 帶與四分位。",
            "hom_pages": HOM_PAGES,
            "sampling_note": "歷史事件月若落在週頻段，取樣是每週一點（多為該週最後一個讀數），與 2026-11 的日線低點不可直接當成同一頻率。",
        },
        "cobb_memo": {
            **{k: COBB_MEMO[k] for k in ("source", "asof", "indices")},
            "note": "這是 MEMO 快照，不隨行情更新。SOX 的 2020／2016／2011 視窗統計，本站以 HOM 月最後一筆截至 2026-11-05 可重現到小數點後兩位；並不表示已向 COBB 確認他使用該資料商。NDX 用同一做法對不上（mean 約差 0.7–1.1，且隨視窗拉長而變大，不是固定位移），資料商未確認，不可把 HOM 現值拿去比 MEMO 的 σ 帶。NDX 2006 起與 2001 起只引用 MEMO，本站不重算（HOM 在 2005-01 至 2006-07 有缺口）。",
        },
        "indices": indices,
        "events": events,
        "home_tags": home_tags,
        "crash_check": crash,
        "memo_month_note": sox_own_note,
        "signal4": _signal4(latest_path),
        "caveats": [
            "半導體等循環股本益比：獲利高峰時 P/E 往往最低，谷底時反而最高，偏低不一定等於便宜。",
            "Forward EPS 是分析師預估，下修通常落後股價；價格不動，P/E 也會因下修而變貴。",
            "NTM blended、不同資料商、成分股調整，數字不能互相比較或加減常數對齊。",
            "2020 年起的視窗含零利率高估值期，均值不一定會回歸；本站首頁標籤改看 2016 年起。",
            "事件底是事後標註，只有 9 個樣本。估值說明位置，不說明時點；P/E 沒有固定地板（SOX 歷次事件月最低約從 11.5 到 23.9 都有）。",
            "不要用「P/E × EPS」推指數目標價，也不要寫成「已低於某次底部所以見底」。",
            "第 4 項燈號看已完成週K 的 10 年分位，首頁標籤看日線現值與 2016 年起 σ 帶，兩者時間與規則都不同，可能不一致。",
        ],
        "signal4_unchanged": "11 項燈號 v3.0 第 4 項規則與門檻未改。本頁只提供估值位置參考，不改燈、也不另設目標價。",
    }


def summarize(val: dict) -> list[str]:
    if not val or not val.get("available"):
        return [val.get("reason") or "估值位置未計算"]
    lines = []
    for name in ("SOX", "NDX"):
        tag = (val.get("home_tags") or {}).get(name) or {}
        latest = val["indices"][name]["latest"]
        lines.append(f"{name} {tag.get('text') or (str(latest['value']) + ' @' + latest['date'])}")
    return lines
