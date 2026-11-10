#!/usr/bin/env python3
"""由 data/latest.json 產生總表 Markdown（報告與網站共用）。用法：python scripts/make_table.py [data/xxx.json]"""
import json, sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
f = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "latest.json")
d = json.load(open(f))
NAMES = {k: v["name"] for k, v in d["summary"].items()}
print("| 訊號 | 市場／標的 | 最新數值 | 判定門檻 | 狀態 | 資料日期 | 來源 |")
print("|---|---|---|---|---|---|---|")
for r in d["rows"]:
    src = "<br>".join(f"[{i+1}]({u})" for i, u in enumerate(r["sources"]))
    val = (r["value"] or "—").replace("|", "／")
    if r["status"] == "❔" and r.get("note"): val += f"（{r['note']}）"
    print(f"| {r['signal']}. {NAMES[r['signal']]} | {r['target']} | {val} | {r['threshold']} | {r['status']} {r['status_label']} | {r['data_date'] or '—'} | {src} |")
print()
print("**整項彙總**")
print()
print("| 訊號 | 整項狀態 | 彙總方式 | 分市場 |")
print("|---|---|---|---|")
for k, v in d["summary"].items():
    bm = "、".join(f"{a} {b}" for a, b in (v["by_market"] or {}).items()) or "—"
    print(f"| {k}. {v['name']} | {v['status']} {v['status_label']} | {v['aggregation']} | {bm} |")
