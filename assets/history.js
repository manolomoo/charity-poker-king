// 慈善撲克王大賽 — 歷史頁邏輯
(function () {
  "use strict";

  var S = window.Site;

  Site.renderNav("history");
  Site.renderFooter();

  function parseJsonl(text) {
    return text
      .split("\n")
      .map(function (line) { return line.trim(); })
      .filter(function (line) { return line.length > 0; })
      .map(function (line) {
        try {
          return JSON.parse(line);
        } catch (e) {
          return null;
        }
      })
      .filter(Boolean);
  }

  function isStale(entry, id) {
    var list = entry.stale || [];
    return list.some(function (s) { return String(s).indexOf(id + "｜") === 0; });
  }

  function buildHeatmap(entries, names) {
    var wrap = S.el("div", { class: "heatmap-wrap" });
    var table = S.el("table", { class: "heatmap" });

    var theadRow = S.el("tr", {}, [S.el("th", { class: "sig-name", text: "訊號" })]);
    entries.forEach(function (entry, idx) {
      var full = entry.queried_at || entry.run_date || "#" + (idx + 1);
      // 欄標題用簡短的「MM/DD HH:MM」（台灣時間），完整查詢時間放 title
      var m = /^(\d{4})-(\d{2})-(\d{2})(?: (\d{2}:\d{2}))?/.exec(full);
      var label = m ? (m[2] + "/" + m[3] + (m[4] ? " " + m[4] : "")) : full;
      var th = S.el("th", { title: full }, [label]);
      if (m) th.appendChild(S.el("span", { class: "full", text: m[1] + (idx === entries.length - 1 ? "・最新" : "") }));
      theadRow.appendChild(th);
    });
    table.appendChild(S.el("thead", {}, [theadRow]));

    var tbody = S.el("tbody");
    for (var i = 1; i <= 11; i++) {
      var id = String(i);
      var row = S.el("tr", {}, [S.el("td", { class: "sig-name", text: id + ". " + (names[id] || "") })]);
      entries.forEach(function (entry) {
        var st = (entry.summary || {})[id] || "❔";
        var cell = S.el("td", { class: "cell " + S.statusClass(st) }, [st]);
        if (isStale(entry, id)) {
          cell.appendChild(S.el("span", { class: "stale-mark", text: "資料過期" }));
        }
        row.appendChild(cell);
      });
      tbody.appendChild(row);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  function buildRunCounts(entries) {
    var section = S.el("section", { class: "run-count-list" }, [S.el("h2", { text: "各次執行已觸發項數" })]);
    entries
      .slice()
      .reverse()
      .forEach(function (entry) {
        var count = Object.keys(entry.summary || {}).filter(function (k) { return entry.summary[k] === "🔴"; }).length;
        var staleNote = entry.stale && entry.stale.length ? "（含 " + entry.stale.length + " 項資料過期）" : "";
        section.appendChild(
          S.el("div", { class: "run-item" }, [
            S.el("span", { text: entry.queried_at || entry.run_date || "—" }),
            S.el("span", { text: count + " / 11 已觸發" + staleNote }),
          ])
        );
      });
    return section;
  }

  function init() {
    Promise.all([S.fetchText("data/history.jsonl"), S.fetchJson("rules.json")])
      .then(function (results) {
        var entries = parseJsonl(results[0]);
        var rules = results[1];
        var names = {};
        Object.keys(rules.signals || {}).forEach(function (id) {
          names[id] = rules.signals[id].name;
        });

        var container = document.getElementById("history-container");
        container.className = "";
        container.innerHTML = "";

        if (!entries.length) {
          container.appendChild(S.el("div", { class: "notice" }, ["目前沒有歷史紀錄。"]));
          return;
        }

        container.appendChild(buildHeatmap(entries, names));
        document.getElementById("run-count-section").appendChild(buildRunCounts(entries));
      })
      .catch(function (err) {
        S.showError(document.getElementById("history-container"), err);
      });
  }

  init();
})();
