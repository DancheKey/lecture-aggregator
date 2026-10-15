// 年份提取跨语言一致性门禁（2026-10-11，G1）。
//
// 为什么重要
// ----------
// 「讲座属于哪一年」由**两个独立实现**共同决定：
//   · Python 侧 scripts/generate_frontend_data.py::year_of() —— 生成 stats.json
//     的学院-年份矩阵与统计页各年份桶；
//   · JS 侧   site/app.display.js::yearOf() —— 首页年份筛选下拉与按年过滤。
// 两者没有任何共享代码，只靠「口径一致」这条约定。若一侧单独改动，Python 侧
// 测试仍全绿、前端测试也全绿，而线上症状是：**统计页把某批讲座归入 A 年、
// 首页筛 A 年却找不到它们**（或年份下拉少一批）——静默错位，零报错。
//
// 本门禁两层：
//   ① 固定样例集逐条跨语言比对（覆盖两个实现的全部分支）；
//   ② 全量真实数据（data/lectures.json）逐条比对——样例想象不到的形态由它兜底。
//
// ⚠ 已知且**有意豁免**的口径差异（2026-10-11 实测，存量影响均为 0）：
//   a) publishTime 带前导空白：Python `strip()[:4]` 命中，JS `/^(\d{4})/`
//      锚定串首不命中 → 走 title 兜底。全库实测 0 条踩中。
//   b) publishTime 为 1~3 位纯数字：Python `[:4].isdigit()` 通过、返回短串；
//      JS 要求恰好 4 位起头、不命中 → 走 title 兜底。全库实测 0 条踩中。
//   a/b 若未来真实数据踩中，② 会红——那时需要人工决策对齐哪一侧，而不是
//   静默错位上线。这正是本门禁的目的。
//
// 2026-10-14（P2-3）：原「a) 兜底值分叉（py='其他' / js=''）」豁免已移除——
//   两侧现统一返回 '其他'（app.display.js::yearOf 与 generate.year_of 对齐），
//   等价关系简化为 js === py。
//
// 运行：node tests/js/app_year_consistency.js（纯 node + vm + python 子进程，无网络）
// 可用 PYTHON 环境变量指定解释器（如 PYTHON="D:/Tools/Python 312/python.exe"）。
// ⚠ 必须用**异步** execFile：Windows 本机 spawnSync 调 python 会报 EBUSY（2026-10-11 实测），
//   异步 execFile 无此问题，CI(Linux) 两者皆可。
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { execFile } = require('child_process');
const { promisify } = require('util');
const execFileAsync = promisify(execFile);

const ROOT = path.join(__dirname, '..', '..');
const SITE = path.join(ROOT, 'site');

// ---------- 0) 固定样例集：覆盖两个实现的全部分支（不含上述 b/c 理论分叉形态） ----------
const CASES = [
  { name: 'lectureStart 空格分隔', item: { lectureStart: '2026-05-01 14:00:00', publishTime: '', title: 'x' } },
  { name: 'lectureStart T 分隔', item: { lectureStart: '2026-05-01T14:00:00', publishTime: '', title: 'x' } },
  { name: 'publishTime 4 位开头', item: { lectureStart: '', publishTime: '2026-01-02 10:00', title: 'x' } },
  { name: 'publishTime 纯 4 位', item: { lectureStart: '', publishTime: '2026', title: 'x' } },
  { name: 'publishTime 开头非数字 → title 兜底', item: { lectureStart: '', publishTime: '发布于2026年', title: '2019年讲座' } },
  { name: 'title 内嵌年份', item: { lectureStart: '', publishTime: '', title: '第2025期活动' } },
  { name: 'title 5 位数字取前 4', item: { lectureStart: '', publishTime: '', title: '20255超大数字' } },
  { name: 'lectureStart 优先于 publishTime/title', item: { lectureStart: '2019-01-01', publishTime: '2026-01-01', title: '2018年' } },
  { name: '全空 → 兜底「其他」（2026-10-14 起两侧一致）', item: { lectureStart: '', publishTime: '', title: '' } },
];

(async () => {
  // ---------- 1) Python 侧：真实 year_of 对样例 + 全量数据 ----------
  const pyProbe = `
import sys, json
sys.path.insert(0, 'scripts')
from generate_frontend_data import year_of
cases = json.loads(r'''${JSON.stringify(CASES.map(c => c.item))}''')
recs = json.load(open('data/lectures.json', encoding='utf-8'))['data']
print(json.dumps({
    'cases': [year_of(dict(c)) for c in cases],
    'all': [year_of(r) for r in recs],
    'n': len(recs),
}, ensure_ascii=False))
`;
  // PYTHONIOENCODING=utf-8（2026-10-12）：Windows 控制台默认 GBK，子进程把「其他」
  // 写成 GBK 字节、父进程按 utf-8 解码得乱码 → 兜底豁免 `py === '其他'` 匹配不上
  // → 本机假红（CI/Linux UTF-8 不复现）。与 test_data_reconciliation._clean_env 同口径。
  const { stdout } = await execFileAsync(process.env.PYTHON || 'python',
    ['-c', pyProbe], { cwd: ROOT, encoding: 'utf-8', maxBuffer: 64 * 1024 * 1024,
                       env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  const PY = JSON.parse(stdout.trim());

  // ---------- 2) JS 侧：vm 加载真实前端分片，取出 yearOf ----------
  const html = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
  const PARTS = (html.match(/<script[^>]*\ssrc=["'](app[^"']*?\.js)(?:\?[^"']*)?["']/g) || [])
    .map(t => t.match(/src=["'](app[^"']*?\.js)/)[1]);

  const noop = () => {};
  const store = {};
  const sandbox = {
    console, performance: { now: () => Date.now() },
    requestAnimationFrame: noop, setInterval: () => 0, setTimeout: () => 0, clearTimeout: noop,
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
  if (typeof M.yearOf !== 'function') {
    console.log('[FAIL] 前端未暴露 yearOf（改名或未挂载）——年份筛选已断');
    process.exit(1);
  }
  const recs = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'lectures.json'), 'utf-8'))['data'];

  // ---------- 3) 比对 ----------
  // 2026-10-14（P2-3）：兜底值已对齐（两侧均返回 '其他'），等价关系简化为逐字相等。
  const eq = (js, py) => js === py;
  const errs = [];

  CASES.forEach((c, i) => {
    const js = M.yearOf(c.item);
    const py = PY.cases[i];
    if (!eq(js, py)) {
      errs.push(`样例「${c.name}」：JS 得 ${JSON.stringify(js)}，Python 得 ${JSON.stringify(py)}`
        + `\n  → 年份口径分叉，统计页与首页筛选会对不上`);
    }
  });

  if (PY.n !== recs.length) {
    errs.push(`两侧读取的数据条数不一致：Python ${PY.n} vs JS ${recs.length}——对账基准不同，结果不可信`);
  }

  const diffs = [];
  for (let i = 0; i < Math.min(recs.length, PY.all.length); i++) {
    const js = M.yearOf(recs[i]);
    const py = PY.all[i];
    if (!eq(js, py)) {
      diffs.push({ i, u: recs[i].sourceUrl, ls: recs[i].lectureStart,
        pt: recs[i].publishTime, t: (recs[i].title || '').slice(0, 40), js, py });
    }
  }
  if (diffs.length) {
    errs.push(`全量对账 ${recs.length} 条中有 ${diffs.length} 条年份不一致`
      + `（可能是文件头 b/c 项理论分叉踩中真实数据，需人工决策对齐侧）：\n`
      + diffs.slice(0, 10).map(d => JSON.stringify(d)).join('\n'));
  }

  // ---------- 4) JS 侧独立行为断言（不依赖 Python 也能拦住单侧退化） ----------
  const extra = [
    [{ lectureStart: '2019-01-01', publishTime: '2026', title: '2018年' }, '2019', 'lectureStart 必须优先于 publishTime/title'],
    [{ lectureStart: '', publishTime: '2026-01-02', title: 'x' }, '2026', 'publishTime 4 位开头应命中'],
    [{ lectureStart: '', publishTime: '', title: '第2025期' }, '2025', 'title 内嵌年份应兜底'],
    [{ lectureStart: '', publishTime: '', title: '20255x' }, '2025', '5 位数字应取前 4 位'],
    [{ lectureStart: '', publishTime: '', title: '' }, '其他', '全空应返回「其他」（与 Python UNKNOWN_YEAR 对齐，2026-10-14）'],
    [null, '', 'null 记录应返回空串且不抛错'],
    [{ lectureStart: '', publishTime: '发布于2026年', title: '2019' }, '2019', 'publishTime 开头非数字时应走 title 兜底'],
  ];
  for (const [item, want, why] of extra) {
    const got = M.yearOf(item);
    if (got !== want) {
      errs.push(`${why}：期望 ${JSON.stringify(want)}，实得 ${JSON.stringify(got)}`);
    }
  }

  if (errs.length) {
    console.log('[FAIL]');
    errs.forEach(e => console.log(' -', e));
    process.exit(1);
  }
  console.log(`[PASS] yearOf 跨语言一致：${CASES.length} 条样例逐条比对`
    + ` + 全量 ${recs.length} 条真实数据对账（0 分叉）+ 7 条 JS 独立行为断言`);
})().catch(e => {
  console.log('[FAIL] 测试自身执行失败（先查 python 子进程/依赖，再查探针脚本）:', e.message);
  process.exit(1);
});
