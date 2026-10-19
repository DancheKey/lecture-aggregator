// 统计页「想听数」门禁（2026-10-19）
//
// 背景：用户要求把「想听」做成与点赞完全对称的指标——首页顶部一个筛选片，
// 统计页也能切换查看。首页那半边由 liked_filter_keys.js 覆盖；本套件锁统计页这半边。
//
// 改动要点：
//   · displayMode 增加第四态 'wants'，与 count/visits/likes 同一套排序 + 显示链路
//   · matrix / yearTotals / rows 三处都要累加 wants，且「跳过空记录」的判据必须
//     带上 wants——否则「只想听、没访问没点赞」的讲座整条被丢掉，想听数恒为 0
//   · 合计行原来写的是三层三元表达式，加第四态后抽成 totalValue()
//
// 运行：node tests/js/stats_wants.js（纯 node，无浏览器无网络）
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');
const STATS_HTML = fs.readFileSync(path.join(SITE, 'stats.html'), 'utf-8');

const noop = () => {};
const store = {};
const sandbox = {
  console,
  performance: { now: () => Date.now() },
  requestAnimationFrame: noop,
  setInterval: () => 0,
  setTimeout: () => 0,
  clearTimeout: noop,
  clearInterval: noop,
  location: { hostname: 'example.com', href: 'https://example.com/' },
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
    querySelector: () => null,
    getElementById: () => null,
    addEventListener: noop, removeEventListener: noop,
    createElement: () => ({ style: {}, select: noop }),
    body: { appendChild: noop, removeChild: noop },
  },
};
sandbox.window.top = sandbox.window;
sandbox.window.self = sandbox.window;
sandbox.globalThis = sandbox;
// 捕获 createApp 的选项对象：stats.js 末尾是 createApp({...}).mount('#app')，
// 直接跑会把组件挂到真实 DOM 上；这里只取 opts，手工装配实例。
sandbox.Vue = {
  createApp(opts) {
    sandbox.__opts = opts;
    return { config: {}, mount: () => ({}) };
  },
};

const ctx = vm.createContext(sandbox);
for (const f of ['app.cloud.js', 'stats.js']) {
  vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf-8'), ctx, { filename: f });
}

const opts = sandbox.__opts;
const errs = [];
const ok = (cond, msg) => { if (!cond) errs.push(msg); };

// 物化实例：data + methods + computed（computed 挂 getter，互相引用走 this）
function makeSelf(over) {
  const self = Object.assign({}, opts.data(), opts.methods, over || {});
  for (const [k, fn] of Object.entries(opts.computed)) {
    Object.defineProperty(self, k, { get: () => fn.call(self), configurable: true });
  }
  return self;
}

// ---------- 夹具：三条讲座，覆盖「只想听」「联合发布」「普通」 ----------
const SUMMARY = {
  lectureCount: 3,
  years: ['2026', '2025'],
  matrix: { '数科院': { 2026: 2 }, '研究生院': { 2025: 1 } },
  campusMatrix: { '石牌': { '数科院': { 2026: 2 } } },
  campusMap: { '数科院': '石牌', '研究生院': '大学城' },
  campusActualMap: {},
  lectures: [
    { u: 'http://a/1', k: 'http://a/1~1', y: '2026', c: '数科院', cs: ['数科院'], s: 0 },
    // 联合发布：cs 两个单位，想听数应在两边各计一次（与点赞/访问同口径）
    { u: 'http://b/2', k: 'http://b/2', y: '2026', c: '数科院', cs: ['数科院', '研究生院'], s: 0 },
    { u: 'http://c/3', k: 'http://c/3', y: '2025', c: '研究生院', cs: ['研究生院'], s: 0 },
  ],
};
const STATS = {
  // 只想听：visits/likes 都是 0，若「跳过空记录」的判据漏了 wants 就会被整条丢掉
  'http://a/1~1': { visits: 0, likes: 0, wants: 5 },
  'http://b/2': { visits: 3, likes: 1, wants: 2 },
  'http://c/3': { visits: 1, likes: 0, wants: 0 },
};

// ---------- test_01：matrix 累加 wants，含联合单位展开 ----------
{
  const self = makeSelf({ summary: SUMMARY, lectureStats: STATS });
  const m = self.matrix;
  ok(m['数科院'] && m['数科院']['2026'] && m['数科院']['2026'].wants === 7,
     `test_01 数科院 2026 想听数应为 7（5+2），实际 ${m['数科院'] && m['数科院']['2026'] && m['数科院']['2026'].wants}`);
  ok(m['研究生院'] && m['研究生院']['2026'] && m['研究生院']['2026'].wants === 2,
     `test_01 联合发布的想听数应在来源单位也计一次（应为 2），实际 ${m['研究生院'] && m['研究生院']['2026'] && m['研究生院']['2026'].wants}`);
  ok(m['研究生院']['2025'].wants === 0,
     `test_01 只有访问没有想听的年份想听数应为 0，实际 ${m['研究生院']['2025'].wants}`);
  ok(m['数科院']['2026'].likes === 1 && m['数科院']['2026'].visits === 3,
     'test_01 加 wants 不得影响既有 likes/visits 累加（应为 1 / 3）');
}

// ---------- test_02：只想听的记录不得被「空记录」判据跳过 ----------
{
  const self = makeSelf({ summary: SUMMARY, lectureStats: STATS });
  const m = self.matrix;
  ok(m['数科院']['2026'].wants === 7,
     'test_02 判据写成 `!st.visits && !st.likes` 时，A~1（只想听）会被整条丢掉 → 想听数变 2');
}

// ---------- test_03：rows 的 wantsTotal 与 grandWants ----------
{
  const self = makeSelf({ summary: SUMMARY, lectureStats: STATS });
  const rows = self.rows;
  const sk = rows.find(r => r.college === '数科院');
  const yjs = rows.find(r => r.college === '研究生院');
  ok(sk && sk.wantsTotal === 7, `test_03 数科院行想听合计应为 7，实际 ${sk && sk.wantsTotal}`);
  ok(yjs && yjs.wantsTotal === 2, `test_03 研究生院行想听合计应为 2，实际 ${yjs && yjs.wantsTotal}`);
  ok(self.grandWants === 9,
     `test_03 全站想听合计应为 9（5 + 2×2），实际 ${self.grandWants}`);
}

// ---------- test_04：cellValue / totalValue 支持 wants 模式 ----------
{
  const self = makeSelf({ summary: SUMMARY, lectureStats: STATS, displayMode: 'wants' });
  ok(self.cellValue({ count: 1, visits: 2, likes: 3, wants: 4 }) === 4,
     'test_04 wants 模式下 cellValue 应取 wants');
  ok(self.totalValue({ count: 1, visits: 2, likes: 3, wants: 4 }) === 4,
     'test_04 wants 模式下 totalValue 应取 wants');
  ok(self.cellValue({ count: 1, visits: 2, likes: 3 }) === 0,
     'test_04 缺 wants 字段时按 0 处理（不得回退成 count，否则数字看着像想听量）');
}

// ---------- test_05：toggleSort('wants') 切换显示模式 + 排序 ----------
{
  const self = makeSelf({ summary: SUMMARY, lectureStats: STATS });
  self.toggleSort('wants');
  ok(self.displayMode === 'wants', `test_05 点「按想听数」后 displayMode 应为 wants，实际 ${self.displayMode}`);
  ok(self.sortBy.key === 'wants' && self.sortBy.order === 'desc',
     `test_05 首次点击应按想听数降序，实际 ${JSON.stringify(self.sortBy)}`);
  self.toggleSort('wants');
  ok(self.sortBy.order === 'asc', `test_05 二次点击应切升序，实际 ${self.sortBy.order}`);
  // 年份列排序不得改变显示模式（与 visits/likes 同一规则）
  self.toggleSort('2026');
  ok(self.displayMode === 'wants', `test_05 点年份列不应改变显示模式，实际 ${self.displayMode}`);
  // 想听模式下的年份排序取 cells[idx].wants
  const rows = self.rows;
  ok(Array.isArray(rows) && rows.length === 2, 'test_05 想听模式下仍应产出两行');
}

// ---------- test_06：校区筛选下想听数同口径过滤 ----------
{
  const self = makeSelf({ summary: SUMMARY, lectureStats: STATS, campusFilter: '石牌' });
  const m = self.matrix;
  ok(m['数科院'] && m['数科院']['2026'].wants === 7,
     `test_06 石牌校区内数科院想听数应为 7，实际 ${m['数科院'] && m['数科院']['2026'].wants}`);
  ok(!m['研究生院'] || !m['研究生院']['2025'],
     'test_06 选中石牌时研究生院（大学城）的行不应带出 2025 的计数');
}

// ---------- test_07：stats.html 真的接上了想听列 ----------
{
  ok(/toggleSort\('wants'\)/.test(STATS_HTML),
     "test_07 stats.html 缺少「按想听数」按钮（toggleSort('wants')）");
  ok(/按想听数/.test(STATS_HTML), 'test_07 stats.html 缺少「按想听数」文案');
  const wantsCells = (STATS_HTML.match(/displayMode === 'wants'/g) || []).length;
  ok(wantsCells >= 4,
     `test_07 桌面/移动的表头、行、合计共 4 处需要 wants 分支，实际 ${wantsCells} 处`);
  ok(/row\.wantsTotal/.test(STATS_HTML), 'test_07 stats.html 行单元格未接 row.wantsTotal');
  ok(/grandWants/.test(STATS_HTML), 'test_07 stats.html 合计行未接 grandWants');
  ok(!/displayMode === 'count' \? n\.count :/.test(STATS_HTML),
     'test_07 合计行年度值应走 totalValue()，不要再写三元链（加第四态后不可读且易漏）');
}

if (errs.length) {
  console.log('[FAIL]');
  errs.forEach(e => console.log(' -', e));
  process.exit(1);
}
console.log('[PASS] 统计页想听数：矩阵/年度合计/行合计/显示模式/排序/校区筛选 全部对齐');
