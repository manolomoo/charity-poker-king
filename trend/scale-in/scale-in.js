/*
 * 分批投入計算器（越跌買越多）的計算邏輯。
 * 公式與使用者 Excel 工作表「分批計算」相同；不依賴 DOM，node 與瀏覽器共用。
 *
 *   瀏覽器：<script src="scale-in.js"> 之後用 ScaleIn.compute(...)
 *   node  ：const ScaleIn = require("./scale-in.js")
 */
(function (root) {
  "use strict";

  var MIN_N = 1;
  var MAX_N = 30;
  var DEFAULTS = { total: 10000000, drop: 0.2, n: 11, k: 3 };

  function isNum(x) {
    return typeof x === "number" && isFinite(x);
  }

  /** 回傳錯誤訊息字串；參數合法時回傳 null。drop 是小數（0.2 = 20%）。 */
  function validate(total, drop, n, k) {
    if (!isNum(total) || !(total > 0)) return "原始金額必須大於 0";
    if (!isNum(drop) || drop < 0 || !(drop < 1)) {
      return "預期總跌幅必須在 0% 到小於 100% 之間（最後一批買進價要大於 0）";
    }
    if (!isNum(n) || Math.floor(n) !== n || n < MIN_N || n > MAX_N) {
      return "分幾次必須是 " + MIN_N + "～" + MAX_N + " 的整數";
    }
    if (!isNum(k) || !(k > 0)) return "遞增強度必須大於 0（每一批的權重都要大於 0）";
    return null;
  }

  /**
   * compute(total, drop, n, k)
   *   total 原始金額；drop 預期總跌幅（小數）；n 分幾次（1–30 整數）；k 遞增強度。
   * 參數不合法時 throw RangeError。
   */
  function compute(total, drop, n, k) {
    var err = validate(total, drop, n, k);
    if (err) throw new RangeError(err);

    var i;
    var triggers = [];
    var weights = [];
    var sumW = 0;
    for (i = 1; i <= n; i++) {
      var trigger = n === 1 ? 0 : (drop * (i - 1)) / (n - 1);
      var weight = n === 1 ? 1 : 1 + ((k - 1) * (i - 1)) / (n - 1);
      triggers.push(trigger);
      weights.push(weight);
      sumW += weight;
    }

    var batches = [];
    var cumAmt = 0;
    var cumUnits = 0;
    for (i = 1; i <= n; i++) {
      var price = 1 - triggers[i - 1];
      var amount = (total * weights[i - 1]) / sumW;
      var units = amount / price;
      cumAmt += amount;
      cumUnits += units;
      var avg = cumAmt / cumUnits;
      batches.push({
        i: i,
        trigger: triggers[i - 1],
        price: price,
        weight: weights[i - 1],
        amount: amount,
        cum_amt: cumAmt,
        cum_share: cumAmt / total,
        units: units,
        avg: avg,
        pnl: price / avg - 1
      });
    }

    var avgCost = total / cumUnits;
    return {
      avg_cost: avgCost,
      below: 1 - avgCost,
      low_pnl: (1 - drop) / avgCost - 1,
      rebound: avgCost / (1 - drop) - 1,
      first: batches[0].amount,
      last: batches[n - 1].amount,
      batches: batches
    };
  }

  var api = {
    compute: compute,
    validate: validate,
    DEFAULTS: DEFAULTS,
    MIN_N: MIN_N,
    MAX_N: MAX_N
  };

  root.ScaleIn = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
