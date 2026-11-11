"""3 日維持的日期窗口。不碰網路。"""
import datetime as dt, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from hold import HOLD_DAYS, in_window, window_end, apply_three_day_hold, hold_text, period_hold_text
from common import RED, YEL, WHT

ASOF = dt.date(2026, 11, 11)

def test_inclusive_seven_calendar_days():
    assert in_window(dt.date(2026, 11, 11), ASOF)
    assert in_window(dt.date(2026, 11, 5), ASOF)   # 含當天共 7 天
    assert not in_window(dt.date(2026, 11, 4), ASOF)  # 第 8 個日曆天，重置
    assert not in_window(dt.date(2026, 11, 12), ASOF)  # 不前視
    assert window_end(dt.date(2026, 11, 5)) == dt.date(2026, 11, 11)
    assert HOLD_DAYS == 7

def test_restart_uses_last_date():
    rows = [{
        "signal": "2", "target": "CNN", "value": "最新 32", "threshold": "< 25",
        "status": YEL, "status_label": "接近門檻", "data_date": "2026-11-11",
        "note": "", "extra": {"obs_date": "2026-11-11", "red_dates": ["2026-11-05", "2026-11-10"]},
    }]
    apply_three_day_hold(rows, ASOF)
    assert rows[0]["status"] == RED
    assert rows[0]["extra"]["hold"]["last_trigger"] == "2026-11-10"
    assert rows[0]["extra"]["hold"]["valid_through"] == "2026-11-16"
    assert rows[0]["extra"]["hold"]["held"] is True
    assert rows[0]["extra"]["last_trigger_info"]["within_hold"] is True
    assert rows[0]["value"].startswith("最後觸發日：2026-11-10（7 天內有效，至 2026-11-16）｜目前數值：")

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

def test_weekly_bar_holds_until_next_week():
    rows = [{
        "signal": "3", "target": "NDX", "value": "J=-3", "threshold": "J ≤ 0",
        "status": RED, "status_label": "已觸發", "data_date": "2026-11-06",
        "note": "", "extra": {"obs_date": "2026-11-06", "red_dates": ["2026-11-06"]},
    }]
    apply_three_day_hold(rows, ASOF)
    assert rows[0]["status"] == RED
    text = "依最新已完成週K（2026-11-06）判定，維持到下一根週K"
    assert rows[0]["extra"]["hold"]["text"] == text
    assert rows[0]["value"].startswith(text + "｜目前數值：")
    assert "已超過 3 個日曆天" not in rows[0]["note"]

def test_weekly_next_bar_replaces_old_red():
    rows = [{
        "signal": "8", "target": "SOXL", "value": "z=2.8", "threshold": "z ≥ 4",
        "status": YEL, "status_label": "接近門檻", "data_date": "2026-11-06",
        "note": "", "extra": {"obs_date": "2026-11-06", "red_dates": ["2026-10-30"]},
    }]
    apply_three_day_hold(rows, ASOF)
    assert rows[0]["status"] == YEL
    assert "hold" not in rows[0]["extra"]

def test_monthly_holds_past_three_days():
    rows = [{
        "signal": "10", "target": "領先指標", "value": "98", "threshold": "< 100",
        "status": RED, "status_label": "已觸發", "data_date": "2026-10-29",
        "note": "", "extra": {"obs_date": "2026-10-29", "red_dates": ["2026-10-29"]},
    }]
    apply_three_day_hold(rows, ASOF)
    assert rows[0]["status"] == RED
    assert rows[0]["extra"]["hold"]["mode"] == "monthly"
    assert rows[0]["extra"]["hold"]["text"] == period_hold_text("monthly", dt.date(2026, 10, 29))

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
    assert hold_text(dt.date(2026, 11, 10)) == "最後觸發日：2026-11-10（7 天內有效，至 2026-11-16）"

def test_expired_daily_still_records_last_trigger():
    rows = [{
        "signal": "2", "target": "CNN", "value": "最新 33", "threshold": "< 25",
        "status": YEL, "status_label": "接近門檻", "data_date": "2026-11-11",
        "note": "", "extra": {"obs_date": "2026-11-11", "red_dates": ["2026-11-01"]},
    }]
    apply_three_day_hold(rows, ASOF)
    assert rows[0]["status"] == YEL
    info = rows[0]["extra"]["last_trigger_info"]
    assert info["date"] == "2026-11-01"
    assert info["within_hold"] is False
    assert info["phrase"] == "已超過維持"

def test_signal9_hold_does_not_stack():
    rows = [{
        "signal": "9", "target": "NDX", "value": "型態", "threshold": "轉折",
        "status": WHT, "status_label": "未觸發", "data_date": "2026-11-11",
        "note": "", "extra": {"obs_date": "2026-11-11", "red_dates": ["2026-11-10"],
                              "last_trigger_info": {"date": "2026-10-01", "within_hold": False, "phrase": "已超過維持", "mode": "turn"}},
    }]
    apply_three_day_hold(rows, ASOF)
    assert rows[0]["status"] == WHT
    assert rows[0]["extra"]["last_trigger_info"]["date"] == "2026-10-01"
    assert "hold" not in rows[0]["extra"]

if __name__ == "__main__":
    test_inclusive_seven_calendar_days()
    test_restart_uses_last_date()
    test_current_trigger_uses_obs_date()
    test_weekly_bar_holds_until_next_week()
    test_weekly_next_bar_replaces_old_red()
    test_monthly_holds_past_three_days()
    test_reference_row_untouched()
    test_hold_text()
    test_expired_daily_still_records_last_trigger()
    test_signal9_hold_does_not_stack()
    print("hold tests ok")
