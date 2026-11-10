# 慈善撲克王大賽 — 台股與美股抄底訊號追蹤

一個純靜態網站，追蹤 11 項「抄底訊號」在台股與美股的觸發狀態，資料由 Python 追蹤器自動抓取、判定，並以 GitHub Actions 自動部署到 GitHub Pages。排程為平日台灣時間 06:30（美股收盤後）、15:00（台股收盤後）、22:00（融資資料公布後）。全站介面為繁體中文，僅供參考，**不是投資建議**。

- 網站（部署後）：`https://manolomoo.github.io/charity-poker-king/`
- 規則版本與門檻：見 [`rules.md`](rules.md) / [`rules.json`](rules.json)（機器可讀，程式直接讀取判定）

## 資料夾結構

```
index.html, rules.html, history.html   網站三個頁面（純 HTML/CSS/JS，無外部相依）
assets/                                共用樣式、Markdown 轉換器與各頁邏輯
  style.css        共用樣式
  common.js         導覽列／頁尾／共用輔助函式
  markdown.js       自製的簡易 Markdown → HTML 轉換器（含 XSS 跳脫）
  app.js            首頁邏輯
  rules.js          規則頁邏輯
  history.js        歷史頁邏輯
  favicon.svg       網站圖示

rules.json, rules.md   規則 v1（門檻、「接近」定義、彙總方式、修改紀錄）
scripts/
  run.py              抓資料＋判定 → data/YYYY-MM-DD.json、data/latest.json、data/history.jsonl
  make_table.py       由 JSON 產生總表 Markdown → reports/_table.md
  build_manifest.py   產生 data/manifest.json（網站用它找「最新報告」路徑）
  common.py           追蹤器共用函式
  build_site.sh       組出部署用的 _site 資料夾（本機與 CI 共用）
run.sh                 重跑追蹤器的入口
data/
  latest.json         網站首頁讀取的主資料
  history.jsonl        每次執行的整項燈號（每行一筆 JSON）
  manifest.json         報告檔案清單，含 latest_report 路徑
  YYYY-MM-DD.json       每次執行的完整快照
reports/
  YYYY-MM-DD.md         含解讀文字的完整報告（人工／LLM 撰寫，網站首頁會渲染）
  _table.md              自動產生的總表（非報告，網站不會當成報告顯示）
.github/workflows/update-and-deploy.yml   自動更新資料並部署網站
.nojekyll               停用 GitHub Pages 的 Jekyll 處理
```

## 本機執行

### 1. 重新抓資料（可選，約 3–4 分鐘）

```bash
./run.sh
```

會更新 `data/latest.json`、`data/history.jsonl`、`reports/_table.md`、`data/manifest.json`。個別資料來源失敗時會自動沿用上一次的數值，並在該筆資料標記「資料過期」，不會讓整個流程中斷。

### 2. 本機瀏覽網站

**不能用 `file://` 直接開啟網頁**（瀏覽器會擋本機檔案的 `fetch`），一定要透過本機伺服器：

```bash
python3 -m http.server 8000
```

再打開 <http://localhost:8000/>。三個頁面（首頁／規則／歷史）都會用相對路徑讀取 `data/`、`rules.json`、`rules.md`、`reports/` 等檔案。

### 3. 驗證部署用的 `_site` 資料夾

```bash
./scripts/build_site.sh
python3 -m http.server 8000 --directory _site
```

`scripts/build_site.sh` 只會複製網站需要的檔案（網頁、`assets/`、`data/` 但排除 `data/cache/`、`reports/`、`rules.md`、`rules.json`、`.nojekyll`），GitHub Actions 的部署流程會呼叫同一支腳本。

## 自動更新與部署排程

`.github/workflows/update-and-deploy.yml` 會在以下時機執行：

- **排程**：平日台灣時間 06:30（美股收盤後）、15:00（台股收盤後）、22:00（融資資料公布後）。台灣時間為 UTC+8，換算成 cron 使用的 UTC：
  - 06:30 台北（美股收盤後，含美國夏令時間）→ `30 22 * * 0-4`（UTC 前一日 22:30；週一 06:30 台北落在週日 UTC）
  - 15:00 台北（台股收盤後）→ `0 7 * * 1-5`（UTC 07:00）
  - 22:00 台北（融資資料公布後）→ `0 14 * * 1-5`（UTC 14:00）
- **手動觸發**：`workflow_dispatch`，可在 GitHub Actions 頁面手動執行。
- **push 到 `main`**：只重新部署網站，**不會**重跑追蹤器（避免每次改網頁都重新抓資料）。

流程分兩個 job：

1. `update`（只在排程或手動觸發時執行）：安裝 Python 依賴 → 執行 `./run.sh` → 若 `data/`、`reports/_table.md` 有變更，以 `github-actions[bot]`身分提交並推回 `main`。因為用 `GITHUB_TOKEN` 推送不會再觸發 push 事件的 workflow，所以部署步驟寫在同一個 workflow 裡，不會因此漏掉。
2. `deploy`：checkout 最新的 `main` → 用 `scripts/build_site.sh` 組出 `_site` → 透過 `actions/upload-pages-artifact` / `actions/deploy-pages` 部署到 GitHub Pages。即使 `update` job 被略過（例如 push 事件），`deploy` 仍會執行。

## 資料過期機制

追蹤器 `scripts/run.py` 在抓取某個資料來源失敗時，不會讓整次執行失敗，而是沿用該筆資料上一次成功查得的數值，並加上：

- `stale: true`
- `stale_since`：上一次成功查得的時間
- `stale_reason`：失敗原因與沿用說明

網站首頁會在最上方列出本次有哪些資料過期，並在對應訊號明細的那一列加上「資料過期」標籤。

- 沿用列保留上一次的燈號、數值與**原資料日期**，不會編造新數字；整項彙總仍會把它算進去，所以請留意「資料過期」標籤。
- 上一次本來就沒有數值的列（例如 QQQ、SMH 資金流）維持 ❔，不標過期。
- 規則門檻一律讀 `rules.json`，CI 不會改動任何門檻。
- `data/cache/tpex_margin/` 是櫃買中心逐日融資快取（會 commit），讓 CI 不必每次重抓 120 天。

## 解讀報告

`reports/YYYY-MM-DD.md` 的解讀文字是人工（或由助理）撰寫的，GitHub Actions 只更新燈號資料，不會自動改寫解讀。若最新報告日期早於資料日期，首頁會提示「解讀可能落後」。

## 推上 GitHub 後，Repo 擁有者要做的設定

1. **Settings → Pages → Source**：選擇 **GitHub Actions**（不要選 branch 部署）。
2. **Settings → Actions → General → Workflow permissions**：選擇 **Read and write permissions**，讓 `update` job 可以用 `GITHUB_TOKEN` 推送 commit。
3. 若 `main` 分支有設定**分支保護規則**，請允許 `github-actions[bot]`（或是允許帶有 `contents: write` 權限的 GitHub Actions）直接推送，否則自動 commit 會被擋下。
4. GitHub 的排程 workflow **只會在預設分支（通常是 `main`）上執行**，且若該 repo **連續 60 天沒有活動**，GitHub 會自動暫停排程，需要手動到 Actions 頁面重新啟用或執行一次 `workflow_dispatch`。

## `data/latest.json` 結構

```
{
  schema, site, rules_version, run_date,
  queried_at,              // 字串，已是台灣時間，例如 "2026-11-10 11:51 (台北時間 UTC+8)"
  previous_run,             // 上次查詢時間，首次執行為 null
  changes_vs_previous,      // 字串或字串陣列
  stale_rows,                // 陣列，可能不存在；本次資料過期的 "訊號｜標的" 清單
  summary: {
    "1": { name, status, status_label, aggregation, by_market },
    ...
    "11": { ... }
  },
  rows: [
    {
      signal, target, value, threshold, status, status_label,
      data_date, sources, queried_at, note, extra,
      stale, stale_since, stale_reason   // 僅資料過期的列才有
    },
    ...
  ]
}
```

- `status` / `status_label`：🔴 已觸發、🟡 接近門檻、⚪ 未觸發、❔ 資料不足。
- `by_market`：部分訊號會分台股／美股列示（例如 `{"台股": "🔴", "美股": "⚪"}`），其餘為 `null`。
- `rows[].value` 為 `null` 時代表該筆無法取得數值。
