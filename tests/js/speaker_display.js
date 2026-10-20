// 多主讲显示规则测试（2026-10-20 用户需求）。
//
// 需求：多主讲时只显示第一位讲者 + 单位 + 「等」，如「王红军（广州市花都区教师发展中心）等」；
//       无单位时名字与「等」之间补一个空格（「王红军 等」），避免「王红军等」被误读成更长名字。
//       ⚠ 只在解析器已识别出多讲者（speakerKeys>1 或 speaker 串多段）时生效；
//         识别不出第二人的海报（抽取问题）不触发——本测试只锁显示逻辑，不涉抽取。
//
// 锁定：
//   S1 speakerDisplayName：单讲者原样、多讲者取第一段（、/，分隔）、英文保留大小写、粘连串回退键首
//   S2 isMultiSpeaker：speakerKeys 长度>1 或 speaker 串多段；单讲者/空/null 均 false 且不抛
//   S3 模板接线：主讲主行用 speakerDisplayName(l)+isMultiSpeaker(l) 且「等」前空格三元在位；
//              论坛场次清单用 speakerDisplayName(r)
//
// 运行：node tests/js/speaker_display.js（纯 node，无浏览器无网络）
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');
const INDEX_HTML = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
const PARTS = (INDEX_HTML.match(/<script[^>]*\ssrc=["'](app[^"']*?\.js)(?:\?[^"']*)?["']/g) || [])
  .map(tag => tag.match(/src=["'](app[^"']*?\.js)/)[1]);

const noop = () => {};
const store = {};
// 与 app_upcoming_filter.js 同款沙箱：加载全部 app.*.js 分片，只取 APP_METHODS
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
    scrollTo: noop, scrollY: 0, pageYOffset: 0, isSecureContext: false,
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
  vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf8'), ctx, { filename: f });
}
const M = vm.runInContext('APP_METHODS', ctx);
Object.keys(M).forEach(k => { if (typeof M[k] === 'function') M[k] = M[k].bind(M); });

const errs = [];
const ok = (cond, msg) => { if (!cond) errs.push(msg); };

// ---- S1 speakerDisplayName ----
ok(M.speakerDisplayName({ speaker: '王红军', speakerKeys: ['王红军'] }) === '王红军',
  'S1a 单讲者原样返回');
ok(M.speakerDisplayName({ speaker: '汪行东、鲁志国', speakerKeys: ['汪行东', '鲁志国'] }) === '汪行东',
  'S1b 顿号多讲者取第一段');
ok(M.speakerDisplayName({ speaker: '张勇，梁燚焱', speakerKeys: ['张勇', '梁燚焱'] }) === '张勇',
  'S1c 逗号多讲者取第一段');
ok(M.speakerDisplayName({ speaker: 'Judith Korb', speakerKeys: ['judith korb'] }) === 'Judith Korb',
  'S1d 英文名保留原始大小写（不能用 speakerKeys 的小写键）');
ok(M.speakerDisplayName({ speaker: '汪行东鲁志国', speakerKeys: ['汪行东', '鲁志国'] }) === '汪行东',
  'S1e 串粘连无分隔但键识别多人 → 回退 speakerKeys[0]');
ok(M.speakerDisplayName({ speaker: '', speakerKeys: null }) === '',
  'S1f 空讲者返回空串');

// ---- S2 isMultiSpeaker ----
ok(M.isMultiSpeaker({ speaker: '王红军', speakerKeys: ['王红军'] }) === false, 'S2a 单讲者 false');
ok(M.isMultiSpeaker({ speaker: '汪行东、鲁志国', speakerKeys: ['汪行东', '鲁志国'] }) === true, 'S2b 多讲者 true');
ok(M.isMultiSpeaker({ speaker: '王红军、冯志旭' }) === true, 'S2c 无 speakerKeys 但串多段 → true');
ok(M.isMultiSpeaker({ speaker: '', speakerKeys: null }) === false, 'S2d 空讲者 false');
ok(M.isMultiSpeaker(null) === false, 'S2e null 不抛异常');

// ---- S3 模板接线（静态断言）----
ok(/speakerDisplayName\(l\)/.test(INDEX_HTML), 'S3a 主讲主行用 speakerDisplayName(l)');
ok(/isMultiSpeaker\(l\)/.test(INDEX_HTML) && /' 等'/.test(INDEX_HTML),
  'S3b 主讲主行「等」前空格三元在位（无单位时补空格）');
ok(/speakerDisplayName\(r\)/.test(INDEX_HTML), 'S3c 论坛场次清单用 speakerDisplayName(r)');

if (errs.length) {
  errs.forEach(e => console.error('  ✗ ' + e));
  console.error('speaker_display: ' + errs.length + ' 项失败');
  process.exit(1);
}
console.log('speaker_display: 单讲者原样 / 多讲者取首+等 / 空格规则 / 模板接线 全部 pass');
