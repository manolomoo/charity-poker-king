"""首頁「各指數本益比」快照：寫 data/index_pe.json。

不參與、也不修改 11 項燈號 v3.0 的任何規則。每個數字都附來源、資料日與口徑（trailing／forward），
抓不到或沒有可靠來源的格子一律 null（首頁顯示「—」），絕不補數字、不混用口徑。

來源
  - SPX、NDX：History of Market（historyofmarket.com，CC BY 4.0）
      trailing＝成分股市值加權 Σ(w·P)/Σ(w·近四季EPS)，每日（forward-pe.json 的 trailing 序列）
      forward ＝12 個月預估 P/E（FY1/FY2 共識盈餘混合，ETF 持股加權），每日（forwardOwn 序列）
      注意：同檔的 forward／current.forward 是週資料，目前停在 2026-08-05，不用。
  - SOX：同上 sectors/forward-pe.json 的 forwardOwnLatest（與 COBB MEMO 2026-11 的 11.69 相符）。
      SOX trailing 沒有可靠來源 → null。
  - 加權：證交所 BWIBBU_d（個股近四季本益比）× t187ap03_L（已發行普通股數）自算，
      Σ市值 ÷ Σ(市值/本益比)，只含本益比為正的個股；非證交所官方「大盤本益比」。
  - 台指期：不適用。

失敗策略：某來源失敗就保留舊 JSON 該格並記錄錯誤、stale=true；永遠以 0 結束。
節流：距上次成功抓取不到 --min-age-hours（預設 3）就直接略過（排程 10 分鐘跑一次，本益比不需要）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from typing import Any
from zoneinfo import ZoneInfo

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "index_pe.json")
TPE = ZoneInfo("Asia/Taipei")
UA = "Mozilla/5.0 (compatible; charity-poker-king/1.0; +https://github.com/manolomoo/charity-poker-king)"

HOM = "https://historyofmarket.com/api"
HOM_URLS = {
    "SPX": HOM + "/sp500/forward-pe.json",
    "NDX": HOM + "/ndx/forward-pe.json",
    "SOX": HOM + "/sectors/forward-pe.json",
}
HOM_PAGE = "historyofmarket.com"
TWSE_PE = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d"
TWSE_CO = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
SHARES_FIELD = "已發行普通股數或TDR原股發行股數"

ORDER = [("SPX", "S&P 500"), ("NDX", "Nasdaq 100"), ("SOX", "費半"), ("TWII", "加權指數")]

TR_HOM = "成分股市值加權，近四季 EPS"
FW_HOM = "12 個月預估，FY1/FY2 共識混合"
TR_TWSE = "證交所個股本益比（近四季）市值加權，本站自算、排除虧損股"

NOTE = ("美股三指數：History of Market（CC BY 4.0）；加權：證交所個股本益比以市值加權自算（非官方大盤本益比）。"
        "近四季＝trailing，預估＝未來 12 個月 forward，兩者口徑不同不可直接相比。台指期不適用；沒有可靠來源的格子顯示「—」。")


def now_tpe() -> dt.datetime:
    return dt.datetime.now(TPE).replace(microsecond=0)


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(str(v).replace(",", "").strip())
    except ValueError:
        return None
    return x if x == x and abs(x) != float("inf") else None


def cell(value: Any, asof: str | None, source: str, method: str, **extra: Any) -> dict | None:
    v = _num(value)
    if v is None or v <= 0 or not asof:
        return None
    d = {"value": round(v, 2), "asof": asof, "source": source, "method": method}
    d.update(extra)
    return d


def _last(series: Any) -> tuple[str | None, Any]:
    """[{date, value}, ...] → 最後一筆 (date, value)；空 → (None, None)。"""
    if isinstance(series, list) and series and isinstance(series[-1], dict):
        return series[-1].get("date"), series[-1].get("value")
    return None, None


def parse_hom_index(payload: Any) -> dict:
    """SPX／NDX forward-pe.json → {"trailing": cell|None, "forward": cell|None}。forward 取 forwardOwn（每日）。"""
    out: dict = {"trailing": None, "forward": None}
    if not isinstance(payload, dict):
        return out
    d, v = _last(payload.get("trailing"))
    out["trailing"] = cell(v, d, HOM_PAGE, TR_HOM)
    d, v = _last(payload.get("forwardOwn"))
    out["forward"] = cell(v, d, HOM_PAGE, FW_HOM)
    return out


def parse_hom_sox(payload: Any) -> dict:
    """sectors/forward-pe.json → SOX。trailing 無可靠來源，固定 None。"""
    out: dict = {"trailing": None, "forward": None}
    try:
        sox = payload["sectors"]["sox"]
    except (KeyError, TypeError):
        return out
    lat = sox.get("forwardOwnLatest") if isinstance(sox, dict) else None
    if isinstance(lat, dict):
        out["forward"] = cell(lat.get("value"), lat.get("date"), HOM_PAGE, FW_HOM)
    else:
        d, v = _last(sox.get("forwardOwn") if isinstance(sox, dict) else None)
        out["forward"] = cell(v, d, HOM_PAGE, FW_HOM)
    return out


def _ymd(s: Any) -> str | None:
    s = str(s or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def compute_twii(pe_rows: Any, co_rows: Any) -> dict | None:
    """BWIBBU_d 個股列＋公司基本資料 → 加權市值加權本益比 cell（含涵蓋率）；資料不足 → None。

    市值＝收盤價×已發行普通股數；P/E＝Σ市值 ÷ Σ(市值/個股本益比)，只含本益比為正者。
    涵蓋率＝入算市值 ÷ BWIBBU_d 內有股數的個股總市值。
    """
    if not isinstance(pe_rows, list) or not isinstance(co_rows, list):
        return None
    shares: dict[str, float] = {}
    for c in co_rows:
        if isinstance(c, dict):
            s = _num(c.get(SHARES_FIELD))
            if s and s > 0:
                shares[str(c.get("公司代號"))] = s
    num = den = total = 0.0
    n = 0
    asof = None
    for r in pe_rows:
        if not isinstance(r, dict):
            continue
        s = shares.get(str(r.get("Code")))
        px = _num(r.get("ClosePrice"))
        if not s or not px or px <= 0:
            continue
        asof = asof or _ymd(r.get("Date"))
        m = px * s
        total += m
        pe = _num(r.get("PEratio"))
        if pe and pe > 0:
            num += m
            den += m / pe
            n += 1
    if n < 500 or total <= 0 or den <= 0 or not asof:
        return None
    cov = round(num / total * 100, 1)
    if cov < 90:  # 涵蓋太低代表資料殘缺，不如顯示「—」
        return None
    return cell(num / den, asof, "TWSE OpenAPI", TR_TWSE, coverage_pct=cov, stocks=n)


def load_old(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _retry(fn, retries: int = 3, wait: float = 2.0):
    last: Exception | None = None
    for k in range(retries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if k < retries - 1:
                time.sleep(wait * (k + 1))
    raise last  # type: ignore[misc]


def _get_json(url: str, timeout: int = 40) -> Any:
    def go():
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    return _retry(go)


def default_fetchers() -> dict:
    return {"hom": _get_json, "twse": _get_json}


def build(now: dt.datetime, old: dict | None, fetchers: dict | None = None) -> dict:
    f = fetchers or default_fetchers()
    old_items = {i.get("key"): i for i in ((old or {}).get("items") or []) if isinstance(i, dict)}
    errors: list[str] = []
    res: dict[str, dict] = {k: {"trailing": None, "forward": None} for k, _ in ORDER}
    failed: set[str] = set()

    payloads: dict[str, Any] = {}
    for key, url in HOM_URLS.items():
        try:
            payloads[key] = f["hom"](url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{key}: History of Market 抓取失敗（{type(e).__name__}: {e}）")
            failed.add(key)
    if "SPX" in payloads:
        res["SPX"] = parse_hom_index(payloads["SPX"])
    if "NDX" in payloads:
        res["NDX"] = parse_hom_index(payloads["NDX"])
    if "SOX" in payloads:
        res["SOX"] = parse_hom_sox(payloads["SOX"])

    try:
        res["TWII"] = {"trailing": compute_twii(f["twse"](TWSE_PE), f["twse"](TWSE_CO)), "forward": None}
        if res["TWII"]["trailing"] is None:
            errors.append("TWII: 證交所資料不足，無法計算")
            failed.add("TWII")
    except Exception as e:  # noqa: BLE001
        errors.append(f"TWII: 證交所抓取失敗（{type(e).__name__}: {e}）")
        failed.add("TWII")

    items = []
    for key, name in ORDER:
        it = {"key": key, "name": name, "trailing": res[key]["trailing"], "forward": res[key]["forward"]}
        if key in failed:
            # 該來源失敗：保留舊的那格（舊值自帶 asof，首頁會顯示資料日）
            prev = old_items.get(key) or {}
            for k in ("trailing", "forward"):
                if it[k] is None and isinstance(prev.get(k), dict):
                    it[k] = prev[k]
        items.append(it)
    return {
        "fetched_at": now.isoformat(),
        "stale": bool(errors),
        "errors": errors,
        "note": NOTE,
        "items": items,
    }


def write(d: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def fresh_enough(old: dict | None, now: dt.datetime, hours: float) -> bool:
    if not old or old.get("stale") or hours <= 0:
        return False
    try:
        t = dt.datetime.fromisoformat(old["fetched_at"])
    except (KeyError, ValueError, TypeError):
        return False
    return 0 <= (now - t).total_seconds() < hours * 3600


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--min-age-hours", type=float, default=3.0)
    args = ap.parse_args(argv)
    now = now_tpe()
    old = load_old(args.out)
    if fresh_enough(old, now, args.min_age_hours):
        print("[index_pe] 上次成功不到 %.1f 小時，略過" % args.min_age_hours)
        return 0
    try:
        d = build(now, old)
        write(d, args.out)
        for it in d["items"]:
            tr, fw = it["trailing"], it["forward"]
            print("[index_pe]", it["key"], "trailing", tr and (tr["value"], tr["asof"]), "forward", fw and (fw["value"], fw["asof"]))
        for e in d["errors"]:
            print("[index_pe] 警告:", e)
    except Exception as e:  # noqa: BLE001
        print(f"[index_pe] 失敗（保留舊檔）：{type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
