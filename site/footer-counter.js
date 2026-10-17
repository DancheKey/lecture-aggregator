/* 页脚访问量：自建 Supabase 云端全站 PV 计数 + 5分钟缓存防重复。
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
 * 实现方式：
 * - 每次页面加载：检查 localStorage 缓存是否过期（5分钟）
 * - 缓存有效：直接显示缓存值，不计数
 * - 缓存过期：LectureCloud.bump('visit','__site__',1) → 云端 +1 → 显示权威值
 * - 云端禁用/失败：降级读随站静态快照 lectures/visits.json（只读不计数），
 *   该快照由 CI 每日从 Supabase 台账出片，数值最多滞后一天。
 *
 * 注：本脚本依赖 app.cloud.js（defer 先于本脚本执行，LectureCloud 已定义）。
 */
(function () {
  var COUNT_KEY = 'lecture_site_count_value';
  var COUNT_TS_KEY = 'lecture_site_count_ts'; // localStorage 时间戳
  var EXPIRE_MS = 5 * 60 * 1000; // 5 分钟过期
  var SITE_KEY = '__site__';     // 全站 PV 专用行（lecture_stats.url 主键）

  // 获取计数值元素
  function getEl() {
    return document.getElementById('site_pv_value');
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

  // 降级数据源：云端不可用时，读取随站发布的静态快照
  // site/lectures/visits.json（由 scripts/fetch_visits_snapshot.py 每日在 CI 中
  // 从 Supabase 取值累积、generate_frontend_data.py 出片）。
  //
  // 只填入纯数字，不附加日期或任何说明文字——与云端正常时的显示完全
  // 一致，避免页面出现「截至某日」这类别扭的标注（用户明确要求）。
  // 该值不写入 localStorage 缓存：下次访问仍应优先尝试实时值，
  // 云端恢复后自动回到实时计数。
  var _fallbackTried = false;
  function fallbackToArchive() {
    if (_fallbackTried) return;
    _fallbackTried = true;
    var cur = getEl();
    if (cur && /^\d+$/.test((cur.textContent || '').trim())) return; // 已有实时值
    fetch('lectures/visits.json', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        var el = getEl();
        if (!el || !d) return;
        if (/^\d+$/.test((el.textContent || '').trim())) return; // 期间实时值已到
        var n = parseInt(d.site_pv, 10);
        if (!isNaN(n) && n > 0) el.textContent = String(n);
      })
      .catch(function () { /* 无快照可用时保留占位符 */ });
  }

  // 云端计数：bump +1，成功则显示数据库权威值并写缓存
  function countViaCloud() {
    var el = getEl();
    if (!el) return;
    if (typeof LectureCloud === 'undefined' || !LectureCloud ||
        typeof LectureCloud.bump !== 'function') {
      fallbackToArchive();   // 云端层未加载（异常形态）→ 静态快照
      return;
    }
    LectureCloud.bump('visit', SITE_KEY, 1).then(function (row) {
      if (!row || typeof row.visits !== 'number') {
        fallbackToArchive(); // 云端禁用/请求失败 → 静态快照
        return;
      }
      var el2 = getEl();
      if (!el2) return;
      el2.textContent = String(row.visits);
      localStorage.setItem(COUNT_KEY, String(row.visits));
      localStorage.setItem(COUNT_TS_KEY, String(Date.now()));
    });
  }

  // 启动
  function init() {
    if (isCacheValid()) {
      // 缓存未过期 → 直接显示，不重复计数
      showCached();
    } else {
      // 缓存过期或首次访问 → 云端计数
      countViaCloud();
    }
  }

  // 确保 DOM 就绪后启动
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
