"""燈號 #1 QQQ／SOXX／SMH 官方資金流解析與快取。不碰規則門檻。"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run as R  # noqa: E402


def _params():
    return dict(week_days=5, month_days=21, m_trigger=-2.0, w_trigger=-1.0, m_near=-1.0, w_near=-0.5)


def _series(n=22, start="2026-10-20", nav=10.0, so0=1000, last5_dso=0):
    """交易日 NAV 逐日微幅變動，避免被當成週末重複列刪掉。"""
    dates = pd.bdate_range(start, periods=n)
    so = np.full(n, so0, dtype=float)
    if last5_dso:
        for i in range(n - 5, n):
            so[i] = so[i - 1] + last5_dso
    navs = np.array([nav + i * 0.01 for i in range(n)], dtype=float)
    x = pd.DataFrame({"NAV": navs, "SO": so, "TNA": so * navs}, index=dates)
    x.index.name = "Date"
    return x


def test_looks_like_html_rejects_disguised_xls():
    html = b"<!DOCTYPE html>\n<html><head></head><body>iShares</body></html>"
    assert R.looks_like_html(html)
    assert R.looks_like_html("<html xmlns='http://www.w3.org/1999/xhtml'>")
    assert not R.looks_like_html(b'{"productId": 239705}')
    assert not R.looks_like_html(b"PK\x03\x04fakezip")
    with pytest.raises(R.EtfDataError, match="HTML"):
        R.parse_ishares_fund_xls(html)
    with pytest.raises(R.EtfDataError, match="HTML"):
        R.parse_ishares_funddownload_json(html)
    with pytest.raises(R.EtfDataError, match="HTML"):
        R.parse_vaneck_histoprices(html)
    with pytest.raises(R.EtfDataError, match="HTML"):
        R.parse_invesco_navs(html)


def test_round_shares_half_up():
    assert R.round_shares(0.5) == 1
    assert R.round_shares(1.5) == 2
    assert R.round_shares(693300000.23) == 693300000
    s = R.round_shares(pd.Series([131941870.52, 0.4, 0.5]))
    assert list(s.astype(int)) == [131941871, 0, 1]


def test_drop_unchanged_rows_removes_weekend_repeats_not_real_days():
    idx = pd.to_datetime(["2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"])
    x = pd.DataFrame({
        "NAV": [154.97, 149.36, 149.36, 149.36, 141.17],
        "SO": [288700000, 275950000, 275950000, 275950000, 254800000],
    }, index=idx)
    x["TNA"] = x["SO"] * x["NAV"]
    y = R.drop_unchanged_rows(x)
    assert [d.strftime("%Y-%m-%d") for d in y.index] == ["2026-11-12", "2026-11-13", "2026-11-16"]
    assert 0 not in y["SO"].tolist()



def test_zero_flow_trading_day_with_nav_change_is_kept():
    x = _series(n=22, so0=1000, last5_dso=0)
    y = R.drop_unchanged_rows(x)
    assert len(y) == 22
    fl = R.etf_flow_frame(x, _params())
    assert fl["week_flow_usd"] == pytest.approx(0.0)
    assert fl["n_obs"] == 22
    assert R._sig1_status(fl["week_pct"], fl["month_pct"], _params()) == R.WHT

def test_etf_flow_frame_insufficient_is_error_not_zero():
    x = _series(n=10)
    with pytest.raises(R.EtfDataError, match="不足"):
        R.etf_flow_frame(x, _params())


def test_etf_flow_frame_week_month_and_status():
    # 最後 5 個交易日每天贖回 2 股；交易日 NAV 有變動，零申贖的前面幾日仍保留在窗口裡。
    x = _series(n=22, so0=1000, last5_dso=-2)
    fl = R.etf_flow_frame(x, _params())
    week_navs = x["NAV"].iloc[-5:]
    exp_week = float(((-2) * week_navs).sum())
    aum0_w = float(x["TNA"].iloc[-6])
    aum0_m = float(x["TNA"].iloc[-22])
    assert fl["week_flow_usd"] == pytest.approx(exp_week)
    assert fl["week_pct"] == pytest.approx(exp_week / aum0_w * 100)
    assert fl["month_flow_usd"] == pytest.approx(exp_week)
    assert fl["month_pct"] == pytest.approx(exp_week / aum0_m * 100)
    assert fl["shares"] == 990
    assert fl["n_obs"] == 22
    # 近週約 -1%（NAV 微幅上移，百分比略小於 1，用實際值對門檻）
    st = R._sig1_status(fl["week_pct"], fl["month_pct"], _params())
    assert st in (R.RED, R.YEL, R.WHT)


def test_etf_flow_frame_does_not_fill_missing_as_zero():
    x = _series(n=22, so0=1000, last5_dso=-2)
    # 抽掉倒數第 2 個交易日：窗口改用剩下的觀測，不會把那天補 0 流量
    dropped = x.drop(x.index[-2])
    assert len(dropped) == 21  # 不足 22
    with pytest.raises(R.EtfDataError, match="不足"):
        R.etf_flow_frame(dropped, _params())


def test_parse_ishares_json_and_flows():
    # 22 個平日 + 一組週末重複列
    bdays = pd.bdate_range("2026-10-20", periods=22)
    asof, nav, so = [], [], []
    shares = 1000
    for i, d in enumerate(bdays):
        if i >= 17:
            shares -= 2
        asof.append(int(d.strftime("%Y%m%d")))
        nav.append(10.0 + i * 0.01)
        so.append(float(shares))
    # 在 11/13 後插入週末重複（若序列裡有週五）
    fri = bdays[0]
    for d in bdays:
        if d.weekday() == 4:
            fri = d
            break
    sat = fri + pd.Timedelta(days=1)
    asof.extend([int(sat.strftime("%Y%m%d")), int((sat + pd.Timedelta(days=1)).strftime("%Y%m%d"))])
    # 週末用週五的值
    fri_i = list(bdays).index(fri)
    nav.extend([nav[fri_i], nav[fri_i]])
    so.extend([so[fri_i], so[fri_i]])
    payload = {
        "componentsByNameMap": {
            "fundDownload": {
                "containersByNameMap": {
                    "historical": {
                        "dataPointsByNameMap": {
                            "asof": {"value": asof[::-1]},
                            "nav": {"value": nav[::-1]},
                            "sharesOutstanding": {"value": so[::-1]},
                        }
                    }
                }
            }
        }
    }
    x = R.parse_ishares_funddownload_json(json.dumps(payload))
    assert len(x) == 24  # 22 平日 + 2 週末，去重前
    fl = R.etf_flow_frame(x, _params())
    assert fl["n_obs"] == 22
    # 最後 5 個交易日每天 -2 股，NAV 約 10；只確認去重後窗口與方向，不用固定 10 當作 NAV
    assert fl["week_flow_usd"] < 0
    assert fl["week_flow_usd"] == pytest.approx(-2 * float(x.loc[bdays[-5:], "NAV"].sum()))


def test_parse_invesco_navs_rounds_and_cross_check():
    recs = []
    so = 693300000
    nav = 645.243731
    start = pd.Timestamp("2026-10-21")
    days = pd.bdate_range(start, periods=22)
    shares = [so + (21 - i) * 10000 for i in range(22)]  # 單調減少到 so
    for d, s in zip(days, shares):
        recs.append({
            "effectiveDate": d.strftime("%Y-%m-%d"),
            "netAssetValue": nav,
            "shareclassTotalNetAssets": s * nav + 0.2,  # 加一點餘數，確認四捨五入
        })
    x = R.parse_invesco_navs(json.dumps(recs))
    assert int(x["SO"].iloc[-1]) == so
    details = R.parse_invesco_fund_details(json.dumps({
        "effectiveDate": "2026-11-19",
        "sharesOutstanding": 693300000,
        "nav": nav,
        "shareclassTotalNetAssets": so * nav,
    }))
    assert details["shares"] == 693300000
    assert abs(int(x["SO"].iloc[-1]) - details["shares"]) <= 1


def test_parse_vaneck_xlsx_round_then_diff(tmp_path):
    dates = pd.bdate_range("2026-10-20", periods=22)
    nav = 447.4342 + np.arange(22) * 0.01
    so = np.full(22, 131941871, dtype=float)
    so[-5:] = so[-6] - np.arange(1, 6) * 50000
    aum = so * nav
    df = pd.DataFrame({
        "Date": dates.strftime("%m/%d/%Y"),
        "NAV": nav,
        "Change": 0,
        "% Change": 0,
        "Last Trade": list(nav),
        "Volume": 1,
        "Premium/Discount": 0,
        "% Premium/Discount": 0,
        "AUM": [f"{v:,.2f}" for v in aum],
        "Index Level": 1,
    })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        title = pd.DataFrame([["VanEck Semiconductor ETF - SMH"] + [""] * 9])
        title.to_excel(w, index=False, header=False, startrow=0)
        df.to_excel(w, index=False, startrow=1)
    raw = buf.getvalue()
    assert raw.startswith(b"PK")
    x = R.parse_vaneck_histoprices(raw)
    assert len(R.drop_unchanged_rows(x)) == 22
    fl = R.etf_flow_frame(x, _params())
    assert fl["shares"] == 131941871 - 250000
    # 近週每天贖回 50000 股，乘以當日 NAV
    exp = float((np.array([-50000] * 5) * nav[-5:]).sum())
    assert fl["week_flow_usd"] == pytest.approx(exp, rel=1e-9)


def test_cache_fallback_marks_stale_and_does_not_invent_zero(tmp_path, monkeypatch):
    cache = tmp_path / "etf"
    cache.mkdir()
    monkeypatch.setattr(R, "ETF_CACHE", str(cache))

    dates = pd.bdate_range("2026-10-20", periods=22)
    recs = [{"effectiveDate": d.strftime("%Y-%m-%d"),
             "netAssetValue": 10.0 + i * 0.01,
             "shareclassTotalNetAssets": 1000 * (10.0 + i * 0.01)} for i, d in enumerate(dates)]
    (cache / "qqq_navs.json").write_text(json.dumps(recs), encoding="utf-8")

    def boom(*args, **kwargs):
        raise R.EtfDataError("HTTP 406")

    monkeypatch.setattr(R, "fetch_bytes", boom)
    x, sources, note_src, stale, err, cross = R._load_qqq_history()
    assert stale is True
    assert "406" in err
    fl = R.etf_flow_frame(x, _params())
    assert fl["week_flow_usd"] == pytest.approx(0.0)
    assert fl["n_obs"] == 22
    row = R._sig1_row("QQQ", fl, sources, "note", stale=True, stale_reason="資料過期：測試")
    assert row["stale"] is True
    assert row["value"] and "近1週" in row["value"]
    assert row["status"] == R.WHT  # 流量 0，不是缺資料

    # 沒有快取時維持 ❔，不編 0
    (cache / "qqq_navs.json").unlink()
    with pytest.raises(R.EtfDataError, match="406"):
        R._load_qqq_history()


def test_invesco_fetch_uses_non_browser_ua(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "ETF_CACHE", str(tmp_path))
    seen = []

    def fake_fetch(url, headers=None, retries=4, timeout=60, sleep=1.0):
        seen.append((url, dict(headers or {})))
        dates = pd.bdate_range("2026-10-20", periods=22)
        if "navs" in url:
            recs = [{"effectiveDate": d.strftime("%Y-%m-%d"),
                     "netAssetValue": 10.0 + i * 0.01,
                     "shareclassTotalNetAssets": 1000 * (10.0 + i * 0.01)} for i, d in enumerate(dates)]
            return json.dumps(recs).encode(), "text/plain;charset=UTF-8", 200
        if "fundDetails" in url or "variationType=fundDetails" in url:
            body = json.dumps({"effectiveDate": "2026-11-19", "sharesOutstanding": 1000, "nav": 10.0})
            return body.encode(), "text/plain;charset=UTF-8", 200
        raise R.EtfDataError("unexpected url")

    monkeypatch.setattr(R, "fetch_bytes", fake_fetch)
    x, sources, note_src, stale, err, cross = R._load_qqq_history()
    assert stale is False
    assert any("vgi-etf-invesco/0.2.2" == h.get("User-Agent") and h.get("Referer") == "https://www.invesco.com/"
               for _, h in seen)
    assert "一致" in cross or "1000" in cross


def test_apply_stale_fallback_keeps_premarked_cache_rows():
    fl = R.etf_flow_frame(_series(n=22), _params())
    cached = R._sig1_row("SOXX", fl, ["https://example.invalid"], "沿用快取", stale=True, stale_reason="資料過期：測試")
    fresh = dict(cached)
    fresh["stale"] = False
    fresh.pop("stale_reason", None)
    # 這次列已標 stale，即使 value 有值也應列入 stale_rows
    out, stale = R.apply_stale_fallback([cached], {"rows": [fresh]}, {})
    assert "1｜SOXX" in stale
    assert out[0]["stale"] is True


def test_rules_thresholds_untouched():
    rules = json.loads((ROOT / "rules.json").read_text(encoding="utf-8"))
    p = rules["signals"]["1"]["params"]
    assert p == {"m_trigger": -2.0, "w_trigger": -1.0, "m_near": -1.0, "w_near": -0.5,
                 "week_days": 5, "month_days": 21}
