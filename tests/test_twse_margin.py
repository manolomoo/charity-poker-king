"""證交所上市融資餘額（scripts/twse_margin.py）與走勢頁融資區塊。與 11 項燈號無關。"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import twse_margin as M  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "twse_margin"
TREND = ROOT / "trend" / "index.html"
HOME = ROOT / "home" / "index.html"
WORKFLOW = ROOT / ".github" / "workflows" / "update-and-deploy.yml"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def rep(name):
    return M.parse_report(load(name))


def by_date(series):
    return {r["date"]: r for r in series}


def test_parse_1119_matches_official_numbers():
    r = rep("20261119.json")
    assert r["date"] == "2026-11-19"
    assert r["margin_amt_k"] == 484368330
    assert r["margin_amt_prev_k"] == 508026642
    assert r["margin_shares"] == 8947619
    assert r["margin_shares_prev"] == 9092265
    assert r["short_shares"] == 209819
    assert r["short_shares_prev"] == 193544


def test_parse_1118():
    r = rep("20261118.json")
    assert (r["date"], r["margin_amt_k"], r["margin_shares"], r["short_shares"]) == (
        "2026-11-18", 508026570, 9092268, 193544)


def test_k_to_yi():
    assert 484368330 / 1e5 == 4843.6833
    assert M.k_to_yi(484368330) == 4843.6833
    assert M.k_to_yi(484368330, None) == 484368330 / 100000


def test_unpublished_returns_none_and_no_exception(tmp_path):
    assert M.parse_report(load("unpublished.json")) is None
    assert M.is_unpublished(load("unpublished.json"))
    # 尚未公布的回應不存檔
    with pytest.raises(M.TwseMarginError):
        M.save_raw(tmp_path, load("unpublished.json"))


def test_bad_payload_raises_custom_error():
    with pytest.raises(M.TwseMarginError):
        M.parse_report({"stat": "OK", "tables": [{"fields": ["項目", "前日餘額", "今日餘額"],
                                                  "data": [["融資(交易單位)", "1", "2"]]}]})
    with pytest.raises(M.TwseMarginError):
        M.parse_report({"stat": "查詢日期大於今日"})


def test_margin_shares_row_not_confused_with_amount_row():
    """融資張數要用「融資(」開頭比對，不能被「融資金額」列蓋掉。"""
    p = load("20261119.json")
    p["tables"][0]["data"] = list(reversed(p["tables"][0]["data"]))
    r = M.parse_report(p)
    assert r["margin_shares"] == 8947619 and r["margin_amt_k"] == 484368330


def test_only_1118_is_provisional():
    s = M.rebuild_series([rep("20261118.json")])
    assert len(s) == 1  # 不用前日餘額發明更早的一天
    assert s[0]["date"] == "2026-11-18"
    assert s[0]["margin_amt_k"] == 508026570
    assert s[0]["provisional"] is True


@pytest.mark.parametrize("order", [("20261118.json", "20261119.json"), ("20261119.json", "20261118.json")])
def test_next_day_prev_balance_confirms(order):
    s = by_date(M.rebuild_series([rep(n) for n in order]))
    assert list(s) == ["2026-11-18", "2026-11-19"]
    assert s["2026-11-18"]["margin_amt_k"] == 508026642
    assert s["2026-11-18"]["margin_shares"] == 9092265
    assert s["2026-11-18"]["provisional"] is False
    assert s["2026-11-19"]["margin_amt_k"] == 484368330
    assert s["2026-11-19"]["provisional"] is True


def test_latest_change_and_short_ratio():
    reports = [rep("20261118.json"), rep("20261119.json")]
    lt = M.latest_summary(M.rebuild_series(reports), reports)
    assert lt["date"] == "2026-11-19" and lt["provisional"] is True
    assert lt["chg_k"] == 484368330 - 508026642 == -23658312
    assert lt["chg_yi"] == pytest.approx(-236.58312)
    assert lt["chg_pct"] == pytest.approx(-4.657, abs=0.001)
    assert lt["margin_shares"] == 8947619 and lt["short_shares"] == 209819
    assert lt["short_to_margin_pct"] == round(209819 / 8947619 * 100, 2)
    assert M.short_to_margin_pct(209819, 8947619, 6) == pytest.approx(209819 / 8947619 * 100, abs=1e-6)


def test_latest_change_without_prev_report_uses_report_prev():
    """只有 11/19 一份時，日變化仍用該日報告的前日餘額。"""
    r = rep("20261119.json")
    lt = M.latest_summary(M.rebuild_series([r]), [r])
    assert lt["chg_k"] == -23658312


def test_gap_does_not_overwrite_non_adjacent_earlier_day():
    """缺 11/17、11/18 時，11/19 的前日餘額（= 11/18）不能寫到 11/16。"""
    r16 = dict(rep("20261118.json"), date="2026-11-16")  # 用 11/18 的報告改日期模擬 11/16
    s = by_date(M.rebuild_series([r16, rep("20261119.json")]))
    assert s["2026-11-16"]["margin_amt_k"] == r16["margin_amt_k"]
    assert s["2026-11-16"]["source"] == "MS"
    assert s["2026-11-16"]["margin_amt_k"] != 508026642


def test_weekend_counts_as_adjacent():
    """週五 → 週一中間只有週末，算相鄰。"""
    fri = dict(rep("20261118.json"), date="2026-11-13")
    mon = dict(rep("20261119.json"), date="2026-11-16")
    s = by_date(M.rebuild_series([fri, mon]))
    assert s["2026-11-13"]["margin_amt_k"] == 508026642 and s["2026-11-13"]["provisional"] is False


def test_trend_seed_placeholder_then_ms_overrides():
    seed = [
        {"date": "2026-11-17", "margin_amt_k": 508721448, "margin_shares": 1, "short_shares": 1, "source": "TREND"},
        {"date": "2026-11-18", "margin_amt_k": 508026570, "margin_shares": 9092268, "short_shares": 193544,
         "source": "TREND"},
    ]
    s = by_date(M.rebuild_series([], seed))
    assert s["2026-11-18"]["source"] == "TREND" and s["2026-11-18"]["provisional"] is True
    s = by_date(M.rebuild_series([rep("20261119.json")], seed))
    assert s["2026-11-18"]["margin_amt_k"] == 508026642 and s["2026-11-18"]["source"] == "MS_PREV"
    assert s["2026-11-17"]["source"] == "TREND" and s["2026-11-17"]["provisional"] is False
    assert s["2026-11-19"]["provisional"] is True


def _cache_with(tmp_path, names):
    for n in names:
        M.save_raw(tmp_path, load(n))
    return tmp_path


class FakeSession:
    def __init__(self, payloads):
        self.payloads = payloads
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params["date"])
        body = self.payloads.get(params["date"], {"stat": "很抱歉，沒有符合條件的資料"})

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return body

        return R()


def test_update_incremental_and_unpublished_is_not_failure(tmp_path):
    _cache_with(tmp_path, ["20261118.json"])
    sess = FakeSession({"20261118": load("20261118.json"), "20261119": load("20261119.json")})
    res = M.update(tmp_path, now=datetime(2026, 11, 20, 22, 0, tzinfo=M.TW_TZ), session=sess, pause=0)
    # 只請求最後一日（重抓）之後到今天；11/20 尚未公布 → 略過
    assert sess.calls == ["20261118", "20261119", "20261120"]
    assert res["report"]["unpublished"] == ["2026-11-20"] and not res["report"]["errors"]
    s = by_date(res["series"])
    assert s["2026-11-18"]["margin_amt_k"] == 508026642
    assert res["latest"]["date"] == "2026-11-19" and res["latest"]["provisional"] is True
    assert (tmp_path / "twse" / "margin" / "raw" / "20261119.json").exists()
    assert not (tmp_path / "twse" / "margin" / "raw" / "20261120.json").exists()


def test_update_before_publish_hour_skips_today(tmp_path):
    _cache_with(tmp_path, ["20261118.json", "20261119.json"])
    sess = FakeSession({})
    M.update(tmp_path, now=datetime(2026, 11, 20, 15, 0, tzinfo=M.TW_TZ), session=sess, pause=0)
    assert sess.calls == ["20261119"]


def test_update_network_error_falls_back_to_cache(tmp_path):
    _cache_with(tmp_path, ["20261118.json", "20261119.json"])

    class Boom(FakeSession):
        def get(self, *a, **k):
            raise ConnectionError("down")

    res = M.update(tmp_path, now=datetime(2026, 11, 20, 22, 0, tzinfo=M.TW_TZ), session=Boom({}), pause=0)
    assert res["report"]["errors"] and res["latest"]["date"] == "2026-11-19"


def test_update_seeds_from_site_when_cache_empty(tmp_path):
    site = {"dates": ["2026-11-18"], "margin_amt_k": [508026570], "margin_shares": [9092268], "short_shares": [193544]}
    sess = FakeSession({"20261119": load("20261119.json")})
    res = M.update(tmp_path, now=datetime(2026, 11, 19, 23, 0, tzinfo=M.TW_TZ), session=sess, pause=0,
                   prev_site_margin=site)
    assert sess.calls == ["20261118", "20261119"]  # 只抓新日子（＋重抓最後一日），不做長回補
    s = by_date(res["series"])
    assert s["2026-11-18"]["margin_amt_k"] == 508026642 and s["2026-11-19"]["provisional"] is True


def test_update_without_any_data_raises_custom_error(tmp_path):
    with pytest.raises(M.TwseMarginError):
        M.update(tmp_path, fetch=False)


def test_site_payload_shape(tmp_path):
    _cache_with(tmp_path, ["20261118.json", "20261119.json"])
    res = M.update(tmp_path, fetch=False)
    a, c = M.site_payload(res, "2026-11-20T22:00:00+08:00", {"2026-11-19": 38000.0}, "test")
    assert a["asof"] == "2026-11-19" and a["source_note"] == "資料：證交所（今日餘額為初值）"
    assert a["latest"]["margin_amt_yi"] == 4843.6833 and a["latest"]["provisional"] is True
    assert c["dates"] == ["2026-11-18", "2026-11-19"]
    assert c["margin_amt_k"] == [508026642, 484368330]
    assert c["provisional"] == [False, True]
    assert c["twii_close"] == [None, 38000.0]
    json.dumps({"a": a, "c": c}, allow_nan=False)


# ---------------- 頁面與排程 ----------------
def test_trend_page_margin_section_between_twii_and_tabook():
    t = TREND.read_text(encoding="utf-8")
    assert '<a href="#twii">台指</a>\n  <a href="#margin">融資餘額</a>' in t
    assert t.find('<section id="twii">') < t.find('<section id="margin"') < t.find('<section id="tabook">')
    assert "資料：證交所（今日餘額為初值）" in t
    assert "renderMargin()" in t and t.find('renderIndex("twii","TWII")') < t.find("renderMargin()")
    assert "初值" in t and "券資比" in t


def test_homepage_has_no_margin_chart():
    home = HOME.read_text(encoding="utf-8")
    # 首頁的「融資餘額」只是燈號第 7 項的名稱；不能有融資圖表區塊或讀 margin 圖表資料
    assert 'id="margin"' not in home and "margin-chart" not in home and "#margin" not in home
    assert "C.margin" not in home and "A.margin" not in home and "charts.margin" not in home


def test_workflow_keeps_three_crons_and_adds_2245():
    w = WORKFLOW.read_text(encoding="utf-8")
    crons = re.findall(r'cron: "([^"]+)"', w)
    assert crons == ["30 22 * * 0-4", "0 7 * * 1-5", "0 14 * * 1-5", "45 14 * * 1-5"]
    assert "path: data/cache/trend/twse" in w


def test_signal_scripts_do_not_use_trend_margin_module():
    for name in ("run.py", "margin_ratio.py", "signals_v2.py"):
        src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "import twse_margin" not in src and "MARGIN." not in src


# ---------------- update_trend 接線（fail-soft） ----------------
def _site():
    return {"analysis": {"meta": {"fetched_at_taipei": "2026-11-20T22:00:00+08:00"}},
            "charts": {"indices": {"TWII": {"chart_daily_1y": {"dates": ["2026-11-19"], "close": [38000.5]}}}}}


def test_attach_margin_success_offline(tmp_path, monkeypatch):
    import update_trend as U
    monkeypatch.setattr(U, "CACHE", tmp_path)
    _cache_with(tmp_path, ["20261118.json", "20261119.json"])
    site = _site()
    line = U.attach_margin(site, None, offline=True)
    assert "2026-11-19" in line and "4,843.68 億" in line and "初值" in line
    assert site["analysis"]["margin"]["latest"]["chg_yi"] == pytest.approx(-236.58312)
    assert site["charts"]["margin"]["twii_close"] == [None, 38000.5]


def test_attach_margin_failure_keeps_previous_or_omits(tmp_path, monkeypatch):
    import update_trend as U

    def boom(*a, **k):
        raise M.TwseMarginError("證交所掛了")

    monkeypatch.setattr(U.MARGIN, "update", boom)
    prev = {"analysis": {"asof": "2026-11-18", "latest": {}}, "charts": {"dates": ["2026-11-18"]}}
    site = _site()
    line = U.attach_margin(site, prev)
    assert "沿用上次" in line and site["analysis"]["margin"] is prev["analysis"]
    site = _site()
    line = U.attach_margin(site, None)
    assert "margin" not in site["analysis"] and "margin" not in site["charts"] and "不出融資圖" in line
