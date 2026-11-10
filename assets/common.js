// 慈善撲克王大賽 — 共用元件（導覽列、頁尾、狀態輔助函式）
(function (global) {
  "use strict";

  var STATUS_CLASS = { "🔴": "red", "🟡": "yellow", "⚪": "gray", "❔": "unknown" };
  var STATUS_LABEL = { "🔴": "已觸發", "🟡": "接近門檻", "⚪": "未觸發", "❔": "資料不足" };

  function statusClass(emoji) {
    return STATUS_CLASS[emoji] || "gray";
  }
  function statusLabel(emoji) {
    return STATUS_LABEL[emoji] || "";
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      if (k === "class") node.className = attrs[k];
      else if (k === "html") node.innerHTML = attrs[k];
      else if (k === "text") node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      if (c === null || c === undefined) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function hostnameOf(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, "");
    } catch (e) {
      return url;
    }
  }

  function renderNav(activePage) {
    var pages = [
      { href: "index.html", label: "首頁", key: "index" },
      { href: "rules.html", label: "規則", key: "rules" },
      { href: "history.html", label: "歷史", key: "history" },
    ];
    var nav = document.getElementById("site-nav");
    if (!nav) return;
    var topnav = el("div", { class: "topnav" }, [
      el("div", { class: "wrap" }, [
        el("a", { class: "brand", href: "index.html" }, [
          el("img", { src: "assets/favicon.svg", alt: "", width: "24", height: "24" }),
          "慈善撲克王大賽",
        ]),
        el(
          "nav",
          { "aria-label": "主要導覽" },
          pages.map(function (p) {
            var a = el("a", { href: p.href }, [p.label]);
            if (p.key === activePage) {
              a.classList.add("active");
              a.setAttribute("aria-current", "page");
            }
            return a;
          })
        ),
      ]),
    ]);
    nav.replaceWith(topnav);
  }

  function renderFooter() {
    var slot = document.getElementById("site-footer");
    if (!slot) return;
    var footer = el("footer", { class: "site-footer" }, [
      el("div", { class: "wrap" }, [
        el("p", {}, [
          "本站僅供參考，非投資建議。觸發項數不代表見底機率，也不是買進比例或槓桿倍數；各訊號可能高度相關，不能當作互相獨立的證據。標示「自訂規則／尚未回測」的門檻不是任何原作者的原始標準。",
        ]),
        el("p", {}, ["資料來源與日期見各訊號明細。"]),
        el("p", {}, [
          "原始碼與資料：",
          el("a", { href: "https://github.com/manolomoo/charity-poker-king", target: "_blank", rel: "noopener" }, [
            "github.com/manolomoo/charity-poker-king",
          ]),
        ]),
      ]),
    ]);
    slot.replaceWith(footer);
  }

  function fetchJson(path) {
    return fetch(path, { cache: "no-store" }).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    });
  }

  function fetchText(path) {
    return fetch(path, { cache: "no-store" }).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.text();
    });
  }

  function describeFetchError(err) {
    var isFileProtocol = location.protocol === "file:";
    if (isFileProtocol) {
      return "無法載入資料：直接用 file:// 開啟網頁無法讀取本機檔案。請在終端機執行「python3 -m http.server」，再以 http://localhost:8000/ 開啟本站。";
    }
    return "資料載入失敗（" + (err && err.message ? err.message : "未知錯誤") + "），請稍後重新整理頁面。";
  }

  function showError(container, err) {
    container.innerHTML = "";
    container.appendChild(el("div", { class: "notice error", role: "alert" }, [describeFetchError(err)]));
  }

  global.Site = {
    statusClass: statusClass,
    statusLabel: statusLabel,
    el: el,
    hostnameOf: hostnameOf,
    renderNav: renderNav,
    renderFooter: renderFooter,
    fetchJson: fetchJson,
    fetchText: fetchText,
    describeFetchError: describeFetchError,
    showError: showError,
  };
})(window);
