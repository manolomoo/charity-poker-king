"""全站 3 日維持（使用者指定 2026-11-11，不是 COBB 的規則）。

🔴 從「最後一次符合觸發條件的資料日」起算，含當日共 3 個日曆天（台北日期比較）。
窗口內再次觸發則從新的資料日重算。超過之後回到該列當下的 🟡／⚪／❔。
週線／月線的資料日是已完成那根的日期，所以可能在下一根出來之前就先重置。
"""
import datetime as dt, json, os
from common import RED, WHT, STATUS_LABEL, DATA

HOLD_DAYS = 3
HOLD_LABEL = "使用者指定（2026-11-11）"

def parse_date(value):
    if value is None: return None
    if isinstance(value, dt.datetime): return value.date()
    if isinstance(value, dt.date): return value
    text = str(value)[:10]
    try: return dt.date.fromisoformat(text)
    except ValueError: return None

def in_window(trigger, asof, days=HOLD_DAYS):
    """含觸發日共 days 個日曆天。asof 早於觸發日（前視）不算。"""
    if trigger is None or asof is None: return False
    delta = (asof - trigger).days
    return 0 <= delta <= days - 1

def window_end(trigger, days=HOLD_DAYS):
    return trigger + dt.timedelta(days=days - 1)

def _participates(row):
    thr = row.get("threshold") or ""
    if "不參與" in thr or "不納入" in thr: return False
    target = row.get("target") or ""
    if "參考" in target: return False
    return True

def _history_red_dates(signal):
    """無法從來源重算時，用 history.jsonl 的整項 🔴 查詢日（不是各標的的市場日）。"""
    path = os.path.join(DATA, "history.jsonl")
    if not os.path.exists(path): return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line: continue
        try: obj = json.loads(line)
        except json.JSONDecodeError: continue
        summary = obj.get("summary") or {}
        if summary.get(str(signal)) == RED and obj.get("run_date"):
            out.append(obj["run_date"])
    return out

def hold_text(trigger, days=HOLD_DAYS):
    end = window_end(trigger, days)
    return f"最後觸發日：{trigger.isoformat()}（{days} 天內有效，至 {end.isoformat()}）"

def apply_three_day_hold(rows, asof):
    """就地改列的 status／value／note。回傳同一清單。"""
    asof = parse_date(asof)
    for row in rows:
        if not _participates(row): continue
        extra = row.get("extra")
        if not isinstance(extra, dict):
            extra = {}
            row["extra"] = extra
        known = "red_dates" in extra
        raw_dates = list(extra.get("red_dates") or [])
        if not known:
            raw_dates = _history_red_dates(row.get("signal"))
            extra["red_dates_source"] = "history.jsonl"
        dates = [d for d in (parse_date(x) for x in raw_dates) if d and d <= asof]
        last = max(dates) if dates else None
        obs = parse_date(extra.get("obs_date")) or parse_date(row.get("data_date"))
        raw = row.get("status")
        # 當下這筆資料本身就是 🔴，而且它的資料日還在窗口內 → 最後觸發日＝該資料日
        current = raw == RED and obs is not None and in_window(obs, asof)
        held = (not current) and last is not None and in_window(last, asof)
        if current or held:
            show = obs if current else last
            end = window_end(show)
            row["status"] = RED
            prefix = hold_text(show) + "｜目前數值："
            value = row.get("value") or "—"
            if not str(value).startswith("最後觸發日："):
                row["value"] = prefix + str(value)
            extra["hold"] = {
                "last_trigger": show.isoformat(),
                "valid_through": end.isoformat(),
                "days": HOLD_DAYS,
                "current": bool(current),
                "held": bool(held),
                "label": HOLD_LABEL,
            }
            tag = f"3 日維持為{HOLD_LABEL}，不是 COBB 的規則。"
            note = row.get("note") or ""
            if tag not in note:
                row["note"] = (note + " " + tag).strip()
        elif raw == RED:
            # 最新一根仍寫著觸發，但資料日已超過 3 天（週線／月線會這樣）
            row["status"] = WHT
            if last:
                extra["expired_trigger"] = last.isoformat()
                note = row.get("note") or ""
                msg = (f"最新資料仍符合觸發門檻，但最後觸發日 {last.isoformat()} 已超過 {HOLD_DAYS} 個日曆天"
                       f"（{HOLD_LABEL}），燈號重置。")
                if msg not in note:
                    row["note"] = (note + " " + msg).strip()
        if "red_dates" in extra:
            # 輸出只留最後一個觸發日，避免把整段歷史寫進 latest.json
            extra["red_dates"] = [last.isoformat()] if last else []
        row["status_label"] = STATUS_LABEL.get(row["status"], row.get("status_label"))
        row["extra"] = extra
    return rows
