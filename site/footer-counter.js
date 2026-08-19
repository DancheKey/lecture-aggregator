/* 页脚访问量：全网统一计数（CountAPI + sessionStorage 防重复）。
 * 所有访客看到同一个总数；同一会话内只计 1 次，切换页面不重复计数。
 *
 * CountAPI：免费、免认证、支持 CORS 与 HTTPS。
 * 参考：https://countapi.xyz/
 */
(function () {
  var el = document.getElementById('busuanzi_site_pv');
  if (!el) return;

  var NAMESPACE = 'scnu-lecture';
  var KEY = 'site-pv';
  var SESSION_KEY = 'lecture_site_counted_this_session';

  // 检查当前会话是否已计数
  function isSessionCounted() {
    return sessionStorage.getItem(SESSION_KEY) === '1';
  }

  function markSessionCounted() {
    sessionStorage.setItem(SESSION_KEY, '1');
  }

  // 递增计数并显示结果
  function hitAndShow() {
    fetch('https://api.countapi.xyz/hit/' + NAMESPACE + '/' + KEY, { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && typeof d.value === 'number') {
          el.textContent = d.value;
          markSessionCounted();
        }
      })
      .catch(function () {
        // 网络故障时显示本地备用值（localStorage 缓存的上次数值）
        var fallback = localStorage.getItem('lecture_site_pv_backup');
        if (fallback) el.textContent = fallback;
      });
  }

  // 仅获取当前值（不递增）
  function getAndShow() {
    fetch('https://api.countapi.xyz/get/' + NAMESPACE + '/' + KEY, { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && typeof d.value === 'number') {
          el.textContent = d.value;
          // 缓存备用（网络故障时使用）
          localStorage.setItem('lecture_site_pv_backup', String(d.value));
        }
      })
      .catch(function () {});
  }

  function init() {
    if (isSessionCounted()) {
      // 本会话已计数过，只获取当前值不递增
      getAndShow();
    } else {
      // 本会话首次访问，递增计数
      hitAndShow();
    }
  }

  init();
})();
