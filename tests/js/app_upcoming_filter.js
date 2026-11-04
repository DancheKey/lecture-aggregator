// 待开始讲座计数 + 筛选行为测试（2026-10-16 用户需求）。
//
// 需求（2026-10-16 用户拍板，A' 展现修订同日）
// --------------------------------------------
// 首页计数行内联一段「待开始 N 场」（A'：紧跟总量后、不独占行，默认统计区
// 保持一行；无待开始讲座时该段 v-if 隐藏）：点数字
// 叠加 upcomingOnly 筛选（可与年份/校区等叠加），再点数字或筛选片 × 取消，
// 「✕ 清除」一键复原。口径乙：未开场即算 = upcoming ∪ (tbd 且开始时间戳未到)，
// 不含进行中（已开场）、不含日期已过/无日期的 tbd；N 恒按全库 all 计、读 tick
// 实时跳变。筛选激活时远期折叠（>7 天）自动放平，取消后恢复。
//
// 锁定语义：
//   T1 upcomingCount 口径乙（upcoming/进行中/已结束/未来 tbd/过去 tbd/无日期）
//   T2 upcomingOnly 筛选 → 只剩待开始条
//   T3 与其他筛选叠加（campus，用户拍板：点击数字不清掉既有筛选）
//   T4 toggleUpcoming 开关 + 页码重置 + hasActiveFilter + clearFilters 复原
//   T5 foldedUnits：默认折远期，筛选激活时放平（用户拍板：自动展开）
//   T6 模板接线静态断言：计数行 v-if / toggleUpcoming 绑定 / 筛选片 v-if
//   T7 日期范围（2026-11-04 用户要求）：待开始视图下年份下拉换成「N天内 / 7天外」，
//      按**北京时间自然日**切分（今天=0）。含跨午夜边界、与 year 互斥、模板接线。
//
// 运行：node tests/js/app_upcoming_filter.js（纯 node，无浏览器无网络）
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');
const INDEX_HTML = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
const PARTS = (INDEX_HTML.match(/<script[^>]*\ssrc=["'](app[^"']*?\.js)(?:\?[^"']*)?["']/g) || [])
  .map(tag => tag.match(/src=["'](app[^"']*?\.js)/)[1]);

const noop = () => {};
const store = {};
// 固定「现在」= 2026-10-16 10:30 **北京时间**：当天 15:00 场 upcoming、09:00-11:30 场 ongoing
// ⚠ 必须用 Date.UTC(..., 小时-8) 构造（2026-10-31）：业务代码把 'YYYY-MM-DD HH:MM:SS'
// 当**北京时间字面量**解析（app.core.js::_bjMs）。若这里用 `new Date(2026,9,16,10,30)`
// （本地时区），本地 UTC+8 恰好自洽、CI（TZ=UTC）下则整体偏移 8 小时 → 15:00 场被
// 判成已结束，7 项断言全错。CI 实测就是在这里红的。
const FIXED_MS = Date.UTC(2026, 9, 16, 10 - 8, 30, 0);
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

// 数据集（覆盖口径乙全部分支，基准时刻 2026-10-16 10:30）：
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
const NOT_STARTED = 'U1,U2,U5,U9';

function makeCtx(over) {
  const cm = Object.assign(vm.runInContext('APP_STATE()', ctx), methods, {
    all: DATA, campus: '', college: '', year: '', query: '', searchField: '',
    speaker: '', showLikedOnly: false, onlineOnly: false, upcomingOnly: false,
    likedUrls: new Set(), pageSize: 25, currentPage: 1,
  }, over || {});
  Object.defineProperty(cm, 'filtered', {
    get: () => computed.filtered.call(cm), configurable: true });
  Object.defineProperty(cm, 'hasActiveFilter', {
    get: () => computed.hasActiveFilter.call(cm), configurable: true });
  return cm;
}
const titles = cm => cm.filtered.map(l => l.title).sort().join(',');
const count = cm => computed.upcomingCount.call(cm);

// ---------- T1：upcomingCount 口径乙 ----------
{
  const n = count(makeCtx());
  ok(n === 4, `T1 待开始数应=4（U1,U2,U5,U9），实际=${n}`);
  // 逐条判定：防总数对但集合错
  const cm = makeCtx();
  for (const l of DATA) {
    const started = methods.isNotStarted.call(cm, l, FIXED_MS);
    const want = NOT_STARTED.split(',').includes(l.title);
    ok(started === want, `T1 isNotStarted(${l.title}) 应=${want}，实际=${started}`);
  }
}
// ---------- T2：upcomingOnly 筛选只留待开始条 ----------
{
  const got = titles(makeCtx({ upcomingOnly: true }));
  ok(got === NOT_STARTED, `T2 upcomingOnly 应得 ${NOT_STARTED}，实际=${got}`);
  ok(titles(makeCtx()) === 'U1,U2,U3,U4,U5,U6,U7,U8,U9', 'T2 未激活时全量返回');
}
// ---------- T3：与 campus 叠加（点击数字不清掉既有筛选） ----------
{
  const got = titles(makeCtx({ upcomingOnly: true, campus: '大学城' }));
  ok(got === 'U1', `T3 待开始+大学城应得 U1，实际=${got}`);
  const got2 = titles(makeCtx({ upcomingOnly: true, campus: '石牌' }));
  ok(got2 === 'U2,U5,U9', `T3 待开始+石牌应得 U2,U5,U9，实际=${got2}`);
}
// ---------- T4：toggleUpcoming 开关 / 页码重置 / hasActiveFilter / clearFilters ----------
{
  const cm = makeCtx({ currentPage: 5 });
  ok(cm.upcomingOnly === false && cm.hasActiveFilter === false, 'T4 初始未激活');
  methods.toggleUpcoming.call(cm);
  ok(cm.upcomingOnly === true, 'T4 toggle 后 upcomingOnly=true');
  ok(cm.currentPage === 1, 'T4 toggle 应重置页码到 1');
  ok(cm.hasActiveFilter === true, 'T4 激活时 hasActiveFilter=true（✕ 清除亮起）');
  ok(titles(cm) === NOT_STARTED, 'T4 激活后 filtered=待开始条');
  methods.toggleUpcoming.call(cm);
  ok(cm.upcomingOnly === false, 'T4 再点数字应取消');
  methods.toggleUpcoming.call(cm);
  cm.campus = '石牌';
  methods.clearFilters.call(cm);
  ok(cm.upcomingOnly === false && cm.campus === '' && cm.currentPage === 1,
     'T4 clearFilters 应复原全部（含 upcomingOnly）');
  ok(cm.hasActiveFilter === false, 'T4 清除后 hasActiveFilter=false');
}
// ---------- T5：远期折叠默认折、筛选激活时放平 ----------
{
  const farUnit = (key, iso) => ({ type: 'single', recs: [{ lectureStart: iso }], key });
  const UNITS = [
    farUnit('a', '2026-11-05 09:00:00'),   // >7 天（cutoff=10-23 10:30）
    farUnit('b', '2026-11-06 09:00:00'),
    farUnit('c', '2026-11-07 09:00:00'),
    farUnit('d', '2026-10-17 09:00:00'),   // ≤7 天，不折
  ];
  const folded = computed.foldedUnits.call(makeCtx({ units: UNITS }));
  ok(folded.some(u => u.type === 'far-fold') && folded.length === 2,
     `T5 默认应把 3 条远期折成 1 容器（长度 2），实际=${folded.length} far=${folded.filter(u => u.type === 'far-fold').length}`);
  const unfolded = computed.foldedUnits.call(makeCtx({ units: UNITS, upcomingOnly: true }));
  ok(!unfolded.some(u => u.type === 'far-fold') && unfolded.length === 4,
     `T5b upcomingOnly 激活应放平（长度 4、无 far-fold），实际=${unfolded.length}`);
}
// ---------- T6：模板接线静态断言 ----------
{
  ok(/v-if="upcomingCount > 0 && !upcomingOnly"/.test(INDEX_HTML),
     'T6 计数行应有 v-if="upcomingCount > 0 && !upcomingOnly"（仅 upcoming 自身激活时隐藏以避免与筛选片重复；其余筛选态仍显示以保持第一行统计完整）');
  ok(/@click="toggleUpcoming\(\)"/.test(INDEX_HTML),
     'T6 计数行数字/筛选片应绑定 toggleUpcoming');
  ok(/v-if="upcomingOnly"/.test(INDEX_HTML),
     'T6 筛选片应有 v-if="upcomingOnly"');
  ok(INDEX_HTML.includes('待开始'), 'T6 计数行文案「待开始」存在');
}

// ---------- T7：日期范围（2026-11-04） ----------
{
  // T7a 基准 2026-10-16 10:30：「N天内」= 距现在不足 N×24 小时。
  //   U1/U9 今天稍后（4.5h / 5min）、U2 10-20 09:00（94.5h）、U5 10-25（205.5h）。
  const at = r => titles(makeCtx({ upcomingOnly: true, upcomingRange: r }));
  ok(at('1') === 'U1,U9', `T7a 「1天内」应只含 24 小时内（U1,U9），实际=${at('1')}`);
  ok(at('3') === 'U1,U9', `T7a 「3天内」不应含 94.5 小时后的 U2，实际=${at('3')}`);
  ok(at('4') === 'U1,U2,U9', `T7a 「4天内」应含 94.5 小时后的 U2，实际=${at('4')}`);
  ok(at('7') === 'U1,U2,U9', `T7a 「7天内」不应含 205.5 小时后的 U5，实际=${at('7')}`);
  ok(at('7+') === 'U5', `T7a 「7天外」应只含 U5，实际=${at('7+')}`);
  ok(at('') === NOT_STARTED, 'T7a 未选范围时等同于无范围筛选');
  // 未激活待开始时，range 残留不生效（防 URL/状态残留误伤全量列表）
  ok(titles(makeCtx({ upcomingRange: '1' })) === 'U1,U2,U3,U4,U5,U6,U7,U8,U9',
     'T7a range 在非待开始视图下不生效');

  // T7b 口径锁：**距现在 N×24 小时**，不是「日历日 N 天」。
  // 用户实测（2026-11-04）：明天 12:30 的场次卡片写「还有 23 时 54 分」，必须落在
  // 「1天内」；首版按自然日切分把它归到「2天内」，被当场指出。
  //   E1 = 23h59m 后 → 1天内      E2 = 24h1m 后 → 2天内
  //   E3 = 6天23h59m 后 → 7天内   E4 = 7天1m 后 → 7天外
  // 若有人把实现改回 Math.floor((ms - 今天零点) / 86400000)，E2 会掉进 1天内、E3 会掉进 7天外，立刻红。
  const EDGE = [
    rec('E1', { lectureStart: '2026-10-17 10:29:00' }),
    rec('E2', { lectureStart: '2026-10-17 10:31:00' }),
    rec('E3', { lectureStart: '2026-10-23 10:29:00' }),
    rec('E4', { lectureStart: '2026-10-23 10:31:00' }),
  ];
  const edge = r => titles(makeCtx({ all: EDGE, upcomingOnly: true, upcomingRange: r }));
  ok(edge('1') === 'E1', `T7b 「1天内」应含 23h59m 后的 E1，实际=${edge('1')}`);
  ok(edge('2') === 'E1,E2', `T7b 「2天内」应含 24h1m 后的 E2（超 24h 即出 1天内），实际=${edge('2')}`);
  ok(edge('7') === 'E1,E2,E3', `T7b 「7天内」应含 6天23h59m 后的 E3，实际=${edge('7')}`);
  ok(edge('7+') === 'E4', `T7b 「7天外」应含 7天1m 后的 E4，实际=${edge('7+')}`);

  // T7c 与 year 互斥：进入待开始清年份，退出清范围
  {
    const cm = makeCtx({ year: '2025' });
    methods.toggleUpcoming.call(cm);
    ok(cm.year === '' && cm.upcomingOnly === true,
       'T7c 进入待开始应清空 year（否则空列表且界面上看不到该条件）');
    cm.upcomingRange = '3';
    methods.toggleUpcoming.call(cm);
    ok(cm.upcomingRange === '' && cm.upcomingOnly === false,
       'T7c 退出待开始应清空 upcomingRange');
  }
  // T7d hasActiveFilter / clearFilters 覆盖范围档位
  {
    const cm = makeCtx({ upcomingOnly: true, upcomingRange: '2' });
    ok(cm.hasActiveFilter === true, 'T7d 仅选范围时 hasActiveFilter=true');
    methods.clearFilters.call(cm);
    ok(cm.upcomingRange === '' && cm.hasActiveFilter === false,
       'T7d clearFilters 应清掉范围档位');
  }
  // T7e 模板接线：待开始视图渲染范围下拉，否则渲染年份下拉
  ok(/v-if="upcomingOnly" v-model="upcomingRange"/.test(INDEX_HTML),
     'T7e 待开始视图应渲染 upcomingRange 下拉');
  ok(/v-else v-model="year"/.test(INDEX_HTML), 'T7e 非待开始视图应渲染 year 下拉');
  ok(INDEX_HTML.includes('7天外') && /v-for="n in 7"/.test(INDEX_HTML),
     'T7e 下拉应含「N天内」循环与「7天外」档');
}

// ---------- 汇总 ----------
if (errs.length) {
  console.log(`[FAIL] ${errs.length} 项`);
  errs.forEach(e => console.log(' - ' + e));
  process.exit(1);
}
console.log('[PASS] 待开始计数口径/筛选叠加/开关清除/远期放平/模板接线全部通过');
