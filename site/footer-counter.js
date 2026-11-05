/* 页脚访问量：自建 Supabase 云端全站 PV 计数 + 5 分钟缓存防重复。
 * 对外文案固定为「本站总访问量」（2026-09-22 用户决定）。
 *
 * 计数源迁移说明（2026-10-18）：
 * - 2026-09-10 ~ 2026-10-17：busuanzi.aspark.cc 第三方计数（UV 依赖其服务端
 *   IP 去重，无法自建复现——弃用后 UV 停更，趋势页只保留 PV 曲线）。
 * - 现行：Supabase lecture_stats 表专用行 __site__（visits 列存全站 PV），
 *   经 increment_visit RPC 增量（钳位 100 万），与讲座计数同一道安全闸门。
 *   种子 1724 = busuanzi 末次台账值（2026-10-17），两段口径同为「页面打开
 *   次数」，无缝衔接。
 *
 * 实现方式（2026-11-05 改造：页脚与趋势页 KPI 共用一次取值）：
 * - window.LectureSitePv.get() 返回**记忆化的同一个 Promise**，整页只解析一次：
 *     · localStorage 缓存有效 → 直接 resolve 缓存值（零网络请求）
 *     · 缓存过期/首次访问 → LectureCloud.bump('visit','__site__',1) 云端 +1，
 *       resolve 数据库权威值并写回缓存
 *     · 云端禁用/失败 → 降级读随站静态快照 lectures/visits.json（只读不计数）
 * - 页脚与趋势页 KPI 都订阅这同一个 Promise，拿到的必然是同一个数字。
 *   旧链路里 KPI 另调 LectureCloud.fetchSitePv 单独读云端，两条链路时点不同，
 *   同屏会出现两个数字（见 reports/site_review_2026-11-05.html #1）。
 * - 计数口径不变：全站共享 5 分钟 localStorage 防刷；趋势页不单独 +1。
 *
 * 注：依赖 app.cloud.js（defer 先于本脚本执行，LectureCloud 已定义）；
 *     defer 顺序上本脚本先于 visits-trend.js，故 LectureSitePv 在其执行时已挂载。
 */
(function () {
  var COUNT_KEY = 'lecture_site_count_value';
  var COUNT_TS_KEY = 'lecture_site_count_ts';
  var EXPIRE_MS = 5 * 60 * 1000;
  var SITE_KEY = '__site__';

  function getEl() { return document.getElementById('site_pv_value'); }

  function isCacheValid() {
    var ts = localStorage.getItem(COUNT_TS_KEY);
    if (!ts) return false;
    return (Date.now() - parseInt(ts, 10)) < EXPIRE_MS;
  }

  // 云端不可用时读随站静态快照（CI 每日出片，最多滞后一天）。
  // 只读不计数，也**不写 localStorage 缓存**：云端恢复后应立刻回到实时值。
  function fallbackArchive() {
    return new Promise(function (resolve) {
      fetch('lectures/visits.json', { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          var n = d ? parseInt(d.site_pv, 10) : NaN;
          resolve(isNaN(n) || n <= 0 ? null : n);
        })
        .catch(function () { resolve(null); });
    });
  }

  // 全页唯一一次取值：记忆化 Promise。多次 get() 不会重复 bump / 重复读快照。
  var _shared = null;
  function resolveSitePv() {
    if (_shared) return _shared;
    _shared = new Promise(function (resolve) {
      if (isCacheValid()) {
        var cached = parseInt(localStorage.getItem(COUNT_KEY), 10);
        resolve(isNaN(cached) ? null : cached);
        return;
      }
      if (typeof LectureCloud === 'undefined' || !LectureCloud ||
          typeof LectureCloud.bump !== 'function') {
        fallbackArchive().then(resolve);
        return;
      }
      LectureCloud.bump('visit', SITE_KEY, 1).then(function (row) {
        if (!row || typeof row.visits !== 'number') {
          fallbackArchive().then(resolve);
          return;
        }
        localStorage.setItem(COUNT_KEY, String(row.visits));
        localStorage.setItem(COUNT_TS_KEY, String(Date.now()));
        resolve(row.visits);
      }).catch(function () { fallbackArchive().then(resolve); });
    });
    return _shared;
  }

  // 对外唯一接口：页脚、趋势页 KPI 共用同一个值（同一个 Promise）。
  window.LectureSitePv = { get: resolveSitePv };

  function init() {
    resolveSitePv().then(function (n) {
      if (n === null || n === undefined) return;   // 降级也无值 → 保留占位符
      var el = getEl();
      if (el) el.textContent = String(n);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
