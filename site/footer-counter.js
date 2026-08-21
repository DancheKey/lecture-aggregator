/* 页脚访问量：busuanzi 全网统一计数 + 同会话只计 1 次。
 *
 * 实现方式：
 * - 首次访问：动态加载 busuanzi 脚本，计数 +1，并将数值缓存到 sessionStorage
 * - 后续页面：不加载 busuanzi，直接显示缓存值（避免切换页面重复计数）
 *
 * busuanzi.aspark.cc 是国内可用的免费计数服务（兼容原版不蒜子写法）。
 */
(function () {
  var SESSION_KEY = 'lecture_site_counted';
  var COUNT_KEY = 'lecture_site_count_value';

  // 获取计数值元素（每次重新查询，防止 Vue 重建 DOM 后引用失效）
  function getEl() {
    return document.getElementById('busuanzi_site_pv');
  }

  // 当前会话已计数 → 不加载 busuanzi，直接显示缓存值
  function showCached() {
    var el = getEl();
    if (!el) return;
    var cached = sessionStorage.getItem(COUNT_KEY);
    if (cached) el.textContent = cached;
  }

  // 从元素中提取数字
  function extractCount(el) {
    var txt = (el.textContent || '').trim();
    if (txt !== '—' && txt !== '' && /^\d+$/.test(txt)) return txt;
    return null;
  }

  // 当前会话未计数 → 动态加载 busuanzi，轮询等待写入后缓存
  function loadBusuanziAndCapture() {
    var el = getEl();
    if (!el) return;

    // 如果元素已有数值（busuanzi 已完成），直接缓存
    var existing = extractCount(el);
    if (existing) {
      sessionStorage.setItem(COUNT_KEY, existing);
      sessionStorage.setItem(SESSION_KEY, '1');
      return;
    }

    // 轮询等待 busuanzi 写入数值（最多等 5 秒）
    var pollCount = 0;
    var pollTimer = setInterval(function () {
      pollCount++;
      el = getEl(); // 重新查询（Vue 可能已重建 DOM）
      if (!el) { clearInterval(pollTimer); return; }

      var val = extractCount(el);
      if (val) {
        sessionStorage.setItem(COUNT_KEY, val);
        sessionStorage.setItem(SESSION_KEY, '1');
        clearInterval(pollTimer);
      } else if (pollCount >= 50) { // 5 秒超时（每 100ms 一次）
        clearInterval(pollTimer);
      }
    }, 100);

    // 动态加载 busuanzi 脚本
    var script = document.createElement('script');
    script.async = true;
    script.src = 'https://busuanzi.aspark.cc/js';
    document.head.appendChild(script);
  }

  // 启动
  function init() {
    if (sessionStorage.getItem(SESSION_KEY) === '1') {
      showCached();
    } else {
      loadBusuanziAndCapture();
    }
  }

  // 确保 DOM 就绪后启动
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
