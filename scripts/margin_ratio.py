"""大盤融資維持率（自算，多口徑）— 上市 TWSE + 上櫃 TPEx 公開資料。
維持率 = Σ(融資餘額張數 × 1000 × 收盤價) ÷ 融資金額餘額 × 100%
口徑：
  all      : 全部可融資標的（含 ETF）— 市場共識口徑（愛玩股／玩股網／凱基）
  ex_etf   : 分子排除 ETF/ETN/受益憑證/特別股（代號非 4 位數字者），分母仍用官方融資金額 — 財經M平方公開說明之口徑「不含ETF之融資股票市值／大盤融資餘額」
  narrow   : ex_etf 再排除 -KY/-DR 與當日停資（註記 X）— 第三方逆向推估的 M平方實際口徑（未經官方證實）
"""
import json, time, re, datetime as dt, requests
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36"}
def g(u, sleep=0):
    r = requests.get(u, headers=UA, timeout=45); r.raise_for_status()
    if sleep: time.sleep(sleep)
    return r.json()
num = lambda s: float(str(s).replace(",", "").strip())
def is_stock(code): return len(code) == 4 and code.isdigit()

def twse(date, sleep=2.5):
    m = g(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date}&selectType=ALL&response=json", sleep)
    if m.get("stat") != "OK": return None
    p = g(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date}&type=ALLBUT0999&response=json", sleep)
    px = {}
    for t in p.get("tables", []):
        f = t.get("fields") or []
        if f and f[0] == "證券代號":
            for r in t["data"]:
                try: px[r[0].strip()] = num(r[8])
                except Exception: pass
    amt = num([r for r in m["tables"][0]["data"] if "融資金額" in r[0]][0][5]) * 1000
    rows = [(r[0].strip(), r[1].strip(), num(r[6]), r[15]) for r in m["tables"][1]["data"]]
    return amt, rows, px

def tpex(date, sleep=1.0):
    d = f"{date[:4]}/{date[4:6]}/{date[6:]}"
    m = g(f"https://www.tpex.org.tw/www/zh-tw/margin/balance?date={d}&response=json", sleep)
    t = m["tables"][0]
    y, mo, dd = t["date"].split("/")
    if f"{int(y)+1911}{mo}{dd}" != date: return None
    amt = [num(s[6]) for s in t["summary"] if "融資金" in s[1]][0] * 1000
    rows = [(r[0].strip(), r[1].strip(), num(r[6]), r[19]) for r in t["data"]]
    p = g(f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={d}&type=EW&response=json", sleep)
    px = {}
    for tb in p["tables"]:
        for r in tb.get("data", []):
            try: px[r[0].strip()] = num(r[2])
            except Exception: pass
    return amt, rows, px

def mv(rows, px, mode):
    s = 0.0; miss = 0
    for code, name, lots, note in rows:
        if lots <= 0: continue
        if mode in ("ex_etf", "narrow") and not is_stock(code): continue
        if mode == "narrow" and ("-KY" in name or "-DR" in name or "DR" in name[-2:] or "X" in (note or "")): continue
        if code not in px: miss += lots; continue
        s += lots * 1000 * px[code]
    return s, miss

def compute(date):
    out = {"date": date}
    a = twse(date)
    if a is None: return None
    b = tpex(date)
    for mode in ("all", "ex_etf", "narrow"):
        s1, _ = mv(a[1], a[2], mode)
        out[f"twse_{mode}"] = s1 / a[0] * 100
        if b and mode != "narrow":
            s2, _ = mv(b[1], b[2], mode)
            out[f"tpex_{mode}"] = s2 / b[0] * 100
            out[f"both_{mode}"] = (s1 + s2) / (a[0] + b[0]) * 100
    out["twse_margin_amt_100m"] = a[0] / 1e8
    if b: out["tpex_margin_amt_100m"] = b[0] / 1e8
    return out

if __name__ == "__main__":
    import sys
    for d in sys.argv[1:]:
        print(json.dumps(compute(d), ensure_ascii=False))
