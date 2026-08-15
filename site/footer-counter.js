/* 页脚访问量 +416 基数叠加（不蒜子兼容服务 aspark 写入数字后自动加旧版基数）
 * 由 index.html / stats.html 共用，避免内联脚本被 CSP 拦截。 */
(function () {
  var el = document.getElementById('busuanzi_site_pv');
  if (!el) return;
  var BASE = 416;
  var obs = new MutationObserver(function () {
    var v = parseInt(el.textContent.replace(/\s/g, ''), 10);
    if (!isNaN(v) && v >= 0) { el.textContent = v + BASE; obs.disconnect(); }
  });
  obs.observe(el, { characterData: true, childList: true, subtree: true });
  // 防止 aspark 已经写入但 observer 没触发（比如已经写入过了）
  var v0 = parseInt(el.textContent.replace(/\s/g, ''), 10);
  if (!isNaN(v0) && v0 >= 0 && v0 < 100) { el.textContent = v0 + BASE; }
})();
