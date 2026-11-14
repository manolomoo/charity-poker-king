#!/usr/bin/env bash
# 組出靜態網站的部署資料夾 _site（本機與 GitHub Actions 共用）。
# 只複製網站需要的檔案：網頁、assets、data（排除 cache）、reports、規則檔、.nojekyll。
set -e
cd "$(dirname "$0")/.."

OUT="${1:-_site}"
rm -rf "$OUT"
mkdir -p "$OUT"

cp index.html rules.html history.html "$OUT/"
cp .nojekyll "$OUT/"
cp rules.md rules.json "$OUT/"

mkdir -p "$OUT/assets"
cp -r assets/. "$OUT/assets/"

mkdir -p "$OUT/data"
cp -r data/. "$OUT/data/"
rm -rf "$OUT/data/cache"

mkdir -p "$OUT/reports"
cp -r reports/. "$OUT/reports/"

# 走勢觀察子站（不影響燈號資料或規則）
if [ -d trend ]; then
  mkdir -p "$OUT/trend"
  cp -r trend/. "$OUT/trend/"
fi

echo "完成：$OUT"
