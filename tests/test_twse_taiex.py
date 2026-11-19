"""證交所台指日線：日期解析、開高低收與成交金額合併、週K 重組、快取月份選擇。不連網、不碰燈號規則。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import twse_taiex as TWSE  # noqa: E402
import update_trend as U  # noqa: E402
import trend_analyze as T  # noqa: E402
import market_stage as MS  # noqa: E402


def test_roc_to_iso():
    assert TWSE.roc_to_iso("115/11/19") == "2026-11-19"
    assert TWSE.roc_to_iso("115年11月19日") == "2026-11-19"
    assert TWSE.roc_to_iso("109/06/01") == "2020-06-01"
    with pytest.raises(TWSE.TwseError):
        TWSE.roc_to_iso("bad")


def test_parse_num_and_volume():
    assert TWSE.parse_num("38,426.89") == 38426.89
    assert TWSE.parse_num("930,987,418,697") == 930987418697
    assert int(round(TWSE.parse_num("930,987,418,697"))) == 930987418697


def _ohlc_payload():
    return {
        "stat": "OK",
        "data": [
            ["115/11/18", "40,000.00", "40,100.00", "39,800.00", "39,900.00"],
            ["115/11/19", "40,145.73", "40,315.53", "38,259.70", "38,426.89"],
        ],
    }


def _vol_payload():
    return {
        "stat": "OK",
        "data": [
            ["115/11/18", "10,000,000,000", "800,000,000,000", "4,000,000", "39,900.00", "-100.00"],
            ["115/11/19", "15,282,042,940", "930,987,418,697", "6,310,047", "38,426.89", "-1,768.02"],
        ],
    }


def test_combine_month_uses_turnover_and_inner_join():
    ohlc = TWSE.parse_ohlc_rows(_ohlc_payload())
    vol = TWSE.parse_volume_rows(_vol_payload())
    out = TWSE.combine_month(ohlc, vol)
    assert list(out["date"]) == ["2026-11-18", "2026-11-19"]
    assert out.loc[out["date"] == "2026-11-19", "close"].iloc[0] == 38426.89
    assert out.loc[out["date"] == "2026-11-19", "volume"].iloc[0] == 930987418697
    # 只在一邊的日期不會進結果
    ohlc2 = ohlc.copy()
    ohlc2.loc[len(ohlc2)] = {"date": "2026-11-20", "open": 1, "high": 1, "low": 1, "close": 1}
    out2 = TWSE.combine_month(ohlc2, vol)
    assert "2026-11-20" not in set(out2["date"])
    assert out2.attrs.get("date_mismatch") == ["2026-11-20"]


def test_weeks_from_daily_monday_bar():
    # 週三到週五三根日K → 一週，日期為週一
    rows = []
    for i, d in enumerate(["2026-11-16", "2026-11-17", "2026-11-18"]):  # Mon, Tue, Wed
        rows.append({"date": d, "open": 10 + i, "high": 20 + i, "low": 5 + i, "close": 12 + i, "adjclose": 12 + i, "volume": 100 * (i + 1)})
    daily = pd.DataFrame(rows)
    w = TWSE.weeks_from_daily(TWSE.attach_bars(daily))
    assert len(w) == 1
    assert w.iloc[0]["date"] == "2026-11-16"
    assert w.iloc[0]["open"] == 10
    assert w.iloc[0]["high"] == 22
    assert w.iloc[0]["low"] == 5
    assert w.iloc[0]["close"] == 14
    assert w.iloc[0]["volume"] == 100 + 200 + 300


def test_weeks_match_rebuild_week():
    dates = pd.bdate_range("2026-10-01", "2026-11-19")
    daily = pd.DataFrame({
        "date": [d.strftime("%Y-%m-%d") for d in dates],
        "open": range(1, len(dates) + 1),
        "high": range(2, len(dates) + 2),
        "low": [0.5] * len(dates),
        "close": range(3, len(dates) + 3),
        "adjclose": range(3, len(dates) + 3),
        "volume": [10] * len(dates),
    })
    daily = TWSE.attach_bars(daily)
    built = TWSE.weeks_from_daily(daily)
    # 與 update_trend.rebuild_week 對最後一週對得上
    frames = {"daily_2y": daily, "weekly_2y": built.iloc[:-1].copy()}
    U.rebuild_week(frames, daily["date"].iloc[-1], "Asia/Taipei")
    last = frames["weekly_2y"].iloc[-1]
    expect = built.iloc[-1]
    assert last["date"] == expect["date"]
    assert last["open"] == expect["open"]
    assert last["high"] == expect["high"]
    assert last["low"] == expect["low"]
    assert last["close"] == expect["close"]
    assert int(last["volume"]) == int(expect["volume"])


def test_months_to_fetch_full_then_incremental():
    today = date(2026, 11, 19)
    months = TWSE.months_to_fetch(None, today, years=5)
    assert months[0] == date(2021, 10, 1)  # 五年再往前一個月
    assert months[-1] == date(2026, 11, 1)
    assert len(months) == 62

    # 已有完整快取：只重抓 10 月與 11 月
    hist_dates = pd.bdate_range("2021-10-01", "2026-11-18")
    history = pd.DataFrame({"date": [d.strftime("%Y-%m-%d") for d in hist_dates], "close": 1})
    inc = TWSE.months_to_fetch(history, today, years=5)
    assert inc == [date(2026, 10, 1), date(2026, 11, 1)]

    # 缺 2024-05：要補抓，外加前月與當月
    dropped = history[~history["date"].str.startswith("2024-05")]
    miss = TWSE.months_to_fetch(dropped, today, years=5)
    assert date(2024, 5, 1) in miss
    assert date(2026, 10, 1) in miss and date(2026, 11, 1) in miss


def test_crosscheck_closes():
    twse = pd.DataFrame({"date": ["2026-11-17", "2026-11-18", "2026-11-19"], "close": [40000.0, 40100.20, 38426.89]})
    yahoo = pd.DataFrame({"date": ["2026-11-17", "2026-11-18"], "close": [40000.0, 40100.00]})
    r = TWSE.crosscheck_closes(twse, yahoo, tol=0.25)
    assert r["overlap"] == 2
    assert r["mismatch_count"] == 0
    r2 = TWSE.crosscheck_closes(twse, yahoo, tol=0.05)
    assert r2["mismatch_count"] == 1
    assert r2["mismatches"][0]["date"] == "2026-11-18"
    assert r2["max_abs_diff"] == pytest.approx(0.20)


def test_build_frames_row_counts():
    dates = pd.bdate_range("2021-11-01", "2026-11-19")
    daily = pd.DataFrame({
        "date": [d.strftime("%Y-%m-%d") for d in dates],
        "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1_000_000_000,
    })
    frames = TWSE.build_frames(daily)
    assert len(frames["daily_2y"]) >= U.MIN_ROWS["daily_2y"]
    assert len(frames["daily_1y"]) >= U.MIN_ROWS["daily_1y"]
    assert len(frames["weekly_2y"]) >= U.MIN_ROWS["weekly_2y"]
    assert len(frames["weekly_5y"]) >= U.MIN_ROWS["weekly_5y"]
    U.validate("TWII", frames)
    assert frames["daily_1y"]["date"].iloc[-1] == "2026-11-19"
    assert frames["weekly_5y"]["close"].iloc[-1] == 105


def test_tw_volume_label_twse_and_yahoo():
    n = 300
    daily = pd.DataFrame({
        "date": pd.bdate_range("2025-01-01", periods=n),
        "open": 1, "high": 1, "low": 1, "close": 1,
        "volume": [10**11] * n,
    })
    vol, label, proxy = T.tw_volume(daily, None, price_source="TWSE")
    assert proxy is None and "證交所" in label and "新台幣" in label and "0050" not in label
    assert vol.equals(daily["volume"])
    vol2, label2, proxy2 = T.tw_volume(daily, None, price_source="Yahoo")
    assert proxy2 is None and "^TWII" in label2


def test_current_month_empty_without_history_is_skipped(tmp_path, monkeypatch):
    """月初當月尚無資料、即使還沒有快取，也應略過當月並留下已抓到的月份。"""
    calls = []

    def fake_month(session, month, pause=0):
        calls.append(month)
        if month == date(2026, 12, 1):
            raise TWSE.TwseEmpty("stat='很抱歉，沒有符合條件的資料！'")
        return pd.DataFrame({
            "date": [month.strftime("%Y-%m-02")],
            "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [100],
        })

    monkeypatch.setattr(TWSE, "fetch_month", fake_month)
    monkeypatch.setattr(TWSE, "LOOKBACK_YEARS", 0)
    # years=0 → lookback_start = prev month of today.replace(year=today.year)
    daily, report = TWSE.fetch_history(tmp_path, today=date(2026, 12, 1), pause=0, years=0)
    assert date(2026, 12, 1) in calls
    assert "2026-12" in report.get("current_month_skipped", "") or report.get("current_month_skipped")
    assert "2026-12-02" not in set(daily["date"])
    assert len(daily) >= 1
    assert daily["date"].iloc[-1] < "2026-12-01"
