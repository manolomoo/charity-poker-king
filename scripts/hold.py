"""維持規則（使用者指定 2026-11-11，不是 COBB 的規則）。

日線（第 1、2、5、6、7 項）：🔴 從最後一次符合條件的資料日起算，含當日共 7 個日曆天。
第 9 項是轉折燈，10 個交易日的窗口自己算，不再疊這 7 天。
週線（第 3、4、8 項）：燈號就是最新一根已完成週K，維持到下一根週K。
月線（第 10、11 項）：維持到下一期資料發布。
"""
import datetime as dt, json, os
from common import RED, WHT, STATUS_LABEL, DATA

HOLD_DAYS = 7
HOLD_LABEL = "使用者指定（2026-11-11）"
WEEKLY_SIGNALS = {"3", "4", "8"}
MONTHLY_SIGNALS = {"10", "11"}
TURN_SIGNALS = {"9"}

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

def period_kind(signal):
    key = str(signal)
    if key in WEEKLY_SIGNALS: return "weekly"
    if key in MONTHLY_SIGNALS: return "monthly"
    if key in TURN_SIGNALS: return "turn"
    return "daily"

def period_hold_text(kind, when):
    stamp = when.isoformat() if isinstance(when, dt.date) else str(when)[:10]
    if kind == "weekly":
        return f"依最新已完成週K（{stamp}）判定，維持到下一根週K"
    return f"依最新已發布月份（{stamp}）判定，維持到下一期資料"

def _phrase(kind, within, until=None):
    if not within:
        return "已超過維持"
    if kind == "weekly":
        return "仍在維持內，到下一根週K"
    if kind == "monthly":
        return "仍在維持內，到下次公布"
    if kind == "turn":
        return "仍在維持內（轉折後 10 個交易日）"
    if until:
        return f"仍在維持內，至 {until.isoformat()}"
    return "仍在維持內"

def _set_last_trigger(extra, when, kind, within, until=None):
    extra["last_trigger_info"] = {
        "date": when.isoformat() if isinstance(when, dt.date) else None,
        "within_hold": bool(within),
        "phrase": _phrase(kind, within, until),
        "mode": kind,
    }

def _mark_red(row, extra, text, hold):
    row["status"] = RED
    prefix = text + "｜目前數值："
    value = row.get("value") or "—"
    if not str(value).startswith(text):
        row["value"] = prefix + str(value)
    extra["hold"] = hold
    tag = f"維持規則為{HOLD_LABEL}，不是 COBB 的規則。"
    note = row.get("note") or ""
    if tag not in note:
        row["note"] = (note + " " + tag).strip()

def apply_three_day_hold(rows, asof):
    """就地改列的 status／value／note。名稱沿用舊函式。回傳同一清單。"""
    asof = parse_date(asof)
    for row in rows:
        if not _participates(row): continue
        extra = row.get("extra")
        if not isinstance(extra, dict):
            extra = {}
            row["extra"] = extra
        known = "red_dates" in extra
        raw_dates = list(extra.get("red_dates") or [])
        if not known and str(row.get("signal")) not in TURN_SIGNALS:
            raw_dates = _history_red_dates(row.get("signal"))
            extra["red_dates_source"] = "history.jsonl"
        dates = [d for d in (parse_date(x) for x in raw_dates) if d and d <= asof]
        last = max(dates) if dates else None
        obs = parse_date(extra.get("obs_date")) or parse_date(row.get("data_date"))
        raw = row.get("status")
        kind = period_kind(row.get("signal"))
        if "trigger_dates" not in extra:
            extra["trigger_dates"] = [d.isoformat() for d in sorted(dates)]
        if kind == "turn":
            # 第 9 項的 10 個交易日由 signals_v2 算完，這裡不再拉長。
            info = extra.get("last_trigger_info")
            if not info:
                _set_last_trigger(extra, last, "turn", False)
            if "red_dates" in extra:
                extra["red_dates"] = [last.isoformat()] if last else []
            row["status_label"] = STATUS_LABEL.get(row["status"], row.get("status_label"))
            row["extra"] = extra
            continue
        if kind in ("weekly", "monthly"):
            show = obs or last
            within = raw == RED
            _set_last_trigger(extra, last or (show if within else None), kind, within)
            if within:
                text = period_hold_text(kind, show) if show else period_hold_text(kind, asof)
                _mark_red(row, extra, text, {
                    "mode": kind,
                    "last_trigger": show.isoformat() if show else None,
                    "text": text,
                    "current": True,
                    "held": True,
                    "label": HOLD_LABEL,
                })
            if "red_dates" in extra:
                extra["red_dates"] = [last.isoformat()] if last else []
            row["status_label"] = STATUS_LABEL.get(row["status"], row.get("status_label"))
            row["extra"] = extra
            continue
        current = raw == RED and obs is not None and in_window(obs, asof)
        held = (not current) and last is not None and in_window(last, asof)
        if current or held:
            show = obs if current else last
            end = window_end(show)
            text = hold_text(show)
            _set_last_trigger(extra, show, "daily", True, end)
            _mark_red(row, extra, text, {
                "mode": "daily",
                "last_trigger": show.isoformat(),
                "valid_through": end.isoformat(),
                "days": HOLD_DAYS,
                "text": text,
                "current": bool(current),
                "held": bool(held),
                "label": HOLD_LABEL,
            })
        else:
            _set_last_trigger(extra, last, "daily", False)
            if raw == RED:
                row["status"] = WHT
                if last:
                    extra["expired_trigger"] = last.isoformat()
                    note = row.get("note") or ""
                    msg = (f"最新資料仍符合觸發門檻，但最後觸發日 {last.isoformat()} 已超過 {HOLD_DAYS} 個日曆天"
                           f"（{HOLD_LABEL}），燈號重置。")
                    if msg not in note:
                        row["note"] = (note + " " + msg).strip()
        if "red_dates" in extra:
            extra["red_dates"] = [last.isoformat()] if last else []
        row["status_label"] = STATUS_LABEL.get(row["status"], row.get("status_label"))
        row["extra"] = extra
    return rows
