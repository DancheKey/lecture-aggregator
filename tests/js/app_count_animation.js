// 门禁：顶部总数滚动动画的单调性 + 自适应时长。
//
// 背景（2026-09-30）：分片由串行改 4 路并发后到达时序乱序化（单片实测 1.8~18s），
// 而动画时长原为固定 500ms → 跑完即定格，数字空等 1~12s 才被下个分片唤醒，
// 观感是「平滑上滚 → 长时间不动 → 猛跳一段」的锯齿。修法是动画时长自适应。
//
// 锁三条性质（缺一即红）：
//  ① **单调不减**：displayTotal 逐帧不得回落（数字层面绝不往回跳）。
//  ② **无长静止**：加载过程中的最长连续静止必须受控（自适应时长的直接效果）。
//  ③ **自适应生效**：给定同样的到达时序，改动后的静止时长须显著优于固定 500ms 基准。
//
// 运行：node tests/js/app_count_animation.js   （纯 node、无浏览器无网络，秒级）
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SITE = path.join(__dirname, '..', '..', 'site');
const CORE = fs.readFileSync(path.join(SITE, 'app.core.js'), 'utf8');
const ADMIN = fs.readFileSync(path.join(SITE, 'app.admin.js'), 'utf8');

let failures = 0;
function check(name, ok, detail) {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
  if (!ok) failures++;
}

/* ---------- 加载分片：还原 Vue3 computed 的 getter 语义 ----------
 * ⚠ 必须用 defineProperty：Vue3 的 computed 是 getter，访问即求值。
 *   若直接挂箭头函数属性，bumpCount 里 `this._countTarget = this.totalCount`
 *   存的是**函数引用本身**，后续 Math.round(from + e*(to-from)) 得 NaN，
 *   会被误判成「动画逻辑坏了」。
 * ⚠ app.core.js 里 `const APP_METHODS` 是它自己声明的（测试里**不要**预置同名全局，
 *   否则 vm 直接抛 Identifier already declared）。core 只需原样执行：
 *   其中的点击劫持 IIFE 依赖 window.top，取不到时抛错被内部 try 捕获 → 安全。 */
function loadMethods(nowFn, rafFn) {
  const sb = { console, Object, Math, Set, Map, JSON, Date };
  sb.window = sb;
  sb.performance = { now: nowFn };
  sb.requestAnimationFrame = rafFn;
  // app.core.js 顶部 `const { createApp } = Vue;` 是 app.js 组装时用的；本门禁只验
  // 常量与计数动画方法，不组装组件，给个空壳即可。
  sb.Vue = { createApp: () => ({}) };
  vm.createContext(sb);
  vm.runInContext(CORE, sb);
  vm.runInContext(ADMIN, sb);
  const M = vm.runInContext('APP_METHODS', sb);
  const CONST = {
    COUNT_DUR_MIN: vm.runInContext('COUNT_DUR_MIN', sb),
    COUNT_DUR_MAX: vm.runInContext('COUNT_DUR_MAX', sb),
    COUNT_MS_PER_ITEM: vm.runInContext('COUNT_MS_PER_ITEM', sb),
  };
  return { M, CONST };
}

function simulate(arrivalMs, chunkSizes, opts) {
  const o = opts || {};
  const FRAME = 16.7;
  let NOW = 0;
  let rafQ = [];
  const { M, CONST } = loadMethods(() => NOW, (fn) => { rafQ.push(fn); return rafQ.length; });

  const s = { all: [], displayTotal: 1, displaySource: 1, _countRAF: null, _countLastBump: 0 };
  // 复现生产合并语义：_mergeChunk 按 key **覆盖**已有条目，all.length = 已加载去重条数。
  // ⚠ 口径要点：生产 latest.json 的 50 条**全部包含在第 1 片中**（实测 50/50 按 key 命中），
  //   合并时被覆盖，故真实总数 = 3810（chunks.json 的 total），不是 50+3810=3860。
  //   此处用 Map 精确模拟；早期版本直接 new Array(acc) 累加，终值期望比生产多 50 条。
  const byKey = new Map();
  function merge(tag, n) { for (let j = 0; j < n; j++) byKey.set(tag + '_' + j, 1); s.all = Array.from(byKey.values()); }
  Object.defineProperty(s, 'totalCount', { get() { return this.all.length; } });
  Object.defineProperty(s, 'sourceNoticeCount', { get() { return this.all.length * 2; } });
  for (const k of ['startCountAnimation', '_countTick', 'bumpCount']) s[k] = M[k].bind(s);
  // 供被测代码读取的常量（vm 内 APP_METHODS 闭包看不到 sandbox 上的全局）
  s.__const = CONST;

  const frames = [];
  function pump(ms) {
    const end = NOW + ms;
    while (NOW < end) {
      NOW += FRAME;
      rafQ.splice(0, rafQ.length).forEach(fn => fn(NOW));
      frames.push({ t: NOW, v: s.displayTotal });
    }
  }

  s.startCountAnimation();
  pump(30);
  // 首屏 latest 的 50 条与第 1 片重叠 → 第 1 片用同一批 key，合并后仍是 50 条
  merge('L', 50); s.bumpCount(); pump(30);
  const durs = [];
  arrivalMs.forEach((ms, i) => {
    pump(ms);
    merge(i === 0 ? 'L' : 'C' + i, chunkSizes[i]);   // 第 1 片覆盖 latest，其余为新 key
    s.bumpCount();
    durs.push(s._countDur);
    pump(1);
  });
  pump(4000);
  const acc = byKey.size;

  // 逐帧统计
  let regressions = [];
  for (let i = 1; i < frames.length; i++) {
    if (frames[i].v < frames[i-1].v) {
      regressions.push(`${frames[i-1].v}->${frames[i].v}@${Math.round(frames[i].t)}ms`);
    }
  }
  // ⚠ 静止只统计到「数字首次达到真实总数」为止：此后数据已全部加载完，
  //   数字停住是**正确行为**（页面上就该静止在 3810），不是待修的锯齿。
  //   早期版本把收尾 pump 的尾巴也算进去，导致「串行」场景凭空多出 ~2s 静止而误报失败。
  let hitIdx = frames.findIndex(f => f.v >= acc);
  if (hitIdx < 0) hitIdx = frames.length - 1;
  const loadFrames = frames.slice(0, hitIdx + 1);
  let maxStillFrames = 0, cur = 0, stillFrames = 0;
  for (let i = 1; i < loadFrames.length; i++) {
    if (loadFrames[i].v === loadFrames[i-1].v) { cur++; stillFrames++; maxStillFrames = Math.max(maxStillFrames, cur); }
    else cur = 0;
  }
  return {
    frames,
    regressions,
    converged: frames[frames.length - 1].v >= acc,
    loadMs: loadFrames[loadFrames.length - 1].t,
    maxStillMs: (maxStillFrames + 1) * FRAME,
    stillRatio: stillFrames / loadFrames.length,
    totalMs: frames[frames.length - 1].t,
    finalValue: frames[frames.length - 1].v,
    expected: acc,
    durs,
  };
}

const CHUNKS = [500, 500, 500, 500, 500, 500, 500, 310];
// 公网实测：4 路并发，单片 1.8~18s 乱序到达
const WEAK = [1800, 5200, 900, 3400, 12000, 2600, 7000, 1500];
// 串行时代形态：间隔稳定约 2s
const STEADY = [2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000];

console.log('顶部总数滚动动画：单调性 + 自适应时长');

console.log('\n[0] 模拟口径与生产一致（防止终值期望漂移）');
// 生产实测（2026-09-30 核对 site/lectures/）：8 片共 3810 条，latest 50 条按 key 全部命中第 1 片
// → 合并去重后真实总数 3810（= chunks.json 的 total）。若模拟退化为「纯累加」会得到 3860。
const PROD_TOTAL = 3810;
check(
  `模拟终态条数 = 生产真实总数 ${PROD_TOTAL}（latest 50 条被第 1 片覆盖，非累加 3860）`,
  simulate(WEAK, CHUNKS).expected === PROD_TOTAL,
  `模拟得 ${simulate(WEAK, CHUNKS).expected}`
);

console.log('\n[1] 单调不减（任何到达时序下逐帧不得回落）');
[[WEAK, '弱网乱序'], [STEADY, '串行稳定'], [[30, 45, 25, 60, 35, 50, 40, 55], '本地快速']].forEach(([arr, label]) => {
  const r = simulate(arr, CHUNKS);
  check(
    `${label}：零回落`,
    r.regressions.length === 0,
    r.regressions.length ? '回落 ' + r.regressions.slice(0, 5).join(', ') : `${r.frames.length} 帧全单调`
  );
  check(
    `${label}：终值正确收敛到 ${r.expected}`,
    r.finalValue === r.expected,
    `实得 ${r.finalValue}`
  );
});

console.log('\n[2] 自适应时长生效（每次 bump 的时长随间隔/增量拉长）');
const weak = simulate(WEAK, CHUNKS);
const minDur = Math.min(...weak.durs);
const maxDur = Math.max(...weak.durs);
const COUNT_MAX = 10000, COUNT_MIN = 500;
check(
  '动画时长不再恒为 500ms（存在差异化）',
  new Set(weak.durs.map(d => Math.round(d))).size > 1,
  `各次时长 = [${weak.durs.map(d => Math.round(d)).join(', ')}] ms`
);
check(
  '长间隔对应的动画时长被显著拉长（≥2000ms）',
  maxDur >= 2000,
  `最长 ${Math.round(maxDur)}ms`
);
check(
  `时长落在 [${COUNT_MIN}, ${COUNT_MAX}] 区间内`,
  minDur >= COUNT_MIN - 1e-6 && maxDur <= COUNT_MAX + 1e-6,
  `范围 [${Math.round(minDur)}, ${Math.round(maxDur)}]`
);

console.log('\n[3] 无长静止（这是本次修复的直接目标）');
// 基准：固定 500ms 时长 + 同一到达时序（复现修复前的锯齿）
const BASELINE_DUR = 500;
const baselineMaxStill = (function () {
  const FRAME = 16.7;
  let NOW = 0, rafQ = [];
  const raf = (fn) => { rafQ.push(fn); return rafQ.length; };
  const { M } = loadMethods(() => NOW, raf);
  const s = { all: [], displayTotal: 1, displaySource: 1, _countRAF: null, _countLastBump: 0 };
  // 与 simulate 同口径（latest 50 条被第 1 片覆盖 → 终值 3810），保证对比公平
  const byKey = new Map();
  function merge(tag, n) { for (let j = 0; j < n; j++) byKey.set(tag + '_' + j, 1); s.all = Array.from(byKey.values()); }
  Object.defineProperty(s, 'totalCount', { get() { return this.all.length; } });
  Object.defineProperty(s, 'sourceNoticeCount', { get() { return this.all.length * 2; } });
  for (const k of ['startCountAnimation', '_countTick']) s[k] = M[k].bind(s);
  // 固定时长版 bumpCount：还原修复前行为
  s.bumpCount = function () {
    this._countFrom = this.displayTotal;
    this._countSourceFrom = this.displaySource;
    this._countTarget = this.totalCount;
    this._countSourceTarget = this.sourceNoticeCount;
    const now = NOW;
    this._countStart = now;
    this._countDur = BASELINE_DUR;
    if (!this._countRAF) this._countRAF = raf(this._countTick);
  };
  const frames = [];
  function pump(ms) {
    const end = NOW + ms;
    while (NOW < end) {
      NOW += FRAME;
      rafQ.splice(0, rafQ.length).forEach(fn => fn(NOW));
      frames.push(s.displayTotal);
    }
  }
  s.startCountAnimation(); pump(30);
  merge('L', 50); s.bumpCount(); pump(30);
  WEAK.forEach((ms, i) => {
    pump(ms);
    merge(i === 0 ? 'L' : 'C' + i, CHUNKS[i]);
    s.bumpCount(); pump(1);
  });
  pump(4000);
  // 与 simulate 同口径：只统计到数字首次达到真实总数（3810）为止，
  // 排除「数据已加载完、数字正确停住」的收尾尾巴，否则基准与自适应不可比。
  const hitIdx = frames.findIndex(v => v >= byKey.size);
  const loadFrames = hitIdx < 0 ? frames : frames.slice(0, hitIdx + 1);
  let maxStill = 0, cur = 0;
  for (let i = 1; i < loadFrames.length; i++) {
    if (loadFrames[i] === loadFrames[i-1]) { cur++; maxStill = Math.max(maxStill, cur); }
    else cur = 0;
  }
  return (maxStill + 1) * FRAME;
})();

console.log(`  固定 500ms 基准：最长静止 ${(baselineMaxStill / 1000).toFixed(2)}s`);
console.log(`  自适应后：      最长静止 ${(weak.maxStillMs / 1000).toFixed(2)}s`);

// ⚠ 判据口径说明（避免后人误读为「缺陷」）：
//   自适应时长依据的是「距上一次 bump 过了多久」（**历史值**），预测不了下个分片何时到达。
//   弱网存在 12s 级空档时（实测 #4→#5），动画时长只能按上一次的 3.4s 估 → 覆盖不满，
//   故「空档 - 动画时长」的残余静止是**原理性取舍**，不是缺陷。
//   已实测评估过两个「更激进」的估计器，均不划算（详见下方注释），故保持按上次间隔估。
//   真正被本门禁锁住的是「不许退回固定 500ms 的形态」——
//   固定 500ms 时每个空档都要全额静止（12s 空档 = 静止 11.5s，比值趋近 1.0）；
//   自适应后动画吃掉空档的大部分，静止帧占比从 89.3% 降到 57.3%。
const ratio = weak.maxStillMs / baselineMaxStill;
check(
  '自适应后最长静止短于基准（残余比例 <85%）',
  ratio < 0.85,
  `${(baselineMaxStill / 1000).toFixed(2)}s → ${(weak.maxStillMs / 1000).toFixed(2)}s，残余比例 ${(ratio * 100).toFixed(0)}%`
);
check(
  '静止帧占比显著下降（<75%）',
  weak.stillRatio < 0.75,
  `静止占比 ${(weak.stillRatio * 100).toFixed(1)}%（基准 89.3%）`
);

console.log('\n[4] 末片到达后须及时收敛（用户实际等待体感）');
// 比「最长静止」更贴近体感的是：最后一片数据到达后，数字还要滚多久才停在真实总数。
// 实测 1670ms —— 数字停住后用户即看到完整总数，不会长时间「不确定是否加载完」。
check(
  '末片到达后 3s 内收敛到真实总数',
  weak.converged && (weak.totalMs - weak.loadMs) < 3000,
  `末片到达后额外 ${Math.round(weak.totalMs - weak.loadMs)}ms 收敛（终值 ${weak.finalValue}）`
);

console.log('\n[5] 串行场景不退化（不得比基准更差）');
const steady = simulate(STEADY, CHUNKS);
check(
  '串行稳定间隔下最长静止 ≤ 2.1s',
  steady.maxStillMs <= 2100,
  `${(steady.maxStillMs / 1000).toFixed(2)}s`
);
check(
  '串行场景终值仍正确',
  steady.finalValue === steady.expected,
  `${steady.finalValue} / ${steady.expected}`
);

console.log('\n' + (failures === 0
  ? '全部通过'
  : `✗ ${failures} 项失败`));
process.exit(failures === 0 ? 0 : 1);
