// 慈善撲克王大賽 — 規則頁邏輯
(function () {
  "use strict";

  var S = window.Site;
  var MD = window.SiteMarkdown;

  Site.renderNav("rules");
  Site.renderFooter();

  function renderChangelog(rules) {
    var section = document.getElementById("changelog-section");
    section.innerHTML = "";

    var meta = document.getElementById("rules-meta");
    meta.appendChild(S.el("span", {}, [S.el("strong", { text: "目前版本：" }), rules.version || "—"]));
    meta.appendChild(S.el("span", {}, [S.el("strong", { text: "建立日期：" }), rules.created || "—"]));

    var changelog = rules.changelog || [];
    if (!changelog.length) return;

    section.appendChild(S.el("h2", { text: "規則變更紀錄" }));
    var wrap = S.el("div", { class: "changelog-wrap" });
    var table = S.el("table", { class: "rows-table changelog-table" });
    table.appendChild(
      S.el("thead", {}, [
        S.el("tr", {}, ["日期", "版本", "修改內容", "原因"].map(function (h) { return S.el("th", { text: h }); })),
      ])
    );
    var tbody = S.el("tbody");
    changelog.forEach(function (entry) {
      tbody.appendChild(
        S.el("tr", {}, [
          S.el("td", { text: entry.date || "—" }),
          S.el("td", { text: entry.version || "—" }),
          S.el("td", { text: entry.change || "—" }),
          S.el("td", { text: entry.reason || "—" }),
        ])
      );
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    section.appendChild(wrap);
  }

  function renderRulesBody(md) {
    var container = document.getElementById("rules-container");
    container.className = "";
    container.innerHTML = "";
    var box = S.el("div", { class: "report-box" });
    var mdDiv = S.el("div", { class: "md" });
    mdDiv.innerHTML = MD.render(md);
    box.appendChild(mdDiv);
    container.appendChild(box);
  }

  function init() {
    S.fetchJson("rules.json")
      .then(function (rules) {
        renderChangelog(rules);
      })
      .catch(function (err) {
        S.showError(document.getElementById("changelog-section"), err);
      });

    S.fetchText("rules.md")
      .then(function (md) {
        renderRulesBody(md);
      })
      .catch(function (err) {
        S.showError(document.getElementById("rules-container"), err);
      });
  }

  init();
})();
