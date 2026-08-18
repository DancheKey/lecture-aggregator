/* 页脚访问量：直接显示 busuanzi.aspark.cc 写入的数字，不做基数叠加。
 * 由 index.html / stats.html 共用，避免内联脚本被 CSP 拦截。 */
(function () {
  var el = document.getElementById('busuanzi_site_pv');
  if (!el) return;
  // aspark 异步写入数字到该 span；此处仅做兜底：若脚本加载失败/写入 0，显示"—"
  var obs = new MutationObserver(function () {
    var v = parseInt(el.textContent.replace(/\s/g, ''), 10);
    if (!isNaN(v) && v > 0) { obs.disconnect(); }
  });
  obs.observe(el, { characterData: true, childList: true, subtree: true });
  // 3 秒后若 aspark 仍未写入有效数字，保持"—"显示
  setTimeout(function () { obs.disconnect(); }, 3000);
})();
