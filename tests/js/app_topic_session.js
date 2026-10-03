// topicHasSession 行为门禁（2026-10-02）：判断题目里是否已含「第N期/讲/场」。
//
// 背景：该函数此前**零测试**，且在 n>99 时会拼出 'undefined十undefined' 这种
// 永不匹配的正则——静默失效，题目被重复追加「（第N期）」。当前全库 lectureIndex
// 最大 32（<100）故零触发，但这是「结构已坏、只是还没触发」的典型状态。
//
// 本门禁锁三类：① 正常区间（1–99）的中文/阿拉伯识别；② n>99 不产生 undefined；
// ③ 哨兵不得退化成空串（那会让任何含「讲座」的标题被误判为「已含期数」）。
//
// 运行：node tests/js/app_topic_session.js（纯 node + vm，无浏览器无网络）
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SITE = path.join(__dirname, '..', '..', 'site');
const html = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
const PARTS = (html.match(/<script[^>]*\ssrc=["'](app[^"']*?\.js)(?:\?[^"']*)?["']/g) || [])
  .map(t => t.match(/src=["'](app[^"']*?\.js)/)[1]);

const noop = () => {};
const store = {};
const sandbox = {
  console,
  performance: { now: () => Date.now() },
  requestAnimationFrame: noop, setInterval: () => 0, setTimeout: () => 0,
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
  vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf-8'), ctx, { filename: f });
}
const M = vm.runInContext('APP_METHODS', ctx);
const has = (t, i) => M.topicHasSession(t, i);

const errs = [];
const ok = (cond, msg) => { if (!cond) errs.push(msg); };

// ---- ① 正常区间 ----
ok(has('砺儒讲坛第3讲', 3) === true, '「第3讲」应识别为已含期数');
ok(has('华师经英seminar第30期', 30) === true, '「第30期」应识别');
ok(has('讲座三', 3) === true, '「讲座三」中文数字应识别');
ok(has('三讲', 3) === true, '「三讲」应识别');
ok(has('经济与管理学院系列讲座', 5) === false, '普通标题不应误判');
ok(has('深度学习前沿报告', 2) === false, '普通标题不应误判');
ok(has('', 3) === false, '空标题应返回 false');
ok(has('任意', 0) === false, 'idx=0 应返回 false');
ok(has('任意', null) === false, 'idx=null 应返回 false');

// ---- ② n>99 不产生 undefined（本次修复的核心） ----
const src = fs.readFileSync(path.join(SITE, 'app.display.js'), 'utf-8');
const body = src.slice(src.indexOf('topicHasSession('),
                       src.indexOf('topicHasSession(') + 1400);
// 守卫必须在使用 units[...] 之前出现，且形如 `else if (n <= 99)`
const guardAt = body.indexOf('n <= 99');
const useAt = body.indexOf('units[Math.floor(n / 10)]');
ok(guardAt >= 0, 'n>99 分支缺少 `n <= 99` 守卫');
ok(useAt >= 0, '未找到 units[Math.floor(n / 10)] 的使用点（源码结构已变，请复核本用例）');
ok(guardAt >= 0 && useAt > guardAt,
   'units[Math.floor(n/10)] 必须位于 `n <= 99` 守卫之后，否则 n>99 会取到 undefined');

// 行为层：n>99 时不得因 undefined 拼接而产生假匹配，也不得误吞
// 关键断言：含「讲座」但无关期数的标题，n=100 时**不能**被判为已含期数
ok(has('本报告主要内容是关于讲座的总结', 100) === false,
   'n=100 时「含讲座」的无关标题被误判为已含期数（哨兵退化成了空串）');
ok(has('第一讲', 100) === false,
   'n=100 时不应因 undefined 拼接产生假匹配');
ok(has('砺儒讲坛第3讲', 3) === true, '守卫不能误伤 1–99 的正常识别');

// ---- ③ 哨兵不得为空串 ----
ok(!/chinese\s*=\s*''/.test(body),
   'n>99 分支把哨兵赋成空串——会匹配任意含「讲座」的标题（静默吞掉期数显示）');
ok(/uFFFF/.test(body), 'n>99 分支应使用 \\uFFFF 哨兵（永不匹配）');

if (errs.length) {
  console.log('[FAIL]');
  errs.forEach(e => console.log(' -', e));
  process.exit(1);
}
console.log('[PASS] topicHasSession：1–99 识别正确，n>99 不再产生 undefined 且不误吞期数');