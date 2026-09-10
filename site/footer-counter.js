/* 页脚访问量：busuanzi 全网统一计数 + 5分钟缓存防重复。
 *
 * 实现方式：
 * - 每次页面加载：检查 localStorage 缓存是否过期（5分钟）
 * - 缓存有效：直接显示缓存值，不重复请求 busuanzi
 * - 缓存过期：重新加载 busuanzi，busuanzi 自动 +1，更新缓存
 * - 同5分钟内刷新不计数，超过5分钟再刷新 PV+1
 *
 * busuanzi.aspark.cc 是国内可用的免费计数服务（兼容原版不蒜子写法）。
 */
(function () {
  var COUNT_KEY = 'lecture_site_count_value';
  var COUNT_TS_KEY = 'lecture_site_count_ts'; // localStorage 时间戳
  var EXPIRE_MS = 5 * 60 * 1000; // 5 分钟过期

  // 获取计数值元素
  function getEl() {
    return document.getElementById('busuanzi_site_pv');
  }

  // 检查缓存是否有效
  function isCacheValid() {
    var ts = localStorage.getItem(COUNT_TS_KEY);
    if (!ts) return false;
    return (Date.now() - parseInt(ts, 10)) < EXPIRE_MS;
  }

  // 显示缓存值
  function showCached() {
    var el = getEl();
    if (!el) return;
    var cached = localStorage.getItem(COUNT_KEY);
    if (cached) el.textContent = cached;
  }

  // 从元素中提取数字
  function extractCount(el) {
    var txt = (el.textContent || '').trim();
    if (txt !== '—' && txt !== '' && /^\d+$/.test(txt)) return txt;
    return null;
  }

  // 降级数据源：busuanzi 不可用时，读取随站发布的静态快照
  // site/lectures/visits.json（由 scripts/fetch_visits_snapshot.py 每日在 CI 中
  // 累积、generate_frontend_data.py 出片）。
  //
  // 只填入纯数字，不附加日期或任何说明文字——与 busuanzi 正常时的显示完全
  // 一致，避免页面出现「截至某日」这类别扭的标注（用户明确要求）。
  // 该值不写入 localStorage 缓存：下次访问仍应优先尝试实时值，
  // 一旦 busuanzi 恢复就自动回到实时计数。
  var _fallbackTried = false;
  function fallbackToArchive() {
    if (_fallbackTried) return;
    _fallbackTried = true;
    if (extractCount(getEl())) return;      // 已有实时值，无需降级
    fetch('lectures/visits.json', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        var el = getEl();
        if (!el || !d) return;
        if (extractCount(el)) return;       // 期间 busuanzi 成功，实时值优先
        var n = parseInt(d.site_pv, 10);
        if (!isNaN(n) && n > 0) el.textContent = String(n);
      })
      .catch(function () { /* 无快照可用时保留占位符 */ });
  }

  // 加载 busuanzi 并缓存
  function loadBusuanziAndCache() {
    var el = getEl();
    if (!el) return;

    // 如果元素已有数值（busuanzi 已完成），直接缓存
    var existing = extractCount(el);
    if (existing) {
      localStorage.setItem(COUNT_KEY, existing);
      localStorage.setItem(COUNT_TS_KEY, String(Date.now()));
      return;
    }

    // 轮询等待 busuanzi 写入数值（最多等 5 秒）
    var pollCount = 0;
    var pollTimer = setInterval(function () {
      pollCount++;
      el = getEl();
      if (!el) { clearInterval(pollTimer); return; }

      var val = extractCount(el);
      if (val) {
        localStorage.setItem(COUNT_KEY, val);
        localStorage.setItem(COUNT_TS_KEY, String(Date.now()));
        clearInterval(pollTimer);
      } else if (pollCount >= 50) {
        clearInterval(pollTimer);
        fallbackToArchive();   // 5 秒仍无数值 → 判定不可用，改用随站快照
      }
    }, 100);

    // 动态加载 busuanzi 脚本
    var script = document.createElement('script');
    script.async = true;
    script.src = 'https://busuanzi.aspark.cc/js';
    script.onerror = fallbackToArchive;   // 脚本加载失败（服务下线/被墙）同样降级
    document.head.appendChild(script);
  }

  // 启动
  function init() {
    // 检查缓存是否有效
    if (isCacheValid()) {
      // 缓存未过期 → 直接显示，不重复计数
      showCached();
    } else {
      // 缓存过期或首次访问 → 加载 busuanzi
      loadBusuanziAndCache();
    }
  }

  // 确保 DOM 就绪后启动
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
