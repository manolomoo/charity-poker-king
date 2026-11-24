"""首頁 ETF 溢折價快取（scripts/etf_premium.py、home/index.html、etf-premium workflow）。

使用者要的 00630L 不是上市代號，證交所 ETF 名單沒有；對應元大台灣50正2 00631L。
fixture 是 2026-11-24 08:53 台北時間實測 all_etf.txt 的縮小版。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import etf_premium as EP  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "etf_premium" / "all_etf_small.json"
HOME = ROOT / "home" / "index.html"
WF = ROOT / ".github" / "workflows" / "etf-premium.yml"
CODES = ["0050", "00631L", "00662", "00670L", "00830", "00646", "00647L"]
NOW = dt.datetime(2026, 11, 24, 9, 0, tzinfo=EP.TPE)


def _fixture():
    return json.loads(FIX.read_text(encoding="utf-8"))


def test_targets_use_00631l_not_00630l():
    assert [c for c, _ in EP.TARGETS] == CODES
    assert EP.CODE_ALIASES == {"00630L": "00631L"}


def test_parse_fixture_listed_codes():
    d = EP.build_ok(EP.parse(_fixture()), NOW)
    by = {i["code"]: i for i in d["items"]}
    assert [i["code"] for i in d["items"]] == CODES
    assert d["stale"] is False and d["error"] is None
    assert by["00830"]["price"] == 65.10 and by["00830"]["nav"] == 69.79 and by["00830"]["premium_pct"] == -6.72
    assert by["00830"]["data_at"] == "2026-11-24T08:53:15+08:00"
    assert by["00830"]["ref_url"] == "https://www.cathaysite.com.tw/ETF/estimate"
    assert by["00662"]["premium_pct"] == -3.06 and by["00662"]["prev_nav"] == 22.55
    assert by["00670L"]["price"] == 148.55 and by["00670L"]["nav"] == 158.10 and by["00670L"]["premium_pct"] == -6.04
    assert by["00631L"]["name"] == "元大台灣50正2"
    assert by["00631L"]["price"] == 22.5 and by["00631L"]["nav"] == 25.76 and by["00631L"]["premium_pct"] == -12.66
    assert by["00631L"]["prev_nav"] == 22.23
    assert by["00631L"]["data_date"] == "20261124" and by["00631L"]["data_time"] == "08:53:00"
    assert by["00646"]["name"] == "元大S&P500"
    assert by["00646"]["price"] == 73.45 and by["00646"]["nav"] == 74.95 and by["00646"]["premium_pct"] == -2.0
    assert by["00646"]["data_at"] == "2026-11-24T08:53:00+08:00"
    assert by["0050"]["name"] == "元大台灣50"
    assert by["0050"]["price"] == 87.0 and by["0050"]["nav"] == 86.57 and by["0050"]["premium_pct"] == 0.5
    assert by["00647L"]["name"] == "元大S&P500正2"
    assert by["00647L"]["price"] == 119.0 and by["00647L"]["nav"] == 124.37 and by["00647L"]["premium_pct"] == -4.32
    # 證交所名單沒有費城半導體正 2 倍，首頁不列這一列
    assert all("費城" not in (s or "") or "正2" not in (s or "") for _, s in EP.TARGETS)


def test_missing_code_and_fields_are_null_not_zero():
    payload = {"a1": [{"msgArray": [
        {"a": "00830", "b": "國泰費城半導體", "e": "-", "f": "", "g": None, "i": "20261124", "j": ""},
        {"a": "00662", "b": "富邦NASDAQ", "e": "21.84", "f": "22.53"},
    ]}]}
    d = EP.build_ok(EP.parse(payload), NOW)
    by = {i["code"]: i for i in d["items"]}
    a = by["00830"]
    assert a["price"] is None and a["nav"] is None and a["premium_pct"] is None
    assert a["data_at"] is None
    assert by["00662"]["premium_pct"] is None  # 缺 g 不自行補 0
    for code in ("00631L", "00670L"):  # 整檔缺
        it = by[code]
        assert it["price"] is None and it["nav"] is None and it["premium_pct"] is None
    assert "00631L" in d["error"] and "00670L" in d["error"]
    txt = json.dumps(d)
    assert '"premium_pct": 0' not in txt and '"price": 0' not in txt


def test_bad_payload_raises():
    for bad in ({}, {"a1": []}, {"a1": [{"msgArray": []}]}, "<html>502</html>"):
        with pytest.raises(ValueError):
            EP.parse(bad)


def test_fetch_failure_keeps_old_values_and_marks_stale(tmp_path):
    out = tmp_path / "etf_premium.json"
    good = EP.run(str(out), fetcher=_fixture)
    assert good["stale"] is False

    def boom():
        raise RuntimeError("502 Bad Gateway")

    d = EP.run(str(out), fetcher=boom)
    assert d["stale"] is True
    assert "502" in d["error"]
    assert d["fetched_at"] == good["fetched_at"]
    assert d["attempted_at"]
    assert d["items"] == good["items"]
    saved = json.loads(out.read_text(encoding="utf-8"))
    by = {i["code"]: i for i in saved["items"]}
    assert saved["stale"] is True and by["00631L"]["premium_pct"] == -12.66


def test_fetch_failure_without_old_file_writes_null_shell(tmp_path):
    out = tmp_path / "etf_premium.json"

    def boom():
        raise RuntimeError("502")

    d = EP.run(str(out), fetcher=boom)
    assert d["stale"] is True and d["fetched_at"] is None
    assert [i["code"] for i in d["items"]] == CODES
    for it in d["items"]:
        assert it["price"] is None and it["nav"] is None and it["premium_pct"] is None
    assert out.exists()


def test_partial_upstream_keeps_old_row_marked_stale(tmp_path):
    out = tmp_path / "etf_premium.json"
    EP.run(str(out), fetcher=_fixture)
    partial = _fixture()
    partial["a1"] = partial["a1"][:2]  # 少了元大那組
    d = EP.run(str(out), fetcher=lambda: partial)
    by = {i["code"]: i for i in d["items"]}
    assert by["00631L"]["premium_pct"] == -12.66 and by["00631L"]["stale"] is True
    assert "00631L" in d["error"]


def test_committed_json_shape():
    d = json.loads((ROOT / "data" / "etf_premium.json").read_text(encoding="utf-8"))
    assert [i["code"] for i in d["items"]] == CODES
    assert "all_etf.txt" in d["source_url"]
    for it in d["items"]:
        for k in ("price", "nav", "premium_pct"):
            assert it[k] is None or isinstance(it[k], float)


# ---------- 首頁 ----------

def _home():
    return HOME.read_text(encoding="utf-8")


def test_home_has_block_between_lights_and_idx():
    h = _home()
    assert 'id="etf-premium"' in h
    assert h.find('id="lights"') < h.find('id="etf-premium"') < h.find('aria-label="指數走勢"') < h.find('id="idx"')
    # 既有順序不變
    assert h.find('id="idx"') < h.find('id="home-nav"') < h.find('id="pe-charts"') < h.find("<footer>")


def test_home_js_codes_labels_and_sources():
    h = _home()
    i = h.find("ETF 溢折價：讀 GitHub Actions")
    js = h[i:h.find("</script>", i)]
    for code in CODES:
        assert f'"{code}"' in js
    assert '"00630L"' not in h
    assert "溢價" in js and "折價" in js and "平盤" in js
    assert "暫無資料" in js
    assert "https://raw.githubusercontent.com/manolomoo/charity-poker-king/main/data/etf_premium.json" in js
    assert '"data/etf_premium.json"' in js
    assert 'cache:"no-store"' in js
    assert "localStorage" in js
    assert "非交易時段，以下為最近一次預估淨值" in js
    assert "盤中快取" in js
    assert "mis.twse.com.tw" not in js  # 不直連（無 CORS），也不走第三方 proxy
    assert "corsproxy" not in h and "allorigins" not in h
    assert "臺灣證券交易所彙整各投信預估淨值；本站交易時段約每小時更新（GitHub 排程可能延遲）" in h
    # 同時抓 raw 與同源，用較新的 fetched_at；網路成功時不優先用 localStorage
    assert "Promise.all([settle(get(RAW)), settle(get(LOCAL))])" in js
    assert "fetchedMs" in js
    assert "if(old && fetchedMs(old) > fetchedMs(net))" in js
    assert 'addEventListener("pageshow"' in js and "e.persisted" in js
    assert 'addEventListener("visibilitychange"' in js
    assert "visibilityState === \"visible\"" in js
    assert "POLL_MS = 600000" in js
    assert 'id="etfp-refresh"' in h and ">更新</button>" in h
    assert "load({ force: true })" in js
    assert "更新中…" in js
    assert "workflow_dispatch" not in js and "actions/workflows" not in js


def test_home_css_uses_tokens():
    h = _home()
    style = h[h.find("<style>"):h.find("</style>")]
    assert ".etfp .pm.prem{color:var(--warn)}" in style
    assert ".etfp .pm.disc{color:var(--up)}" in style
    assert "min-height:44px" in style  # 更新鈕拇指可點
    assert ".etfp-head{display:flex" in style


# ---------- workflow ----------

def test_workflow_schedule_and_scope():
    wf = WF.read_text(encoding="utf-8")
    crons = re.findall(r'cron:\s*"([^"]+)"', wf)
    # 已於 ac2faff 改為每小時 7/17/27… 分，避開整點尖峰
    assert crons == ["7,17,27,37,47,57 1-4 * * 1-5", "7,17,27 5 * * 1-5", "7 6 * * 1-5"]
    assert "workflow_dispatch" in wf
    assert "python scripts/etf_premium.py" in wf
    assert "git add data/etf_premium.json" in wf
    assert "git pull --rebase origin main" in wf
    for bad in ("run.sh", "update_trend.py", "deploy-pages", "upload-pages-artifact", "build_site.sh", "pages: write"):
        assert bad not in wf, bad


def test_existing_pipeline_schedule_unchanged():
    wf = (ROOT / ".github" / "workflows" / "update-and-deploy.yml").read_text(encoding="utf-8")
    crons = re.findall(r'cron:\s*"([^"]+)"', wf)
    assert crons == ["30 22 * * 0-4", "0 7 * * 1-5", "0 14 * * 1-5", "45 14 * * 1-5"]


def test_signal_rule_files_untouched():
    files = ["rules.json", "rules.md", "scripts/signals_v2.py", "scripts/margin_ratio.py", "scripts/run.py"]
    try:
        r = subprocess.run(["git", "diff", "--name-only", "HEAD", "--", *files],
                           cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("沒有 git")
    assert r.stdout.strip() == ""
    for f in files[2:]:
        assert "etf_premium" not in (ROOT / f).read_text(encoding="utf-8")
