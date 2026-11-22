"""歷史跌幅列表改為 Cobb 風格：本波卡、固定 0%～−100% 軸、紅刻度、深淺分色；本波黃棒保留。"""
from __future__ import annotations

from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "trend" / "drawdowns" / "index.html"


def _html() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_list_has_now_card_legend_fixed_axis_and_yellow_bar():
    html = _html()
    assert 'id="dd-now"' in html and "function paintNowCard" in html
    assert 'id="dd-leg"' in html and 'id="bars"' in html and 'id="sort"' in html
    assert "本波跌到這裡" in html
    assert "跌得比本波深" in html
    assert "比本波淺" in html
    assert "本波（進行中）" in html
    assert "條長固定以 0%～−100% 為尺，越長跌越深。" in html
    assert 'id="dd-fn"' in html
    # 列表固定 0%～−100% 軸，不再依最深跌幅縮放（時間軸示意仍可用 axisMin）
    assert '"0%"' in html and '"−100%"' in html
    bars = html[html.index("function paintBars"):html.index("function paintTimeline")]
    assert "axisMin" not in bars
    assert "shownAbsPct(e.change_pct)" in bars
    # 紅刻度對齊本波深度；深／淺用顯示到兩位小數後的絕對值比較
    assert "dd-tick" in html and "--tick" in html
    assert "function shownAbsPct" in html and "function barKind" in html
    assert 'return "now"' in html
    assert 'return shownAbsPct(e.change_pct) > shownAbsPct(now.change_pct) ? "deep" : "shallow"' in html
    # 本波黃棒、黃底列、進行中標籤都還在
    assert 'class="dd-row ' in html and "dd-fill" in html
    assert ".dd-row.now .dd-fill{background:var(--now)}" in html
    assert "rgba(242,181,82,.08)" in html
    assert "pill-now" in html and "進行中" in html
    # 本波卡有跌幅、點數、日曆天、交易日、高點、低點
    for phrase in ("跌幅", "歷時", "高點", "低點", "個交易日", "下跌 "):
        assert phrase in html, phrase
    assert "change_pts" in html and "start_px" in html and "end_px" in html
    # 名次比較仍接在本波卡下面
    assert "rankBoxHtml(rankOf(ix))" in html
    assert "var ev = ordered(ix)" in html
    assert "ordered(ix).forEach" in html


def test_list_uses_html_not_svg_and_keeps_other_sections():
    html = _html()
    # 列表改 HTML/CSS，長事件名可換行
    bars = html[html.index("function paintBars"):html.index("function paintTimeline")]
    assert "createElementNS" not in bars
    assert "text-wrap:pretty" in html or "text-wrap: pretty" in html
    assert "function breakName" in html
    # 時間軸、明細表、反彈對照仍在，列表不再畫平均虛線或 20% 分色
    assert 'id="timeline"' in html and "function paintTimeline" in html
    assert 'id="tbody"' in html and 'id="rebound"' in html
    assert "function paintRebound" in html
    # 舊列表圖例的 20% 分色已拿掉；明細表註解仍可提到紅底＝跌幅達 20%
    assert ">跌幅達 20% 以上<" not in html
    assert "sw soft" not in html
    assert "平均跌幅" in html  # 仍在摘要卡
    # 舊圖例「平均跌幅」虛線只應出現在時間軸用的 --avg，不在列表圖例
    assert "sw avg" not in html
    assert "依跌幅排序時本波會插入目前名次" in html


def test_recovered_wave_has_neutral_style_and_no_tick():
    html = _html()
    assert "已創新高" in html
    assert "目前沒有進行中的下跌可以比較" in html
    assert 'return "neutral"' in html
    # 沒有本波時不畫紅刻度
    bars = html[html.index("function paintBars"):html.index("function paintTimeline")]
    assert "if(tickAt != null)" in bars
    # 摘要四卡不再放 nowbox（本波卡改到列表頂）
    summary = html[html.index("function paintSummary"):html.index("function ordered(ix)")]
    assert "nowbox" not in summary
    assert "歷史下跌次數" in summary
