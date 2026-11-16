"""分批投入計算器（/trend/scale-in/）：Python 獨立重算，對照 node 跑的 scale-in.js 與 Excel 對照值。不碰燈號規則。"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "trend" / "scale-in" / "scale-in.js"
PAGE = ROOT / "trend" / "scale-in" / "index.html"
HOME = ROOT / "home" / "index.html"
EXPECTANCY = ROOT / "trend" / "expectancy" / "index.html"
FIX = ROOT / "tests" / "fixtures" / "scale_in_expected.json"

CASES = {
    "defaults": dict(total=10000000, drop=0.2, n=11, k=3),
    "n1": dict(total=10000000, drop=0.2, n=1, k=3),
    "equal": dict(total=10000, drop=0.2, n=5, k=1),
    "two": dict(total=1000, drop=0.1, n=2, k=3),
    "flat": dict(total=8000000, drop=0.0, n=5, k=2),
}
SUMMARY_KEYS = ("avg_cost", "below", "low_pnl", "rebound", "first", "last")
BATCH_KEYS = ("trigger", "price", "weight", "amount", "cum_amt", "cum_share", "units", "avg", "pnl")


def py_compute(total, drop, n, k):
    """照 Excel 工作表「分批計算」的公式，Python 自己算一份。"""
    trig = [0.0 if n == 1 else drop * (i - 1) / (n - 1) for i in range(1, n + 1)]
    w = [1.0 if n == 1 else 1 + (k - 1) * (i - 1) / (n - 1) for i in range(1, n + 1)]
    sw = sum(w)
    batches, cum_amt, cum_units = [], 0.0, 0.0
    for i in range(1, n + 1):
        price = 1 - trig[i - 1]
        amount = total * w[i - 1] / sw
        units = amount / price
        cum_amt += amount
        cum_units += units
        avg = cum_amt / cum_units
        batches.append(dict(i=i, trigger=trig[i - 1], price=price, weight=w[i - 1], amount=amount,
                            cum_amt=cum_amt, cum_share=cum_amt / total, units=units, avg=avg,
                            pnl=price / avg - 1))
    avg_cost = total / cum_units
    return dict(avg_cost=avg_cost, below=1 - avg_cost, low_pnl=(1 - drop) / avg_cost - 1,
                rebound=avg_cost / (1 - drop) - 1, first=batches[0]["amount"],
                last=batches[-1]["amount"], batches=batches)


def node_eval(expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("本機沒有 node")
    code = (
        "const S=require(process.argv[1]);"
        "let out;try{out={ok:true,value:(%s)}}catch(e){out={ok:false,name:e.name,message:e.message}}"
        "process.stdout.write(JSON.stringify(out));" % expr
    )
    res = subprocess.run([node, "-e", code, str(JS)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def node_compute(total, drop, n, k):
    out = node_eval(f"S.compute({total!r},{drop!r},{n!r},{k!r})")
    assert out["ok"], out
    return out["value"]


def close(a, b):
    return a == pytest.approx(b, rel=1e-12, abs=1e-9)


def assert_same(got, want, label):
    for key in SUMMARY_KEYS:
        assert close(got[key], want[key]), f"{label} {key}: {got[key]} != {want[key]}"
    assert len(got["batches"]) == len(want["batches"]), label
    for g, w in zip(got["batches"], want["batches"]):
        assert g["i"] == w["i"]
        for key in BATCH_KEYS:
            assert close(g[key], w[key]), f"{label} 第{w['i']}批 {key}: {g[key]} != {w[key]}"


@pytest.fixture(scope="module")
def expected():
    return json.loads(FIX.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", list(CASES))
def test_node_matches_python(name):
    assert_same(node_compute(**CASES[name]), py_compute(**CASES[name]), name)


@pytest.mark.parametrize("name", list(CASES))
def test_matches_excel_fixture(name, expected):
    want = expected[name]
    for key, val in CASES[name].items():
        assert want[key] == val, f"fixture {name} 的輸入 {key} 與測試不一致"
    assert_same(py_compute(**CASES[name]), want, f"python/{name}")
    assert_same(node_compute(**CASES[name]), want, f"node/{name}")


def test_defaults_headline_numbers():
    """預設輸入的六個總結數字（已用 Excel 快取值核對）。"""
    want = dict(avg_cost=0.8759961835406166, below=0.12400381645938341,
                low_pnl=-0.08675401213901857, rebound=0.09499522942577077,
                first=454545.45454545453, last=1363636.3636363635)
    py = py_compute(**CASES["defaults"])
    js = node_compute(**CASES["defaults"])
    for key, val in want.items():
        assert close(py[key], val), key
        assert close(js[key], val), key
    assert len(js["batches"]) == 11
    assert close(js["batches"][-1]["cum_amt"], 10000000)
    assert close(js["batches"][-1]["avg"], js["avg_cost"])
    assert close(js["batches"][-1]["pnl"], js["low_pnl"])


def test_js_defaults_constant():
    out = node_eval("S.DEFAULTS")
    assert out["value"] == dict(total=10000000, drop=0.2, n=11, k=3)


@pytest.mark.parametrize("args", [
    "0,0.2,11,3",          # 金額必須 > 0
    "-5,0.2,11,3",
    "1e7,1,11,3",          # 跌 100% 會除以零
    "1e7,1.2,11,3",
    "1e7,-0.1,11,3",
    "1e7,0.2,0,3",         # 批數 1–30 整數
    "1e7,0.2,31,3",
    "1e7,0.2,2.5,3",
    "1e7,0.2,11,0",        # 權重必須 > 0
    "1e7,0.2,11,-1",
    "NaN,0.2,11,3",
    "1e7,0.2,11,Infinity",
    "'1e7',0.2,11,3",
])
def test_invalid_params_throw(args):
    out = node_eval(f"S.compute({args})")
    assert out["ok"] is False, f"compute({args}) 應該丟錯"
    assert out["name"] == "RangeError"
    assert out["message"]
    msg = node_eval(f"S.validate({args})")
    assert msg["ok"] and isinstance(msg["value"], str) and msg["value"]


def test_valid_params_validate_null():
    assert node_eval("S.validate(1e7,0.2,11,3)")["value"] is None
    assert node_eval("S.validate(1e7,0,30,0.5)")["value"] is None


def test_js_has_no_dom_dependency():
    src = JS.read_text(encoding="utf-8")
    assert "document" not in src and "window" not in src
    assert "module.exports" in src and "ScaleIn" in src


def test_page_has_excel_wording():
    html = PAGE.read_text(encoding="utf-8")
    for text in [
        "分批投入計算器（越跌買越多）",
        "只需修改黃底藍字的 4 格，其餘全部自動計算。跌幅以「第一批買進價」為起點。",
        "註：權重由第 1 批的 1 線性遞增到最後一批的「遞增強度」倍；投入金額 = 原始金額 × 該批權重 ÷ 權重總和。所有數字來自輸入區，無外部資料。",
        "計算只是數學試算，不代表會跌到這裡或會反彈，非投資建議。",
        "原始金額（元）", "要投入的總金額",
        "預期總跌幅", "最後一批買在比起點低多少（例：20%）",
        "分幾次", "1～30 批，觸發跌幅平均分配",
        "遞增強度", "最後一批是第一批的幾倍；1 = 每批一樣多，數字越大越下面買越多",
        "全部買完的平均成本（相對起點價）",
        "全部批次買完後，平均每股成本是起點價的幾 %（例：起點 100 元、顯示 87.6% → 平均成本約 87.6 元）",
        "平均成本比起點低",
        "平均成本比第一批便宜幾 %（= 100% − 上一格）；股價高於平均成本就是整體賺錢",
        "跌到最低點時的帳面損益", "從最低點反彈多少就回本", "第一批金額", "最後一批金額",
        "批次", "觸發跌幅", "買進價（相對起點）", "權重", "投入金額", "累計投入", "累計投入占比",
        "換算單位（計算用）", "買到此批的平均成本", "此時帳面損益",
        "如果只跌到這一批就反彈，你的平均成本是起點價的幾 %",
        "剛買完這一批時，整體部位的帳面損益",
        "依使用者提供的 Excel 分批計算表做成網頁版",
    ]:
        assert text in html, f"頁面缺少原文：{text}"
    assert "<h1>分批投入計算器（越跌買越多）</h1>" in html
    assert 'src="./scale-in.js"' in html
    for href in ('href="../../"', 'href="../"', 'href="../expectancy/"'):
        assert href in html
    # 純 SVG，不引入外部圖表庫；外部資源只有字體
    for src in re.findall(r'<script[^>]*\bsrc="([^"]+)"', html):
        assert src == "./scale-in.js", src
    assert "書" not in html


def test_home_footer_link_only():
    html = HOME.read_text(encoding="utf-8")
    link = '<a href="./trend/scale-in/">分批計算</a>'
    assert html.count("scale-in") == 1, "首頁只能有頁腳那一個連結"
    assert html.count("分批") == 1
    footer = re.search(r"<footer>.*?</footer>", html, re.S).group(0)
    assert link in footer
    assert re.search(r'估值位置</a>｜<a href="\./trend/scale-in/">分批計算</a></p>', footer)
    outside = html.replace(footer, "")
    for word in ("scale-in", "分批", "ScaleIn", "越跌買越多", "遞增強度"):
        assert word not in outside, f"首頁主內容不該出現 {word}"


def test_expectancy_links():
    html = EXPECTANCY.read_text(encoding="utf-8")
    nav = re.search(r"<nav>.*?</nav>", html, re.S).group(0)
    footer = re.search(r"<footer>.*?</footer>", html, re.S).group(0)
    assert '<a href="../scale-in/">分批計算</a>' in nav
    assert '<a href="../scale-in/">分批計算</a>' in footer
    # 期望值公式沒動
    assert "return win*reward - (1-win)*risk;" in html
