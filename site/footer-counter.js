/* 页脚访问量：基于 sessionStorage 的防重复计数。
 * 同一个浏览器会话（session）内只计 1 次访问，切换页面不重复计数。
 * 使用 localStorage 持久化存储总访问量，页面刷新/重新打开后仍保留。
 *
 * 注：不再依赖 busuanzi.aspark.cc，避免不同页面 URL 导致计数不一致。
 */
(function () {
  var el = document.getElementById('busuanzi_site_pv');
  if (!el) return;

  var STORAGE_KEY = 'lecture_site_total_visits';
  var SESSION_KEY = 'lecture_site_session_counted';

  // 获取当前总访问量
  function getCount() {
    var raw = localStorage.getItem(STORAGE_KEY);
    var n = parseInt(raw, 10);
    return isNaN(n) ? 0 : n;
  }

  // 设置总访问量
  function setCount(n) {
    localStorage.setItem(STORAGE_KEY, String(n));
  }

  // 检查当前会话是否已计数
  function isSessionCounted() {
    return sessionStorage.getItem(SESSION_KEY) === '1';
  }

  // 标记当前会话已计数
  function markSessionCounted() {
    sessionStorage.setItem(SESSION_KEY, '1');
  }

  // 初始化计数
  function init() {
    // 如果当前会话未计数，则 +1
    if (!isSessionCounted()) {
      var newCount = getCount() + 1;
      setCount(newCount);
      markSessionCounted();
    }
    // 显示当前总访问量
    el.textContent = getCount();
  }

  // 启动
  init();
})();
