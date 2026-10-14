// P1-6 守卫：loadLectureStats 的 localStorage 写侧异常不得回滚已合并的服务端值。
// 背景（2026-10-14 MIMO 评审）：app.social.js 与 stats.js 的
// `this.lectureStats = merged; localStorage.setItem(...)` 写侧无 try——配额满/
// 隐私模式下 QuotaExceededError 冒泡到 .catch，被 `this.lectureStats = localStats`
// 用本地旧值覆盖，「图标高亮但数字为 0」。修复=写侧包 try，catch 只留网络回落语义。
//
// 覆盖两份同构实现（app.social.js / stats.js），各跑三个场景：
//   T1 配额异常：内存态必须是合并后的服务端权威值（不是本地旧值）
//   T2 网络失败：保持既有回落语义（本地旧值）——防把 T1 修成「永不回落」
//   T3 两份实现行为一致（同桩同输入同输出）
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');

const SITE = path.join(__dirname, '..', '..', 'site');

function makeCtx({ setItemImpl, fetchImpl }) {
  const store = {};
  const sandbox = {
    console,
    performance: { now: () => Date.now() },
    requestAnimationFrame: () => 0,
    setInterval: () => 0,
    setTimeout: () => 0,
    clearTimeout: () => {},
    location: { hostname: 'example.com', href: 'https://example.com/', search: '' },
    navigator: {},
    fetch: fetchImpl,
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => setItemImpl(k, v, store),
      removeItem: k => { delete store[k]; },
    },
    window: {
      top: null, self: null, innerWidth: 1280,
      addEventListener: () => {}, removeEventListener: () => {},
      scrollTo: () => {}, scrollY: 0, pageYOffset: 0, isSecureContext: false,
    },
    document: {
      documentElement: { style: {}, classList: { remove: () => {} } },
      querySelectorAll: () => [],
      addEventListener: () => {}, removeEventListener: () => {},
      createElement: () => ({ style: {} }),
      body: { appendChild: () => {}, removeChild: () => {} },
    },
  };
  sandbox.window.top = sandbox.window;
  sandbox.window.self = sandbox.window;
  sandbox.globalThis = sandbox;
  sandbox.Vue = {
    createApp() { return { config: {}, mount() {} }; },
  };
  return { ctx: vm.createContext(sandbox), sandbox, store };
}

function loadParts(ctx, parts) {
  for (const f of parts) {
    const src = fs.readFileSync(path.join(SITE, f), 'utf-8');
    vm.runInContext(src, ctx, { filename: f });
  }
}

// 造一个最小 this：loadLectureStats 只碰 this.lectureStats（+ 隐性 this.loadError 等）
function makeThis() {
  return { lectureStats: null };
}

// loadLectureStats 自身不返回 Promise（Vue 方法惯例），测试侧轮询等终态
async function waitSettled(self, ms = 3000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (self.lectureStats !== null) return;
    await new Promise(r => setTimeout(r, 10));
  }
  throw new Error('loadLectureStats 3s 内未写入 lectureStats（then 链未完成）');
}

const SERVER = { stats: { 'http://a/1.html': { visits: 9, likes: 3 } } };
const LOCAL = { 'http://a/1.html': { visits: 1, likes: 2 } };
// 合并语义（源码注释）：后端有的字段以后端权威值覆盖本地 → 整条取服务端值。
// 配额异常时若写侧异常冒泡到 .catch，会被 localStats（visits=1）整体回滚。
const MERGED = { 'http://a/1.html': { visits: 9, likes: 3 } };

// 跨 vm realm 的对象不能直接 deepStrictEqual（prototype 不同源会假红），
// 对序列化结果比较——本测试只关心字段值语义。
function sameJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

async function runSocial(fetchImpl, setItemImpl) {
  const { ctx, store } = makeCtx({ fetchImpl, setItemImpl });
  store['lecture_stats_v1'] = JSON.stringify(LOCAL);
  loadParts(ctx, ['app.core.js', 'app.social.js']);
  const fn = vm.runInContext('APP_METHODS.loadLectureStats', ctx);
  const self = makeThis();
  fn.call(self);
  await waitSettled(self);
  return self;
}

async function runStats(fetchImpl, setItemImpl) {
  const { ctx, store } = makeCtx({ fetchImpl, setItemImpl });
  store['lecture_stats_v1'] = JSON.stringify(LOCAL);
  let captured = null;
  vm.runInContext(`
    Vue = { createApp(opts) { globalThis.__opts = opts; return { mount() {} }; } };
  `, ctx);
  loadParts(ctx, ['stats.js']);
  const fn = vm.runInContext('__opts.methods.loadLectureStats', ctx);
  const self = makeThis();
  fn.call(self);
  await waitSettled(self);
  return self;
}

(async () => {
  const errs = [];
  const okFetch = () => Promise.resolve({ json: () => Promise.resolve(SERVER) });
  const badFetch = () => Promise.reject(new Error('offline'));
  const quotaSet = (k, v, store) => { throw new Error('QuotaExceededError'); };
  const normalSet = (k, v, store) => { store[k] = String(v); };

  for (const [name, run] of [['app.social.js', runSocial], ['stats.js', runStats]]) {
    // T1 配额异常 → 内存态必须是合并后的服务端权威值
    try {
      const self = await run(okFetch, quotaSet);
      assert.ok(sameJson(self.lectureStats, MERGED),
        `T1 ${name}: 配额异常后内存态被本地旧值回滚（服务端 visits=9 丢失），`
        + `实得 ${JSON.stringify(self.lectureStats)}`);
    } catch (e) { errs.push(`T1 ${name}: ${e.message}`); }

    // T2 网络失败 → 保持既有回落语义（本地旧值）
    try {
      const self = await run(badFetch, normalSet);
      assert.ok(sameJson(self.lectureStats, LOCAL),
        `T2 ${name}: 网络失败时应回落本地旧值（既有语义被改坏），`
        + `实得 ${JSON.stringify(self.lectureStats)}`);
    } catch (e) { errs.push(`T2 ${name}: ${e.message}`); }
  }

  // T3 两份实现同桩同输入 → 输出一致
  try {
    const a = await runSocial(okFetch, quotaSet);
    const b = await runStats(okFetch, quotaSet);
    assert.ok(sameJson(a.lectureStats, b.lectureStats),
      'T3: app.social.js 与 stats.js 配额场景行为不一致');
  } catch (e) { errs.push(`T3: ${e.message}`); }

  if (errs.length) {
    console.log('[FAIL]');
    errs.forEach(e => console.log(' -', e));
    process.exit(1);
  }
  console.log('[OK] P1-6 配额守卫 3/3（两份实现 × 配额异常/网络回落/一致性）');
})();
