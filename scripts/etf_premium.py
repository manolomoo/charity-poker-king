"""首頁「ETF 溢折價」快取：抓證交所 all_etf.txt，寫 data/etf_premium.json。

資料來源：臺灣證券交易所「申贖資訊及即時淨值揭露專區」彙整各投信的預估淨值
  https://mis.twse.com.tw/stock/data/all_etf.txt
  （需帶 Referer etf_nav.jsp 與一般瀏覽器 User-Agent，否則常 502）
該端點沒有 CORS，GitHub Pages 的瀏覽器無法直連，所以由 GitHub Actions 定時抓、寫成 JSON，
首頁再讀 raw.githubusercontent.com 上的這份 JSON。

欄位：a 代號、b 名稱、e 成交價、f 預估淨值、g 預估折溢價幅度（%）、h 前一營業日淨值、
     i 資料日期 YYYYMMDD、j 資料時間 HH:MM:SS（台北時間）。

使用者要的 00630L 不是上市代號（全市場 ETF 名單沒有），對應元大台灣50正2 的 00631L。

失敗策略：上游 502 會 retry；仍失敗就保留舊 JSON 的數字、標 stale=true、留下錯誤與嘗試時間，以 0 結束。
舊檔也沒有時寫出空殼（四檔都列出、數值為 null），一樣以 0 結束，讓首頁顯示「暫無資料」。
缺值一律寫 null，絕不寫成 0。
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
OUT = os.path.join(ROOT, "data", "etf_premium.json")
TPE = ZoneInfo("Asia/Taipei")

URL = "https://mis.twse.com.tw/stock/data/all_etf.txt"
REFERER = "https://mis.twse.com.tw/stock/etf_nav.jsp"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
SOURCE = "臺灣證券交易所 申贖資訊及即時淨值揭露專區（彙整各投信預估淨值）"

# (代號, 首頁簡稱)；順序就是首頁列的順序。
# 順序依使用者：0050／正2、00662／正2、00830、00646／正2。
# 費城半導體正 2 倍在證交所 all_etf.txt 找不到（沒有名稱含費城／費半／半導體且為正2／2倍／L 的檔），這一列省略。
TARGETS: list[tuple[str, str]] = [
    ("0050", "元大台灣50"),
    ("00631L", "元大台灣50正2"),  # 使用者寫的 00630L 不存在，對應 00631L
    ("00662", "富邦NASDAQ"),
    ("00670L", "富邦NASDAQ正2"),
    ("00830", "國泰費半"),
    ("00646", "元大S&P500"),
    ("00647L", "元大S&P500正2"),
]
CODE_ALIASES = {"00630L": "00631L"}


def now_tpe() -> dt.datetime:
    return dt.datetime.now(TPE).replace(microsecond=0)


def _num(v: Any) -> float | None:
    """證交所欄位轉數字；空字串、'-'、非數字 → None（不是 0）。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "--", "N/A", "null"):
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    if x != x:  # NaN
        return None
    return x


def _data_at(d: Any, t: Any) -> str | None:
    """i=YYYYMMDD、j=HH:MM:SS → ISO 8601（+08:00）。任一缺漏 → None。"""
    d = str(d or "").strip()
    t = str(t or "").strip()
    if len(d) != 8 or not d.isdigit():
        return None
    try:
        if t:
            x = dt.datetime.strptime(d + " " + t, "%Y%m%d %H:%M:%S")
        else:
            return None
    except ValueError:
        return None
    return x.replace(tzinfo=TPE).isoformat()


def empty_item(code: str, short: str) -> dict:
    return {
        "code": code, "short": short, "name": None,
        "price": None, "nav": None, "premium_pct": None, "prev_nav": None,
        "data_date": None, "data_time": None, "data_at": None, "ref_url": None,
    }


def parse(payload: Any) -> dict[str, dict]:
    """解析 all_etf.txt 的 JSON，回傳 {代號: 原始欄位整理後的 dict}（全市場，不只四檔）。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("a1"), list):
        raise ValueError("all_etf 格式不符：缺少 a1 陣列")
    out: dict[str, dict] = {}
    for blk in payload["a1"]:
        if not isinstance(blk, dict):
            continue
        ref = blk.get("refURL")
        for row in blk.get("msgArray") or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("a") or "").strip()
            if not code:
                continue
            out[code] = {
                "name": (str(row.get("b")).strip() or None) if row.get("b") is not None else None,
                "price": _num(row.get("e")),
                "nav": _num(row.get("f")),
                "premium_pct": _num(row.get("g")),
                "prev_nav": _num(row.get("h")),
                "data_date": (str(row.get("i")).strip() or None) if row.get("i") else None,
                "data_time": (str(row.get("j")).strip() or None) if row.get("j") else None,
                "data_at": _data_at(row.get("i"), row.get("j")),
                "ref_url": ref or None,
            }
    if not out:
        raise ValueError("all_etf 沒有任何 ETF 列")
    return out


def pick(rows: dict[str, dict]) -> list[dict]:
    items = []
    for code, short in TARGETS:
        it = empty_item(code, short)
        if code in rows:
            it.update(rows[code])
        items.append(it)
    return items


def fetch(retries: int = 5, wait: float = 3.0, session: requests.Session | None = None) -> Any:
    """抓 all_etf.txt；502／非 JSON 會 retry（間隔遞增），全失敗丟最後的例外。"""
    s = session or requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Referer": REFERER,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
    })
    last: Exception | None = None
    for k in range(retries):
        try:
            if k > 0:
                # 先走一次揭露專區頁面拿 cookie，常能讓下一次不被擋
                try:
                    s.get(REFERER, timeout=15)
                except requests.RequestException:
                    pass
            r = s.get(URL, params={"_": int(time.time() * 1000)}, timeout=20)
            r.raise_for_status()
            return json.loads(r.content.decode("utf-8-sig"))
        except (requests.RequestException, ValueError) as e:
            last = e
            print(f"[etf_premium] 第 {k + 1}/{retries} 次失敗：{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            if k < retries - 1:
                time.sleep(wait * (k + 1))
    assert last is not None
    raise last


def load_old(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("items"), list) else None
    except (OSError, ValueError):
        return None


def _base(now: dt.datetime) -> dict:
    return {
        "version": 1,
        "source": SOURCE,
        "source_url": URL,
        "note": "交易時段約每 10 分鐘由 GitHub Actions 更新的快取，不是瀏覽器直連即時報價。",
        "code_aliases": dict(CODE_ALIASES),
        "attempted_at": now.isoformat(),
    }


def build_ok(rows: dict[str, dict], now: dt.datetime, old: dict | None = None) -> dict:
    """抓取成功。若某檔這次缺列、舊檔有值，沿用舊值並在該列標 stale。"""
    items = pick(rows)
    old_by = {i.get("code"): i for i in (old or {}).get("items", []) if isinstance(i, dict)}
    missing = []
    for n, it in enumerate(items):
        if it["code"] not in rows:
            missing.append(it["code"])
            o = old_by.get(it["code"])
            if o and o.get("data_at"):
                items[n] = {**empty_item(it["code"], it["short"]), **o, "short": it["short"], "stale": True}
    d = _base(now)
    d.update({
        "fetched_at": now.isoformat(),
        "stale": False,
        "error": ("上游缺少：" + "、".join(missing)) if missing else None,
        "items": items,
    })
    return d


def build_fail(err: Exception | str, now: dt.datetime, old: dict | None) -> dict:
    """抓取失敗：有舊檔就保留舊值；沒有就寫空殼（四檔、值為 null）。"""
    d = _base(now)
    if old:
        old_by = {i.get("code"): i for i in old.get("items", []) if isinstance(i, dict)}
        items = []
        for code, short in TARGETS:
            o = old_by.get(code)
            items.append({**empty_item(code, short), **(o or {}), "short": short})
        d.update({
            "fetched_at": old.get("fetched_at"),
            "stale": True,
            "error": str(err)[:300],
            "items": items,
        })
    else:
        d.update({
            "fetched_at": None,
            "stale": True,
            "error": str(err)[:300],
            "items": [empty_item(c, s) for c, s in TARGETS],
        })
    return d


def write(d: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def run(path: str = OUT, retries: int = 5, wait: float = 3.0, fetcher=None) -> dict:
    now = now_tpe()
    old = load_old(path)
    try:
        payload = (fetcher or (lambda: fetch(retries=retries, wait=wait)))()
        rows = parse(payload)
        d = build_ok(rows, now, old)
    except Exception as e:  # noqa: BLE001 — 任何上游問題都退回舊值
        d = build_fail(f"{type(e).__name__}: {e}", now, old)
    write(d, path)
    return d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--wait", type=float, default=3.0)
    a = ap.parse_args(argv)
    d = run(a.out, a.retries, a.wait)
    tag = "STALE" if d["stale"] else "OK"
    print(f"[etf_premium] {tag} fetched_at={d['fetched_at']} error={d['error']}")
    for it in d["items"]:
        print(f"  {it['code']:7s} e={it['price']} f={it['nav']} g={it['premium_pct']} at={it['data_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
