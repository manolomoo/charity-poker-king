"""首頁「指數報價」快照：寫 data/indices.json。

來源
  - CNBC quote API：.SPX／.NDX／.SOX（以及加權的備援 .TWII）
  - 證交所 MIS：加權指數 tse_t00.tw（無 CORS，只能伺服器端抓）
  - 期交所 MIS：台指期近月（無 CORS，只能伺服器端抓；日盤、夜盤各抓一次取較新者）

首頁瀏覽器會自己每 60 秒直連 CNBC；這份快照負責台指期，以及 CNBC 連不上時的備援。

失敗策略：某來源失敗就保留該項目舊 JSON 的數字並記錄錯誤、stale=true；缺值一律 null，絕不寫 0；永遠以 0 結束。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from typing import Any
from zoneinfo import ZoneInfo

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "indices.json")
TPE = ZoneInfo("Asia/Taipei")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
CNBC_URL = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
            "?symbols=.SPX%7C.NDX%7C.SOX%7C.TWII&requestMethod=itv&noform=1&partnerId=2"
            "&fund=1&exthrs=1&output=json&events=1")
TWSE_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0"
TWSE_REFERER = "https://mis.twse.com.tw/stock/index.jsp"
TAIFEX_URL = "https://mis.taifex.com.tw/futures/api/getQuoteList"

# (key, 名稱)；順序就是輸出順序
ORDER = [("SPX", "S&P 500"), ("NDX", "Nasdaq 100"), ("SOX", "費半"), ("TWII", "加權指數"), ("TX", "台指期")]
CNBC_SYMBOLS = {".SPX": "SPX", ".NDX": "NDX", ".SOX": "SOX", ".TWII": "TWII"}
NOTE = "美股三指數來自 CNBC；加權來自證交所 MIS（失敗改用 CNBC，非即時）；台指期來自期交所 MIS 近月。時間為台北時間。"


def now_tpe() -> dt.datetime:
    return dt.datetime.now(TPE).replace(microsecond=0)


def _num(v: Any) -> float | None:
    """'7,182.78'、'+156.27'、'-0.25%'、'UNCH' → float；空值、'-'、非數字 → None。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        return x if x == x else None
    s = str(v).strip().replace(",", "").replace("%", "").replace("+", "")
    if s.upper() == "UNCH":
        return 0.0
    if s in ("", "-", "--", "N/A", "null"):
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x == x else None


def empty_item(key: str, name: str) -> dict:
    return {"key": key, "name": name, "last": None, "change": None, "change_pct": None,
            "prev_close": None, "data_at": None, "date_only": False, "realtime": None, "source": None}


def _iso_offset(s: str) -> str | None:
    """'2026-11-27T13:33:15.000+0800' → '2026-11-27T13:33:15+08:00'；無法解析 → None。"""
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", s.strip())
    if not m:
        return None
    off = m.group(2)
    if off is None:
        return None
    if off == "Z":
        off = "+00:00"
    elif ":" not in off:
        off = off[:3] + ":" + off[3:]
    return m.group(1) + off


def parse_cnbc_item(q: Any, key: str, name: str) -> dict | None:
    """CNBC FormattedQuote 一筆 → 項目；code 非 0 或沒有 last → None。"""
    if not isinstance(q, dict):
        return None
    if str(q.get("code", "0")) not in ("0", ""):
        return None
    last = _num(q.get("last"))
    if last is None:
        return None
    it = empty_item(key, name)
    it["last"] = last
    it["change"] = _num(q.get("change"))
    it["change_pct"] = _num(q.get("change_pct"))
    it["prev_close"] = _num(q.get("previous_day_closing"))
    lt = str(q.get("last_time") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", lt):
        it["data_at"], it["date_only"] = lt, True
    else:
        it["data_at"] = _iso_offset(lt)
    rt = str(q.get("realTime", "")).strip().lower()
    it["realtime"] = True if rt == "true" else False if rt == "false" else None
    it["source"] = "CNBC"
    return it


def parse_cnbc(payload: Any) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        rows = payload["FormattedQuoteResult"]["FormattedQuote"]
    except (KeyError, TypeError):
        return out
    if isinstance(rows, dict):
        rows = [rows]
    names = dict(ORDER)
    for q in rows or []:
        key = CNBC_SYMBOLS.get(q.get("symbol")) if isinstance(q, dict) else None
        if not key:
            continue
        it = parse_cnbc_item(q, key, names[key])
        if it:
            out[key] = it
    return out


def parse_twse(payload: Any) -> dict | None:
    """證交所 MIS getStockInfo 回應 → 加權指數項目；z 為 '-'（尚無成交）→ None。"""
    try:
        m = payload["msgArray"][0]
    except (KeyError, IndexError, TypeError):
        return None
    z, y = _num(m.get("z")), _num(m.get("y"))
    if z is None or y is None or y == 0:
        return None
    it = empty_item("TWII", "加權指數")
    chg = round(z - y, 2)
    it["last"], it["prev_close"] = z, y
    it["change"] = chg
    it["change_pct"] = round(chg / y * 100, 2)
    d, t = str(m.get("d") or ""), str(m.get("t") or "")
    try:
        it["data_at"] = dt.datetime.strptime(d + t, "%Y%m%d%H:%M:%S").replace(tzinfo=TPE).isoformat()
    except ValueError:
        it["data_at"] = None
    it["realtime"] = True
    it["source"] = "TWSE MIS"
    return it


def _taifex_time(row: dict, night: bool, day_date: str | None = None) -> dt.datetime | None:
    try:
        t = dt.datetime.strptime(str(row.get("CDate")) + str(row.get("CTime")).zfill(6), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    # 夜盤跨日後的時間若 CDate 仍是日盤那天，實際上是隔天凌晨
    if night and t.hour < 6 and day_date and str(row.get("CDate")) == day_date:
        t += dt.timedelta(days=1)
    return t.replace(tzinfo=TPE)


def pick_taifex(payload: Any, night: bool = False, day_date: str | None = None) -> dict | None:
    """期交所 QuoteList → 近月項目。跳過第一筆現貨，取下一筆 SymbolID 以 -F（日）／-M（夜）結尾且有成交價者。"""
    try:
        rows = payload["RtData"]["QuoteList"]
    except (KeyError, TypeError):
        return None
    suffix = "-M" if night else "-F"
    for r in rows or []:
        if not isinstance(r, dict) or not str(r.get("SymbolID", "")).endswith(suffix):
            continue
        last = _num(r.get("CLastPrice"))
        if last is None:
            continue
        it = empty_item("TX", "台指期")
        it["last"] = last
        it["change"] = _num(r.get("CDiff"))
        it["change_pct"] = _num(r.get("CDiffRate"))
        it["prev_close"] = _num(r.get("CRefPrice"))
        t = _taifex_time(r, night, day_date)
        it["data_at"] = t.isoformat() if t else None
        it["realtime"] = False
        it["source"] = "TAIFEX MIS"
        it["session"] = "夜盤" if night else "日盤"
        it["contract"] = r.get("DispCName") or r.get("SymbolID")
        it["_date"] = str(r.get("CDate"))
        return it
    return None


def choose_taifex(day: dict | None, night: dict | None) -> dict | None:
    """日盤與夜盤取資料時間較新者。"""
    cands = [x for x in (day, night) if x]
    if not cands:
        return None
    best = max(cands, key=lambda x: x.get("data_at") or "")
    return best


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


def fetch_cnbc() -> Any:
    def go():
        r = requests.get(CNBC_URL, headers={"User-Agent": UA}, timeout=10)
        r.raise_for_status()
        return r.json()
    return _retry(go)


def fetch_twse() -> Any:
    def go():
        r = requests.get(TWSE_URL, headers={"User-Agent": UA, "Referer": TWSE_REFERER}, timeout=10)
        r.raise_for_status()
        return r.json()
    return _retry(go)


def fetch_taifex(market_type: str) -> Any:
    body = {"MarketType": market_type, "SymbolType": "F", "KindID": "1", "CID": "TXF",
            "ExpireMonth": "", "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"}

    def go():
        r = requests.post(TAIFEX_URL, json=body, headers={"User-Agent": UA, "Referer": "https://mis.taifex.com.tw/futures/"}, timeout=10)
        r.raise_for_status()
        return r.json()
    return _retry(go)


def load_old(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def build(now: dt.datetime, old: dict | None, fetchers: dict | None = None) -> dict:
    f = {"cnbc": fetch_cnbc, "twse": fetch_twse, "taifex": fetch_taifex}
    f.update(fetchers or {})
    errors: list[str] = []
    got: dict[str, dict] = {}

    cnbc: dict[str, dict] = {}
    try:
        cnbc = parse_cnbc(f["cnbc"]())
        if not cnbc:
            errors.append("CNBC：回應沒有可用報價")
    except Exception as e:  # noqa: BLE001
        errors.append(f"CNBC：{type(e).__name__}: {e}")
    for k in ("SPX", "NDX", "SOX"):
        if k in cnbc:
            got[k] = cnbc[k]
        elif cnbc:
            errors.append(f"CNBC：缺 {k}")

    try:
        tw = parse_twse(f["twse"]())
        if tw:
            got["TWII"] = tw
        else:
            errors.append("證交所 MIS：加權指數無成交價")
    except Exception as e:  # noqa: BLE001
        errors.append(f"證交所 MIS：{type(e).__name__}: {e}")
    if "TWII" not in got and "TWII" in cnbc:
        got["TWII"] = cnbc["TWII"]  # 備援（非即時）；錯誤已記錄，但這項有值

    day = night = None
    try:
        day = pick_taifex(f["taifex"]("0"), night=False)
    except Exception as e:  # noqa: BLE001
        errors.append(f"期交所 MIS 日盤：{type(e).__name__}: {e}")
    try:
        night = pick_taifex(f["taifex"]("1"), night=True, day_date=(day or {}).get("_date"))
    except Exception as e:  # noqa: BLE001
        errors.append(f"期交所 MIS 夜盤：{type(e).__name__}: {e}")
    tx = choose_taifex(day, night)
    if tx:
        got["TX"] = tx
    elif not any(e.startswith("期交所") for e in errors):
        errors.append("期交所 MIS：無可用台指期報價")

    old_by = {it.get("key"): it for it in (old or {}).get("items", []) if isinstance(it, dict)}
    items = []
    for key, name in ORDER:
        it = got.get(key)
        if it is None:
            prev = old_by.get(key)
            it = dict(prev) if prev else empty_item(key, name)
            it["key"], it["name"] = key, name
        it.pop("_date", None)
        items.append(it)
    return {"fetched_at": now.isoformat(), "stale": bool(errors), "errors": errors, "note": NOTE, "items": items}


def write(d: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    try:
        d = build(now_tpe(), load_old(args.out))
        write(d, args.out)
        print(f"寫入 {args.out}：stale={d['stale']}，errors={len(d['errors'])}")
    except Exception as e:  # noqa: BLE001
        print(f"index_quotes 失敗（忽略）：{type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
