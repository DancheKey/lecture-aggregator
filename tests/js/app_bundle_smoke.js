// 集成冒烟：模拟浏览器按顺序执行 8 个分片，验证跨文件全局共享与组装可用。
// 用 vm 在同一 context 里依次执行，等价于 classic script 的顶层 const 共享语义。
const fs = require('fs');
const vm = require('vm');
const path = require('path');

// 用法：node tests/js/app_bundle_smoke.js（CI 与本地通用）
const SITE = path.join(__dirname, '..', '..', 'site');
const PARTS = ['app.core.js', 'app.state.js', 'app.computed.js', 'app.display.js',
  'app.social.js', 'app.data.js', 'app.admin.js', 'app.js'];

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

const nMethods = Object.keys(methods).length;
if (nMethods !== 94) errs.push(`方法数 ${nMethods} != 94`);
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
