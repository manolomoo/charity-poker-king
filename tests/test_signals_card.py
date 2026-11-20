"""燈號頁收合卡片顯示目前數值。不碰燈號規則 v3.0。"""
from __future__ import annotations

import json

import pytest
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "assets" / "app.js"
STYLE = ROOT / "assets" / "style.css"
LATEST = ROOT / "data" / "latest.json"
RUN_PY = ROOT / "scripts" / "run.py"
MARGIN_RATIO = ROOT / "scripts" / "margin_ratio.py"
SIGNALS_V2 = ROOT / "scripts" / "signals_v2.py"
RULES = ROOT / "rules.json"


def test_app_js_reads_extra_value_not_hardcoded():
    js = APP.read_text(encoding="utf-8")
    assert "121.48" not in js
    assert "121.477" not in js
    assert "extra.value" in js or "ex.value" in js
    assert "data_date" in js
    assert "fmtFixed(ex.value, 2)" in js
    assert "自算值通常比玩股網高約 0.5～3.3 個百分點" in js
    assert "function currentSnapshot" in js
    assert "sig-now-note" in js
    # 規則腳本不會被這個顯示層 import
    for p in (RUN_PY, MARGIN_RATIO, SIGNALS_V2):
        txt = p.read_text(encoding="utf-8")
        assert "currentSnapshot" not in txt
        assert "sig-now" not in txt


def test_style_has_full_width_now_line():
    css = STYLE.read_text(encoding="utf-8")
    assert ".signal-card summary .sig-now" in css
    assert "flex: 1 1 100%" in css
    assert ".sig-now-note" in css
    assert "var(--ink-faint)" in css


def test_rules_thresholds_unchanged():
    rules = json.loads(RULES.read_text(encoding="utf-8"))
    assert rules["signals"]["6"]["trigger"] == "< 130%"
    assert rules["signals"]["6"]["near"] == "130% ≤ 維持率 < 140%"
    assert rules["signals"]["6"]["params"]["trigger"] == 130


def _judging(rows):
    out = []
    for row in rows:
        thr = row.get("threshold") or ""
        if "不參與" in thr or "不納入" in thr:
            continue
        if "參考" in (row.get("target") or ""):
            continue
        out.append(row)
    return out


def _short_md(iso):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    if not m:
        return ""
    return f"{int(m.group(2))}/{int(m.group(3))}"


def expected_from_latest():
    data = json.loads(LATEST.read_text(encoding="utf-8"))
    by = {}
    for row in data["rows"]:
        by.setdefault(str(row["signal"]), []).append(row)
    exp = {}
    r6 = _judging(by["6"])[0]
    val = float(r6["extra"]["value"])
    date = _short_md(r6["data_date"])
    thr = re.sub(r"（接近：[^）]*）", "", r6["threshold"] or "")
    thr = re.sub(r"\s+", " ", thr).strip()
    exp["6"] = f"目前：{val:.2f}%（{date}，自算）｜門檻 {thr}"
    exp["6_note"] = "自算值通常比玩股網高約 0.5～3.3 個百分點"
    r2 = _judging(by["2"])[0]
    score = float(r2["extra"]["last_close"])
    exp["2"] = f"目前：{score:.1f}（{_short_md(r2['data_date'] or r2['extra'].get('last_date'))}）"
    parts = []
    d5 = ""
    for r in _judging(by["5"]):
        parts.append(f"{r['target']} {float(r['extra']['close']):.2f}")
        d5 = d5 or _short_md(r["data_date"])
    exp["5"] = "目前：" + "、".join(parts) + (f"（{d5}）" if d5 else "")
    r7 = _judging(by["7"])[0]
    yi = f"{round(float(r7['extra']['total_100m'])):,}"
    dd = float(r7["extra"]["dd_pct"])
    dd_txt = f"{dd:+.1f}%" if dd != 0 else "0.0%"
    # Python format for negative already has minus; we used + for positive
    if dd < 0:
        dd_txt = f"{dd:.1f}%"
    exp["7"] = f"目前：{yi} 億（{_short_md(r7['data_date'])}）｜較 120 日高點 {dd_txt}"
    return exp


def test_expected_sig6_matches_user_example_shape():
    exp = expected_from_latest()
    assert re.match(r"目前：\d+\.\d{2}%（\d{1,2}/\d{1,2}，自算）｜門檻 < 130%$", exp["6"])
    # 用目前 latest 核對使用者舉的 11/20 例子（若資料日期已變就只檢查格式）
    data = json.loads(LATEST.read_text(encoding="utf-8"))
    r6 = _judging([r for r in data["rows"] if r["signal"] == "6"])[0]
    if r6["data_date"] == "2026-11-20" and abs(float(r6["extra"]["value"]) - 121.4771958118148) < 1e-9:
        assert exp["6"] == "目前：121.48%（11/20，自算）｜門檻 < 130%"


def test_collapsed_cards_show_current_values():
    import socket
    import threading
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    exp = expected_from_latest()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def log_message(self, format, *args):
            return

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()
    httpd = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/index.html"
    try:
      with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 390, "height": 900}, device_scale_factor=2)
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_selector("#signal-6 summary .sig-now-line", timeout=15000)
        # 收合狀態（不要被 hash 自動展開干擾）
        page.evaluate("document.querySelectorAll('details.signal-card').forEach(d => d.open = false)")
        got = page.evaluate(
            """() => {
              const read = id => {
                const el = document.querySelector('#signal-' + id);
                const line = el.querySelector('.sig-now-line');
                const note = el.querySelector('.sig-now-note');
                const open = el.open;
                const w = document.documentElement.clientWidth;
                const wide = [el, ...el.querySelectorAll('*')].filter(n => n.scrollWidth > w + 1)
                  .map(n => [n.tagName, n.className, n.scrollWidth]);
                return {text: line ? line.textContent : null, note: note ? note.textContent : null, open, wide};
              };
              return {s6: read(6), s2: read(2), s5: read(5), s7: read(7)};
            }"""
        )
        browser.close()
    finally:
        httpd.shutdown()
    assert got["s6"]["open"] is False
    assert got["s6"]["text"] == exp["6"]
    assert got["s6"]["note"] == exp["6_note"]
    assert got["s6"]["wide"] == []
    assert got["s2"]["text"] == exp["2"]
    assert got["s5"]["text"] == exp["5"]
    assert got["s7"]["text"] == exp["7"]
    assert got["s2"]["wide"] == [] and got["s5"]["wide"] == [] and got["s7"]["wide"] == []
