// 长文本键（_ltKey）跨语言一致性门禁（2026-10-02）。
//
// 为什么重要
// ----------
// 长文本（简介/摘要）的查找键由**两个独立实现**共同决定：
//   · Python 侧 scripts/frontend_fields.lt_key()  —— 负责写 detail 桶与 manifest
//   · JS 侧   site/app.data.js 的 _ltKey()        —— 负责按桶号取回并填入卡片
// 两者**没有任何共享代码**，只靠「逐字一致」这条约定。若 JS 侧把 `'#'` 改成 `':'`
// 或调整字段顺序，Python 侧测试仍全绿，而线上症状是：
// **所有卡片的简介/摘要静默变空、零报错**——正是本项目最痛的失败模式。
//
// 此前已有的覆盖是「巧合式」的：tests/js/app_longtext_flow.js 的桩数据恰好也用
// 同一个 `#` 字面量，于是改 JS 也会一起变、断言仍成立。改桩即失效，且无注释说明
// 这层绑定。本门禁改为**直接执行真实 _ltKey**并与 Python 侧结果逐条比对——
// 桩数据与被测代码解耦，任何一侧单独改动都会被抓到。
//
// 运行：node tests/js/app_ltkey_consistency.js（纯 node + vm，无浏览器无网络）
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { execFileSync } = require('child_process');

const ROOT = path.join(__dirname, '..', '..');
const SITE = path.join(ROOT, 'site');

// ---------- 1) 取出 Python 侧 lt_key 的真实输出 ----------
const pyProbe = `
import io, json, sys
sys.path.insert(0, 'scripts')
from frontend_fields import lt_key
cases = [
    {'sourceUrl': 'https://x/a.html', 'lectureIndex': 3},
    {'sourceUrl': 'https://x/a.html', 'lectureIndex': 0},
    {'sourceUrl': 'https://x/a.html'},
    {'sourceUrl': 'https://x/a.html', 'lectureIndex': None},
    {'sourceUrl': '', 'lectureIndex': 2},
    {'sourceUrl': 'u#weird', 'lectureIndex': 7},
]
print(json.dumps([lt_key(c) for c in cases], ensure_ascii=False))
`;
// 可用 PYTHON 环境变量指定解释器（本机 python 未入 PATH 时必需，
// 如 PYTHON="D:/Tools/Python 312/python.exe" node tests/js/app_ltkey_consistency.js）
const pyOut = execFileSync(process.env.PYTHON || 'python', ['-c', pyProbe], { cwd: ROOT, encoding: 'utf-8' });
const PY_KEYS = JSON.parse(pyOut.trim());

// ---------- 2) 在 vm 里加载真实前端分片，取出 _ltKey ----------
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
if (typeof M._ltKey !== 'function') {
  console.log('[FAIL] 前端未暴露 _ltKey（改名或未挂载）');
  process.exit(1);
}

const JS_CASES = [
  { sourceUrl: 'https://x/a.html', lectureIndex: 3 },
  { sourceUrl: 'https://x/a.html', lectureIndex: 0 },
  { sourceUrl: 'https://x/a.html' },
  { sourceUrl: 'https://x/a.html', lectureIndex: null },
  { sourceUrl: '', lectureIndex: 2 },
  { sourceUrl: 'u#weird', lectureIndex: 7 },
];

// ---------- 3) 逐条比对 ----------
const errs = [];
JS_CASES.forEach((c, i) => {
  const js = M._ltKey(c);
  const py = PY_KEYS[i];
  if (js !== py) {
    errs.push(`用例 ${i} ${JSON.stringify(c)}：JS 得 ${JSON.stringify(js)}，`
      + `Python 得 ${JSON.stringify(py)}`
      + `\n  → 长文本查找键不一致，卡片上的简介/摘要会静默变空`);
  }
});

// ---------- 4) 独立的行为断言（不依赖 Python 也能拦住）----------
const extra = [
  [{ sourceUrl: 'u', lectureIndex: 3 }, 'u#3', '多场次带期号'],
  [{ sourceUrl: 'u' }, 'u#', '无期号时留空（不是 undefined/null 字符串）'],
  [{ sourceUrl: 'u', lectureIndex: null }, 'u#', 'lectureIndex=null 与缺失同义'],
  [{ sourceUrl: 'u', lectureIndex: 0 }, 'u#0', '期号 0 不得被 || 吞成空串'],
];
for (const [c, want, why] of extra) {
  const got = M._ltKey(c);
  if (got !== want) {
    errs.push(`${why}：期望 ${JSON.stringify(want)}，实得 ${JSON.stringify(got)}`);
  }
}

// ---------- 5) 注释指向的文件名别再错 ----------
const disp = fs.readFileSync(path.join(SITE, 'app.data.js'), 'utf-8');
if (/generate_frontend_data\._lt_key\(\)/.test(disp)) {
  errs.push('app.data.js 的 _ltKey 注释仍写 `_lt_key()`（旧名），'
    + '实际 Python 实现叫 lt_key（frontend_fields.py）——注释会误导定位');
}

if (errs.length) {
  console.log('[FAIL]');
  errs.forEach(e => console.log(' -', e));
  process.exit(1);
}
console.log(`[PASS] _ltKey 跨语言一致（${JS_CASES.length} 条真实用例逐条比对）`
  + ` + 4 条独立行为断言`);