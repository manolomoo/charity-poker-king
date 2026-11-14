"""牛熊三階段參考指標與期望值公式的回歸檢查。不碰燈號規則。"""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import market_stage as MS  # noqa: E402
import trend_analyze as T  # noqa: E402

CACHE = ROOT / "data" / "cache" / "trend"


def test_ev_formula_matches_cobb_examples():
    tab = MS.ev_table()
    def ev(reward, win):
        row = next(r for r in tab["rows"] if r["reward"] == reward)
        cell = next(c for c in row["cells"] if abs(c["win"] - win) < 1e-9)
        return cell["ev"]
    assert ev(100, 0.50) == 0
    assert ev(200, 0.35) == 5
    assert ev(400, 0.20) == 0
    # 100:100 at 20% should be negative
    assert ev(100, 0.20) < 0


def _stage(name):
    daily = T.load_csv(CACHE / f"{name}_daily_2y.csv")
    # 用現成的週線結構（與網站同一套）
    ix = T.analyze_one(name, CACHE)
    med = ix["dow_weekly_medium_swings"]["structure"]
    coarse = ix["dow_weekly_coarse_swings"]["structure"]
    vol = None if name == "SOX" else daily["volume"]
    return MS.detect_market_stage(daily, name, med, coarse, volume=vol)


def test_current_sox_is_bear_panic():
    s = _stage("SOX")
    ev = s["evidence"]
    assert ev["drawdown_pct"] <= -20
    assert ev["weekly_low_broken_3pct"] is True
    assert ev["below_sma200_streak"] >= 10 or ev["vs_sma200_pct"] <= -3
    assert s["regime"] == "空頭"
    assert s["stage_code"] == "bear_2"
    assert ev["panic_in_lookback"] is True
    assert ev["panic"]["drop"] <= -18


def test_current_ndx_undetermined():
    s = _stage("NDX")
    ev = s["evidence"]
    # 回撤約 -10.5%，未達 20%；相對 200 日線只有 -0.34%，連續 1 日
    assert ev["drawdown_pct"] > -20
    assert ev["below_sma200_streak"] < 10
    assert ev["vs_sma200_pct"] > -3
    assert s["regime"] == "無法判定"
    assert s["headline"] == "無法判定"


def test_current_spx_bull_stage_undetermined():
    s = _stage("SPX")
    ev = s["evidence"]
    assert ev["drawdown_pct"] > -10
    assert ev["above_sma200"] is True
    assert ev["sma200_slope_up"] is True
    assert ev["weekly_low_broken_3pct"] is False
    assert s["regime"] == "多頭"
    # 收盤低於 50 日，不是第二階段
    assert ev["above_sma50"] is False
    assert s["stage_code"] == "stage_undetermined"


def test_panic_synthetic():
    # 平盤後 10 日跌 20%，應觸發 NDX 12% 恐慌
    dates = pd.bdate_range("2025-01-01", periods=250)
    close = [100.0] * 240 + [98, 96, 94, 90, 88, 85, 83, 82, 81, 80]
    assert len(close) == 250
    df = pd.DataFrame({
        "date": dates,
        "open": close, "high": [c * 1.01 for c in close],
        "low": [c * 0.99 for c in close], "close": close,
        "volume": [1_000_000] * 250,
    })
    # 做一個明顯低於 200 日且回撤夠深的情境：再加長跌勢
    # 這裡只測 find_panic_events
    evs = MS.find_panic_events(df["close"], 0.12)
    assert evs, "應偵測到恐慌"
    assert evs[-1]["drop"] <= -0.12


def test_vbp_ndx_available_sox_without_soxx_not():
    ndx = T.load_csv(CACHE / "NDX_daily_2y.csv")
    v = MS.volume_by_price(ndx)
    assert v["available"] is True
    assert abs(sum(b["share"] for b in v["bins"]) - 1) < 1e-3
    sox = T.load_csv(CACHE / "SOX_daily_2y.csv")
    v2 = MS.volume_by_price(sox)
    assert v2["available"] is False


if __name__ == "__main__":
    test_ev_formula_matches_cobb_examples()
    test_panic_synthetic()
    test_vbp_ndx_available_sox_without_soxx_not()
    test_current_sox_is_bear_panic()
    test_current_ndx_undetermined()
    test_current_spx_bull_stage_undetermined()
    for name in ("SOX", "NDX", "SPX"):
        s = _stage(name)
        print(name, s["headline"], s["stage_code"], s["evidence"]["drawdown_pct"], s["detail"])
    print("ok")
