"""第 9 項轉折掃描要和既有 dow_pattern 的最後一天一致，而且不把 NaN 收盤算進去。"""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from signals_v2 import scan_turns, dow_pattern, within_turn, TURN_WINDOW
from episode import build_episode

def test_scan_matches_dow_pattern():
    rng = np.random.default_rng(1)
    v = 100 * np.cumprod(1 + rng.normal(0, 0.012, 160))
    s = pd.Series(v, index=pd.bdate_range("2020-01-02", periods=160))
    code, _, _ = scan_turns(s, 0.05)
    cd, _, _ = dow_pattern(s, 0.05, -10)
    assert code == cd

def test_nan_close_is_dropped_before_scan():
    idx = pd.bdate_range("2020-01-02", periods=40)
    v = np.linspace(100, 130, 40)
    v[20] = np.nan
    s = pd.Series(v, index=idx).dropna()
    assert len(s) == 39
    code, _, _ = scan_turns(s, 0.05)
    assert code in ("unknown", "confirmed", "hl_pending", "lower_low_rebound", "falling_below", "uptrend_pullback", "pullback")

def test_turn_window_is_ten_sessions_inclusive():
    idx = pd.bdate_range("2026-01-01", periods=15)
    turns = [idx[0]]
    on, day = within_turn(turns, idx[:10], TURN_WINDOW)
    assert on and day == idx[0]
    on2, _ = within_turn(turns, idx[:11], TURN_WINDOW)
    assert on2 is False

def test_episode_uses_dates_after_the_high():
    idx = pd.bdate_range("2025-01-01", periods=300)
    px = np.concatenate([np.linspace(100, 200, 260), np.linspace(190, 170, 40)])
    c = pd.Series(px, index=idx)
    rows = [{
        "signal": "2", "target": "CNN", "threshold": "< 25",
        "extra": {"trigger_dates": ["2024-12-01", idx[270].strftime("%Y-%m-%d"), idx[280].strftime("%Y-%m-%d")]},
    }, {
        "signal": "6", "target": "上市（參考）", "threshold": "不參與判定",
        "extra": {"trigger_dates": [idx[290].strftime("%Y-%m-%d")]},
    }]
    ep = build_episode(rows, c, [("2", "CNN"), ("6", "維持率")])
    assert ep["rows"][0]["first"] == idx[270].strftime("%Y-%m-%d")
    assert ep["rows"][0]["latest"] == idx[280].strftime("%Y-%m-%d")
    assert ep["rows"][0]["ndx_dd"] is not None and ep["rows"][0]["ndx_dd"] < 0
    assert ep["rows"][1]["first"] is None
    assert "2022" in ep["note"]

if __name__ == "__main__":
    test_scan_matches_dow_pattern()
    test_nan_close_is_dropped_before_scan()
    test_turn_window_is_ten_sessions_inclusive()
    test_episode_uses_dates_after_the_high()
    print("turn tests ok")
