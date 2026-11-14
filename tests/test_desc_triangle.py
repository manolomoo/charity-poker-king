"""下降直角三角形 v2（COBB 主高點讀法）的回歸檢查。不碰燈號規則。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import trend_analyze as T  # noqa: E402

CACHE = ROOT / "data" / "cache" / "trend"


def test_sox_confirmed_neckline_10445():
    daily = T.load_csv(CACHE / "SOX_daily_2y.csv")
    highs, lows = T.swings(daily, left=10, right=10)
    tri = T.detect_descending_right_triangle(daily, highs, lows)
    assert tri is not None, "SOX 應判定為下降直角三角形"
    assert tri["active"] is True
    assert tri["status"] == "已確認跌破"
    assert abs(tri["support"] - 10445.44) < 1.0
    assert tri["h1"]["date"] == "2026-06-22"
    assert tri["h2"]["date"] == "2026-10-06"
    assert tri["confirmed"] is True
    assert tri["first_3pct_date"] == "2026-11-05"
    # 11-13 收盤相對頸線約 -2.86%，不可改判未確認
    assert tri["close_vs_support_pct"] < 0
    assert 6200 < tri["target"] < 6300
    # 8 月中間高點在降勢線下，不得作廢
    inter_dates = [x["date"] for x in tri["intermediate_highs"]]
    assert "2026-08-17" in inter_dates


def test_ndx_spx_not_active():
    for name in ("NDX", "SPX"):
        daily = T.load_csv(CACHE / f"{name}_daily_2y.csv")
        highs, lows = T.swings(daily, left=10, right=10)
        tri = T.detect_descending_right_triangle(daily, highs, lows)
        assert tri is None or tri.get("active") is not True, f"{name} 不應是有效的下降直角三角形，得到 {tri}"


def test_sma20_streak_sox():
    daily = T.load_csv(CACHE / "SOX_daily_2y.csv")
    n = T.sma20_below_streak(daily["close"])
    assert n == 25


if __name__ == "__main__":
    test_sox_confirmed_neckline_10445()
    test_ndx_spx_not_active()
    test_sma20_streak_sox()
    print("ok")
