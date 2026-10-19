// 计数键迁移门禁（2026-10-18 审查报告 B1 + B3）：讲座级计数键 ck 下沉的
// 过渡期一致性与健壮性。
//
// 背景：ck 改造前，多场拆分讲座的赞/想听记在**页面级**裸 URL 上；改造后
// 展开场次查 sourceUrl~N 新键。两个过渡期问题（云端实测均复现）：
//   B1 老用户本机「已赞」状态丢失——likedUrls/wantedUrls 存的是旧键，
//      hasLiked(ck) 查新键 → 图标不高亮，但云端计数还在，再点一次会 +1。
//      全站 94 个多场源页受影响。修法：migrateCountKeys() 把旧键按
//      「页面 → 该页全部场次」展开；旧键保留（合并卡 ck 仍是无后缀 URL）。
//   B3 数据缺 ck 时 single 单元的 ck 回退——有 URL 的分支回退为
//       sourceUrl（可达路径）；无 URL 分支 sourceUrl 恒空，回退只保证
//       键是字符串而非 undefined，不再写 l.sourceUrl（那是死代码）。
//   B2（2026-10-19 用户实测反向缺陷）旧键展开范围过大——只有合并卡
//      （同页同日 ≥3 场）才是「整页一个赞」，其余多场页的展开会伪造已赞：
//      只赞 1 条却筛出 3 条，多出来的图标高亮、数字 0。
//
// 运行：node tests/js/count_key_migration.js（纯 node，无浏览器无网络）
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');

const SITE = path.join(__dirname, '..', '..', 'site');
const INDEX_HTML = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
const PARTS = (INDEX_HTML.match(/<script[^>]*\ssrc=["'](app[^"']*?\.js)(?:\?[^"']*)?["']/g) || [])
  .map(tag => tag.match(/src=["'](app[^"']*?\.js)/)[1]);

const LIKED_KEY = 'lecture_liked_urls_v1';
const WANTED_KEY = 'lecture_wanted_urls_v1';

// localStorage 桩：可预置内容、可数 setItem 调用次数（验证幂等「写完即忘」）
function makeSandbox(seed) {
  const store = Object.assign({}, seed || {});
  let setCalls = 0;
  const sandbox = {
    console,
    performance: { now: () => Date.now() },
    requestAnimationFrame: () => 0,
    setInterval: () => 0,
    setTimeout: () => 0,
    clearTimeout: () => {},
    location: { hostname: 'example.com', href: 'https://example.com/' },
    navigator: { clipboard: null },
    fetch: () => Promise.resolve({ ok: false, json: () => ({}) }),
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); setCalls++; },
      removeItem: k => { delete store[k]; },
    },
    window: {
      top: null, self: null, innerWidth: 1280,
      addEventListener: () => {}, removeEventListener: () => {},
      scrollTo: () => {}, scrollY: 0, pageYOffset: 0,
      isSecureContext: false,
    },
    document: {
      documentElement: { style: {} },
      querySelectorAll: () => [],
      addEventListener: () => {}, removeEventListener: () => {},
      createElement: () => ({ style: {}, select: () => {} }),
      body: { appendChild: () => {}, removeChild: () => {} },
    },
  };
  sandbox.window.top = sandbox.window;
  sandbox.window.self = sandbox.window;
  sandbox.globalThis = sandbox;
  sandbox.Vue = { createApp: () => ({ config: {}, mount: () => {} }) };
  return { ctx: vm.createContext(sandbox), store, setCalls: () => setCalls };
}

function loadApp(seed) {
  const { ctx, store, setCalls } = makeSandbox(seed);
  for (const f of PARTS) {
    vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf-8'), ctx, { filename: f });
  }
  return {
    methods: vm.runInContext('APP_METHODS', ctx),
    computed: vm.runInContext('APP_COMPUTED', ctx),
    state: () => vm.runInContext('APP_STATE()', ctx),
    store, setCalls,
  };
}

const errs = [];
const ok = (cond, msg) => { if (!cond) errs.push(msg); };

// ---------- 构造数据：多场页 A（3 场）+ 单场页 B ----------
const A = 'http://a.scnu.edu.cn/a/20261012/10265.html';   // 多场拆分源页
const B = 'http://b.scnu.edu.cn/b/20261001/998.html';     // 单场源页
function mkMulti(i) {
  return {
    sourceUrl: A, title: `讲座${i}`, lectureStart: '2026-05-01 14:00:00',
    topic: '', location: '', speaker: `讲者${i}`, speakerKeys: [],
    college: '文学院', campus: '石牌', lectureIndex: i, ck: `${A}~${i}`,
  };
}
function mkSingle() {
  return {
    sourceUrl: B, title: '单场讲座', lectureStart: '2026-05-02 14:00:00',
    topic: '', location: '', speaker: '讲者', speakerKeys: [],
    college: '文学院', campus: '石牌',
  };
}

// ================= B1：旧键 → 新键迁移 =================
{
  // 预置：改造前用户在 A 页（多场）与 B 页（单场）都点过赞/想听
  const app = loadApp({
    [LIKED_KEY]: JSON.stringify([A, B]),
    [WANTED_KEY]: JSON.stringify([A]),
  });
  const cm = Object.assign(app.state(), app.methods, {
    all: [mkMulti(1), mkMulti(2), mkMulti(3), mkSingle()],
  });
  cm.loadLikes();
  cm.loadWants();
  const before = { liked: [...cm.likedUrls].sort(), wanted: [...cm.wantedUrls].sort() };
  ok(JSON.stringify(before.liked) === JSON.stringify([A, B].sort()),
     'B1 前置：改造前 likedUrls 应恰为两个旧键，实得 ' + JSON.stringify(before.liked));

  cm.migrateCountKeys();
  const liked = [...cm.likedUrls].sort();
  const wanted = [...cm.wantedUrls].sort();
  const wantLiked = [A, B, `${A}~1`, `${A}~2`, `${A}~3`].sort();
  ok(JSON.stringify(liked) === JSON.stringify(wantLiked),
     'B1 迁移后 likedUrls 应为「旧键全保留 + A 页三场新键」，实得 ' + JSON.stringify(liked));
  ok(liked.includes(A) && liked.includes(B),
     'B1 旧键必须保留（合并卡 ck = 无后缀 URL，页面级状态自洽；单场页 ck 逐字等于旧键）');
  ok(JSON.stringify(wanted) === JSON.stringify([A, `${A}~1`, `${A}~2`, `${A}~3`].sort()),
     'B1 wantedUrls 应同样展开，实得 ' + JSON.stringify(wanted));
  ok(!liked.includes(`${B}~1`),
     'B1 单场页不得被造出 ~1 键（无 lectureIndex 就没有场次维度）');

  // localStorage 写回（刷新/重开浏览器后迁移结果不丢）
  const storedLiked = JSON.parse(app.store[LIKED_KEY]);
  ok(storedLiked.includes(`${A}~3`),
     'B1 迁移结果须写回 localStorage，否则刷新后「已赞」又丢');

  // 幂等：第二次调用不得再写盘（写完即忘），集合内容不变
  const writesAfterFirst = app.setCalls();
  const sizeBefore = cm.likedUrls.size;
  cm.migrateCountKeys();
  ok(cm.likedUrls.size === sizeBefore,
     `B1 二次迁移应无变化（幂等），实得 ${cm.likedUrls.size} vs ${sizeBefore}`);
  ok(app.setCalls() === writesAfterFirst,
     'B1 二次迁移不得再写 localStorage（无变化即无写入）');
}

// ================= B1b：数据未就绪时静默返回（不炸） =================
{
  const app = loadApp({ [LIKED_KEY]: JSON.stringify([A]) });
  const cm = Object.assign(app.state(), app.methods, { all: [] });
  cm.loadLikes();
  cm.migrateCountKeys();   // all 为空：等下次数据阶段变化时再跑
  ok(cm.likedUrls.has(A) && cm.likedUrls.size === 1,
     'B1b all 为空时应原样保留（由后续 full 阶段补齐迁移）');
}

// ================= B1c：已含新键的集合不被重复展开 =================
{
  const app = loadApp({ [LIKED_KEY]: JSON.stringify([A, `${A}~1`, `${A}~2`, `${A}~3`]) });
  const cm = Object.assign(app.state(), app.methods, {
    all: [mkMulti(1), mkMulti(2), mkMulti(3)],
  });
  cm.loadLikes();
  cm.migrateCountKeys();
  ok(cm.likedUrls.size === 4,
     `B1c 已迁移过的集合应保持 4 个键，实得 ${cm.likedUrls.size}`);
}

// ================= B2：展开只作用于「合并组」，不得伪造已赞（2026-10-19 用户实测） =================
// 现象：只点赞 1 条，「只看已赞」筛出 3 条；另外 2 条是同一源页拆出的讲座，
// 图标变蓝但数字为 0。根因：旧迁移把页面级键展开到该页**全部**场次，而只有
// 合并卡（同页同日 ≥3 场）才是「整页一个赞」的语义。全站 94 个多场页里只有
// 30 组会渲染成合并卡，其余 64 页的展开都是伪造状态。
{
  // 非合并页 C：3 场分散在 3 天（每天 1 场 → 逐场单卡，无整页赞入口）
  const C = 'http://c.scnu.edu.cn/c/20261012/777.html';
  const mkC = (i, day) => ({
    sourceUrl: C, title: `C讲座${i}`, lectureStart: `${day} 14:00:00`,
    topic: '', location: '', speaker: `讲者${i}`, speakerKeys: [],
    college: '文学院', campus: '石牌', lectureIndex: i, ck: `${C}~${i}`,
  });
  const C_ALL = [mkC(1, '2026-05-01'), mkC(2, '2026-05-02'), mkC(3, '2026-05-03')];

  // --- B2a：非合并页的旧页面级键不展开 ---
  {
    const app = loadApp({ [LIKED_KEY]: JSON.stringify([C]) });
    const cm = Object.assign(app.state(), app.methods, { all: C_ALL });
    cm.loadLikes();
    cm.migrateCountKeys();
    ok(cm.likedUrls.size === 1 && cm.likedUrls.has(C),
       `B2a 非合并页不得展开旧键（会造成 3 条虚假已赞），实得 ${JSON.stringify([...cm.likedUrls])}`);
  }

  // --- B2b：历史展开留下的 0 计数键被 prune 掉 ---
  {
    const app = loadApp({ [LIKED_KEY]: JSON.stringify([C, `${C}~1`, `${C}~2`, `${C}~3`]) });
    const cm = Object.assign(app.state(), app.methods, {
      all: C_ALL,
      lectureStats: {
        [`${C}~1`]: { visits: 0, likes: 0 },
        [`${C}~2`]: { visits: 0, likes: 0 },
        [`${C}~3`]: { visits: 0, likes: 0 },
      },
    });
    cm.loadLikes();
    cm.migrateCountKeys();
    ok(cm.likedUrls.size === 0,
       `B2b 非合并页上计数为 0 的 url / url~N 键应全部清掉，实得 ${JSON.stringify([...cm.likedUrls])}`);
  }

  // --- B2c：合并页（同日 3 场）的页面级键必须保留、且照旧展开 ---
  {
    const app = loadApp({ [LIKED_KEY]: JSON.stringify([A]) });
    const cm = Object.assign(app.state(), app.methods, {
      all: [mkMulti(1), mkMulti(2), mkMulti(3)],
      lectureStats: {},   // 合并卡计数记在裸 URL 上，这里刻意为空：prune 不得误删
    });
    cm.loadLikes();
    cm.migrateCountKeys();
    ok(cm.likedUrls.has(A), 'B2c 合并页的页面级键（合并卡 ck）不得被 prune 掉');
    ok(cm.likedUrls.has(`${A}~1`) && cm.likedUrls.has(`${A}~3`),
       'B2c 合并页的旧键仍须展开到组内场次（改造前「整页一个赞」= 整卡赞）');
  }

  // --- B2d：混合页（同日 3 场 + 另一天 1 场）只展开合并组内的场次 ---
  {
    const D = 'http://d.scnu.edu.cn/d/20261012/888.html';
    const mkD = (i, day) => ({
      sourceUrl: D, title: `D讲座${i}`, lectureStart: `${day} 14:00:00`,
      topic: '', location: '', speaker: `讲者${i}`, speakerKeys: [],
      college: '文学院', campus: '石牌', lectureIndex: i, ck: `${D}~${i}`,
    });
    const app = loadApp({ [LIKED_KEY]: JSON.stringify([D]) });
    const cm = Object.assign(app.state(), app.methods, {
      all: [mkD(1, '2026-05-01'), mkD(2, '2026-05-01'), mkD(3, '2026-05-01'), mkD(4, '2026-05-09')],
    });
    cm.loadLikes();
    cm.migrateCountKeys();
    ok(cm.likedUrls.has(`${D}~1`) && cm.likedUrls.has(`${D}~3`),
       'B2d 合并组内（同日 3 场）的场次应被展开');
    ok(!cm.likedUrls.has(`${D}~4`),
       'B2d 同页另一天的零散场次是独立单卡，不得被整页赞波及');
  }
}

// ================= B2e：统计就绪后必须补跑一次迁移 =================
// prune 以「计数为 0」为判据，lectureStats 未赋值时整段被守卫跳过；而数据阶段的
// 调用发生在 loadLectureStats 之前。若不补跑，伪造键永远清不掉。
{
  const src = fs.readFileSync(path.join(SITE, 'app.social.js'), 'utf-8');
  const body = src.slice(src.indexOf('loadLectureStats()'));
  const seg = body.slice(0, body.indexOf('showToast(msg'));
  const hits = (seg.match(/applyStats\(/g) || []).length;
  ok(hits >= 4, `B2e loadLectureStats 的三条分支（云端 / api / 降级）都要经 applyStats 补跑迁移，实得 ${hits} 处`);
  ok(/applyStats\s*=\s*\([^)]*\)\s*=>\s*\{[\s\S]{0,200}?migrateCountKeys\(\)/.test(seg),
     'B2e applyStats 内必须调用 migrateCountKeys()（统计就绪后再 prune 一次）');
}

// ================= B3：缺 ck 时 single 单元键回退 =================
{
  const app = loadApp({});
  const cm = Object.assign(app.state(), app.methods, {
    all: [], campus: '', college: '', year: '', query: '', searchField: '',
    speaker: '', showLikedOnly: false, likedUrls: new Set(),
  });
  Object.defineProperty(cm, 'filtered', {
    get: () => app.computed.filtered.call(cm),
    configurable: true,
  });
  // 异常脏数据：有 sourceUrl 但缺 ck（生成器恒产出，仅理论路径）
  const noCk = { sourceUrl: B, title: '缺ck', lectureStart: '2026-05-03 14:00:00', speakerKeys: [] };
  cm.all = [noCk];
  const u2 = app.computed.units.call(cm);
  const single = u2.find(x => x.type === 'single');
  ok(!!single, 'B3 单条数据应产出 single 单元');
  if (single) {
    ok(single.ck === B,
       `B3 缺 ck 时 single 的 ck 应回退为 sourceUrl（键非空，toggleLike 才不静默 return），实得 ${JSON.stringify(single.ck)}`);
  }
  // sourceUrl 为空的极端脏数据：ck 回退为空串但不炸
  const noUrl = { sourceUrl: '', title: '无源', lectureStart: '2026-05-04 14:00:00', speakerKeys: [] };
  cm.all = [noUrl];
  const u3 = app.computed.units.call(cm);
  const s3 = u3.find(x => x.type === 'single');
  ok(!!s3 && s3.ck === '' , 'B3 sourceUrl 为空时 ck 应为空串（不炸、类型为字符串）');
}

// ================= B3 静态断言：回退链源码形态 =================
{
  // 无 URL 分支（!u）的 l.sourceUrl 恒为空串，回退到它等于没回退——
  // 2026-10-18 复查删掉了这段死代码，静态断言锁住「别再把它加回来」。
  // 真正可达的回退在下方 single（有 URL）分支：ck: l.ck || u。
  const src = fs.readFileSync(path.join(SITE, 'app.computed.js'), 'utf-8');
  ok(src.includes("ck: l.ck || ''"),
     'B3 静态：app.computed.js 的 !u 分支回退链须为 l.ck || \'\'（该分支 sourceUrl 恒空，勿再加 l.sourceUrl 死回退）');
  ok(/ck: l\.ck \|\| u\b/.test(src),
     'B3 静态：single（有 URL）回退链须为 l.ck || u');
  ok(!src.includes("ck: l.ck || l.sourceUrl"),
     'B3 静态：不得存在 l.ck || l.sourceUrl 形态——在 !u 分支里它是死代码，在别处会掩盖真正的回退位置');
}

if (errs.length) {
  console.log('[FAIL]');
  errs.forEach(e => console.log(' -', e));
  process.exit(1);
}
console.log('count_key_migration: B1 迁移/幂等/写回 + B2 合并组展开/prune + B3 回退 全部 pass');
