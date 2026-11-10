| 訊號 | 市場／標的 | 最新數值 | 判定門檻 | 狀態 | 資料日期 | 來源 |
|---|---|---|---|---|---|---|
| 1. 指數 ETF 資金外流 | SPY | 近1週 -6.82B 美元（-0.85% AUM）；近1月 -2.31B（-0.28% AUM）；AUM 791.9B；流通股數 1052.1M | 近一個月淨流量 ≤ −2.0% AUM，或近一週 ≤ −1.0% AUM | 🟡 接近門檻 | 2026-11-09 | [1](https://www.ssga.com/library-content/products/fund-data/etfs/us/navhist-us-en-spy.xlsx) |
| 1. 指數 ETF 資金外流 | SOXX | 流通股數 255.75M、AUM 37.53B（Nov 09, 2026）；近1週／近1月淨流量：無歷史資料（iShares 官網只公開當日流通股數，歷史下載檔以程式讀取失敗；已開始每日存快照（data/etf_shares_snapshots.csv），累積 22 個交易日後即可依規則計算。） | 近一個月淨流量 ≤ −2.0% AUM，或近一週 ≤ −1.0% AUM | ❔ 資料不足 | Nov 09, 2026 | [1](https://www.ishares.com/us/products/239705/ishares-semiconductor-etf) |
| 1. 指數 ETF 資金外流 | QQQ | —（Invesco 資料 API 拒絕程式存取（HTTP 406），未取得每日流通股數；不以資產規模縮水代替資金外流。） | 近一個月淨流量 ≤ −2.0% AUM，或近一週 ≤ −1.0% AUM | ❔ 資料不足 | — | [1](https://www.invesco.com/qqq-etf/en/about.html) |
| 1. 指數 ETF 資金外流 | SMH | —（VanEck 官網需 Cookie／瀏覽器，程式讀取被導向錯誤頁；未取得每日流通股數。） | 近一個月淨流量 ≤ −2.0% AUM，或近一週 ≤ −1.0% AUM | ❔ 資料不足 | — | [1](https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/) |
| 2. CNN 恐懼貪婪指數 | CNN 恐懼貪婪指數 | 31.7（fear）；前一交易日 31.7、1 週前 23.5、1 個月前 43.5 | < 25 | ⚪ 未觸發 | 2026-11-10 09:00 台北 | [1](https://edition.cnn.com/markets/fear-and-greed)<br>[2](https://production.dataviz.cnn.io/index/fearandgreed/graphdata/2026-09-11) |
| 3. 週線 KDJ 的 J 值 ≤ 0 | 台灣加權指數 | J=-6.0（K=34.7, D=55.0；前一週 J=2.6） | 週 J ≤ 0 | 🔴 已觸發 | 週線截至 2026-11-06（已完成週） | [1](https://finance.yahoo.com/quote/%5ETWII/history) |
| 3. 週線 KDJ 的 J 值 ≤ 0 | NDX | J=-14.3（K=24.0, D=43.2；前一週 J=-9.5） | 週 J ≤ 0 | 🔴 已觸發 | 週線截至 2026-11-06（已完成週） | [1](https://finance.yahoo.com/quote/%5ENDX/history) |
| 3. 週線 KDJ 的 J 值 ≤ 0 | SOX | J=-9.4（K=20.1, D=34.9；前一週 J=-7.5） | 週 J ≤ 0 | 🔴 已觸發 | 週線截至 2026-11-06（已完成週） | [1](https://finance.yahoo.com/quote/%5ESOX/history) |
| 4. 估值進入便宜區間 | NDX | NTM P/E 19.01×；10年分位 9%（5年 1%）；隱含 NTM EPS 約1個月 +2.5%、自 08/05 起 +11.6% | 10 年分位 ≤ 20% | 🔴 已觸發 | 2026-11-09 | [1](https://historyofmarket.com/nasdaq/ndx-forward-pe/)<br>[2](https://historyofmarket.com/api/ndx/forward-pe.json) |
| 4. 估值進入便宜區間 | SOX | NTM P/E 14.14×；10年分位 9%（5年 4%）；隱含 NTM EPS 約1個月 +7.3%、自 08/05 起 +30.4% | 10 年分位 ≤ 20% | 🔴 已觸發 | 2026-11-09 | [1](https://historyofmarket.com/zh-tw/semi/semi-fwdpe/)<br>[2](https://historyofmarket.com/api/sectors/forward-pe.json) |
| 5. VIX／VXN 升到高檔 | VIX | 收盤 19.92（10年分位 68%）；本規則門檻 p90=27.38、p80=22.84；近5日最高收盤 28.18（11/05） | ≥ 自身10年p90（27.38） | ⚪ 未觸發 | 2026-11-09 | [1](https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv)<br>[2](https://fred.stlouisfed.org/series/VIXCLS) |
| 5. VIX／VXN 升到高檔 | VXN | 收盤 28.97（10年分位 82%）；本規則門檻 p90=32.70、p80=28.35；近5日最高收盤 38.26（11/05） | ≥ 自身10年p90（32.70） | 🟡 接近門檻 | 2026-11-09 | [1](https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv)<br>[2](https://fred.stlouisfed.org/series/VXNCLS) |
| 6. 台股融資維持率偏低 | 全市場信用交易整戶擔保維持率（官方） | 168.44%；近5日 11/03 165.53%、11/04 167.42%、11/05 159.77%、11/06 158.05%、11/09 168.44%；近60個交易日（20260811起）最低 158.05%（20261106）、最高 200.51%；追繳戶 1,197 | < 160%（接近：160–166%） | ⚪ 未觸發 | 2026-11-09 | [1](https://www.twse.com.tw/dashboard/zh/credit/margin.html)<br>[2](https://www.twse.com.tw/rwd/zh/marginTrading/BFIJ3U_TREND?response=json&days=60) |
| 6. 台股融資維持率偏低 | 資料商推估融資維持率（僅參考） | —（財經M平方等資料商頁面有 Cloudflare 驗證／付費牆，程式無法讀取；口徑（只算融資、自行估算）與官方整戶維持率不同，不可混用。） | 不納入判定 | ❔ 資料不足 | — | [1](https://www.macromicro.me/charts/53117/taiwan-taiex-maintenance-margin) |
| 7. 台股融資餘額降至低檔 | 上市＋上櫃融資餘額合計 | 7,101 億元；較 120 日高點（2026-10-12）-19.5%；上市融資／上市市值 0.396%（近5年年底中位數 0.44%，≤基準） | 回落 ≥20% 且 比重 ≤ 基準 | 🟡 接近門檻 | 2026-11-09 | [1](https://www.twse.com.tw/zh/trading/margin/mi-margn.html)<br>[2](https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN_TREND?response=json&days=60)<br>[3](https://www.tpex.org.tw/zh-tw/mainboard/trading/margin-trading/transactions.html)<br>[4](https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN_HISTORY?response=json) |
| 7. 台股融資餘額降至低檔 | 上市融資餘額（參考） | 5,399 億元；較高點（2026-10-12）-17.4% | （整項以合計判定） | 🟡 接近門檻 | 2026-11-09 | [1](https://www.twse.com.tw/zh/trading/margin/mi-margn.html)<br>[2](https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN_TREND?response=json&days=60) |
| 7. 台股融資餘額降至低檔 | 上櫃融資餘額（參考） | 1,702 億元；較高點（2026-10-07）-25.7% | （整項以合計判定） | 🔴 已觸發 | 2026-11-09 | [1](https://www.tpex.org.tw/zh-tw/mainboard/trading/margin-trading/transactions.html) |
| 8. 槓桿 ETF 爆量 | QLD | 最新日 7.3M 股＝1.13×；近5日最大 3.30×（11/03）；上週 58M 股＝2.86×（20週均） | 日 ≥2.0× 或 週 ≥1.5× | 🔴 已觸發 | 日線 2026-11-09；週線截至 2026-11-06 | [1](https://finance.yahoo.com/quote/QLD/history) |
| 8. 槓桿 ETF 爆量 | TQQQ | 最新日 91.3M 股＝1.18×；近5日最大 3.59×（11/05）；上週 599M 股＝2.12×（20週均） | 日 ≥2.0× 或 週 ≥1.5× | 🔴 已觸發 | 日線 2026-11-09；週線截至 2026-11-06 | [1](https://finance.yahoo.com/quote/TQQQ/history) |
| 8. 槓桿 ETF 爆量 | SOXL | 最新日 261.5M 股＝1.73×；近5日最大 4.69×（11/05）；上週 1393M 股＝4.15×（20週均） | 日 ≥2.0× 或 週 ≥1.5× | 🔴 已觸發 | 日線 2026-11-09；週線截至 2026-11-06 | [1](https://finance.yahoo.com/quote/SOXL/history) |
| 9. 底底高＋高點更高 | 台灣加權指數 | 確認低點 L1=44,210（2026-08-28，距52週高 -5.5%）→ L2=45,398（2026-09-18）↑底底高；其間波段高 H=無；未突破；最新完成週 收 39,702／低 37,837；⚠️已跌破 L2，結構失效 | L2>L1 且週收盤突破 H | ⚪ 未觸發 | 週線截至 2026-11-06 | [1](https://finance.yahoo.com/quote/%5ETWII/history) |
| 9. 底底高＋高點更高 | NDX | 確認低點 L1=28,876（2026-08-28，距52週高 -5.0%）→ L2=28,753（2026-09-18）↓未墊高；其間波段高 H=無；未突破；最新完成週 收 26,990／低 26,606；⚠️已跌破 L2，結構失效 | L2>L1 且週收盤突破 H | ⚪ 未觸發 | 週線截至 2026-11-06 | [1](https://finance.yahoo.com/quote/%5ENDX/history) |
| 9. 底底高＋高點更高 | SOX | 確認低點 L1=10,445（2026-07-31，距52週高 -27.2%）→ L2=11,056（2026-09-04）↑底底高；其間波段高 H=12,771（2026-08-21）；已突破；最新完成週 收 9,390／低 8,997；⚠️已跌破 L2，結構失效 | L2>L1 且週收盤突破 H | ⚪ 未觸發 | 週線截至 2026-11-06 | [1](https://finance.yahoo.com/quote/%5ESOX/history) |
| 10. 台灣領先指標連三個月低於 100 | 領先指標不含趨勢指數 | 2026/06 102.96；2026/07 103.54；2026/08 104.11；2026/09 104.69（連續低於100：0 個月） | 連續3個月皆 <100 | ⚪ 未觸發 | 2026/09（資料月份 2026/09，國發會 2026-10-29 發布） | [1](https://data.gov.tw/dataset/6099)<br>[2](https://index.ndc.gov.tw/n/zh_tw/data/eco)<br>[3](https://ws.ndc.gov.tw/Download.ashx?u=LzAwMS9hZG1pbmlzdHJhdG9yLzEwL3JlbGZpbGUvNTc4MS82MzkyL2VhMjM1YmQ5LWQwNTItNGE2OS1hYmZjLWQ1Yzc4NWQzZDBlMi56aXA%3d&n=5pmv5rCj5oyH5qiZ5Y%2bK54eI6JmfLnppcA%3d%3d&icon=.zip) |
| 11. 台灣景氣藍燈 | 景氣對策信號 | 2026/09 41 分 紅燈；近月：2026/04 40(紅)、2026/05 39(紅)、2026/06 41(紅)、2026/07 41(紅)、2026/08 40(紅) | 藍燈（9–16分） | ⚪ 未觸發 | 2026/09（資料月份 2026/09，國發會 2026-10-29 發布） | [1](https://data.gov.tw/dataset/6099)<br>[2](https://www.ndc.gov.tw/nc_332_40618)<br>[3](https://ws.ndc.gov.tw/Download.ashx?u=LzAwMS9hZG1pbmlzdHJhdG9yLzEwL3JlbGZpbGUvNTc4MS82MzkyL2VhMjM1YmQ5LWQwNTItNGE2OS1hYmZjLWQ1Yzc4NWQzZDBlMi56aXA%3d&n=5pmv5rCj5oyH5qiZ5Y%2bK54eI6JmfLnppcA%3d%3d&icon=.zip) |

**整項彙總**

| 訊號 | 整項狀態 | 彙總方式 | 分市場 |
|---|---|---|---|
| 1. 指數 ETF 資金外流 | 🟡 接近門檻 | any | — |
| 2. CNN 恐懼貪婪指數 | ⚪ 未觸發 | single | — |
| 3. 週線 KDJ 的 J 值 ≤ 0 | 🔴 已觸發 | any（並分台股、美股各自列示） | 台股 🔴、美股 🔴 |
| 4. 估值進入便宜區間 | 🔴 已觸發 | any | — |
| 5. VIX／VXN 升到高檔 | 🟡 接近門檻 | any | — |
| 6. 台股融資維持率偏低 | ⚪ 未觸發 | single（只依官方數據） | — |
| 7. 台股融資餘額降至低檔 | 🟡 接近門檻 | 以『上市＋上櫃合計』判定，上市、上櫃分別列示供參考 | — |
| 8. 槓桿 ETF 爆量 | 🔴 已觸發 | any | — |
| 9. 底底高＋高點更高 | ⚪ 未觸發 | 分市場列示；整項採 any（但台股與美股結論分開敘述） | 台股 ⚪、美股 ⚪ |
| 10. 台灣領先指標連三個月低於 100 | ⚪ 未觸發 | single | — |
| 11. 台灣景氣藍燈 | ⚪ 未觸發 | single | — |
