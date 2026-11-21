"""估值頁改版：一句話＋大圖、事件留在圖上與圖例；詳細數據收進 details。不碰燈號規則。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "trend" / "valuation" / "index.html"
HOME = ROOT / "home" / "index.html"

EVENTS = [
    ("2015-08", "中國股災+美國預期升息"),
    ("2016-02", "美國升息+中國股災"),
    ("2018-12", "中美貿易戰加劇"),
    ("2020-03", "疫情風暴"),
    ("2022-10", "暴力升息+庫存調整"),
    ("2023-10", "殖利率倒掛收斂+美債信評降評"),
    ("2024-08", "日圓套利平倉+薩姆法則觸發"),
    ("2025-04", "對等關稅"),
    ("2026-03", "美以伊戰爭+油價失控"),
]


def _html() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_title_and_anchors_preserved():
    html = _html()
    assert "<title>慈善撲克王｜Forward P/E 估值位置</title>" in html
    for aid in ("now", "sox", "ndx", "events", "memo", "caveat"):
        assert f'id="{aid}"' in html
    assert '<a href="../../">首頁</a>' in html
    assert "僅供參考，非投資建議。" in html


def test_front_has_headline_bars_chart_and_event_legend():
    html = _html()
    assert 'id="headline"' in html
    assert 'id="bars"' in html
    assert 'id="main-chart"' in html
    assert 'id="event-legend"' in html
    assert "pbar-track" in html or "paintBars" in html
    assert "這是位置描述，不是買賣建議。" in html
    # 事件圖例在 details 之外
    front, details = html.split('id="details"', 1)
    assert 'id="event-legend"' in front
    assert 'id="main-chart"' in front
    assert 'id="headline"' in front
    assert "event-legend" not in details.split("event-tables")[0]


def test_details_keep_all_existing_data_blocks():
    html = _html()
    for phrase in (
        "這些數字怎麼算",
        "資料來源與更新時間",
        "口徑差異",
        "世界重大事件對照表",
        "完整月線數字表",
        "名詞小辭典",
        "使用限制與免責",
        "COBB MEMO 快照",
        "該月最低 P/E",
        "核對 MEMO 當日讀數是否像資料異常",
        "11 項燈號 v3.0 第 4 項規則沒有改",
        "不要把 HOM 現值拿去比 MEMO 的 σ 帶",
        "不一定代表便宜",
        "本頁不是另一顆燈",
    ):
        assert phrase in html, phrase
    # 事件名稱都還在頁面（圖例用資料動態產出，表格也會列出 label）
    assert "e.label" in html
    assert "eventsInWindow" in html
    # 完整事件表仍在 details
    assert 'id="event-tables"' in html.split('id="events"', 1)[1]


def test_chart_uses_css_pixels_not_scaled_900x340():
    html = _html()
    assert "clientWidth" in html
    assert "font-size=\"12\"" in html or "font-size='12'" in html
    # 不再用固定 900×340 當唯一 viewBox 再等比例縮到手機寬
    assert "var W=900, H=340" not in html
    assert "h: wide ? 440 : 340" in html
    assert "barPlace" in html and "(z+3)/6" in html


def test_homepage_not_modified_by_this_page():
    home = HOME.read_text(encoding="utf-8")
    assert "<title>慈善撲克王大賽</title>" in home
    assert '<a href="./trend/valuation/">估值</a>' in home or 'href="./trend/valuation/"' in home


def test_helper_math_bar_and_events(tmp_path):
    """用 Node 直接跑頁面上的純函式邏輯（複製對應片段）。"""
    html = _html()
    # 抽出 eventsInWindow / barPlace / ymSlash
    js = r"""
    function ymSlash(ym){ var p=String(ym).split("-"); return p[0]+"/"+(p[1]||""); }
    function eventsInWindow(events, start){
      var rows = (events||[]).filter(function(ev){
        return ev && ev.available !== false && ev.ym && (!start || ev.ym >= start);
      }).slice().sort(function(a,b){ return String(a.ym).localeCompare(String(b.ym)); });
      return rows.map(function(ev, i){
        return { n:i+1, ym:ev.ym, label:ev.label||"", short:ev.short||ev.label||"" };
      });
    }
    function barPlace(z){
      if(typeof z !== "number" || !isFinite(z)) return {left:50, pin:"none", z:null};
      if(z < -3) return {left:0, pin:"low", z:z};
      if(z > 3) return {left:100, pin:"high", z:z};
      return {left: (z+3)/6*100, pin:"none", z:z};
    }
    const ev = [
      {ym:"2015-08", label:"中國股災+美國預期升息", available:true},
      {ym:"2020-03", label:"疫情風暴", available:true},
      {ym:"2022-10", label:"暴力升息+庫存調整", available:true},
      {ym:"2025-04", label:"對等關稅", available:true},
      {ym:"2026-03", label:"美以伊戰爭+油價失控", available:true}
    ];
    const w2016 = eventsInWindow(ev, "2016-01");
    const w2020 = eventsInWindow(ev, "2020-01");
    if (w2016[0].ym !== "2020-03" || w2016[0].n !== 1) throw new Error("2016 window first");
    if (w2016.some(e => e.ym === "2015-08")) throw new Error("2015 should drop");
    if (w2020.length !== 4) throw new Error("2020 count "+w2020.length);
    if (ymSlash("2020-03") !== "2020/03") throw new Error("slash");
    const a = barPlace(-1.71);
    if (a.pin !== "none") throw new Error("pin");
    if (Math.abs(a.left - ((-1.71+3)/6*100)) > 1e-9) throw new Error("left");
    if (a.left < 15 || a.left > 30) throw new Error("visible left "+a.left);
    const b = barPlace(-3.32);
    if (b.pin !== "low" || b.left !== 0) throw new Error("clamp low");
    console.log("ok", a.left, w2016.map(e=>e.n+e.label).join("|"));
    """
    # 確認頁面裡的函式本文與測試用複本一致（關鍵公式）
    assert "if(z < -3) return {left:0, pin:\"low\", z:z};" in html
    assert "return {left: (z+3)/6*100, pin:\"none\", z:z};" in html
    assert "ev.ym >= start" in html
    import subprocess
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ok" in r.stdout


def test_no_signal_rule_files_touched_by_page_only():
    # 這個測試只鎖定估值頁沒去引用或改寫規則檔路徑作為可改來源
    html = _html()
    assert "rules.json" not in html
    assert "signals_v2" not in html
    assert "margin_ratio" not in html
