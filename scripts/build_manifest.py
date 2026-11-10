#!/usr/bin/env python3
"""產生 data/manifest.json，讓靜態網站知道有哪些報告與每日資料檔（GitHub Pages 無法列目錄）。"""
import json, os, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rep = sorted((f for f in os.listdir(os.path.join(ROOT, "reports")) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", f)), reverse=True)
daily = sorted((f for f in os.listdir(os.path.join(ROOT, "data")) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f)), reverse=True)
m = {"reports": [f"reports/{f}" for f in rep], "latest_report": f"reports/{rep[0]}" if rep else None,
     "daily": [f"data/{f}" for f in daily]}
json.dump(m, open(os.path.join(ROOT, "data", "manifest.json"), "w"), ensure_ascii=False, indent=2)
print(json.dumps(m, ensure_ascii=False))
