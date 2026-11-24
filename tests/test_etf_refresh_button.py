"""首頁 ETF「更新」鈕：逾時必結束，不可卡在「更新中…」（iPhone／WebKit）。"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TPE = timezone(timedelta(hours=8))
RAW = "https://raw.githubusercontent.com/manolomoo/charity-poker-king/main/data/etf_premium.json"


def _payload(fetched_at: str) -> dict:
    src = json.loads((ROOT / "data" / "etf_premium.json").read_text(encoding="utf-8"))
    src["fetched_at"] = fetched_at
    src["stale"] = False
    src["error"] = None
    return src


class _Server:
    def __init__(self, root: Path):
        self.root = root

        class H(SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(root), **k)

            def log_message(self, *a):  # noqa: ARG002
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _prep(tmp_path: Path, payload: dict) -> _Server:
    (tmp_path / "index.html").write_text(
        (ROOT / "home" / "index.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "etf_premium.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return _Server(tmp_path)


def _launch():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    engine = "webkit"
    try:
        browser = pw.webkit.launch(headless=True)
    except Exception as exc:
        try:
            browser = pw.chromium.launch(headless=True, channel="chrome")
            engine = "chrome"
        except Exception:
            pw.stop()
            pytest.skip(f"Playwright WebKit／Chrome 都開不起來: {exc}")
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844},
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        locale="zh-TW",
        timezone_id="Asia/Taipei",
    )
    return pw, browser, ctx, engine


def _fulfill(route, payload):
    route.fulfill(
        status=200,
        content_type="application/json; charset=utf-8",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"},
        body=json.dumps(payload, ensure_ascii=False),
    )


def _hang(route):
    # 故意不 fulfill／abort，模擬 iOS 上請求一直掛住、abort 也不結束
    return


@pytest.fixture
def browser_env():
    pw, browser, ctx, engine = _launch()
    yield ctx, engine
    ctx.close()
    browser.close()
    pw.stop()


def test_refresh_button_already_latest_feedback(tmp_path, browser_env):
    ctx, engine = browser_env
    fetched = "2026-11-24T11:34:12+08:00"
    payload = _payload(fetched)
    srv = _prep(tmp_path, payload)
    try:
        page = ctx.new_page()
        hanging = {"on": False}

        def on_raw(route):
            if route.request.method == "OPTIONS":
                route.fulfill(
                    status=204,
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET",
                        "Access-Control-Allow-Headers": "*",
                    },
                )
                return
            if hanging["on"]:
                return _hang(route)
            _fulfill(route, payload)

        page.route("https://raw.githubusercontent.com/**", on_raw)
        page.goto(srv.url, wait_until="domcontentloaded")
        page.wait_for_function(
            "() => document.getElementById('etfp-when') && "
            "document.getElementById('etfp-when').textContent.indexOf('本站抓取') >= 0",
            timeout=15000,
        )
        # 43 分鐘未更新不應再警告（門檻已改 75 分鐘）；僅在盤中斷言
        now = datetime.now(TPE)
        minutes = now.hour * 60 + now.minute
        if now.weekday() < 5 and 540 <= minutes <= 810:
            when = page.inner_text("#etfp-when")
            assert "快取已" not in when and "未更新" not in when, when

        btn = page.locator("#etfp-refresh")
        assert btn.inner_text() == "更新"
        btn.click()
        page.wait_for_function(
            "() => document.getElementById('etfp-refresh').textContent !== '更新'",
            timeout=3000,
        )
        # 同一份資料：應出現「已是最新（11:34），下次約 12:34」
        page.wait_for_function(
            "() => document.getElementById('etfp-refresh').textContent !== '更新中…'",
            timeout=12000,
        )
        label = btn.inner_text()
        fb = page.inner_text("#etfp-fb")
        assert label in ("已是最新", "更新", "已更新"), (label, fb, engine)
        assert "已是最新（11:34），下次約 12:34" in fb or "目前已是最新資料" in fb or "已更新" in fb, (label, fb)
        page.wait_for_function(
            "() => document.getElementById('etfp-refresh').textContent === '更新' "
            "&& !document.getElementById('etfp-fb').textContent",
            timeout=4000,
        )
        assert btn.get_attribute("aria-busy") in (None, "false")
        assert not btn.is_disabled()
    finally:
        srv.close()


def test_refresh_button_recovers_when_request_hangs(tmp_path, browser_env):
    """兩邊請求都掛住時，約 8 秒逾時後鈕必須離開「更新中…」，in-flight 不得卡住。"""
    ctx, engine = browser_env
    fetched = "2026-11-24T11:34:12+08:00"
    payload = _payload(fetched)
    srv = _prep(tmp_path, payload)
    try:
        page = ctx.new_page()
        hanging = {"on": False}

        def on_raw(route):
            if route.request.method == "OPTIONS":
                route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "*"})
                return
            if hanging["on"]:
                return _hang(route)
            _fulfill(route, payload)

        def on_local(route):
            if hanging["on"]:
                return _hang(route)
            _fulfill(route, payload)

        page.route("https://raw.githubusercontent.com/**", on_raw)
        page.route("**/data/etf_premium.json*", on_local)
        page.goto(srv.url, wait_until="domcontentloaded")
        page.wait_for_function(
            "() => document.getElementById('etfp-when') && "
            "document.getElementById('etfp-when').textContent.indexOf('本站抓取') >= 0",
            timeout=15000,
        )
        btn = page.locator("#etfp-refresh")
        assert btn.inner_text() == "更新"

        hanging["on"] = True
        t0 = datetime.now()
        btn.click()
        page.wait_for_function(
            "() => document.getElementById('etfp-refresh').textContent === '更新中…'",
            timeout=3000,
        )
        page.wait_for_function(
            "() => document.getElementById('etfp-refresh').textContent !== '更新中…'",
            timeout=12000,
        )
        elapsed = (datetime.now() - t0).total_seconds()
        label = btn.inner_text()
        fb = page.inner_text("#etfp-fb")
        assert elapsed < 11.5, f"逾時後鈕仍未復原：{elapsed:.1f}s {label!r} {fb!r} ({engine})"
        assert elapsed >= 7.0, f"逾時過早：{elapsed:.1f}s ({engine})"
        assert label != "更新中…", (label, fb, engine)
        assert label in ("已是最新", "已更新", "更新"), (label, fb, engine)
        # 仍是同一份 11:34 資料
        assert "已是最新（11:34），下次約 12:34" in fb or "目前已是最新資料" in fb, (label, fb)
        page.wait_for_function(
            "() => document.getElementById('etfp-refresh').textContent === '更新'",
            timeout=4000,
        )
        # in-flight 已清掉：再點一次（仍掛住）還能再進入「更新中…」
        btn.click()
        page.wait_for_function(
            "() => document.getElementById('etfp-refresh').textContent === '更新中…'",
            timeout=3000,
        )
        page.wait_for_function(
            "() => document.getElementById('etfp-refresh').textContent !== '更新中…'",
            timeout=12000,
        )
        assert page.inner_text("#etfp-refresh") != "更新中…"
    finally:
        srv.close()


def test_refresh_shows_updated_when_newer_copy_arrives(tmp_path, browser_env):
    ctx, engine = browser_env
    old = _payload("2026-11-24T11:34:12+08:00")
    new = _payload("2026-11-24T12:34:12+08:00")
    srv = _prep(tmp_path, old)
    try:
        page = ctx.new_page()
        state = {"payload": old}

        def on_raw(route):
            if route.request.method == "OPTIONS":
                route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "*"})
                return
            _fulfill(route, state["payload"])

        page.route("https://raw.githubusercontent.com/**", on_raw)
        page.goto(srv.url, wait_until="domcontentloaded")
        page.wait_for_function(
            "() => document.getElementById('etfp-when') && "
            "document.getElementById('etfp-when').textContent.indexOf('11:34') >= 0",
            timeout=15000,
        )
        state["payload"] = new
        (tmp_path / "data" / "etf_premium.json").write_text(
            json.dumps(new, ensure_ascii=False), encoding="utf-8"
        )
        btn = page.locator("#etfp-refresh")
        btn.click()
        page.wait_for_function(
            "() => document.getElementById('etfp-refresh').textContent !== '更新中…' && "
            "document.getElementById('etfp-refresh').textContent !== '更新'",
            timeout=12000,
        )
        label = btn.inner_text()
        fb = page.inner_text("#etfp-fb")
        when = page.inner_text("#etfp-when")
        assert label == "已更新" or "已更新" in fb, (label, fb, when, engine)
        assert "12:34" in when
        page.wait_for_function(
            "() => document.getElementById('etfp-refresh').textContent === '更新'",
            timeout=4000,
        )
    finally:
        srv.close()
