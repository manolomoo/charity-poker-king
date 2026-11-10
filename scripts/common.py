"""共用工具：時間、HTTP、狀態彙總。"""
import json, os, time, datetime as dt
from zoneinfo import ZoneInfo
import requests

SITE_NAME = "COBB我大哥"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "raw")
DATA = os.path.join(ROOT, "data")
TPE = ZoneInfo("Asia/Taipei")
NY = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
S = requests.Session(); S.headers.update({"User-Agent": UA})

RED, YEL, WHT, UNK, REF = "🔴", "🟡", "⚪", "❔", "🔍"
STATUS_LABEL = {RED: "已觸發", YEL: "接近門檻", WHT: "未觸發", UNK: "資料不足", REF: "參考（需看型態判斷）"}

def now_tpe():
    return dt.datetime.now(TPE)

def ts():
    return now_tpe().strftime("%Y-%m-%d %H:%M (台北時間 UTC+8)")

def get(url, sleep=0.0, **kw):
    kw.setdefault("timeout", 30)
    r = S.get(url, **kw)
    if sleep: time.sleep(sleep)
    r.raise_for_status()
    return r

def save_raw(name, content, binary=False):
    os.makedirs(RAW, exist_ok=True)
    p = os.path.join(RAW, name)
    with open(p, "wb" if binary else "w") as f:
        f.write(content)
    return p

def agg_any(statuses):
    """任一符合：有🔴→🔴；否則有🟡→🟡；否則有❔→❔；否則⚪"""
    s = list(statuses)
    if not s: return UNK
    for k in (RED, YEL, UNK, REF):
        if k in s: return k
    return WHT

def agg_all(statuses):
    """全部符合：全部🔴→🔴；有❔且其餘🔴→❔；否則若全部為🔴/🟡→🟡；否則⚪"""
    s = list(statuses)
    if not s: return UNK
    if all(x == RED for x in s): return RED
    known = [x for x in s if x != UNK]
    if UNK in s and all(x == RED for x in known): return UNK
    if all(x in (RED, YEL) for x in known) and known: return YEL
    return WHT

def r(x, n=2):
    return None if x is None else round(float(x), n)
