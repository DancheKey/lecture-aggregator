/* 页脚访问量：全网统一计数（CountAPI + 3 分钟防刷节流）。
 * 所有访客看到同一个总数；同一浏览器 3 分钟内只计 1 次，防恶意刷新刷量。
 *
 * CountAPI：免费、免认证、支持 CORS 与 HTTPS。
 * 参考：https://countapi.xyz/
 */
(function () {
  var el = document.getElementById('busuanzi_site_pv');
  if (!el) return;

  var NAMESPACE = 'scnu-lecture';
  var KEY = 'site-pv';
  var THROTTLE_MS = 180 * 1000;            // 3 分钟节流窗口
  var HIT_AT_KEY = 'lecture_site_last_hit_at'; // localStorage 键：上次成功_INCREMENT_的时间戳

  // 检查距离上次计数是否已过节流窗口
  function canCountNow() {
    var last = parseInt(localStorage.getItem(HIT_AT_KEY), 10);
    if (isNaN(last)) return true;           // 从未计数过，允许
    return Date.now() - last >= THROTTLE_MS;
  }

  // 记录本次计数时间戳
  function markCounted() {
    localStorage.setItem(HIT_AT_KEY, String(Date.now()));
  }

  // 递增计数并显示结果
  function hitAndShow() {
    fetch('https://api.countapi.xyz/hit/' + NAMESPACE + '/' + KEY, { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && typeof d.value === 'number') {
          el.textContent = d.value;
          markCounted();
        }
      })
      .catch(function () {
        // 网络故障时显示本地备用值
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
          localStorage.setItem('lecture_site_pv_backup', String(d.value));
        }
      })
      .catch(function () {});
  }

  function init() {
    if (canCountNow()) {
      // 已过节流窗口，递增计数
      hitAndShow();
    } else {
      // 节流窗口内，只获取不递增
      getAndShow();
    }
  }

  init();
})();
