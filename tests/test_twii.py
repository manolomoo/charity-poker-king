"""首頁台指（^TWII）圖卡的回歸檢查。不碰燈號規則，也不依賴某一天的型態名稱。"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import re
import shutil
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import market_stage as MS  # noqa: E402
import trend_analyze as T  # noqa: E402
import update_trend as U  # noqa: E402

CACHE = ROOT / "data" / "cache" / "trend"
HOME = ROOT / "home" / "index.html"
TREND = ROOT / "trend" / "index.html"
STAGE = ROOT / "trend" / "stage" / "index.html"
TPE = ZoneInfo("Asia/Taipei")
NY = ZoneInfo("America/New_York")


# ---------------- 美股／台股分組 ----------------
def test_tw_ids_separate_from_us():
    assert T.IDS == ["SOX", "NDX", "SPX"], "美股三檔的同日檢查不可混入台指"
    assert T.TW_IDS == ["TWII"]
    assert T.SOURCE_TICKERS["TWII"] == "^TWII"
    assert "TWII" not in MS.PANIC_TH, "沒有台指的恐慌門檻，不要發明數字"


def _copy_cache(tmp_path, skip=()):
    for p in CACHE.iterdir():
        if p.is_file() and not any(p.name.startswith(s) for s in skip):
            shutil.copy(p, tmp_path / p.name)
    return tmp_path


def _drop_last_daily(d, name):
    for key in ("daily_1y", "daily_2y"):
        p = d / f"{name}_{key}.csv"
        pd.read_csv(p).iloc[:-1].to_csv(p, index=False)


def _build(d):
    return T.build(d, ROOT / "data" / "latest.json", ROOT / "rules.json", "2026-11-16T15:00:00+08:00")


def test_build_us_date_mismatch_still_fails(tmp_path):
    d = _copy_cache(tmp_path)
    _drop_last_daily(d, "NDX")
    with pytest.raises(ValueError, match="最新交易日不一致"):
        _build(d)


def test_build_twii_other_date_ok_and_excluded_from_us_parts(tmp_path):
    d = _copy_cache(tmp_path)
    # 不依賴今天的快取剛好哪一天：台指若跟美股同一天，就拿掉一根讓它不同天
    last = lambda n: pd.read_csv(d / f"{n}_daily_2y.csv")["date"].iloc[-1]
    if last("TWII") == last("SOX"):
        _drop_last_daily(d, "TWII")
    analysis, charts = _build(d)
    us = {analysis["indices"][k]["last"]["date"] for k in T.IDS}
    tw = analysis["indices"]["TWII"]
    assert len(us) == 1
    assert tw["last"]["date"] not in us
    assert analysis["meta"]["price_asof"] == us.pop(), "price_asof 維持美股"
    assert analysis["meta"]["tw_asof"] == {"TWII": tw["last"]["date"]}
    assert "home_pattern" in tw  # 可以是 None（不構成典型形態），但一定要算過
    assert "market_stage" not in tw
    assert "volume_by_price" in tw
    assert charts["indices"]["TWII"]["chart_daily_1y"]["dates"][-1] == tw["last"]["date"]
    # 三指數對照、階段總覽、估值都不含台指
    assert [r["id"] for r in analysis["confirmation"]["table"]] == ["SOX", "NDX", "SPX"]
    assert "TWII" not in analysis["stages"]["home_line"] and "台指" not in analysis["stages"]["home_line"]
    assert "TWII" not in ((analysis["valuation"].get("home_tags")) or {})
    assert "TWII" not in ((analysis["valuation"].get("indices")) or {})


def test_build_without_twii_cache_still_ok(tmp_path):
    d = _copy_cache(tmp_path, skip=("TWII_", "0050_"))
    analysis, charts = _build(d)
    assert list(analysis["indices"]) == ["SOX", "NDX", "SPX"]
    assert "TWII" not in charts["indices"]
    assert "TWII" in analysis["meta"]["tw_skipped"]


def test_offline_meta_only_requires_us_cache(tmp_path, monkeypatch):
    d = _copy_cache(tmp_path, skip=("TWII_", "0050_"))
    monkeypatch.setattr(U, "CACHE", d)
    assert U.load_cached_meta()["fetched_at_taipei"]


def test_summarize_stages_ignores_twii():
    fake = {"regime": "空頭", "headline": "空頭・測試", "asof": "2026-11-16"}
    s = MS.summarize_stages({"SOX": fake, "NDX": fake, "SPX": fake, "TWII": fake})
    assert s["home_line"].count("空頭・測試") == 3
    assert "TWII" not in s["home_line"]
    assert s["primary"] == ["NDX", "SOX"] and s["reference"] == ["SPX"]


# ---------------- 未完成K：市場別 ----------------
def _frames(last_day="2026-11-16"):
    """到 last_day（週一）為止的日K；週K 只到上一週（Yahoo ^TWII 收盤後常見的樣子）。"""
    days = [x.strftime("%Y-%m-%d") for x in pd.bdate_range(end=last_day, periods=30)]
    close = [100.0 + i for i in range(len(days))]
    daily = pd.DataFrame({
        "date": days, "timestamp": 0, "open": close, "high": [c + 1 for c in close],
        "low": [c - 1 for c in close], "close": close, "adjclose": close, "volume": 1000,
    })
    mondays = sorted({(pd.Timestamp(x) - pd.Timedelta(days=pd.Timestamp(x).weekday())).strftime("%Y-%m-%d") for x in days[:-1]})
    weekly = pd.DataFrame({
        "date": mondays, "timestamp": 0, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "adjclose": 1.5, "volume": 5000,
    })
    return {"daily_1y": daily.copy(), "daily_2y": daily.copy(), "weekly_2y": weekly.copy(), "weekly_5y": weekly.copy()}


def test_tw_session_1500_keeps_finished_bar():
    f = _frames()
    dropped = U.drop_unfinished_session(f, market="TW", now=datetime(2026, 11, 16, 15, 0, tzinfo=TPE))
    assert dropped is None
    assert f["daily_2y"]["date"].iloc[-1] == "2026-11-16"
    assert f["daily_1y"]["date"].iloc[-1] == "2026-11-16"


def test_tw_session_2200_and_next_morning_keep_bar():
    for now in (datetime(2026, 11, 16, 22, 0, tzinfo=TPE), datetime(2026, 11, 17, 6, 30, tzinfo=TPE)):
        f = _frames()
        assert U.drop_unfinished_session(f, market="TW", now=now) is None
        assert f["daily_2y"]["date"].iloc[-1] == "2026-11-16"


def test_tw_session_1100_drops_unfinished_bar():
    f = _frames()
    dropped = U.drop_unfinished_session(f, market="TW", now=datetime(2026, 11, 16, 11, 0, tzinfo=TPE))
    assert dropped == "2026-11-16"
    assert f["daily_2y"]["date"].iloc[-1] == "2026-11-13"
    assert f["daily_1y"]["date"].iloc[-1] == "2026-11-13"
    assert f["weekly_2y"]["date"].iloc[-1] == "2026-11-09"  # 週一盤中：本週還沒有已完成的日K


def test_tw_week_rebuilt_with_taipei_monday():
    f = _frames()
    U.rebuild_week(f, f["daily_2y"]["date"].iloc[-1], U.MARKETS["TW"][2])
    for key in ("weekly_2y", "weekly_5y"):
        row = f[key].iloc[-1]
        assert row["date"] == "2026-11-16"
        assert row["timestamp"] == int(pd.Timestamp("2026-11-16", tz="Asia/Taipei").timestamp())
        assert row["timestamp"] != int(pd.Timestamp("2026-11-16", tz="America/New_York").timestamp())
        assert row["close"] == f["daily_2y"]["close"].iloc[-1]


def test_us_session_logic_unchanged():
    # 紐約 10:00（盤中）：拿掉當日K；週K 星期一時間戳仍用紐約
    f = _frames("2026-11-17")
    assert U.drop_unfinished_session(f, now=datetime(2026, 11, 17, 10, 0, tzinfo=NY)) == "2026-11-17"
    assert f["daily_2y"]["date"].iloc[-1] == "2026-11-16"
    row = f["weekly_2y"].iloc[-1]
    assert row["date"] == "2026-11-16"
    assert row["timestamp"] == int(pd.Timestamp("2026-11-16", tz="America/New_York").timestamp())
    # 紐約 16:15 之後：留著
    f = _frames("2026-11-17")
    assert U.drop_unfinished_session(f, now=datetime(2026, 11, 17, 16, 15, tzinfo=NY)) is None
    assert f["daily_2y"]["date"].iloc[-1] == "2026-11-17"
    # 台北 15:00 等於紐約凌晨：同一份「當日」美股K 在美股規則下是未來的K，要拿掉
    f = _frames("2026-11-16")
    assert U.drop_unfinished_session(f, market="US", now=datetime(2026, 11, 16, 15, 0, tzinfo=TPE)) == "2026-11-16"


def test_proxy_frames_with_only_daily_2y():
    f = {"daily_2y": _frames()["daily_2y"]}
    assert U.drop_unfinished_session(f, market="TW", now=datetime(2026, 11, 16, 11, 0, tzinfo=TPE)) == "2026-11-16"
    assert list(f) == ["daily_2y"]


# ---------------- 籌碼量來源 ----------------
def _daily(volumes):
    n = len(volumes)
    dates = pd.bdate_range(end="2026-11-16", periods=n)
    close = [100.0 + (i % 37) for i in range(n)]
    return pd.DataFrame({"date": dates, "open": close, "high": close, "low": close, "close": close, "volume": volumes})


def test_volume_uses_twii_when_window_has_volume():
    n = 300
    vols = [1000] * n
    vols[-1] = 0  # 收盤後當日量一度是 0：不可因此整段改用 0050
    daily = _daily(vols)
    proxy = _daily([7] * n)
    vol, label, proxy_name = T.tw_volume(daily, proxy, price_source="TWSE")
    assert proxy_name is None
    assert "證交所" in label and "0050" not in label
    assert vol.equals(daily["volume"])


def test_volume_falls_back_to_0050_when_window_is_zero():
    n = 300
    vols = [1000] * (n - MS.VBP_BARS) + [0] * MS.VBP_BARS  # 視窗外有量也不算
    daily = _daily(vols)
    proxy = _daily([7] * n)
    vol, label, proxy_name = T.tw_volume(daily, proxy)
    assert proxy_name == "0050"
    assert "0050.TW" in label
    assert float(vol.tail(MS.VBP_BARS).sum()) == 7 * MS.VBP_BARS
    v = MS.volume_by_price(daily, volume=vol, source_label=label)
    assert v["available"] is True
    assert "0050.TW" in v["source_label"]
    assert v["bars"] == MS.VBP_BARS == 126 and v["bin_count"] == MS.VBP_BINS == 20


def test_volume_zero_and_no_proxy_is_unavailable_not_error():
    daily = _daily([0] * 300)
    vol, label, proxy_name = T.tw_volume(daily, None)
    assert vol is None and proxy_name is None
    assert "0050" in label


def test_analyze_tw_writes_proxy_label(tmp_path):
    d = _copy_cache(tmp_path)
    for key in ("daily_1y", "daily_2y"):
        p = d / f"TWII_{key}.csv"
        df = pd.read_csv(p)
        df["volume"] = 0
        df.to_csv(p, index=False)
    ix = T.analyze_tw("TWII", d)
    vbp = ix["volume_by_price"]
    if (d / T.TW_PROXY_CSV).exists():
        assert vbp["proxy"] == "0050" and "0050.TW" in vbp["source_label"] and vbp["available"] is True
    else:
        assert vbp["available"] is False and "0050" in vbp["source_label"]


# ---------------- 頁面 ----------------
def test_homepage_draws_twii_after_spx_before_ta_nav():
    home = HOME.read_text(encoding="utf-8")
    m = re.search(r'var IDS = (\[[^\]]+\]);\s*\n\s*var SYM', home)
    assert m, "找不到首頁圖卡的 IDS 清單"
    assert re.findall(r'"(\w+)"', m.group(1)) == ["SOX", "NDX", "SPX", "TWII"]
    assert 'TWII:"台指"' in home
    assert "台股加權指數（證交所）" in home
    assert "證交所" in home
    # 圖卡都畫進 #idx，#idx 在 #ta-nav 前面
    assert 0 < home.find('id="idx"') < home.find('id="ta-nav"') < home.find('id="pe-charts"')
    assert "目前不構成典型形態" in home
    assert "./trend/#" in home
    # 籌碼代理標示由資料決定
    assert "vbp.proxy" in home


def test_homepage_pe_section_still_sox_ndx_only():
    home = HOME.read_text(encoding="utf-8")
    pe = home[home.find('<section id="pe-charts"'):home.find("<footer>")]
    assert re.findall(r'class="pe-card" id="([\w-]+)"', pe) == ["pe-sox", "pe-ndx"]
    assert "twii" not in pe.lower() and "台指" not in pe
    drawn = re.findall(r'drawOne\(document\.getElementById\("([\w-]+)"\), "(\w+)"', home)
    assert drawn == [("pe-sox", "SOX"), ("pe-ndx", "NDX")]
    # P/E 小標只在 valuation.home_tags[id] 有資料時才渲染；台指沒有來源
    assert "A.valuation.home_tags[id]" in home
    assert "TWII" not in T.VP.CACHE_NAME


def test_generated_data_has_no_twii_pe_or_stage():
    import json
    data = json.loads((ROOT / "trend" / "data.json").read_text(encoding="utf-8"))
    a = data["analysis"]
    assert "TWII" not in ((a.get("valuation") or {}).get("home_tags") or {})
    assert "TWII" not in a["stages"]["home_line"]
    tw = a["indices"].get("TWII")
    if tw:  # 這一輪有抓到台指
        assert "market_stage" not in tw
        assert tw["name"].startswith("台股加權指數")
        assert data["charts"]["indices"]["TWII"]["chart_daily_1y"]["dates"]


def test_trend_page_has_twii_section_but_not_in_compare_or_stage_cards():
    t = TREND.read_text(encoding="utf-8")
    assert '<a href="#twii">台指</a>' in t
    assert t.find('<section id="spx">') < t.find('<section id="twii">') < t.find('<section id="tabook">')
    assert 'renderIndex("twii","TWII")' in t
    assert 'const IDS = ["SOX","NDX","SPX"];' in t, "三指數對照仍只有美股三檔"
    stage_block = t[t.find("/* 牛熊階段摘要 */"):t.find("function drawVbp")]
    assert '["SOX","NDX","SPX"].forEach' in stage_block and "TWII" not in stage_block


def test_stage_page_has_no_twii():
    s = STAGE.read_text(encoding="utf-8")
    assert "TWII" not in s and "台指" not in s
    assert '["SOX","NDX","SPX"].forEach' in s
