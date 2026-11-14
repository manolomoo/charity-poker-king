/* 教學頁段落導覽（單一來源）。首頁與 /trend/ta-book/ 都讀這份，以免兩邊對不上。 */
window.TA_BOOK_NAV = [
  { id: "core", label: "核心觀念" },
  { id: "trend", label: "趨勢" },
  { id: "dow-stages", label: "牛熊階段" },
  { id: "reversal", label: "反轉形態" },
  { id: "consol", label: "整固形態" },
  { id: "breakout", label: "突破與目標" },
  { id: "gaps", label: "缺口" },
  { id: "sr", label: "支撐與趨勢線" },
  { id: "tactics", label: "交易戰術" },
  { id: "expectancy", label: "期望值" }
];
window.renderTaBookNav = function(el, prefix){
  if(!el || !window.TA_BOOK_NAV) return;
  prefix = prefix || "";
  el.innerHTML = window.TA_BOOK_NAV.map(function(it){
    var href = prefix + "#" + it.id;
    return '<a href="'+href+'">'+it.label+'</a>';
  }).join("");
};
