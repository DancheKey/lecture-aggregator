// 进行中讲座计数 + 筛选行为测试（2026-10-29 用户需求，复用待开始逻辑）。
//
// 需求（2026-10-29 用户拍板，对称 app_upcoming_filter.js 的待开始逻辑）
// --------------------------------------------
// 首页计数行内联一段「进行中 N 场」（2026-10-20 补充、本次改为可点）：点数字
// 叠加 ongoingOnly 筛选（可与年份/校区等叠加），再点数字或筛选片 × 取消，
// 「✕ 清除」一键复原。口径：statusOf(l) === 'ongoing'（开始 ≤ 现在 ≤ 结束；
// 无结束或结束 ≤ 开始时按开始+2h 兜底，半日边界截断）。N 恒按全库 all 计、读 tick
// 实时跳变。筛选激活时统计段隐藏、改由筛选片显示「进行中讲座」。
//
// 锁定语义：
//   T1 ongoingCount 口径（U3 进行中；其余 upcoming/ended/tbd 不计）
//   T2 ongoingOnly 筛选 → 只剩进行中条
//   T3 与其他筛选叠加（campus，用户拍板：点击数字不清掉既有筛选）
//   T4 toggleOngoing 开关 + 页码重置 + hasActiveFilter + clearFilters 复原
//   T5 模板接线静态断言：计数行 v-if / toggleOngoing 绑定 / 筛选片 v-if / 文案「进行中讲座」
//
// 运行：node tests/js/app_ongoing_filter.js（纯 node，无浏览器无网络）
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');
const INDEX_HTML = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
const PARTS = (INDEX_HTML.match(/<script[^>]*\ssrc=["'](app[^"']*?\.js)(?:\?[^"']*)?["']/g) || [])
  .map(tag => tag.match(/src=["'](app[^"']*?\.js)/)[1]);

const noop = () => {};
const store = {};
// 固定「现在」= 2026-10-16 10:30（本地时区）：当天 15:00 场 upcoming、09:00-11:30 场 ongoing
const FIXED_MS = new Date(2026, 9, 16, 10, 30, 0).getTime();
class FakeDate extends Date {
  constructor(...args) {
    if (args.length === 0) super(FIXED_MS);
    else super(...args);
  }
  static now() { return FIXED_MS; }
}
const sandbox = {
  console,
  performance: { now: () => Date.now() },
  requestAnimationFrame: noop,
  setInterval: () => 0,
  setTimeout: () => 0,
  clearTimeout: noop,
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
    addEventListener: noop, removeEventListener: noop,
    createElement: () => ({ style: {}, select: noop }),
    body: { appendChild: noop, removeChild: noop },
  },
};
sandbox.window.top = sandbox.window;
sandbox.window.self = sandbox.window;
sandbox.globalThis = sandbox;
sandbox.Date = FakeDate;
sandbox.Vue = { createApp: () => ({ config: {}, mount: noop }) };

const ctx = vm.createContext(sandbox);
for (const f of PARTS) {
  vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf8'), ctx, { filename: f });
}
const computed = vm.runInContext('APP_COMPUTED', ctx);
const methods = vm.runInContext('APP_METHODS', ctx);

const errs = [];
const ok = (cond, msg) => { if (!cond) errs.push(msg); };

// 记录工厂：title 即断言键，默认未来日（2026-10-20 09:00 → upcoming）
function rec(title, extra) {
  return Object.assign({
    sourceUrl: `http://x.scnu.edu.cn/u_${title}`, title,
    lectureStart: '2026-10-20 09:00:00', topic: '', location: '地点',
    speaker: '讲者', speakerKeys: [], college: '文学院', campus: '石牌',
    lectureIndex: 1,
  }, extra || {});
}

// 数据集（与 app_upcoming_filter.js 同口径，基准时刻 2026-10-16 10:30）：
//   U1 upcoming 今天稍后   U2 upcoming 未来日    U3 ongoing（09:00-11:30）
//   U4 ended（昨天）       U5 tbd 未来日期       U6 tbd 过去日期
//   U7 tbd 今天占位已过    U8 tbd 无日期         U9 upcoming 5 分钟后
const DATA = [
  rec('U1', { lectureStart: '2026-10-16 15:00:00', campus: '大学城' }),
  rec('U2'),
  rec('U3', { lectureStart: '2026-10-16 09:00:00', lectureEnd: '2026-10-16 11:30:00' }),
  rec('U4', { lectureStart: '2026-10-15 15:00:00', lectureEnd: '2026-10-15 17:00:00' }),
  rec('U5', { lectureStart: '2026-10-25 00:00:00', timeUnknown: true }),
  rec('U6', { lectureStart: '2026-10-01 00:00:00', timeUnknown: true }),
  rec('U7', { lectureStart: '2026-10-16 00:00:00', timeUnknown: true }),
  rec('U8', { lectureStart: null, timeUnknown: true }),
  rec('U9', { lectureStart: '2026-10-16 10:35:00' }),
];
// 进行中：仅 U3（09:00-11:30 覆盖基准时刻 10:30）；其余 upcoming/ended/tbd 均不计
const ONGOING = 'U3';

function makeCtx(over) {
  const cm = Object.assign(vm.runInContext('APP_STATE()', ctx), methods, {
    all: DATA, campus: '', college: '', year: '', query: '', searchField: '',
    speaker: '', showLikedOnly: false, onlineOnly: false,
    upcomingOnly: false, ongoingOnly: false,
    likedUrls: new Set(), pageSize: 25, currentPage: 1,
  }, over || {});
  Object.defineProperty(cm, 'filtered', {
    get: () => computed.filtered.call(cm), configurable: true });
  Object.defineProperty(cm, 'hasActiveFilter', {
    get: () => computed.hasActiveFilter.call(cm), configurable: true });
  return cm;
}
const titles = cm => cm.filtered.map(l => l.title).sort().join(',');
const count = cm => computed.ongoingCount.call(cm);

// ---------- T1：ongoingCount 口径（仅 statusOf==='ongoing'） ----------
{
  const n = count(makeCtx());
  ok(n === 1, `T1 进行中数应=1（U3），实际=${n}`);
  // 逐条判定：防总数对但集合错
  const cm = makeCtx();
  for (const l of DATA) {
    const on = methods.isOngoing.call(cm, l, FIXED_MS);
    const want = ONGOING.split(',').includes(l.title);
    ok(on === want, `T1 isOngoing(${l.title}) 应=${want}，实际=${on}`);
  }
}
// ---------- T2：ongoingOnly 筛选只留进行中条 ----------
{
  const got = titles(makeCtx({ ongoingOnly: true }));
  ok(got === ONGOING, `T2 ongoingOnly 应得 ${ONGOING}，实际=${got}`);
  ok(titles(makeCtx()) === 'U1,U2,U3,U4,U5,U6,U7,U8,U9', 'T2 未激活时全量返回');
}
// ---------- T3：与 campus 叠加（点击数字不清掉既有筛选） ----------
{
  const got = titles(makeCtx({ ongoingOnly: true, campus: '石牌' }));
  ok(got === 'U3', `T3 进行中+石牌应得 U3，实际=${got}`);
  const got2 = titles(makeCtx({ ongoingOnly: true, campus: '大学城' }));
  ok(got2 === '', `T3 进行中+大学城应得 空，实际=${got2}`);
}
// ---------- T4：toggleOngoing 开关 / 页码重置 / hasActiveFilter / clearFilters ----------
{
  const cm = makeCtx({ currentPage: 5 });
  ok(cm.ongoingOnly === false && cm.hasActiveFilter === false, 'T4 初始未激活');
  methods.toggleOngoing.call(cm);
  ok(cm.ongoingOnly === true, 'T4 toggle 后 ongoingOnly=true');
  ok(cm.currentPage === 1, 'T4 toggle 应重置页码到 1');
  ok(cm.hasActiveFilter === true, 'T4 激活时 hasActiveFilter=true（✕ 清除亮起）');
  ok(titles(cm) === ONGOING, 'T4 激活后 filtered=进行中条');
  methods.toggleOngoing.call(cm);
  ok(cm.ongoingOnly === false, 'T4 再点数字应取消');
  methods.toggleOngoing.call(cm);
  cm.campus = '石牌';
  methods.clearFilters.call(cm);
  ok(cm.ongoingOnly === false && cm.campus === '' && cm.currentPage === 1,
     'T4 clearFilters 应复原全部（含 ongoingOnly）');
  ok(cm.hasActiveFilter === false, 'T4 清除后 hasActiveFilter=false');
}
// ---------- T5：模板接线静态断言 ----------
{
  ok(/v-if="ongoingCount > 0 && !ongoingOnly"/.test(INDEX_HTML),
     'T5 计数行应有 v-if="ongoingCount > 0 && !ongoingOnly"（仅 ongoing 自身激活时隐藏以避免与筛选片重复；其余筛选态仍显示以保持第一行统计完整）');
  ok(/@click="toggleOngoing\(\)"/.test(INDEX_HTML),
     'T5 计数行数字/筛选片应绑定 toggleOngoing');
  ok(/v-if="ongoingOnly"/.test(INDEX_HTML),
     'T5 筛选片应有 v-if="ongoingOnly"');
  ok(INDEX_HTML.includes('进行中讲座'), 'T5 筛选片文案「进行中讲座」存在');
}

// ---------- 汇总 ----------
if (errs.length) {
  console.log(`[FAIL] ${errs.length} 项`);
  errs.forEach(e => console.log(' - ' + e));
  process.exit(1);
}
console.log('[PASS] 进行中计数口径/筛选叠加/开关清除/模板接线全部通过');
