import datetime as dt
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("index_pe", os.path.join(ROOT, "scripts", "index_pe.py"))
ip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ip)

NOW = dt.datetime(2026, 11, 28, 12, 0, tzinfo=ip.TPE)


def hom_idx():
    return {
        "current": {"trailing": 1, "forward": 20.12, "forwardOwn": 1},
        "trailing": [{"date": "2026-11-26", "value": 23.99}, {"date": "2026-11-27", "value": 24.05}],
        "forward": [{"date": "2026-08-05", "value": 20.12}],  # 週資料、已過期：不能被用到
        "forwardOwn": [{"date": "2026-11-25", "value": 16.73, "basis": "x"}, {"date": "2026-11-26", "value": 16.74, "basis": "x"}],
    }


def hom_sox():
    return {"sectors": {"sox": {"forwardOwnLatest": {"date": "2026-11-26", "value": 12.07},
                                "series": [{"date": "2026-08-05", "value": 21.68}]}}}


def test_parse_hom_index_uses_latest_daily_series_not_stale_weekly():
    r = ip.parse_hom_index(hom_idx())
    assert r["trailing"]["value"] == 24.05 and r["trailing"]["asof"] == "2026-11-27"
    assert r["forward"]["value"] == 16.74 and r["forward"]["asof"] == "2026-11-26"
    assert r["trailing"]["method"] != r["forward"]["method"]  # 口徑分開標


def test_parse_hom_index_missing_is_none():
    assert ip.parse_hom_index({}) == {"trailing": None, "forward": None}
    assert ip.parse_hom_index(None) == {"trailing": None, "forward": None}
    r = ip.parse_hom_index({"trailing": [{"date": "2026-11-27", "value": None}], "forwardOwn": []})
    assert r == {"trailing": None, "forward": None}


def test_sox_has_forward_only():
    r = ip.parse_hom_sox(hom_sox())
    assert r["trailing"] is None
    assert r["forward"]["value"] == 12.07 and r["forward"]["asof"] == "2026-11-26"
    assert ip.parse_hom_sox({}) == {"trailing": None, "forward": None}


def twse_rows(n=600, pe="20", price="10"):
    pe_rows = [{"Date": "20261127", "Code": str(1000 + i), "ClosePrice": price, "PEratio": pe} for i in range(n)]
    co_rows = [{"公司代號": str(1000 + i), ip.SHARES_FIELD: "1000000"} for i in range(n)]
    return pe_rows, co_rows


def test_compute_twii_market_cap_weighted_harmonic():
    pe_rows, co_rows = twse_rows()
    r = ip.compute_twii(pe_rows, co_rows)
    assert r["value"] == 20.0 and r["asof"] == "2026-11-27" and r["coverage_pct"] == 100.0
    # 一檔大型股 PE 10、市值是其餘總和的 100 倍：結果應被它拉近 10（市值加權，不是簡單平均）
    pe_rows.append({"Date": "20261127", "Code": "9999", "ClosePrice": "10", "PEratio": "10"})
    co_rows.append({"公司代號": "9999", ip.SHARES_FIELD: "60000000000"})
    r2 = ip.compute_twii(pe_rows, co_rows)
    assert 10.0 < r2["value"] < 10.2


def test_compute_twii_excludes_loss_makers_and_reports_coverage():
    pe_rows, co_rows = twse_rows()
    for r in pe_rows[:30]:
        r["PEratio"] = ""  # 虧損股沒有本益比
    r = ip.compute_twii(pe_rows, co_rows)
    assert r["value"] == 20.0 and r["coverage_pct"] == 95.0 and r["stocks"] == 570


def test_compute_twii_refuses_thin_or_bad_data():
    pe_rows, co_rows = twse_rows(n=100)
    assert ip.compute_twii(pe_rows, co_rows) is None  # 筆數太少
    pe_rows, co_rows = twse_rows()
    for r in pe_rows[:100]:
        r["PEratio"] = "-"
    assert ip.compute_twii(pe_rows, co_rows) is None  # 涵蓋 < 90%
    assert ip.compute_twii(None, None) is None
    assert ip.compute_twii([], []) is None


def fetchers(**over):
    pe_rows, co_rows = twse_rows()

    def hom(url):
        if "sectors" in url:
            return hom_sox()
        return hom_idx()

    def twse(url):
        return pe_rows if "BWIBBU" in url else co_rows
    f = {"hom": hom, "twse": twse}
    f.update(over)
    return f


def test_build_full():
    d = ip.build(NOW, None, fetchers())
    assert d["stale"] is False and d["errors"] == []
    by = {i["key"]: i for i in d["items"]}
    assert list(by) == ["SPX", "NDX", "SOX", "TWII"]  # 台指期不列
    assert by["SOX"]["trailing"] is None and by["TWII"]["forward"] is None
    assert by["TWII"]["trailing"]["value"] == 20.0
    assert by["SPX"]["forward"]["asof"] == "2026-11-26"


def test_build_keeps_old_cell_when_source_fails_and_never_invents():
    old = ip.build(NOW, None, fetchers())

    def boom(url):
        raise RuntimeError("x")
    d = ip.build(NOW, old, fetchers(hom=boom))
    assert d["stale"] is True and len(d["errors"]) == 3
    by = {i["key"]: i for i in d["items"]}
    assert by["SPX"]["forward"]["value"] == 16.74  # 沿用舊值（自帶資料日）
    assert by["SOX"]["trailing"] is None  # 本來就沒有就維持沒有
    # 完全沒有舊檔：全部 None，不寫 0
    d2 = ip.build(NOW, None, {"hom": boom, "twse": boom})
    for it in d2["items"]:
        assert it["trailing"] is None and it["forward"] is None


def test_fresh_enough_throttle():
    d = {"fetched_at": "2026-11-28T10:30:00+08:00", "stale": False}
    assert ip.fresh_enough(d, NOW, 3) is True
    assert ip.fresh_enough(d, NOW, 1) is False
    assert ip.fresh_enough({**d, "stale": True}, NOW, 3) is False  # 上次有失敗就重抓
    assert ip.fresh_enough(None, NOW, 3) is False
    assert ip.fresh_enough(d, NOW, 0) is False


def test_homepage_wired():
    html = open(os.path.join(ROOT, "home", "index.html"), encoding="utf-8").read()
    assert 'id="idxpe-rows"' in html and "data/index_pe.json" in html
    for wf in ("etf-premium.yml", "update-and-deploy.yml"):
        y = open(os.path.join(ROOT, ".github", "workflows", wf), encoding="utf-8").read()
        assert "scripts/index_pe.py" in y
