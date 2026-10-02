// 前端 computed 行为测试（2026-10-02）：B1 论坛卡 total 口径 + B2 clamp 签名覆盖。
//
// 背景
// ----
// B1：app.computed.units() 折叠论坛卡时写 `total: recs.length`，而 recs 来自
//     filtered（已应用筛选）→ total 恒等于 recs.length，下游两处变成死条件：
//       ① index.html 的「⚠ 当前筛选命中 X / Y 场」（`recs.length < total`）；
//       ② app.display.isForumOpen 的默认值（`recs.length < total` 永为 false），
//          「筛选命中子集时自动展开场次清单」整条策略静默失效。
//     修法：total 取 all（未筛选全集）的同组场次，并保证 total >= recs.length。
//
// B2：app.display._measureClamp 的签名漏了 speaker / showLikedOnly / searchField
//     三个筛选维度，而它们都会换掉卡片 DOM 却不改签名 → 跳过测量 →
//     「展开简介/摘要」按钮缺失（且 _clampMeasured=true 后字符数兜底也失效）。
//
// 运行：node tests/js/app_forum_and_clamp.js（纯 node，无浏览器无网络）
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');
const INDEX_HTML = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
const PARTS = (INDEX_HTML.match(/<script[^>]*\ssrc=["'](app[^"']*?\.js)(?:\?[^"']*)?["']/g) || [])
  .map(tag => tag.match(/src=["'](app[^"']*?\.js)/)[1]);

const noop = () => {};
const store = {};
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
sandbox.Vue = { createApp: () => ({ config: {}, mount: noop }) };

const ctx = vm.createContext(sandbox);
for (const f of PARTS) {
  vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf-8'), ctx, { filename: f });
}

const computed = vm.runInContext('APP_COMPUTED', ctx);
const methods = vm.runInContext('APP_METHODS', ctx);

const errs = [];
const ok = (cond, msg) => { if (!cond) errs.push(msg); };

// ---------- 构造测试数据：同一 sourceUrl 同一天 5 场 ----------
function mk(i, speaker) {
  return {
    sourceUrl: 'http://x.scnu.edu.cn/a', title: `讲座${i}`,
    lectureStart: '2026-05-01 14:00:00',
    topic: '', location: '', speaker: speaker || `讲者${i}`,
    speakerKeys: [], college: '文学院', campus: '石牌',
  };
}
function mkCM(i) {
  return {
    sourceUrl: 'http://x.scnu.edu.cn/a', title: `会${i}`,
    lectureStart: '2026-05-01 09:00:00', topic: '', location: '',
    speaker: `集体${i}`, speakerKeys: [], college: '文学院', campus: '大学城',
  };
}

// units() 依赖 filtered；而 filtered 在真实运行时是**计算属性**，已由框架缓存。
// 本测试直接调用 computed 函数，故须像框架那样先把 filtered 物化到实例上，
// 否则 units() 会读到 undefined.filtered（2026-10-02 首次运行即踩到）。
function makeCtx(allData, over) {
  const cm = Object.assign(vm.runInContext('APP_STATE()', ctx), {
    all: allData, campus: '', college: '', year: '', query: '', searchField: '',
    speaker: '', showLikedOnly: false, likedUrls: new Set(),
  }, over || {});
  Object.defineProperty(cm, 'filtered', {
    get: () => computed.filtered.call(cm),
    configurable: true,
  });
  return cm;
}

// ================= B1：论坛卡 total 口径 =================
// 5 场石牌 + 1 场大学城，同 sourceUrl 同一天 → 折叠阈值 MIN=3 命中，
// 无筛选时该组共 6 场（total 应为 6，不是 5——total 统计的是**全集同组场次**）。
const all = [mk(1), mk(2), mk(3), mk(4), mk(5), mkCM(1)];

// 场景 1：无筛选 → total === recs.length（6 场全命中）
{
  const cm = makeCtx(all);
  const units = computed.units.call(cm);
  const forum = units.find(u => u.type === 'forum');
  ok(!!forum, 'B1: 同源同日 6 场应折叠成论坛卡');
  if (forum) {
    ok(forum.total === 6, `B1: 无筛选时 total 应为全集 6 场，实得 ${forum.total}`);
    ok(forum.recs.length === forum.total, 'B1: 无筛选时 recs 应等于 total');
    ok(methods.isForumOpen.call(Object.assign({}, cm, { expandedForums: {} }), forum) === false,
       'B1: 无筛选时论坛卡应默认收起（命中全部，无需自动展开）');
  }
}

// 场景 2：按学院筛选 —— 同源同日 6 场（4 场文学院 + 2 场数学院）
//   筛「文学院」→ 命中 4 场（≥MIN=3，仍折叠），但全集 6 场 → total=6 > recs=4，
//   正是「部分命中」：论坛卡应默认自动展开，且卡片上显示「命中 4/6 场」。
{
  const mk2 = (i, college, speaker) => Object.assign(mk(i, speaker), { college });
  const all6 = [mk2(1, '文学院'), mk2(2, '文学院'), mk2(3, '文学院'), mk2(4, '文学院'),
                mk2(5, '数学院'), mk2(6, '数学院')];
  const cm = makeCtx(all6, { college: '文学院' });
  const units = computed.units.call(cm);
  const forum = units.find(u => u.type === 'forum');
  ok(!!forum, 'B1: 筛「文学院」命中 4 场应仍折叠成论坛卡');
  if (forum) {
    ok(forum.recs.length === 4, `B1: 文学院命中 4 场，实得 recs=${forum.recs.length}`);
    ok(forum.total === 6, `B1: total 应取全集 6 场（修复前会退化成 recs.length=4），实得 ${forum.total}`);
    ok(forum.total >= forum.recs.length, 'B1: 不变式 total >= recs.length 被破坏');
    ok(forum.total > forum.recs.length, 'B1: 本用例本应构成「部分命中」');
    // 这正是 index.html 的「⚠ 当前筛选命中 X / Y 场」与 isForumOpen 自动展开的判据
    ok(methods.isForumOpen.call(Object.assign({}, cm, { expandedForums: {} }), forum) === true,
       'B1: 部分命中时论坛卡应默认自动展开');
    // 显式 toggle 后应能收起（expandedForums 优先级高于默认值）
    const t = Object.assign({}, cm, { expandedForums: { [forum.url]: false } });
    ok(methods.isForumOpen.call(t, forum) === false, 'B1: 用户显式收起后应保持收起');
  }
}

// 场景 3：单场不折叠（阈值 MIN=3 的回归）
{
  const cm = makeCtx([mk(1), mk(2)]);
  const units = computed.units.call(cm);
  ok(units.every(u => u.type === 'single'), 'B1: 仅 2 场时不应折叠');
}

// 场景 4：数据未全量加载完时 all 少于 recs，total 必须仍 >= recs.length
{
  const cm = makeCtx([mk(1), mk(2), mk(3)]);   // all 只有 3 场
  cm.filteredOverride = null;
  Object.defineProperty(cm, 'filtered', {
    get: () => [mk(1), mk(2), mk(3), mk(4), mk(5)],  // 模拟分片竞态
    configurable: true,
  });
  const units = computed.units.call(cm);
  const forum = units.find(u => u.type === 'forum');
  ok(!!forum, 'B1: 3 场应折叠');
  if (forum) ok(forum.total >= forum.recs.length, 'B1: 竞态下 total < recs.length，恒等式被破坏');
}

// ================= B2：clamp 签名必须覆盖全部筛选维度 =================
{
  const src = fs.readFileSync(path.join(SITE, 'app.display.js'), 'utf-8');
  const m = src.match(/const sig = \[([\s\S]*?)\]\.join\('\|'\)/);
  ok(!!m, 'B2: 找不到 _measureClamp 的 sig 定义');
  if (m) {
    for (const field of ['speaker', 'showLikedOnly', 'searchField', 'currentPage',
                         'query', 'campus', 'college', 'year']) {
      ok(m[1].includes('this.' + field),
         `B2: clamp 签名缺少 this.${field} —— 换筛选后 DOM 变了却不重测，展开按钮会缺失`);
    }
  }
  // 静态锁：speaker 必须进签名（B2 的主体）
  ok(/this\.speaker/.test(src), 'B2: app.display.js 的 clamp 签名未包含 this.speaker');
}

if (errs.length) {
  console.log('[FAIL]');
  errs.forEach(e => console.log(' -', e));
  process.exit(1);
}
console.log('[PASS] B1 论坛卡 total 取全集口径、部分命中自动展开；B2 clamp 签名覆盖全部筛选维度');