// 筛选状态 ↔ URL 同步测试（2026-10-14 第三批，step 实现 + 主审复核修复）。
//
// 锁定语义（实现在 site/app.core.js 尾部 + app.js 接线 + app.state.js 白名单）：
//   T1 parse：?campus=大学城&year=2024&q=量子&online=1&upcoming=1&page=3 → 归一化 state
//   T2 非法值回落：campus 不在白名单 / year 非 4 位 / field 非枚举 / page<2 → 一律默认空值
//   T3 serialize 往返：state → 查询串 → parse → normalize 与原 state 一致
//   T4 serialize 空态 → 空查询串（收掉 ?）
//   T5 push/replace 语义：_flushUrlSync(true)→pushState，(false)→replaceState，
//      与当前 URL 相同 → 不写（去重）
//   T6 ⚠ 竞态回归（2026-10-14 修复）：防抖窗口内 popstate → 挂起的定时器必须被
//      清掉（修复前：定时器触发 _flushUrlSync 把「后退前」的旧筛选 push 回去，
//      抵消用户的后退操作，state 与 URL 分叉）
//   T7 回放抑制：_applyUrlFiltersFromUrl 期间 _scheduleUrlSync 全部被抑制
//   T8 页码上界 + watch.totalPages 钳位（P1-5：?page=99999 与失效旧深链接）
//   T9 upcoming 参数（2026-10-16 待开始筛选）：serialize 只在激活时带 upcoming=1、
//      parse 回放归一化为 upcomingOnly，且 watch.upcomingOnly 已接线
//   T10 ongoing 参数（2026-10-30 补）：同上三条链路 + 与 upcoming 同真时归一化层
//      保留 ongoing（互斥枚举，同真必空集）
//   T11 popstate 回放恢复 upcomingRange（2026-11-05 评审 #3）：解析/序列化两条
//      链路都有 range，唯独回放漏赋；URL 有 ?range=3 而 state 为空会展示全部待开始
//   T12 翻页也要能后退（2026-11-14 用户确认）：watch.currentPage 必须以 push
//      语义调度，翻页产生历史条目，popstate 回放不得再 push（否则后退被抵消）
//
// 运行：node tests/js/app_url_sync.js（纯 node，无浏览器无网络）
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');
const INDEX_HTML = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
const PARTS = (INDEX_HTML.match(/<script[^>]*\ssrc=["'](app[^"']*?\.js)(?:\?[^"']*)?["']/g) || [])
  .map(tag => tag.match(/src=["'](app[^"']*?\.js)/)[1]);

const noop = () => {};
const store = {};
// 可控时钟：记录 setTimeout/clearTimeout，手动触发到期回调
let timers = [];
let timerSeq = 0;
const sandbox = {
  console,
  URLSearchParams,   // parse/serialize 依赖（node 内置，需显式注入 vm 沙箱）
  performance: { now: () => Date.now() },
  requestAnimationFrame: noop,
  setInterval: () => 0,
  setTimeout: (fn, ms) => { timers.push({ id: ++timerSeq, fn, ms: ms || 0 }); return timerSeq; },
  clearTimeout: (id) => { timers = timers.filter(t => t.id !== id); },
  location: { hostname: 'example.com', href: 'https://example.com/', pathname: '/', search: '' },
  navigator: { clipboard: null },
  fetch: () => Promise.resolve({ ok: false, json: () => ({}) }),
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
  },
  window: {
    top: null, self: null, innerWidth: 1280,
    addEventListener: noop, removeEventListener: noop,
    scrollTo: noop, scrollY: 0, pageYOffset: 0,
    isSecureContext: false,
  },
  document: {
    documentElement: { style: {} },
    querySelectorAll: () => [],
    addEventListener: noop, removeEventListener: noop,
    createElement: () => ({ style: {}, select: noop }),
    body: { appendChild: noop, removeChild: noop },
  },
  history: {
    _push: [], _replace: [],
    pushState(s, t, url) { this._push.push(url); },
    replaceState(s, t, url) { this._replace.push(url); },
  },
};
sandbox.window.top = sandbox.window;
sandbox.window.self = sandbox.window;
sandbox.globalThis = sandbox;
// 捕获 createApp 的 opts：T8 要取 watch.totalPages（P1-5 钳位接线）
let appOpts = null;
sandbox.Vue = { createApp: (opts) => { appOpts = opts; return { config: {}, mount: noop }; } };

const ctx = vm.createContext(sandbox);
for (const f of PARTS) {
  vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf8'), ctx, { filename: f });
}
const methods = vm.runInContext('APP_METHODS', ctx);

const errs = [];
const ok = (cond, msg) => { if (!cond) errs.push(msg); };
const setUrl = (search) => {
  sandbox.location.search = search;
  sandbox.location.href = 'https://example.com' + (search || '/');
};
const fireTimers = () => { const due = timers; timers = []; due.forEach(t => t.fn()); };

// ---- T1 parse + normalize ----
setUrl('?campus=大学城&year=2024&q=%E9%87%8F%E5%AD%90&online=1&page=3');
{
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.campus === '大学城', `T1 campus 解析: ${n.campus}`);
  ok(n.year === '2024', `T1 year 解析: ${n.year}`);
  ok(n.query === '量子', `T1 q 解析(解码): ${n.query}`);
  ok(n.onlineOnly === true, `T1 online 解析: ${n.onlineOnly}`);
  ok(n.currentPage === 3, `T1 page 解析: ${n.currentPage}`);
  ok(n.college === '' && n.speaker === '' && n.searchField === '' && n.upcomingRange === '',
     `T1 缺省键回落空值: ${JSON.stringify(n)}`);
}
setUrl('?upcoming=1&range=3');
{
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.upcomingOnly === true && n.upcomingRange === '3',
     `T1 range 解析: upcoming=${n.upcomingOnly} range=${n.upcomingRange}`);
}
// 待开始视图下年份下拉被替换成「N天内」，残留 year 会变成看不见却生效的条件 → 归一化丢弃
setUrl('?upcoming=1&year=2024&range=2');
{
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.year === '', `T1 待开始视图应丢弃 year: ${n.year}`);
  ok(n.upcomingRange === '2', `T1 待开始视图保留 range: ${n.upcomingRange}`);
}
// 无 upcoming 时 range 是无主参数，丢弃
setUrl('?range=5');
{
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.upcomingOnly === false && n.upcomingRange === '',
     `T1 无待开始视图应丢弃 range: ${n.upcomingRange}`);
}

// ---- T2 非法值回落 ----
setUrl('?campus=月球&year=24&field=evil&page=1&online=0&upcoming=0');
{
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.campus === '', `T2 非法 campus 回落: ${n.campus}`);
  ok(n.year === '', `T2 非 year 格式回落: ${n.year}`);
  ok(n.searchField === '', `T2 非法 field 回落: ${n.searchField}`);
  ok(n.currentPage === 1, `T2 page<2 回落: ${n.currentPage}`);
  ok(n.onlineOnly === false, `T2 online=0 回落: ${n.onlineOnly}`);
  ok(n.upcomingOnly === false, `T2 upcoming=0 回落: ${n.upcomingOnly}`);
}
setUrl('?field=topic');
{
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.searchField === 'topic', `T2 合法 field 保留: ${n.searchField}`);
}

// ---- T3 serialize 往返 ----
{
  const state = {
    campus: '汕尾', college: '文学院', year: '2023', speaker: '张三',
    query: ' 人工智慧 ', searchField: 'topic', onlineOnly: true,
    upcomingOnly: false, currentPage: 7,
  };
  const qs = vm.runInContext('serializeUrlFilters', ctx)(state);
  setUrl('?' + qs);
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.campus === '汕尾' && n.college === '文学院' && n.year === '2023'
     && n.speaker === '张三' && n.query === '人工智慧' && n.searchField === 'topic'
     && n.onlineOnly === true && n.upcomingOnly === false && n.currentPage === 7,
     `T3 往返一致: qs=${qs} n=${JSON.stringify(n)}`);
  ok(!/(^|&)q=$/.test(qs) && qs.includes('page=7') && qs.includes('year=2023'),
     `T3 只含非默认值: qs=${qs}`);
}
// 待开始 + 日期范围的往返（2026-11-04）：range 依附 upcoming，year 不同存
{
  const state = {
    campus: '', college: '', year: '', speaker: '', query: '',
    searchField: '', onlineOnly: false, upcomingOnly: true, upcomingRange: '3',
    currentPage: 1,
  };
  const qs = vm.runInContext('serializeUrlFilters', ctx)(state);
  ok(qs === 'upcoming=1&range=3', `T3b 范围往返查询串: "${qs}"`);
  setUrl('?' + qs);
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.upcomingOnly === true && n.upcomingRange === '3' && n.year === '',
     `T3b 范围往返一致: ${JSON.stringify(n)}`);
  // 未激活待开始时 range 不写 URL（避免无主参数）
  const qs2 = vm.runInContext('serializeUrlFilters', ctx)(
    Object.assign({}, state, { upcomingOnly: false }));
  ok(!qs2.includes('range'), `T3b 非待开始视图不应写 range: "${qs2}"`);
}

// ---- T4 空态 serialize → 空串 ----
{
  const qs = vm.runInContext('serializeUrlFilters', ctx)({
    campus: '', college: '', year: '', speaker: '', query: '',
    searchField: '', onlineOnly: false, upcomingOnly: false, currentPage: 1,
  });
  ok(qs === '', `T4 空态查询串应收空: "${qs}"`);
}

// ---- T5 push/replace/去重 ----
{
  const vmThis = {
    campus: '石牌', college: '', year: '', speaker: '', query: '',
    searchField: '', onlineOnly: false, currentPage: 1,
    _urlSyncSuppress: false,
  };
  sandbox.history._push = [];
  sandbox.history._replace = [];
  setUrl('');
  methods._flushUrlSync.call(vmThis, true);
  ok(sandbox.history._push.length === 1 && sandbox.history._replace.length === 0,
     `T5 push 语义: push=${sandbox.history._push.length} replace=${sandbox.history._replace.length}`);
  ok(sandbox.history._push[0] === '?campus=%E7%9F%B3%E7%89%8C',
     `T5 push 目标: ${sandbox.history._push[0]}`);
  // 相同查询串 → 去重不写
  setUrl('?campus=%E7%9F%B3%E7%89%8C');
  methods._flushUrlSync.call(vmThis, true);
  ok(sandbox.history._push.length === 1, `T5 去重：相同 URL 不重复写`);
  // 清空筛选 → 收掉查询串（replace 不 push）
  vmThis.campus = '';
  methods._flushUrlSync.call(vmThis, false);
  ok(sandbox.history._replace.length === 1 && sandbox.history._replace[0] === '/',
     `T5 清空收查询串(replace): ${sandbox.history._replace[0]}`);
}

// ---- T6 竞态回归：防抖窗口内 popstate，挂起定时器必须被清掉 ----
{
  setUrl('?campus=%E5%A4%A7%E5%AD%A6%E5%9F%8E');
  const nextTick = (fn) => fn();   // 立即执行版 nextTick
  const vmThis = {
    campus: '石牌', college: '', year: '', speaker: '', query: '',
    searchField: '', onlineOnly: false, currentPage: 2,
    campusList: ['', '石牌', '大学城', '佛山', '汕尾', '校级'],
    _urlSyncSuppress: false, _urlSyncPendingPush: false, _urlSyncTimer: 0,
    _flushUrlSync: methods._flushUrlSync,   // 真实组件 this 上带全部方法
    $nextTick: nextTick,
  };
  // 模拟用户改筛选：push 调度进 450ms 防抖窗口
  methods._scheduleUrlSync.call(vmThis, true, 450);
  ok(timers.length === 1 && vmThis._urlSyncPendingPush === true,
     `T6 前置：防抖已挂起 push=${vmThis._urlSyncPendingPush}`);
  // 此时用户按「后退」→ popstate 回放
  sandbox.history._push = [];
  methods._applyUrlFiltersFromUrl.call(vmThis);
  ok(timers.length === 0, `T6 回放后挂起定时器应被清除，实存 ${timers.length}`);
  ok(vmThis._urlSyncPendingPush === false, `T6 回放后 pendingPush 应复位`);
  // 即便手动触发残留 flush（双保险路径），suppress 期间也不得写
  methods._flushUrlSync.call(vmThis, true);
  ok(sandbox.history._push.length === 0,
     `T6 suppress 期间 flush 不得写 history`);
  // nextTick 放行后恢复写回能力
  vmThis.campus = '佛山';
  methods._scheduleUrlSync.call(vmThis, true, 120);
  ok(timers.length === 1, `T6 放行后可重新调度`);
  fireTimers();
  ok(sandbox.history._push.length === 1, `T6 放行后 flush 正常写入`);
}

// ---- T7 回放抑制：suppress 期间 _scheduleUrlSync 直接忽略 ----
{
  const vmThis = {
    campus: '', college: '', year: '', speaker: '', query: '',
    searchField: '', onlineOnly: false, currentPage: 1,
    campusList: ['', '石牌', '大学城', '佛山', '汕尾', '校级'],
    _urlSyncSuppress: true, _urlSyncPendingPush: false, _urlSyncTimer: 0,
    _flushUrlSync: methods._flushUrlSync,
    $nextTick: (fn) => fn(),
  };
  timers = [];
  methods._scheduleUrlSync.call(vmThis, true, 120);
  ok(timers.length === 0 && vmThis._urlSyncPendingPush === false,
     `T7 suppress 期间调度被忽略`);
}

// ---- T8 页码上界 + totalPages 钳位（2026-10-14 评审 P1-5）----
// 场景：① ?page=99999 天文数字不得进 state；② 数据/筛选变化使 totalPages 变小时，
// currentPage 必须被钳回末页（否则 pagedUnits 切片越界渲染空白列表）。
setUrl('?page=99999');
{
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.currentPage === 1, `T8 天文 page 回落: ${n.currentPage}`);
}
setUrl('?page=3');
{
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.currentPage === 3, `T8 合法 page 保留: ${n.currentPage}`);
}
setUrl('');
{
  const w = appOpts && appOpts.watch;
  ok(!!w && typeof w.totalPages === 'function', 'T8 watch.totalPages 未接线（P1-5 钳位缺失）');
  if (w && typeof w.totalPages === 'function') {
    const over = { currentPage: 5 };
    w.totalPages.call(over, 3);
    ok(over.currentPage === 3, `T8 越界钳位: currentPage=${over.currentPage}（应钳为 3）`);
    const within = { currentPage: 2 };
    w.totalPages.call(within, 7);
    ok(within.currentPage === 2, `T8 未越界不误动: currentPage=${within.currentPage}`);
  }
}

// ---- T9 upcoming 参数（2026-10-16 待开始筛选进 URL）----
{
  const qsOn = vm.runInContext('serializeUrlFilters', ctx)({
    campus: '', college: '', year: '', speaker: '', query: '',
    searchField: '', onlineOnly: false, upcomingOnly: true, currentPage: 1,
  });
  ok(qsOn === 'upcoming=1', `T9 激活时 serialize 应=upcoming=1: "${qsOn}"`);
  setUrl('?upcoming=1');
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.upcomingOnly === true, `T9 parse upcoming=1 → upcomingOnly=true: ${n.upcomingOnly}`);
  setUrl('');
  const w = appOpts && appOpts.watch;
  ok(!!w && typeof w.upcomingOnly === 'function',
     'T9 watch.upcomingOnly 未接线（URL 同步缺失）');
}

// ---- T10 ongoing 参数（2026-10-29 进行中筛选进 URL；2026-10-30 补测）----
// 该功能上线时只补了筛选逻辑的测试（app_ongoing_filter.js T1-T5），URL 三条链路
// （parse / serialize / watch）一个断言都没有——现在好使，回归会无声无息。
{
  // ① serialize：只在激活时带 ongoing=1
  const qsOn = vm.runInContext('serializeUrlFilters', ctx)({
    campus: '', college: '', year: '', speaker: '', query: '',
    searchField: '', onlineOnly: false, upcomingOnly: false, ongoingOnly: true, currentPage: 1,
  });
  ok(qsOn === 'ongoing=1', `T10 激活时 serialize 应=ongoing=1: "${qsOn}"`);

  const qsOff = vm.runInContext('serializeUrlFilters', ctx)({
    campus: '', college: '', year: '', speaker: '', query: '',
    searchField: '', onlineOnly: false, upcomingOnly: false, ongoingOnly: false, currentPage: 1,
  });
  ok(qsOff === '', `T10 未激活时不应带 ongoing: "${qsOff}"`);

  // ② parse → normalize
  setUrl('?ongoing=1');
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.ongoingOnly === true, `T10 parse ongoing=1 → ongoingOnly=true: ${n.ongoingOnly}`);

  // ③ 矛盾输入归一：两个同真时保留 ongoing、清 upcoming（否则 filtered 必空集）
  setUrl('?ongoing=1&upcoming=1');
  const both = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(both.ongoingOnly === true && both.upcomingOnly === false,
     `T10 矛盾输入应归一到 ongoing：ongoing=${both.ongoingOnly} upcoming=${both.upcomingOnly}`);

  // ④ watch 接线
  setUrl('');
  const w = appOpts && appOpts.watch;
  ok(!!w && typeof w.ongoingOnly === 'function',
     'T10 watch.ongoingOnly 未接线（URL 同步缺失）');
}

// ---- T11 popstate 回放必须带上 upcomingRange（2026-11-05 评审 #3）----
// 场景：选「3天内」→ 清除 → 后退，URL 有 ?upcoming=1&range=3 而 state 为空。
// 修复前 _applyUrlFiltersFromUrl 漏赋 upcomingRange，筛选片亮着但下拉未选、
// 列表展示全部待开始；刷新才恢复。解析/序列化两条链路本来就有 range，
// 只漏了回放这一条，故本例专门锁 popstate round-trip。
{
  setUrl('?upcoming=1&range=3');
  const vmThis = {
    campus: '', college: '', year: '', speaker: '', query: '',
    searchField: '', onlineOnly: false, upcomingOnly: false, ongoingOnly: false,
    upcomingRange: '', currentPage: 1,
    campusList: ['', '石牌', '大学城', '佛山', '汕尾', '校级'],
    _urlSyncSuppress: false, _urlSyncPendingPush: false, _urlSyncTimer: 0,
    _flushUrlSync: methods._flushUrlSync,
    $nextTick: (fn) => fn(),
  };
  timers = [];
  methods._applyUrlFiltersFromUrl.call(vmThis);
  ok(vmThis.upcomingOnly === true && vmThis.upcomingRange === '3',
     `T11 回放应恢复 range=3，实得 upcoming=${vmThis.upcomingOnly} range=${vmThis.upcomingRange}`);
  // 反向：URL 无 range 时不得残留旧值（否则「后退到无范围」仍被旧筛选卡住）
  setUrl('?upcoming=1');
  vmThis.upcomingRange = '7';
  methods._applyUrlFiltersFromUrl.call(vmThis);
  ok(vmThis.upcomingRange === '',
     `T11 无 range 的 URL 应清空残留范围，实得 "${vmThis.upcomingRange}"`);
}

// ---- T12 翻页也要能后退（2026-11-14 用户确认「翻页也要能后退」）----
// 原实现页码用 replaceState（不进历史栈），按后退会直接离开当前筛选视图。
// 改成 push 后每页都是可回放的历史条目；同时回放路径不得再 push，否则后退被抵消。
{
  const w = appOpts && appOpts.watch;
  ok(!!w && typeof w.currentPage === 'function', 'T12 watch.currentPage 未接线');
  if (w && typeof w.currentPage === 'function') {
    // ① watcher 必须以 push 语义调度 URL 同步
    const calls = [];
    w.currentPage.call({
      _ensureLongTextAll: () => {},
      _scheduleUrlSync: (push, delay) => calls.push([push, delay]),
    });
    ok(calls.length === 1 && calls[0][0] === true,
       `T12 翻页必须 push（可后退）：${JSON.stringify(calls)}`);

    // ② 端到端：翻到第 3 页产生历史条目，后退到 ?page=2 时回放不得新增条目
    sandbox.history._push = [];
    sandbox.history._replace = [];
    timers = [];
    setUrl('');
    const vmThis = {
      campus: '', college: '', year: '', speaker: '', query: '', searchField: '',
      onlineOnly: false, upcomingOnly: false, ongoingOnly: false, upcomingRange: '',
      currentPage: 3,
      campusList: ['石牌', '大学城', '佛山', '汕尾', '校级'],
      _urlSyncSuppress: false, _urlSyncPendingPush: false, _urlSyncTimer: 0,
      _flushUrlSync: methods._flushUrlSync,
      $nextTick: (fn) => fn(),
    };
    methods._scheduleUrlSync.call(vmThis, true, 120);
    fireTimers();
    ok(sandbox.history._push.length === 1 && sandbox.history._push[0] === '?page=3',
       `T12 翻页应 push ?page=3：${JSON.stringify(sandbox.history._push)}`);
    setUrl('?page=2');
    methods._applyUrlFiltersFromUrl.call(vmThis);
    ok(sandbox.history._push.length === 1,
       `T12 后退回放不得新增历史条目：${JSON.stringify(sandbox.history._push)}`);
    ok(vmThis.currentPage === 2,
       `T12 后退应回放页码 2，实得 ${vmThis.currentPage}`);
  }
}

console.log('');
if (errs.length) {
  console.log('[FAIL] URL 同步 ' + errs.length + ' 项失败:');
  errs.forEach(e => console.log('  - ' + e));
  process.exit(1);
} else {
  console.log('[PASS] 筛选状态 ↔ URL 同步全部用例通过');
}
