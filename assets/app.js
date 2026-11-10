// 慈善撲克王大賽 — 首頁邏輯
(function () {
  "use strict";

  var S = window.Site;
  var MD = window.SiteMarkdown;

  Site.renderNav("index");
  Site.renderFooter();

  var GROUPS = [
    { title: "一、恐慌、籌碼與估值（第 1–8 項）", ids: ["1", "2", "3", "4", "5", "6", "7", "8"] },
    { title: "二、價格回穩確認（第 9 項）", ids: ["9"] },
    { title: "三、台灣總體景氣背景（第 10–11 項）", ids: ["10", "11"] },
  ];

  var AGG_LABELS = null; // 待 rules.json 載入後填入

  function translateAggregation(str) {
    if (!str) return "";
    var m = String(str).match(/^(any|all|single)(.*)$/);
    if (m && AGG_LABELS[m[1]]) {
      var rest = m[2].trim();
      return AGG_LABELS[m[1]] + (rest ? "　" + rest : "");
    }
    return str;
  }

  function byMarketText(byMarket) {
    if (!byMarket) return "";
    return Object.keys(byMarket)
      .map(function (k) { return k + " " + byMarket[k]; })
      .join("・");
  }

  function renderMeta(latest) {
    var wrap = document.getElementById("meta-line");
    wrap.innerHTML = "";
    var items = [
      ["查詢時間", latest.queried_at || "—"],
      ["規則版本", latest.rules_version || "—"],
      ["上次查詢", latest.previous_run || "（無，首次建立）"],
    ];
    items.forEach(function (pair) {
      wrap.appendChild(S.el("span", {}, [S.el("strong", { text: pair[0] + "：" }), pair[1]]));
    });

    var changes = latest.changes_vs_previous;
    var changesText = Array.isArray(changes) ? changes.join("；") : changes;
    if (changesText) {
      var full = S.el("div", { class: "meta-line", style: "margin-top:2px;" }, [
        S.el("strong", { text: "與上次相比：" }),
        changesText,
      ]);
      wrap.parentNode.appendChild(full);
    }
  }

  function renderStaleNotice(latest) {
    var box = document.getElementById("stale-notice");
    box.innerHTML = "";
    var staleRows = (latest.rows || []).filter(function (r) { return r.stale; });
    var staleList = latest.stale_rows;

    if (!staleRows.length && (!staleList || !staleList.length)) return;

    var items = [];
    if (staleRows.length) {
      items = staleRows.map(function (r) {
        return r.signal + "．" + r.target + "：" + (r.stale_reason || "資料過期，沿用上次數值。");
      });
    } else {
      items = staleList;
    }

    box.appendChild(
      S.el("div", { class: "notice", role: "status" }, [
        S.el("strong", { text: "提醒：" }),
        "部分資料本次抓取失敗，沿用上次數值（標示「資料過期」）。",
        S.el("ul", {}, items.map(function (t) { return S.el("li", { text: t }); })),
      ])
    );
  }

  function renderSummaryCard(latest) {
    var card = document.getElementById("summary-card");
    card.innerHTML = "";

    var counts = { "🔴": 0, "🟡": 0, "⚪": 0, "🔍": 0, "❔": 0 };
    var order = [];
    for (var i = 1; i <= 11; i++) {
      var s = latest.summary[String(i)];
      if (!s) continue;
      counts[s.status] = (counts[s.status] || 0) + 1;
      order.push(s.status);
    }

    var breakdownParts = [
      "已觸發 " + counts["🔴"] + " 項",
      "接近 " + counts["🟡"] + " 項",
      "未觸發 " + counts["⚪"] + " 項",
    ];
    if (counts["🔍"]) breakdownParts.push("型態參考 " + counts["🔍"] + " 項");
    breakdownParts.push("資料不足 " + counts["❔"] + " 項");

    var inner = S.el("div", { class: "summary-card" });
    var top = S.el("div", { class: "summary-top" }, [
      S.el("div", { class: "summary-count" }, [String(counts["🔴"]), S.el("small", { text: " / 11 已觸發" })]),
      S.el("div", { class: "summary-breakdown" }, [breakdownParts.join("・")]),
    ]);
    inner.appendChild(top);

    var segWrap = S.el("div", { class: "progress-segments", "aria-hidden": "true" });
    order.forEach(function (st) {
      segWrap.appendChild(S.el("div", { class: "seg " + S.statusClass(st) }));
    });
    inner.appendChild(segWrap);

    var legend = S.el("div", { class: "legend" }, [
      S.el("span", {}, [S.el("span", { class: "dot red" }), "已觸發"]),
      S.el("span", {}, [S.el("span", { class: "dot yellow" }), "接近門檻"]),
      S.el("span", {}, [S.el("span", { class: "dot gray" }), "未觸發"]),
      S.el("span", {}, [S.el("span", { class: "dot ref" }), "型態參考"]),
      S.el("span", {}, [S.el("span", { class: "dot unknown" }), "資料不足"]),
    ]);
    inner.appendChild(legend);

    card.appendChild(inner);
  }

  function formatNum(n) {
    n = Math.round(n);
    var s = String(Math.abs(n));
    s = s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return (n < 0 ? "-" : "") + s;
  }

  function svgNode(tag, attrs) {
    var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  function svgText(x, y, text, anchor) {
    var t = svgNode("text", { x: x, y: y, class: "sig9-axis-label", "text-anchor": anchor || "start" });
    t.textContent = text;
    return t;
  }

  // 純 inline SVG 折線圖：收盤序列 + 落在範圍內的 zigzag 波段點（H 紅、L 綠）
  function buildMiniChart(points, pivots) {
    if (!points || points.length < 2) return null;
    var W = 300, H = 140;
    var plotL = 4, plotR = W - 4, plotT = 18, plotB = H - 30;
    var n = points.length;
    var closes = points.map(function (p) { return p[1]; });
    var minV = Math.min.apply(null, closes);
    var maxV = Math.max.apply(null, closes);

    function xAt(i) { return plotL + (plotR - plotL) * (n === 1 ? 0 : i / (n - 1)); }
    function yAt(v) {
      if (maxV === minV) return (plotT + plotB) / 2;
      return plotB - ((v - minV) / (maxV - minV)) * (plotB - plotT);
    }

    var dateIndex = {};
    points.forEach(function (p, i) { dateIndex[p[0]] = i; });

    var svg = svgNode("svg", {
      viewBox: "0 0 " + W + " " + H,
      preserveAspectRatio: "none",
      class: "sig9-svg",
    });

    var polyPoints = points.map(function (p, i) { return xAt(i) + "," + yAt(p[1]); }).join(" ");
    svg.appendChild(svgNode("polyline", { points: polyPoints, class: "sig9-line" }));

    (pivots || []).forEach(function (piv) {
      var type = piv[0], date = piv[1], val = piv[2];
      var idx = dateIndex[date];
      if (idx === undefined) return;
      var cx = xAt(idx), cy = yAt(val);
      svg.appendChild(svgNode("circle", { cx: cx, cy: cy, r: 3, class: "sig9-dot " + (type === "H" ? "sig9-dot-h" : "sig9-dot-l") }));
      var anchor = cx > W - 40 ? "end" : cx < 40 ? "start" : "middle";
      svg.appendChild(svgText(cx, type === "H" ? cy - 6 : cy + 11, formatNum(val), anchor));
    });

    svg.appendChild(svgText(plotL, plotT - 6, formatNum(maxV), "start"));
    svg.appendChild(svgText(plotL, plotB + 12, formatNum(minV), "start"));
    svg.appendChild(svgText(plotL, H - 4, points[0][0], "start"));
    svg.appendChild(svgText(plotR, H - 4, points[n - 1][0], "end"));

    return svg;
  }

  function buildSig9Charts(row) {
    var chart = row.extra && row.extra.chart;
    if (!chart) return null;

    var pair = S.el("div", { class: "sig9-chart-pair" });
    var any = false;

    if (chart.daily && chart.daily.length > 1) {
      var dailySvg = buildMiniChart(chart.daily, chart.daily_pivots);
      if (dailySvg) {
        any = true;
        var dailyBox = S.el("div", { class: "sig9-chart-box" }, [
          S.el("div", { class: "sig9-chart-title", text: row.target + "：日線（近 130 日）" }),
        ]);
        dailyBox.appendChild(dailySvg);
        pair.appendChild(dailyBox);
      }
    }

    if (chart.weekly && chart.weekly.length > 1) {
      var weeklySvg = buildMiniChart(chart.weekly, chart.weekly_pivots);
      if (weeklySvg) {
        any = true;
        var weeklyBox = S.el("div", { class: "sig9-chart-box" }, [
          S.el("div", { class: "sig9-chart-title", text: row.target + "：週線（近 104 週）" }),
        ]);
        weeklyBox.appendChild(weeklySvg);
        pair.appendChild(weeklyBox);
      }
    }

    if (!any) return null;

    var wrap = S.el("div", { class: "sig9-charts" });
    wrap.appendChild(pair);

    if (row.value) {
      var caption = String(row.value).split("｜").slice(0, 2).join("｜");
      if (caption) wrap.appendChild(S.el("div", { class: "sig9-chart-caption", text: caption }));
    }

    return wrap;
  }

  function renderSources(sources) {
    if (!sources || !sources.length) return S.el("span", { text: "—" });
    var wrap = S.el("span");
    sources.forEach(function (url, idx) {
      if (idx > 0) wrap.appendChild(document.createTextNode(" "));
      wrap.appendChild(
        S.el("a", { href: url, target: "_blank", rel: "noopener", class: "src-link" }, [S.hostnameOf(url)])
      );
    });
    return wrap;
  }

  function rowStatusNote(row) {
    var parts = [];
    if (row.status === "❔" && row.note) parts.push(row.note);
    else if (row.note) parts.push(row.note);
    return parts.join(" ");
  }

  function buildRowsTable(rows) {
    var wrap = S.el("div", { class: "rows-table-wrap" });
    var table = S.el("table", { class: "rows-table" });
    table.appendChild(
      S.el("thead", {}, [
        S.el("tr", {}, [
          "市場／標的", "最新數值", "門檻", "狀態", "資料日期", "來源", "備註",
        ].map(function (h) { return S.el("th", { text: h }); })),
      ])
    );
    var tbody = S.el("tbody");
    rows.forEach(function (row) {
      var tr = S.el("tr", row.status === "❔" ? { class: "unk-row" } : {});
      tr.appendChild(S.el("td", { text: row.target || "—" }));
      tr.appendChild(S.el("td", { text: row.value == null ? "—" : row.value }));
      tr.appendChild(S.el("td", { text: row.threshold || "—" }));
      var statusTd = S.el("td");
      statusTd.appendChild(S.el("span", { class: "pill small " + S.statusClass(row.status) }, [row.status + " " + row.status_label]));
      if (row.stale) statusTd.appendChild(S.el("span", { class: "badge-stale", style: "margin-left:6px;" }, ["資料過期"]));
      tr.appendChild(statusTd);
      tr.appendChild(S.el("td", { text: row.data_date || "—" }));
      var srcTd = S.el("td");
      srcTd.appendChild(renderSources(row.sources));
      tr.appendChild(srcTd);
      var noteTd = S.el("td", row.status === "❔" ? { class: "note-highlight" } : {}, [rowStatusNote(row) || "—"]);
      tr.appendChild(noteTd);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  function buildRowCards(rows) {
    var wrap = S.el("div", { class: "row-cards" });
    rows.forEach(function (row) {
      var card = S.el("div", { class: "row-card" + (row.status === "❔" ? " unk" : "") });
      var head = S.el("div", { class: "rc-head" }, [
        S.el("span", { text: row.target || "—" }),
        S.el("span", { class: "pill small " + S.statusClass(row.status) }, [row.status + " " + row.status_label]),
      ]);
      card.appendChild(head);
      if (row.stale) card.appendChild(S.el("div", { class: "badge-stale" }, ["資料過期"]));
      card.appendChild(S.el("div", { class: "rc-line" }, [S.el("strong", { text: "最新數值：" }), row.value == null ? "—" : row.value]));
      card.appendChild(S.el("div", { class: "rc-line" }, [S.el("strong", { text: "判定門檻：" }), row.threshold || "—"]));
      card.appendChild(S.el("div", { class: "rc-line" }, [S.el("strong", { text: "資料日期：" }), row.data_date || "—"]));
      var srcLine = S.el("div", { class: "rc-line" }, [S.el("strong", { text: "來源：" })]);
      srcLine.appendChild(renderSources(row.sources));
      card.appendChild(srcLine);
      var note = rowStatusNote(row);
      if (row.stale && row.stale_reason) {
        card.appendChild(S.el("div", { class: "rc-note" }, [row.stale_reason]));
      }
      if (note) card.appendChild(S.el("div", { class: "rc-note" }, [note]));
      wrap.appendChild(card);
    });
    return wrap;
  }

  function buildSignalCard(id, summaryItem, rulesSignal, rows) {
    var cls = S.statusClass(summaryItem.status);
    var details = S.el("details", { class: "signal-card " + cls });

    var summaryEl = S.el("summary");
    summaryEl.appendChild(S.el("span", { class: "num", text: id + "." }));
    summaryEl.appendChild(S.el("span", { class: "name", text: summaryItem.name }));
    summaryEl.appendChild(S.el("span", { class: "pill " + cls }, [summaryItem.status + " " + summaryItem.status_label]));
    if (summaryItem.by_market) {
      summaryEl.appendChild(S.el("span", { class: "by-market", text: byMarketText(summaryItem.by_market) }));
    }
    var origin = (rulesSignal && rulesSignal.origin) || "";
    // 整項門檻為自訂 → 「自訂規則／尚未回測」；只有「接近」定義等部分為自訂 → 「部分自訂規則」
    if (origin.indexOf("自訂規則") === 0) {
      summaryEl.appendChild(
        S.el("span", { class: "badge-custom", title: origin, tabindex: "0" }, ["自訂規則／尚未回測"])
      );
    } else if (origin.indexOf("自訂規則") !== -1) {
      summaryEl.appendChild(
        S.el("span", { class: "badge-custom partial", title: origin, tabindex: "0" }, ["部分自訂規則"])
      );
    }
    summaryEl.appendChild(S.el("span", { class: "chev", "aria-hidden": "true", text: "▸" }));
    details.appendChild(summaryEl);

    var body = S.el("div", { class: "signal-body" });
    body.appendChild(
      S.el("p", { class: "agg-line" }, [
        S.el("strong", { text: "彙總方式：" }),
        translateAggregation(summaryItem.aggregation),
      ])
    );

    if (rulesSignal) {
      var dl = S.el("dl", { class: "trigger-grid" });
      function addPair(dt, dd) {
        if (!dd) return;
        dl.appendChild(S.el("dt", { text: dt }));
        dl.appendChild(S.el("dd", { text: dd }));
      }
      addPair("判定門檻", rulesSignal.trigger);
      addPair("接近門檻定義", rulesSignal.near);
      addPair("EPS 註記", rulesSignal.eps_flag);
      addPair("依據", rulesSignal.origin);
      body.appendChild(dl);
    }

    if (rows && rows.length) {
      body.appendChild(buildRowsTable(rows));
      body.appendChild(buildRowCards(rows));
      if (id === "9") {
        rows.forEach(function (row) {
          var chartsEl = buildSig9Charts(row);
          if (chartsEl) body.appendChild(chartsEl);
        });
      }
    } else {
      body.appendChild(S.el("p", { text: "本次沒有明細資料。" }));
    }

    details.appendChild(body);
    return details;
  }

  function renderSignalGroups(latest, rules) {
    var container = document.getElementById("signal-groups");
    container.innerHTML = "";
    var rowsBySignal = {};
    (latest.rows || []).forEach(function (r) {
      (rowsBySignal[r.signal] = rowsBySignal[r.signal] || []).push(r);
    });

    GROUPS.forEach(function (group) {
      container.appendChild(S.el("h2", { class: "group-title", text: group.title }));
      group.ids.forEach(function (id) {
        var summaryItem = latest.summary[id];
        if (!summaryItem) return;
        var rulesSignal = rules && rules.signals ? rules.signals[id] : null;
        container.appendChild(buildSignalCard(id, summaryItem, rulesSignal, rowsBySignal[id]));
      });
    });
  }

  function renderReport(latest, manifest) {
    var container = document.getElementById("report-container");
    container.className = "";
    container.innerHTML = "";

    var reportPath = manifest && manifest.latest_report;
    if (!reportPath) {
      container.appendChild(S.el("div", { class: "notice" }, ["目前沒有可顯示的報告。"]));
      return;
    }

    var dateMatch = reportPath.match(/(\d{4}-\d{2}-\d{2})\.md$/);
    var reportDate = dateMatch ? dateMatch[1] : null;

    // 報告裡的「總表」與上方即時燈號重複、欄位又多，收進可展開區塊（數值以上方燈號為準）
    function collapseTableSection(root) {
      var h2s = root.querySelectorAll("h2");
      for (var i = 0; i < h2s.length; i++) {
        if (h2s[i].textContent.trim() !== "總表") continue;
        var det = document.createElement("details");
        det.className = "report-table-collapse";
        var sum = document.createElement("summary");
        sum.textContent = "報告撰寫時的總表（點開查看；最新數值以上方燈號為準）";
        det.appendChild(sum);
        var n = h2s[i].nextSibling;
        while (n && !(n.nodeType === 1 && n.tagName === "H2")) {
          var next = n.nextSibling; det.appendChild(n); n = next;
        }
        h2s[i].parentNode.replaceChild(det, h2s[i]);
        break;
      }
    }

    S.fetchText(reportPath)
      .then(function (md) {
        var html = MD.render(md);
        var box = S.el("div", { class: "report-box" });

        if (reportDate && latest.run_date && reportDate < latest.run_date) {
          box.appendChild(
            S.el("div", { class: "notice" }, [
              "解讀文字為 " + reportDate + " 撰寫，燈號資料已更新至 " + latest.run_date + "，解讀可能落後。",
            ])
          );
        }
        box.appendChild(S.el("div", { class: "report-meta", text: "報告日期：" + (reportDate || "未知") }));

        var md_div = S.el("div", { class: "md" });
        md_div.innerHTML = html;
        collapseTableSection(md_div);
        box.appendChild(md_div);

        container.appendChild(box);
      })
      .catch(function (err) {
        S.showError(container, err);
      });
  }

  function init() {
    Promise.all([S.fetchJson("data/latest.json"), S.fetchJson("rules.json"), S.fetchJson("data/manifest.json")])
      .then(function (results) {
        var latest = results[0];
        var rules = results[1];
        var manifest = results[2];

        AGG_LABELS = {
          any: (rules.aggregation_definitions && rules.aggregation_definitions.any) || "任一符合",
          all: (rules.aggregation_definitions && rules.aggregation_definitions.all) || "全部符合",
          single: "單一資料來源：只依這一項數字判定",
        };

        S.renderFraming(rules);
        renderMeta(latest);
        renderStaleNotice(latest);
        renderSummaryCard(latest);
        renderSignalGroups(latest, rules);
        renderReport(latest, manifest);
      })
      .catch(function (err) {
        S.showError(document.getElementById("summary-card"), err);
        document.getElementById("report-container").innerHTML = "";
      });
  }

  init();
})();
