// 筛选状态 ↔ URL 同步测试（2026-10-14 第三批，step 实现 + 主审复核修复）。
//
// 锁定语义（实现在 site/app.core.js 尾部 + app.js 接线 + app.state.js 白名单）：
//   T1 parse：?campus=大学城&year=2024&q=量子&online=1&page=3 → 归一化 state
//   T2 非法值回落：campus 不在白名单 / year 非 4 位 / field 非枚举 / page<2 → 一律默认空值
//   T3 serialize 往返：state → 查询串 → parse → normalize 与原 state 一致
//   T4 serialize 空态 → 空查询串（收掉 ?）
//   T5 push/replace 语义：_flushUrlSync(true)→pushState，(false)→replaceState，
//      与当前 URL 相同 → 不写（去重）
//   T6 ⚠ 竞态回归（2026-10-14 修复）：防抖窗口内 popstate → 挂起的定时器必须被
//      清掉（修复前：定时器触发 _flushUrlSync 把「后退前」的旧筛选 push 回去，
//      抵消用户的后退操作，state 与 URL 分叉）
//   T7 回放抑制：_applyUrlFiltersFromUrl 期间 _scheduleUrlSync 全部被抑制
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
sandbox.Vue = { createApp: () => ({ config: {}, mount: noop }) };

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
  ok(n.college === '' && n.speaker === '' && n.searchField === '',
     `T1 缺省键回落空值: ${JSON.stringify(n)}`);
}

// ---- T2 非法值回落 ----
setUrl('?campus=月球&year=24&field=evil&page=1&online=0');
{
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.campus === '', `T2 非法 campus 回落: ${n.campus}`);
  ok(n.year === '', `T2 非 year 格式回落: ${n.year}`);
  ok(n.searchField === '', `T2 非法 field 回落: ${n.searchField}`);
  ok(n.currentPage === 1, `T2 page<2 回落: ${n.currentPage}`);
  ok(n.onlineOnly === false, `T2 online=0 回落: ${n.onlineOnly}`);
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
    query: ' 人工智慧 ', searchField: 'topic', onlineOnly: true, currentPage: 7,
  };
  const qs = vm.runInContext('serializeUrlFilters', ctx)(state);
  setUrl('?' + qs);
  const n = vm.runInContext('normalizeUrlFilters(parseUrlFilters(), CAMPUS_LIST)', ctx);
  ok(n.campus === '汕尾' && n.college === '文学院' && n.year === '2023'
     && n.speaker === '张三' && n.query === '人工智慧' && n.searchField === 'topic'
     && n.onlineOnly === true && n.currentPage === 7,
     `T3 往返一致: qs=${qs} n=${JSON.stringify(n)}`);
  ok(!/(^|&)q=$/.test(qs) && qs.includes('page=7'), `T3 只含非默认值: qs=${qs}`);
}

// ---- T4 空态 serialize → 空串 ----
{
  const qs = vm.runInContext('serializeUrlFilters', ctx)({
    campus: '', college: '', year: '', speaker: '', query: '',
    searchField: '', onlineOnly: false, currentPage: 1,
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

console.log('');
if (errs.length) {
  console.log('[FAIL] URL 同步 ' + errs.length + ' 项失败:');
  errs.forEach(e => console.log('  - ' + e));
  process.exit(1);
} else {
  console.log('[PASS] 筛选状态 ↔ URL 同步全部用例通过');
}
