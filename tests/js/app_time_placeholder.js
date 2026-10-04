// 「讲座时刻是否占位」的前后端口径一致性门禁（2026-10-03）。
//
// ## 为什么需要
//
// 08:00 / 00:00 在本项目是**占位约定**（源页只给日期时解析器统一填充），
// 但确实存在真有讲座在 8:00 开始的情况——这类靠人工标注 `timeUnknown` 区分。
//
// 此前两侧各答一套：
//   · 前端 site/app.display.js::isTimeTBD —— 08:00 与 00:00 **都**算占位
//   · 后端 scraper/hybrid.py 时间守卫      —— **只有** 00:00 算占位
//
// 于是「08:00 且 timeUnknown 未设」的记录，前端说"时间待定"、后端认为是真实时刻
// 并禁止模型覆盖——同一份数据两套答案。实测当时库里这种记录为 0 条（37 条 08:00
// 全带标注）才没出事，那是运气不是设计。
//
// 本门禁用**同一批样例**分别跑 Python（field_vocab.is_placeholder_time）与
// JS（app.display.js::isTimeTBD），断言结论逐条一致。
//
// 运行：node tests/js/app_time_placeholder.js（纯 node，无浏览器无网络）
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.join(__dirname, '..', '..');
const SITE = path.join(ROOT, 'site');

// ---------- 1) Python 侧 ----------
const pyProbe = `
import json, sys
sys.path.insert(0, 'scraper')
from field_vocab import is_placeholder_time
cases = [
    {'lectureStart': '2025-12-28 08:00:00', 'timeUnknown': False},
    {'lectureStart': '2025-12-28 08:00:00', 'timeUnknown': True},
    {'lectureStart': '2025-12-28 08:00:00'},
    {'lectureStart': '2025-12-28 00:00:00'},
    {'lectureStart': '2025-12-28 00:00:00', 'timeUnknown': False},
    {'lectureStart': '2025-12-28 14:30:00'},
    {'lectureStart': '2025-12-28 08:45:00'},
    {'lectureStart': '2025-12-28 08:30:00'},
    {'lectureStart': ''},
    {},
]
print(json.dumps([bool(is_placeholder_time(c)) for c in cases]))
`;
const PY = JSON.parse(
  execFileSync('python', ['-c', pyProbe], { cwd: ROOT, encoding: 'utf-8' }).trim());

// ---------- 2) JS 侧 ----------
const html = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
const PARTS = [...html.matchAll(/<script[^>]*\ssrc="(app[^"]*?\.js)(?:\?[^"]*)?"/g)]
  .map(m => m[1]);
const noop = () => {};
const store = {};
const sb = {
  console, performance: { now: () => Date.now() },
  requestAnimationFrame: noop, setInterval: () => 0, setTimeout: () => 0,
  clearTimeout: noop,
  location: { hostname: 'x.com', href: 'y' }, navigator: { clipboard: null },
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
    documentElement: { style: {} }, querySelectorAll: () => [],
    addEventListener: noop, removeEventListener: noop,
    createElement: () => ({ style: {}, select: noop }),
    body: { appendChild: noop, removeChild: noop },
  },
};
sb.window.top = sb.window; sb.window.self = sb.window;
sb.globalThis = sb;
sb.Vue = { createApp: () => ({ config: {}, mount: noop }) };
const ctx = vm.createContext(sb);
for (const f of PARTS) {
  vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf-8'), ctx, { filename: f });
}
const M = vm.runInContext('APP_METHODS', ctx);

const CASES = [
  { rec: { lectureStart: '2025-12-28 08:00:00', timeUnknown: false }, why: '已确认真实 08:00' },
  { rec: { lectureStart: '2025-12-28 08:00:00', timeUnknown: true }, why: '标注为未知' },
  { rec: { lectureStart: '2025-12-28 08:00:00' }, why: '未标注的 08:00 → 占位' },
  { rec: { lectureStart: '2025-12-28 00:00:00' }, why: '未标注的 00:00 → 占位' },
  { rec: { lectureStart: '2025-12-28 00:00:00', timeUnknown: false }, why: '已确认的真实 00:00' },
  { rec: { lectureStart: '2025-12-28 14:30:00' }, why: '正常时间' },
  { rec: { lectureStart: '2025-12-28 08:45:00' }, why: '08:45 不是占位' },
  { rec: { lectureStart: '2025-12-28 08:30:00' }, why: '08:30 不是占位' },
  { rec: { lectureStart: '' }, why: '缺时间' },
  { rec: {}, why: '无字段' },
];

const errs = [];
CASES.forEach((c, i) => {
  const js = M.isTimeTBD(c.rec);
  const py = PY[i];
  if (js !== py) {
    errs.push(`${c.why}：JS 得 ${js}，Python 得 ${py} —— `
      + '两侧对「08:00/00:00 是否占位」的理解不一致');
  }
});

// 期望值本身也要对（防两侧一起写错而互相"印证"）
const EXPECT = [false, true, true, true, false, false, false, false, true, true];
CASES.forEach((c, i) => {
  if (PY[i] !== EXPECT[i]) {
    errs.push(`${c.why}：Python 得 ${PY[i]}，期望 ${EXPECT[i]}`);
  }
  if (M.isTimeTBD(c.rec) !== EXPECT[i]) {
    errs.push(`${c.why}：JS 得 ${M.isTimeTBD(c.rec)}，期望 ${EXPECT[i]}`);
  }
});

// 库里不该再出现「08:00 且未标注」的记录（那是分叉的触发条件）
const dataPath = path.join(ROOT, 'data', 'lectures.json');
if (fs.existsSync(dataPath)) {
  const d = JSON.parse(fs.readFileSync(dataPath, 'utf-8')).data;
  const risky = d.filter(r => {
    const s = String(r.lectureStart || '');
    return s.slice(11, 16) === '08:00' && r.timeUnknown === undefined;
  });
  if (risky.length) {
    errs.push(`库中有 ${risky.length} 条「08:00 且无 timeUnknown」的记录——`
      + '正是前后端口径分叉的触发条件，须补标注');
  }
}

if (errs.length) {
  console.log('[FAIL]');
  errs.forEach(e => console.log(' -', e));
  process.exit(1);
}
console.log(`[PASS] 前后端占位判定一致（${CASES.length} 条样例逐条比对），`
  + '且库中无「08:00 无标注」记录');