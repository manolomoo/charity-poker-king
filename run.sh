#!/usr/bin/env bash
# 重跑：抓最新資料 → data/YYYY-MM-DD.json、data/latest.json、data/history.jsonl → 總表 reports/_table.md → data/manifest.json
# 個別來源失敗時沿用上次數值並標「資料過期」。程式本身失敗（非零結束）會讓這個腳本失敗。
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  [ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
  PY=.venv/bin/python
fi
$PY scripts/run.py
$PY scripts/make_table.py > reports/_table.md
$PY scripts/build_manifest.py
echo "完成：data/latest.json、reports/_table.md、data/manifest.json（解讀段落需另寫入 reports/YYYY-MM-DD.md）"
