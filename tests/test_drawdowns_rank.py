"""本波下跌在歷史跌幅列表中的名次與排序（只測顯示邏輯，不改歷史資料）。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "trend" / "drawdowns" / "index.html"
DATA = ROOT / "trend" / "drawdowns" / "drawdowns.json"

# 名次與差距以原始 change_pct 計算後四捨五入到小數兩位；與 drawdowns.json 目前資料對照（asof 2026-11-23；SOX 本波低點仍是 2026-11-20）。
EXPECTED = {
    "SOX": {
        "rank": 4,
        "total": 16,
        "prev": "通膨失控與美國暴力升息",
        "next": "川普MAGA新政，關稅風暴席捲全球",
        "gap_prev": "5.55",
        "gap_next": "0.20",
        "asof": "2026-11-23",
    },
    "NDX": {
        "rank": 7,
        "total": 9,
        "prev": "中美貿易戰加劇",
        "next": "中國上證股災、美國升息預期",
        "gap_prev": "2.88",
        "gap_next": "1.25",
        "asof": "2026-11-23",
    },
    "SPX": {
        "rank": 10,
        "total": 10,
        "prev": "中國上證股災、美國升息預期",
        "next": None,
        "gap_prev": "0.28",
        "gap_next": None,
        "asof": "2026-11-23",
    },
    "TWII": {
        "rank": 7,
        "total": 13,
        "prev": "川普MAGA新政，關稅風暴席捲全球",
        "next": "歐債危機、美債遭降評",
        "gap_prev": "0.05",
        "gap_next": "0.75",
        "asof": "2026-11-23",
    },
}


def _extract_helpers(html: str) -> str:
    start = html.index("function waveOf")
    end = html.index("// --- end rank helpers ---")
    block = html[start:end]
    # 句子／HTML 輔助函式會呼叫頁面上的 pctSigned／esc／asofLine；測排序時給最小 stub。
    stubs = """
function pctSigned(x, d){
  d = d==null ? 1 : d;
  var n = x*100, s = Math.abs(n).toFixed(d);
  if(parseFloat(s)===0) return "0%";
  return (n<0?"−":"+") + s + "%";
}
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/\"/g,"&quot;"); }
function asofLine(c){
  if(!c) return "";
  return c.asof ? ("資料截至 " + c.asof) : "";
}
"""
    return stubs + block + """
module.exports = {
  waveOf, isCurrentEvent, ppAbs, ppText, cmpDepth, cmpTime,
  listWithCurrent, orderedEvents, rankOf, rankHint, rankCompareSentence,
  rankBoxHtml, currentSubLines
};
"""


def _run_node(expr: str, helpers: str, payload: dict | None = None) -> dict:
    script = helpers + "\n"
    if payload is not None:
        script += "const DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n"
    script += "const result = " + expr + ";\nconsole.log(JSON.stringify(result));\n"
    proc = subprocess.run(
        ["node", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}\n{proc.stdout}")
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def helpers() -> str:
    return _extract_helpers(PAGE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_page_wires_rank_into_bars_table_and_summary():
    html = PAGE.read_text(encoding="utf-8")
    assert "function rankOf" in html and "function orderedEvents" in html
    assert "function currentSubLines" in html and "function rankBoxHtml" in html
    assert "ordered(ix).forEach" in html
    assert "var ev = ordered(ix)" in html
    assert "items.push" not in html
    assert "rankBoxHtml(rankOf(ix))" in html
    assert "rankCompareSentence(rk)" in html
    assert "目前排第" in html
    assert "個百分點" in html
    assert "跌幅可能繼續擴大" in html
    assert "pill-now" in html and "進行中" in html
    assert "依跌幅排序時本波會插入目前名次" in html
    # 反彈區塊的排序函式仍獨立，不跟本波跌幅名次混用
    assert "function paintRebound" in html
    rb = html[html.index("function paintRebound"):html.index("function paint()")]
    assert "rankOf" not in rb and "orderedEvents" not in rb


def test_sox_current_is_about_43_percent_fourth(payload):
    """使用者說 SOX 本波約 −43%、第四名：以 drawdowns.json 實際值核對，不捏造。"""
    sox = next(ix for ix in payload["indices"] if ix["id"] == "SOX")
    cur = sox["current"]
    assert cur["asof"] == "2026-11-23"
    assert cur["recovered"] is False
    # −43.08%（顯示兩位小數），不是以前的 −38.61%
    assert cur["change_pct"] == pytest.approx(-0.4307714142811232, rel=0, abs=1e-12)
    assert cur["start_date"] == "2026-06-22" and cur["end_date"] == "2026-11-20"


def test_pp_rounds_from_raw_not_displayed(helpers, payload):
    # 顯示 −43.08% 與 −42.87% 相減是 0.21；差距要以原始值相減再四捨五入，是 0.20
    sox = next(ix for ix in payload["indices"] if ix["id"] == "SOX")
    cur = sox["current"]["change_pct"]
    trump = next(e for e in sox["events"] if "川普" in e["name"])["change_pct"]
    out = _run_node(
        "{a: ppText(DATA.cur, DATA.trump), b: ppAbs(DATA.cur, DATA.trump), shown: (Math.round(DATA.cur*10000)/100) - (Math.round(DATA.trump*10000)/100)}",
        helpers,
        {"cur": cur, "trump": trump},
    )
    assert out["a"] == "0.20"
    assert out["b"] == 0.2
    assert abs(out["shown"] - (-0.21)) < 1e-9


def test_live_ranks_and_neighbors(helpers, payload):
    expr = """DATA.indices.map(function(ix){
      var rk = rankOf(ix);
      return {
        id: ix.id,
        asof: ix.current && ix.current.asof,
        rank: rk && rk.rank,
        total: rk && rk.total,
        prev: rk && rk.prev && rk.prev.name,
        next: rk && rk.next && rk.next.name,
        gap_prev: rk && rk.prev ? ppText(rk.wave.change_pct, rk.prev.change_pct) : null,
        gap_next: rk && rk.next ? ppText(rk.wave.change_pct, rk.next.change_pct) : null
      };
    })"""
    rows = {r["id"]: r for r in _run_node(expr, helpers, payload)}
    assert set(rows) >= set(EXPECTED)
    for idx_id, exp in EXPECTED.items():
        got = rows[idx_id]
        assert got["asof"] == exp["asof"], idx_id
        assert got["rank"] == exp["rank"], idx_id
        assert got["total"] == exp["total"], idx_id
        assert got["prev"] == exp["prev"], idx_id
        assert got["next"] == exp["next"], idx_id
        assert got["gap_prev"] == exp["gap_prev"], idx_id
        assert got["gap_next"] == exp["gap_next"], idx_id


def test_size_sort_inserts_current_at_rank(helpers, payload):
    expr = """DATA.indices.map(function(ix){
      var size = orderedEvents(ix, "size");
      var time = orderedEvents(ix, "time");
      var rk = rankOf(ix);
      function names(xs){ return xs.map(function(e){ return isCurrentEvent(e) ? "CURRENT" : e.name; }); }
      return {
        id: ix.id,
        size: names(size),
        timeLast: names(time)[names(time).length-1],
        sizePos: names(size).indexOf("CURRENT") + 1,
        histCount: ix.events.length,
        sizeLen: size.length,
        timeLen: time.length,
        rank: rk.rank
      };
    })"""
    rows = _run_node(expr, helpers, payload)
    for r in rows:
        assert r["sizeLen"] == r["histCount"] + 1
        assert r["timeLen"] == r["histCount"] + 1
        assert r["timeLast"] == "CURRENT", r["id"]
        assert r["sizePos"] == r["rank"], r["id"]
        # 歷史列順序以外，本波不可被釘在依跌幅清單的最後（除非它真的最淺）
        if r["rank"] != r["sizeLen"]:
            assert r["sizePos"] != r["sizeLen"], r["id"]
    sox = next(r for r in rows if r["id"] == "SOX")
    assert sox["sizePos"] == 4
    assert sox["size"][2] == "通膨失控與美國暴力升息"
    assert sox["size"][3] == "CURRENT"
    assert sox["size"][4] == "川普MAGA新政，關稅風暴席捲全球"
    spx = next(r for r in rows if r["id"] == "SPX")
    assert spx["sizePos"] == 10 and spx["size"][-1] == "CURRENT"


def test_tie_puts_historical_before_current(helpers):
    ix = {
        "events": [
            {"name": "A更深", "change_pct": -0.50, "start_date": "2008-01-01", "end_date": "2008-12-01"},
            {"name": "B同分", "change_pct": -0.20, "start_date": "2020-01-01", "end_date": "2020-03-01"},
            {"name": "C較淺", "change_pct": -0.10, "start_date": "2022-01-01", "end_date": "2022-06-01"},
        ],
        "current": {
            "seq": "現在",
            "name": "現在本波下跌",
            "change_pct": -0.20,
            "start_date": "2026-01-01",
            "end_date": "2026-11-20",
            "recovered": False,
        },
    }
    out = _run_node(
        """(function(){
          var size = orderedEvents(DATA, "size").map(function(e){ return e.name; });
          var rk = rankOf(DATA);
          return {size: size, rank: rk.rank, total: rk.total, prev: rk.prev.name, next: rk.next.name, gap: ppText(rk.wave.change_pct, rk.prev.change_pct)};
        })()""",
        helpers,
        ix,
    )
    assert out["size"] == ["A更深", "B同分", "現在本波下跌", "C較淺"]
    assert out["rank"] == 3 and out["total"] == 4
    assert out["prev"] == "B同分" and out["next"] == "C較淺"
    assert out["gap"] == "0.00"


def test_deepest_and_recovered_have_no_neighbor_or_rank(helpers):
    deepest = {
        "events": [
            {"name": "舊", "change_pct": -0.10, "start_date": "2020-01-01", "end_date": "2020-03-01"},
        ],
        "current": {
            "seq": "現在",
            "name": "現在本波下跌",
            "change_pct": -0.40,
            "start_date": "2026-01-01",
            "end_date": "2026-11-20",
            "recovered": False,
        },
    }
    recovered = {
        "events": [
            {"name": "舊", "change_pct": -0.10, "start_date": "2020-01-01", "end_date": "2020-03-01"},
        ],
        "current": {
            "seq": "現在",
            "name": "現在本波下跌",
            "change_pct": -0.40,
            "recovered": True,
            "high_date": "2026-11-20",
        },
    }
    deep = _run_node(
        """(function(){
          var rk = rankOf(DATA);
          var size = orderedEvents(DATA, "size").map(function(e){ return e.name; });
          return {rank: rk.rank, total: rk.total, prev: rk.prev, next: rk.next && rk.next.name, size: size, hint: rankHint(rk), box: rankBoxHtml(rk)};
        })()""",
        helpers,
        deepest,
    )
    assert deep["rank"] == 1 and deep["total"] == 2 and deep["prev"] is None
    assert deep["next"] == "舊"
    assert deep["size"][0] == "現在本波下跌"
    assert "目前最深的一次" in deep["hint"]
    assert "上一名：無" in deep["box"]

    rec = _run_node(
        """(function(){
          return {rk: rankOf(DATA), size: orderedEvents(DATA, "size").map(function(e){ return e.name; }), wave: waveOf(DATA)};
        })()""",
        helpers,
        recovered,
    )
    assert rec["rk"] is None and rec["wave"] is None
    assert rec["size"] == ["舊"]


def test_compare_sentence_for_sox_and_spx(helpers, payload):
    expr = """(function(){
      var sox = DATA.indices.filter(function(x){return x.id==="SOX";})[0];
      var spx = DATA.indices.filter(function(x){return x.id==="SPX";})[0];
      return {sox: rankCompareSentence(rankOf(sox)), spx: rankCompareSentence(rankOf(spx))};
    })()"""
    out = _run_node(expr, helpers, payload)
    assert "目前排第 4 名／共 16 次" in out["sox"]
    assert "比上一名「通膨失控與美國暴力升息」（−48.63%）淺 5.55 個百分點" in out["sox"]
    assert "比下一名「川普MAGA新政，關稅風暴席捲全球」（−42.87%）深 0.20 個百分點" in out["sox"]
    assert "尚未創新高，跌幅可能繼續擴大。" in out["sox"]
    assert "目前排第 10 名／共 10 次，是目前最淺的一次" in out["spx"]
    assert "比上一名「中國上證股災、美國升息預期」" in out["spx"]
    assert "淺 0.28 個百分點" in out["spx"]
    assert "下一名" not in out["spx"]
