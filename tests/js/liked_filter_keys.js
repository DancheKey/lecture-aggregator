// 只看已赞 / 统计聚合 / 折叠计数的键口径门禁（2026-10-19）
//
// 背景：这四条缺陷同源于「计数键下沉到讲座级」（888a686）只改了写入侧，
// 读取侧三处没跟上，而当时的门禁一条都没覆盖：
//
//   B3 「只看已赞」按 l.sourceUrl 过滤，点赞却写在 ck（多场 = url~N）→ 筛不出。
//      注意修法**不能**写成 `hasLiked(l.ck || l.sourceUrl)` 二选一：多场记录的
//      ck 恒非空，永远走不到回退分支，合并卡（赞记在页面级无后缀 url 上）仍然筛不出来。
//   B4 统计页只按 l.k 聚合，合并卡的页面级计数查不到 → 统计页恒 0（30 组 / 225 场）。
//   B5 分页导航用 units.length 判定，而分页实际基于 foldedUnits。
//   B6 远期折叠条文案是「N 场」，却用渲染单元数（一张合并卡 5 场算成 1）。
//
// 本门禁锁：
//   test_01 场次级赞（url~N）能被「只看已赞」筛出
//   test_02 页面级赞（无后缀 url，合并卡）能筛出整组，且仍渲染为 1 张合并卡
//   test_03 未赞的场次不被筛出
//   test_04 单场记录（ck === sourceUrl）照常筛出
//   test_05 stats.json：只有「合并卡页」（同页同日 ≥3 场）才有 k=u 的页面级索引
//          （B4；2026-10-19 收窄——早前给全部多场页都补，统计页与首页条数对不上）
//   test_06 远期折叠条 count 是场次数而非渲染单元数（B6）
//   test_07 index.html 分页导航用 totalPages 判定（B5）
//
// 运行：node tests/js/liked_filter_keys.js（纯 node，无浏览器无网络）
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
const FAR_FOLD_KEY = vm.runInContext('FAR_FOLD_KEY', ctx);

const errs = [];
const ok = (cond, msg) => { if (!cond) errs.push(msg); };

// 一条讲座记录：默认字段足够让 filtered/units 跑通
function rec(over) {
  return Object.assign({
    sourceUrl: '', title: '', lectureStart: '2026-10-20 10:00:00',
    topic: '', location: '地点', speaker: '讲者', speakerKeys: [],
    college: '文学院', campus: '石牌', lectureIndex: null,
  }, over || {});
}

// 物化 computed 链（与 app_far_future_fold.js 同一套装配）
function makeCtx(allData, over) {
  const cm = Object.assign(vm.runInContext('APP_STATE()', ctx), methods, {
    all: allData, campus: '', college: '', year: '', query: '', searchField: '',
    speaker: '', showLikedOnly: false, likedUrls: new Set(),
    pageSize: 25, currentPage: 1,
  }, over || {});
  for (const [name, fn] of [
    ['filtered', 'filtered'], ['units', 'units'], ['foldedUnits', 'foldedUnits'],
    ['pagedUnits', 'pagedUnits'], ['pagedGroups', 'pagedGroups'], ['totalPages', 'totalPages'],
  ]) {
    Object.defineProperty(cm, name, {
      get: () => computed[fn].call(cm), configurable: true });
  }
  return cm;
}

// ---------- 共用数据：一页 3 场（会渲染成合并卡）+ 一条单场 ----------
const P = 'http://x.scnu.edu.cn/a/1.html';          // 多场源页
const S = 'http://x.scnu.edu.cn/b/2.html';          // 单场源页
const multi = [1, 2, 3].map(i => rec({
  sourceUrl: P, lectureIndex: i, ck: `${P}~${i}`, title: `第${i}场`,
}));
const solo = rec({ sourceUrl: S, lectureIndex: null, ck: S, title: '单场' });
const ALL = multi.concat([solo]);

// ---------- test_01：场次级赞能被筛出 ----------
{
  const cm = makeCtx(ALL, { showLikedOnly: true, likedUrls: new Set([`${P}~2`]) });
  const hit = cm.filtered;
  ok(hit.length === 1, `test_01 只看已赞应命中 1 条（场次级 ck），实际 ${hit.length}`);
  ok(hit[0] && hit[0].ck === `${P}~2`, `test_01 命中的应是 url~2，实际 ${hit[0] && hit[0].ck}`);
  const u = cm.units;
  ok(u.length === 1 && u[0].ck === `${P}~2`,
     `test_01 渲染单元应只有 1 张且 ck=url~2，实际 ${JSON.stringify(u.map(x => x.ck))}`);
}

// ---------- test_02：页面级赞（合并卡）能筛出整组 ----------
{
  const cm = makeCtx(ALL, { showLikedOnly: true, likedUrls: new Set([P]) });
  const hit = cm.filtered;
  // 过滤是逐条记录：同页 3 场的 sourceUrl 都命中页面级键 → 整组保留，
  // 从而仍满足 MIN=3 的合并条件（形态不退化成单卡）。
  ok(hit.length === 3, `test_02 页面级赞应保留整页 3 场，实际 ${hit.length}`);
  const u = cm.units;
  ok(u.length === 1, `test_02 应仍渲染为 1 个渲染单元，实际 ${u.length}`);
  ok(u[0] && u[0].type === 'forum', `test_02 单元类型应为 forum，实际 ${u[0] && u[0].type}`);
  ok(u[0] && u[0].ck === P, `test_02 合并卡的计数键应为页面级 url，实际 ${u[0] && u[0].ck}`);
  ok(u[0] && u[0].total === 3, `test_02 合并卡 total 应为 3，实际 ${u[0] && u[0].total}`);
}

// ---------- test_03：未赞的不被筛出 ----------
{
  const cm = makeCtx(ALL, { showLikedOnly: true, likedUrls: new Set() });
  ok(cm.filtered.length === 0, `test_03 无任何已赞时应为 0 条，实际 ${cm.filtered.length}`);
}

// ---------- test_04：单场（ck === sourceUrl）照常筛出 ----------
{
  const cm = makeCtx(ALL, { showLikedOnly: true, likedUrls: new Set([S]) });
  const hit = cm.filtered;
  ok(hit.length === 1 && hit[0].title === '单场',
     `test_04 单场赞应命中 1 条，实际 ${hit.length}`);
}

// ---------- test_05：stats.json 只为「合并卡页」补页面级索引（B4，2026-10-19 收窄） ----------
// 判定口径必须与 app.computed.js units() 的 MIN=3 完全一致：同源页 + 同一天 ≥3 场
// 才渲染成合并卡，才有「整页一个赞」的入口，页面级键（无后缀 url）才会被写入。
// 早前对**全部** 94 个多场页都补了 k=u 虚拟记录，跨天拆分的页根本没有这个入口，
// 统计页却把页面级键（历史脏值）当一条记录计入 → 与首页「只看已赞」条数对不上。
{
  const statsPath = path.join(SITE, 'lectures', 'stats.json');
  const dataPath = path.join(SITE, 'lectures.json');
  if (!fs.existsSync(statsPath)) {
    errs.push('test_05 site/lectures/stats.json 不存在');
  } else {
    const st = JSON.parse(fs.readFileSync(statsPath, 'utf-8'));
    const L = st.lectures || [];
    // k 必须唯一：同一页 N 场只能补 1 条，否则一份计数被放大 N 倍
    const dup = L.length - new Set(L.map(x => x.k)).size;
    ok(dup === 0, `test_05 stats.json 的 k 不唯一（重复 ${dup} 条），统计页会重复累加`);

    // 用全量数据算出「会渲染成合并卡的源页」集合（同源页同日 ≥3 场）
    if (!fs.existsSync(dataPath)) {
      errs.push('test_05 site/lectures.json 不存在，无法校验合并页口径');
    } else {
      const raw = JSON.parse(fs.readFileSync(dataPath, 'utf-8'));
      const recs = raw.data || [];
      const dayCount = new Map();
      recs.forEach(r => {
        const u = r.sourceUrl || '';
        if (!u) return;
        const gk = u + '|' + String(r.lectureStart || '').slice(0, 10);
        dayCount.set(gk, (dayCount.get(gk) || 0) + 1);
      });
      const mergedPages = new Set();
      dayCount.forEach((n, gk) => {
        if (n >= 3) mergedPages.add(gk.slice(0, gk.lastIndexOf('|')));
      });
      const virtualPages = new Set(L.filter(x => x.p === 1).map(x => x.u));
      const extra = [...virtualPages].filter(u => !mergedPages.has(u));
      const miss = [...mergedPages].filter(u => !virtualPages.has(u));
      ok(extra.length === 0,
         `test_05 ${extra.length} 个「非合并页」被补了页面级索引（k=u），统计页会多算一条：`
         + extra.slice(0, 3).join(', '));
      ok(miss.length === 0,
         `test_05 ${miss.length} 个合并卡页缺页面级索引（k=u），统计页会漏计：`
         + miss.slice(0, 3).join(', '));
    }
  }
}

// ---------- test_06：折叠条 count 是场次数（B6） ----------
{
  // 3 个远期渲染单元：一页同日 5 场（合并卡）+ 2 条单场 → 实际 7 场
  const day = '2026-11-20';
  const forumPage = 'http://x.scnu.edu.cn/c/9.html';
  const data = [];
  for (let i = 1; i <= 5; i++) {
    data.push(rec({ sourceUrl: forumPage, lectureIndex: i, ck: `${forumPage}~${i}`,
                    lectureStart: `${day} 10:00:00`, title: `论坛第${i}场` }));
  }
  data.push(rec({ sourceUrl: 'http://x.scnu.edu.cn/d/1.html', ck: 'http://x.scnu.edu.cn/d/1.html',
                  lectureStart: '2026-11-21 10:00:00', title: '远期单场1' }));
  data.push(rec({ sourceUrl: 'http://x.scnu.edu.cn/d/2.html', ck: 'http://x.scnu.edu.cn/d/2.html',
                  lectureStart: '2026-11-22 10:00:00', title: '远期单场2' }));
  const cm = makeCtx(data);
  const fold = cm.pagedGroups.find(g => g.key === FAR_FOLD_KEY);
  ok(!!fold, 'test_06 应产生远期折叠组');
  if (fold) {
    ok(fold.count === 7,
       `test_06 折叠条应显示 7 场（5+1+1），实际 ${fold.count}——`
       + '用 recs.length 会把 5 场的合并卡算成 1');
  }
}

// ---------- test_07：分页导航用 totalPages 判定（B5） ----------
{
  const m = INDEX_HTML.match(/<nav\s+v-if="([^"]+)"[^>]*>/);
  ok(!!m, 'test_07 index.html 未找到分页 nav');
  if (m) {
    ok(/totalPages\s*>\s*1/.test(m[1]),
       `test_07 分页 nav 的显示条件应为 totalPages > 1（与分页实际使用的 foldedUnits 同源），`
       + `实际 v-if="${m[1]}"`);
  }
}

if (errs.length) {
  console.log('[FAIL]');
  errs.forEach(e => console.log(' -', e));
  process.exit(1);
}
console.log('[PASS] 只看已赞：场次级 + 页面级双键命中；统计索引覆盖页面级键；折叠计数为场次数');
