// 集成冒烟：模拟浏览器按顺序执行 8 个分片，验证跨文件全局共享与组装可用。
// 用 vm 在同一 context 里依次执行，等价于 classic script 的顶层 const 共享语义。
const fs = require('fs');
const vm = require('vm');
const path = require('path');

// 用法：node tests/js/app_bundle_smoke.js（CI 与本地通用）
const SITE = path.join(__dirname, '..', '..', 'site');
// 分片顺序不写死，直接取 index.html 里的实际加载顺序：
// 既验证「文件都在」，也验证「index.html 没漏挂」（漏挂时方法集合会缺，随后报错）。
const INDEX_HTML = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
const PARTS = (INDEX_HTML.match(/<script[^>]*\ssrc=["'](app[^"']*?\.js)(?:\?[^"']*)?["']/g) || [])
  .map(tag => tag.match(/src=["'](app[^"']*?\.js)/)[1]);

const onDisk = fs.readdirSync(SITE).filter(f => /^app.*\.js$/.test(f)).sort();
const errsEarly = [];
if (!PARTS.length) errsEarly.push('index.html 里没有解析到任何 app*.js 引用');
const notReferenced = onDisk.filter(f => !PARTS.includes(f));
if (notReferenced.length) {
  errsEarly.push(`磁盘上有分片未被 index.html 引用（会整体失效）：${notReferenced.join(', ')}`);
}
if (errsEarly.length) {
  console.log('[FAIL]');
  errsEarly.forEach(e => console.log(' -', e));
  process.exit(1);
}

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

let mounted = null;
sandbox.Vue = {
  createApp(opts) {
    return {
      config: {},
      mount(sel) { mounted = { sel, opts }; },
    };
  },
};

const ctx = vm.createContext(sandbox);
for (const f of PARTS) {
  const fp = path.join(SITE, f);
  if (!fs.existsSync(fp)) {
    console.log(`[FAIL] 分片缺失或改名未同步：site/${f}（index.html 的加载清单与本脚本都要同步）`);
    process.exit(1);
  }
  const src = fs.readFileSync(fp, 'utf-8');
  vm.runInContext(src, ctx, { filename: f });   // 同一 context = 模拟同页多个 script
}

const errs = [];
const methods = vm.runInContext('APP_METHODS', ctx);
const state = vm.runInContext('APP_STATE()', ctx);
const computed = vm.runInContext('APP_COMPUTED', ctx);

// 方法数用于发现「某分片的方法没挂上」或「意外增删」；改动分片后需同步此值。
// （2026-09-30：新增 warmLongText / _ensureLongTextAll —— 长文本改为交互即预取，94 → 96）
// （2026-09-30：顶部数字改「阶梯跳变」，删除已无插值作用的 _countTick，96 → 95）
// （2026-10-04：新增 isDateSuspect —— timeConfidence=low 的记录挂「日期待核」角标，95 → 96）
const nMethods = Object.keys(methods).length;
if (nMethods !== 96) errs.push(`方法数 ${nMethods} != 96`);
if (typeof state !== 'object' || !('all' in state)) errs.push('APP_STATE 未产出正确状态对象');
if (!computed || !computed.filtered) errs.push('APP_COMPUTED 缺少 filtered');
if (!mounted) errs.push('app.mount 未被调用');
else {
  const o = mounted.opts;
  if (typeof o.data !== 'function') errs.push('组件 data 未接上');
  if (!o.computed || !o.computed.filtered) errs.push('组件 computed 未接上');
  if (!o.methods || !o.methods.toggleLike) errs.push('组件 methods 未接上');
  if (!o.mounted || !o.watch) errs.push('生命周期/watch 丢失');
}
// 抽查跨文件引用：display 分片里用到 core 的常量
const hasConst = vm.runInContext("typeof COUNT_CAP !== 'undefined' && typeof LIKED_KEY !== 'undefined'", ctx);
if (!hasConst) errs.push('core 常量未跨文件可见');

console.log(`方法 ${nMethods}；状态字段 ${Object.keys(state).length}；computed ${Object.keys(computed).length}；mount=${mounted && mounted.sel}`);
if (errs.length) { console.log('[FAIL]'); errs.forEach(e => console.log(' -', e)); process.exit(1); }
console.log('[PASS] 8 分片按序加载、跨文件常量共享、组件组装完整');
