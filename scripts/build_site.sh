#!/usr/bin/env bash
# 組出靜態網站的部署資料夾 _site（本機與 GitHub Actions 共用）。
#
#   _site/                極簡首頁（來源 home/index.html）＋首頁要抓的 data/、rules.json、assets/
#   _site/signals/        完整燈號詳細站（repo 根目錄的 index.html / rules.html / history.html）
#   _site/trend/          走勢觀察與技術分析圖解
#
# data/ 會複製兩份（都排除 cache）：根目錄給首頁，signals/ 給詳細站，
# 兩邊都用相對路徑 data/latest.json。
set -e
cd "$(dirname "$0")/.."

OUT="${1:-_site}"
rm -rf "$OUT"
mkdir -p "$OUT"

copy_data() {
  mkdir -p "$1/data"
  cp -r data/. "$1/data/"
  rm -rf "$1/data/cache"
}

# ── 根目錄：極簡首頁 ──
cp home/index.html "$OUT/index.html"
cp .nojekyll "$OUT/"
cp rules.json "$OUT/"
mkdir -p "$OUT/assets"
cp -r assets/. "$OUT/assets/"
copy_data "$OUT"

# ── /signals/：完整燈號詳細站 ──
SIG="$OUT/signals"
mkdir -p "$SIG"
cp index.html rules.html history.html "$SIG/"
cp rules.md rules.json "$SIG/"
mkdir -p "$SIG/assets"
cp -r assets/. "$SIG/assets/"
copy_data "$SIG"
mkdir -p "$SIG/reports"
cp -r reports/. "$SIG/reports/"

# 詳細頁原本在根目錄，裡面的 ./trend/ 連結搬到 /signals/ 後要往上一層。
# 只改部署出去的那份，repo 裡的 index.html 不動。
sed -i.bak 's|href="\./trend/"|href="../trend/"|g' "$SIG/index.html"
rm -f "$SIG/index.html.bak"

# ── /trend/：走勢觀察子站（不影響燈號資料或規則）──
if [ -d trend ]; then
  mkdir -p "$OUT/trend"
  cp -r trend/. "$OUT/trend/"
fi

echo "完成：$OUT"
