// 校区筛选行为测试（2026-10-13）：校级讲座按实际举办校区（actualCampus）参与筛选。
//
// 背景
// ----
// campus 字段是 sources.yaml 的**归口管理单位**（校级单位如研究生院 campus=校级），
// 不代表举办地。用户要求校级讲座按 location 推导的实际举办校区参与校区筛选
// （点「大学城」能出研究生院讲座），同时「校级」筛选项保持全部校级记录
// （2026-10-13 用户拍板：校级=归口视角，全部保留）。
//
// 推导规则本体在 scraper/field_vocab.actual_campus（Python 侧，南海归佛山），
// 由 test_frontend_consistency 锁双端一致；本测试锁定**前端消费侧**语义：
//   T1 校级+actualCampus → 点对应校区筛得出
//   T2 「校级」筛选不受推导影响 → 全部校级记录仍在（含已推导与未推导）
//   T3 非校级 campus 匹配、sources[].campus 匹配（两条回归不破坏）
//   T4 佛山（南海归佛山）筛选
//   T5 无筛选时全量返回
//   T6 交叉筛选（校区+学院）语义不串
//   T7 actualCampus 不造成「张冠李戴」：未推导的校级条在任何具体校区筛里都不出现
//
// 运行：node tests/js/app_campus_filter.js（纯 node，无浏览器无网络）
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');
const INDEX_HTML = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
const PARTS = (INDEX_HTML.match(/<script[^>]*\ssrc=["'](app[^"']*?\.js)(?:\?[^"']*)?["']/g) || [])
  .map(tag => tag.match(/src=["'](app[^"']*?\.js)/)[1]);

const noop = () => {};
const store = {};
let FIXED_MS = new Date(2026, 9, 13, 12, 30, 0).getTime();
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

// 记录工厂：全部用同一 lectureStart（筛选不依赖时刻），title 即断言键
function rec(title, campus, extra) {
  return Object.assign({
    sourceUrl: `http://x.scnu.edu.cn/cf_${title}`, title,
    lectureStart: '2026-10-15 09:00:00', topic: '', location: '地点',
    speaker: '讲者', speakerKeys: [], college: '文学院', campus,
    lectureIndex: 1,
  }, extra || {});
}

// 数据集（覆盖全部筛选路径）：
//   A 校级·大学城   B 校级·无推导   C 校级·南海(→佛山)   D 石牌学院
//   E 大学城学院    F 校级·石牌      G 校级·来源跨校区(sources 命中石牌)
const DATA = [
  rec('A', '校级', { actualCampus: '大学城' }),
  rec('B', '校级', { location: '待公布' }),
  rec('C', '校级', { actualCampus: '佛山' }),
  rec('D', '石牌'),
  rec('E', '大学城'),
  rec('F', '校级', { actualCampus: '石牌' }),
  rec('G', '校级', { sources: [{ sourceUrl: 'http://x.scnu.edu.cn/g_src', college: '法学院', campus: '石牌', title: '来源页' }] }),
];

function makeCtx(over) {
  const cm = Object.assign(vm.runInContext('APP_STATE()', ctx), methods, {
    all: DATA, campus: '', college: '', year: '', query: '', searchField: '',
    speaker: '', showLikedOnly: false, likedUrls: new Set(),
    pageSize: 25, currentPage: 1,
  }, over || {});
  Object.defineProperty(cm, 'filtered', {
    get: () => computed.filtered.call(cm), configurable: true });
  return cm;
}
const titles = cm => cm.filtered.map(l => l.title).sort().join(',');

// ---------- T1：校级+actualCampus → 点对应校区筛得出 ----------
{
  const got = titles(makeCtx({ campus: '大学城' }));
  ok(got === 'A,E', `T1 选「大学城」应得 A,E（A 靠 actualCampus），实际=${got}`);
}
// ---------- T2：「校级」筛选不受推导影响（用户拍板：全部保留） ----------
{
  const got = titles(makeCtx({ campus: '校级' }));
  ok(got === 'A,B,C,F,G', `T2 选「校级」应得全部校级 A,B,C,F,G，实际=${got}`);
}
// ---------- T3：非校级 campus 与 sources[].campus 回归 ----------
{
  const got = titles(makeCtx({ campus: '石牌' }));
  ok(got === 'D,F,G', `T3 选「石牌」应得 D(归口),F(actualCampus),G(sources)，实际=${got}`);
}
// ---------- T4：佛山（南海归佛山） ----------
{
  const got = titles(makeCtx({ campus: '佛山' }));
  ok(got === 'C', `T4 选「佛山」应得 C（南海归佛山），实际=${got}`);
}
// ---------- T5：无筛选全量 ----------
{
  const got = titles(makeCtx({ campus: '' }));
  ok(got === 'A,B,C,D,E,F,G', `T5 无筛选应返回全部 7 条，实际=${got}`);
}
// ---------- T6：交叉筛选（校区+学院）语义不串 ----------
{
  const cm = makeCtx({ campus: '石牌', college: '法学院' });
  const got = cm.filtered.map(l => l.title).join(',');
  ok(got === 'G', `T6 石牌+法学院应仅得 G（sources 级交叉），实际=${got}`);
}
// ---------- T7：未推导校级条不张冠李戴 ----------
{
  // B（无 actualCampus）在任何具体校区筛里都不出现，但校级筛里在
  for (const c of ['石牌', '大学城', '佛山', '汕尾']) {
    const got = titles(makeCtx({ campus: c }));
    ok(!got.split(',').includes('B'),
       `T7 选「${c}」不应出现未推导的 B，实际=${got}`);
  }
}

// ---------- 汇总 ----------
if (errs.length) {
  console.log(`[FAIL] ${errs.length} 项`);
  errs.forEach(e => console.log(' - ' + e));
  process.exit(1);
}
console.log('[PASS] 校区筛选 OR 语义/校级全量/sources 回归/交叉筛选全部通过');
