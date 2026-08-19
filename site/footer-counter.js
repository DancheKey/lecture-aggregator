/* 页脚访问量：busuanzi 全网统一计数 + sessionStorage 防同会话重复。
 * busuanzi.aspark.cc 是国内可用的免费计数服务（兼容原版不蒜子写法）。
 * sessionStorage 确保同一会话内只计 1 次，切换页面不重复计数。
 */
(function () {
  var el = document.getElementById('busuanzi_site_pv');
  if (!el) return;

  var SESSION_KEY = 'lecture_site_counted_this_session';
  var OBSERVER = null;

  // 检查当前会话是否已计数
  function isSessionCounted() {
    return sessionStorage.getItem(SESSION_KEY) === '1';
  }

  function markSessionCounted() {
    sessionStorage.setItem(SESSION_KEY, '1');
  }

  // 监听 busuanzi 写入计数值
  function watchBusuanzi() {
    if (!el) return;

    // 如果本会话已计数，不再处理
    if (isSessionCounted()) return;

    // 用 MutationObserver 监听 busuanzi 写入的数值
    OBSERVER = new MutationObserver(function (mutations) {
      var txt = el.textContent || '';
      // busuanzi 写入数字后（不再是占位符 "—"）
      if (txt !== '—' && txt !== '' && /^\d+$/.test(txt.trim())) {
        markSessionCounted();
        if (OBSERVER) { OBSERVER.disconnect(); OBSERVER = null; }
      }
    });

    OBSERVER.observe(el, { childList: true, characterData: true, subtree: true });

    // 兜底：如果 busuanzi 已写入（observer 可能错过），直接标记
    var txt = el.textContent || '';
    if (txt !== '—' && txt !== '' && /^\d+$/.test(txt.trim())) {
      markSessionCounted();
    }
  }

  // 启动监听
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', watchBusuanzi);
  } else {
    watchBusuanzi();
  }
})();
