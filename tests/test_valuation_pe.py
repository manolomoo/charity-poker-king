"""Forward P/E 估值位置：σ 帶、已完成月份、以及 SOX 可重現 COBB MEMO 統計。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import valuation_pe as VP  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "sox_hom_monthly_to_2026-11-05.json"
CACHE = ROOT / "data" / "cache" / "trend"


def test_sigma_band_edges():
    assert VP.sigma_band(10, 20, 4)[0] == "低於 μ−1.5σ"  # z=-2.5
    assert VP.sigma_band(14.5, 20, 4)[0] == "μ−1.5σ～μ−1σ"  # z=-1.375
    assert VP.sigma_band(17, 20, 4)[0] == "μ−1σ～μ−0.5σ"  # z=-0.75
    assert VP.sigma_band(19, 20, 4)[0] == "μ−0.5σ～μ"      # z=-0.25
    assert VP.sigma_band(21, 20, 4)[0] == "μ～μ+0.5σ"
    assert VP.sigma_band(40, 20, 4)[0] == "高於 μ+1.5σ"
    assert VP.sigma_band(20, 20, 0)[0] == "無法判定"


def test_sox_monthly_through_memo_date_matches_cobb():
    raw = json.loads(FIX.read_text(encoding="utf-8"))
    s = pd.Series({pd.Timestamp(p["date"]): p["value"] for p in raw["monthly"]}).sort_index()
    assert raw["end"] == "2026-11-05"
    assert abs(float(s.iloc[-1]) - 12.72) < 1e-9
    cobb = VP.COBB_MEMO["indices"]["SOX"]["windows"]
    for start, expect in cobb.items():
        st = VP.window_stats(s[s.index >= pd.Timestamp(f"{start}-01-01")])
        for k, ev in expect.items():
            assert st[k] == ev, f"{start} {k}: got {st[k]} want {ev}"


def test_completed_month_excludes_current_month():
    end = VP.last_completed_month_end(date(2026, 11, 15))
    assert end == pd.Timestamp("2026-10-31")
    # 11 月未完成，不應進入帶
    idx = pd.to_datetime(["2026-09-30", "2026-10-31", "2026-11-13"])
    s = pd.Series([20.0, 19.0, 12.72], index=idx)
    monthly = VP.monthly_last(s)
    completed = monthly[monthly.index <= end]
    assert completed.index[-1] == pd.Timestamp("2026-10-31")
    assert 12.72 not in set(completed.values)


def test_build_uses_2016_home_window_and_skips_ndx_long_windows():
    if not (CACHE / "hom_sox_forward-pe.json").exists() or not (CACHE / "hom_ndx_forward-pe.json").exists():
        # CI 若還沒抓 HOM，只檢查模組常數；完整組裝在本機／排程測。
        assert VP.HOME_WINDOW == "2016"
        return
    val = VP.build_valuation(CACHE, ROOT / "data" / "latest.json", today=date(2026, 11, 15))
    assert val["available"] is True
    assert val["home_window"] == "2016"
    assert val["completed_month_end"] == "2026-10-31"
    assert val["indices"]["SOX"]["memo_repro"]["exact_match"] is True
    assert val["indices"]["NDX"]["memo_repro"]["exact_match"] is False
    # NDX 不重算 2006／2001
    assert "2006" not in val["indices"]["NDX"]["windows"]
    assert "2001" not in val["indices"]["NDX"]["windows"]
    # 首頁標籤用 HOM 現值、2016 視窗，且帶來源日期
    tag = val["home_tags"]["SOX"]
    assert tag["window"] == "2016"
    assert tag["date"] == val["indices"]["SOX"]["latest"]["date"]
    assert "2016起" in tag["text"]
    assert "HOM" in tag["text"]
    # 現值不可拿去比 MEMO 的 NDX 帶：home tag 的 band 必須來自 HOM 重算
    ndx_band = val["indices"]["NDX"]["windows"]["2016"]["current_vs"]["band"]
    assert val["home_tags"]["NDX"]["band"] == ndx_band
    # 燈號規則聲明存在
    assert "第 4 項" in val["signal4_unchanged"]
    # SOX 12.72 是 11/05 單日
    assert val["indices"]["SOX"]["memo_print"]["value"] == 12.72
    assert val["indices"]["SOX"]["memo_print"]["date"] == "2026-11-05"


def test_event_short_labels():
    shorts = {e["ym"]: e["short"] for e in VP.EVENT_BOTTOMS}
    assert shorts["2020-03"] == "疫情"
    assert shorts["2022-10"] == "升息"
    assert shorts["2025-04"] == "關稅"
    assert shorts["2026-03"] == "戰爭"
    assert all("short" in e for e in VP.EVENT_BOTTOMS)


if __name__ == "__main__":
    test_sigma_band_edges()
    test_sox_monthly_through_memo_date_matches_cobb()
    test_completed_month_excludes_current_month()
    test_build_uses_2016_home_window_and_skips_ndx_long_windows()
    test_event_short_labels()
    print("ok")
