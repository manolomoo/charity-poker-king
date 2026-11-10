"""3 日維持的日期窗口。不碰網路。"""
import datetime as dt, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from hold import HOLD_DAYS, in_window, window_end, apply_three_day_hold, hold_text
from common import RED, YEL, WHT

ASOF = dt.date(2026, 11, 11)

def test_inclusive_three_calendar_days():
    assert in_window(dt.date(2026, 11, 11), ASOF)
    assert in_window(dt.date(2026, 11, 10), ASOF)
    assert in_window(dt.date(2026, 11, 9), ASOF)
    assert not in_window(dt.date(2026, 11, 8), ASOF)  # 第 4 個日曆天，重置
    assert not in_window(dt.date(2026, 11, 6), ASOF)  # 週五 bar，週三已過期
    assert not in_window(dt.date(2026, 11, 12), ASOF)  # 不前視
    assert window_end(dt.date(2026, 11, 9)) == dt.date(2026, 11, 11)
    assert HOLD_DAYS == 3

def test_restart_uses_last_date():
    rows = [{
        "signal": "2", "target": "CNN", "value": "最新 32", "threshold": "< 25",
        "status": YEL, "status_label": "接近門檻", "data_date": "2026-11-11",
        "note": "", "extra": {"obs_date": "2026-11-11", "red_dates": ["2026-11-05", "2026-11-10"]},
    }]
    apply_three_day_hold(rows, ASOF)
    assert rows[0]["status"] == RED
    assert rows[0]["extra"]["hold"]["last_trigger"] == "2026-11-10"
    assert rows[0]["extra"]["hold"]["valid_through"] == "2026-11-12"
    assert rows[0]["extra"]["hold"]["held"] is True
    assert rows[0]["value"].startswith("最後觸發日：2026-11-10（3 天內有效，至 2026-11-12）｜目前數值：")

def test_current_trigger_uses_obs_date():
    rows = [{
        "signal": "5", "target": "VXN", "value": "盤中 36", "threshold": "> 35",
        "status": RED, "status_label": "已觸發", "data_date": "2026-11-10",
        "note": "", "extra": {"obs_date": "2026-11-10", "red_dates": ["2026-11-03", "2026-11-10"]},
    }]
    apply_three_day_hold(rows, ASOF)
    assert rows[0]["extra"]["hold"]["current"] is True
    assert rows[0]["extra"]["hold"]["last_trigger"] == "2026-11-10"
    assert "目前數值：盤中 36" in rows[0]["value"]

def test_weekly_bar_expires_before_next_bar():
    rows = [{
        "signal": "3", "target": "NDX", "value": "J=-3", "threshold": "J ≤ 0",
        "status": RED, "status_label": "已觸發", "data_date": "2026-11-06",
        "note": "", "extra": {"obs_date": "2026-11-06", "red_dates": ["2026-11-06"]},
    }]
    apply_three_day_hold(rows, ASOF)
    assert rows[0]["status"] == WHT
    assert "已超過 3 個日曆天" in rows[0]["note"]
    assert "hold" not in rows[0]["extra"]

def test_reference_row_untouched():
    rows = [{
        "signal": "7", "target": "上市融資餘額（參考）", "value": "x", "threshold": "（整項以合計判定）",
        "status": RED, "status_label": "已觸發", "data_date": "2026-11-06",
        "note": "", "extra": {"red_dates": ["2026-11-10"]},
    }]
    apply_three_day_hold(rows, ASOF)
    assert rows[0]["status"] == RED
    assert rows[0]["value"] == "x"

def test_hold_text():
    assert hold_text(dt.date(2026, 11, 10)) == "最後觸發日：2026-11-10（3 天內有效，至 2026-11-12）"

if __name__ == "__main__":
    test_inclusive_three_calendar_days()
    test_restart_uses_last_date()
    test_current_trigger_uses_obs_date()
    test_weekly_bar_expires_before_next_bar()
    test_reference_row_untouched()
    test_hold_text()
    print("hold tests ok")
