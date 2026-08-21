/* 页脚访问量：busuanzi 全网统一计数 + 同会话只计 1 次。
 *
 * 实现方式：
 * - 首次访问：动态加载 busuanzi 脚本，计数 +1，并将数值缓存到 sessionStorage
 * - 后续页面：不加载 busuanzi，直接显示缓存值（避免切换页面重复计数）
 *
 * busuanzi.aspark.cc 是国内可用的免费计数服务（兼容原版不蒜子写法）。
 */
(function () {
  var el = document.getElementById('busuanzi_site_pv');
  if (!el) return;

  var SESSION_KEY = 'lecture_site_counted';
  var COUNT_KEY = 'lecture_site_count_value';

  // 当前会话已计数 → 不加载 busuanzi，直接显示缓存值
  function showCached() {
    var cached = sessionStorage.getItem(COUNT_KEY);
    if (cached) el.textContent = cached;
  }

  // 当前会话未计数 → 动态加载 busuanzi，监听写入后缓存
  function loadBusuanziAndCapture() {
    // 监听 busuanzi 写入计数值
    var observer = new MutationObserver(function () {
      var txt = (el.textContent || '').trim();
      if (txt !== '—' && txt !== '' && /^\d+$/.test(txt)) {
        // 缓存数值并标记会话已计数
        sessionStorage.setItem(COUNT_KEY, txt);
        sessionStorage.setItem(SESSION_KEY, '1');
        observer.disconnect();
      }
    });

    observer.observe(el, { childList: true, characterData: true, subtree: true });

    // 兜底：如果 busuanzi 已写入（observer 可能错过）
    var txt = (el.textContent || '').trim();
    if (txt !== '—' && txt !== '' && /^\d+$/.test(txt)) {
      sessionStorage.setItem(COUNT_KEY, txt);
      sessionStorage.setItem(SESSION_KEY, '1');
      return;
    }

    // 动态加载 busuanzi 脚本
    var script = document.createElement('script');
    script.async = true;
    script.src = 'https://busuanzi.aspark.cc/js';
    document.head.appendChild(script);
  }

  // 启动
  if (sessionStorage.getItem(SESSION_KEY) === '1') {
    showCached();
  } else {
    loadBusuanziAndCapture();
  }
})();
