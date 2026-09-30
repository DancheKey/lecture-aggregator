// 门禁：顶部总数「阶梯跳变」的正确性。
//
// 背景（2026-09-30，两轮）：
//   ① 原始形态是「固定 500ms 滚动动画」+ 分片乱序到达（1.8~18s），观感是
//      「平滑上滚 → 长时间不动 → 猛跳一段」。先试过「动画时长自适应」，
//      但方向错了：让用户去猜进度（数字要滚 3~6 秒才停，看不出加载到哪），
//      且 12s 空档仍有 ~9s 静止 —— 治标未治本。
//   ② 用户实测发现**数字出现过负数**。根因在插值动画里：缓动进度
//      t = (now - _countStart) / _countDur 只钳了上限、没钳下限，而
//      requestAnimationFrame 回调的 now 是「本帧开始时刻」、_countStart 取自
//      帧内某刻的 performance.now()，两者不同时刻基准时 t 为负 → easeOutCubic
//      输出负增量 → 插值出负数（实测滞后 30ms 即 -29）。
//
// 最终形态：阶梯跳变 —— 每个分片到达时数字**直接跳**到已加载真实条数
//   （50 → 550 → 1050 → … → 3810），不做任何插值。
//   没有插值就没有中间值，也就没有负数与锯齿，数字本身即加载进度。
//
// 本门禁锁四条性质（缺一即红）：
//   ① **无负数**：任何时序下 displayTotal 不得为负（回归上一轮的实测 bug）。
//   ② **恒等于真实条数**：显示值必须始终等于已加载真实条数，不得是插值中间值。
//   ③ **单调不减**：逐帧不得回落。
//   ④ **分片到达必跳变**：每次分片到位数字要立刻更新（不能吞掉一次更新）。
//
// ⚠ 门禁自身的一条教训（别再踩）：上一版仿真里 rAF 回调恰好传入「当前时刻」，
//   导致 t 恒 ≥ 0，**永远复现不出负数 bug** —— 门禁全绿而线上有 bug。
//   仿真的时钟假设必须比真实环境更苛刻，否则门禁只是在验证自己的假设。
//   本版因此改为「不依赖 rAF 时序」：直接断言显示值与真实条数相等。
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

/* ---------- 加载方法 ----------
 * ⚠ app.core.js 里的 `const APP_METHODS` 是它自己声明的，**不要**在 sandbox 里预置同名
 *   全局，否则 vm 直接抛 `Identifier already declared`。正解是完整执行 app.core.js
 *   （它会自行声明），再执行 app.admin.js。
 * ⚠ core 顶部有 `const { createApp } = Vue;`，需给个空壳；点劫持 IIFE 依赖 window.top，
 *   window=self 时被内部 try 捕获，不影响。 */
function loadMethods() {
  const sb = { console, Object, Math, Set, Map, JSON, Date };
  sb.window = sb;
  sb.performance = { now: () => 0 };
  sb.cancelAnimationFrame = () => {};
  sb.Vue = { createApp: () => ({}) };
  vm.createContext(sb);
  vm.runInContext(CORE, sb);
  vm.runInContext(ADMIN, sb);
  return vm.runInContext('APP_METHODS', sb);
}

/* ---------- 模拟分片到达 ----------
 * 复现生产合并语义：_mergeChunk 按 key **覆盖**已有条目。
 * ⚠ 口径要点：生产 latest.json 的 50 条按 key **50/50 全在第 1 片内**（实测核对
 *   site/lectures/），合并时被覆盖，故真实总数 = 3810（= chunks.json 的 total），
 *   不是 50+3810=3860。早期版本用纯累加模拟，终值期望比生产多 50 条。 */
const CHUNKS = [500, 500, 500, 500, 500, 500, 500, 310];
// 公网实测：4 路并发，单片 1.8~18s 乱序到达
const WEAK = [1800, 5200, 900, 3400, 12000, 2600, 7000, 1500];
// 串行时代形态：间隔稳定约 2s
const STEADY = [2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000];
// 本地快速：25~60ms 一片
const FAST = [30, 45, 25, 60, 35, 50, 40, 55];
const PROD_TOTAL = 3810;

function simulate(arrivalMs, chunkSizes) {
  const M = loadMethods();
  const s = { all: [], displayTotal: 0, displaySource: 0 };
  // 还原 Vue3 computed 的 getter 语义（见本文件顶部注释）
  Object.defineProperty(s, 'totalCount', { get() { return this.all.length; } });
  Object.defineProperty(s, 'sourceNoticeCount', { get() { return this.all.length * 2; } });
  for (const k of ['syncCountDisplay', 'bumpCount']) s[k] = M[k].bind(s);

  const byKey = new Map();
  function merge(tag, n) { for (let j = 0; j < n; j++) byKey.set(tag + '_' + j, 1); s.all = Array.from(byKey.values()); }

  const timeline = [];   // 每次 bump 后的 {已加载条数, 显示值}
  s.syncCountDisplay();
  timeline.push({ loaded: s.all.length, shown: s.displayTotal });
  merge('L', 50); s.bumpCount();
  timeline.push({ loaded: s.all.length, shown: s.displayTotal });
  arrivalMs.forEach((ms, i) => {
    merge(i === 0 ? 'L' : 'C' + i, chunkSizes[i]);   // 第 1 片覆盖 latest
    s.bumpCount();
    timeline.push({ loaded: s.all.length, shown: s.displayTotal, gap: ms });
  });
  return { timeline, finalShown: s.displayTotal, expected: byKey.size };
}

console.log('顶部总数：阶梯跳变（无插值）');

console.log('\n[0] 模拟口径与生产一致（防止终值期望漂移）');
const base = simulate(WEAK, CHUNKS);
check(
  `模拟终态条数 = 生产真实总数 ${PROD_TOTAL}（latest 50 条被第 1 片覆盖，非累加 3860）`,
  base.expected === PROD_TOTAL,
  `模拟得 ${base.expected}`
);

console.log('\n[1] 无负数 + 恒等于真实条数 + 单调不减（三种到达时序）');
[[WEAK, '弱网乱序'], [STEADY, '串行稳定'], [FAST, '本地快速']].forEach(([arr, label]) => {
  const r = simulate(arr, CHUNKS);
  const negatives = r.timeline.filter(x => x.shown < 0);
  check(
    `${label}：无负数`,
    negatives.length === 0,
    negatives.length ? '负值 ' + negatives.slice(0, 5).map(x => x.shown).join(', ') : `全程非负（终值 ${r.finalShown}）`
  );
  const mismatched = r.timeline.filter(x => x.shown !== x.loaded);
  check(
    `${label}：显示值恒等于已加载真实条数（无插值中间值）`,
    mismatched.length === 0,
    mismatched.length
      ? `不匹配 ${mismatched.length} 处：` + mismatched.slice(0, 4).map(x => `载${x.loaded}/显${x.shown}`).join(', ')
      : `${r.timeline.length} 个观测点全部一致`
  );
  let regress = 0;
  for (let i = 1; i < r.timeline.length; i++) if (r.timeline[i].shown < r.timeline[i - 1].shown) regress++;
  check(
    `${label}：单调不减`,
    regress === 0,
    `${regress === 0 ? r.timeline.map(x => x.shown).join(' → ') : '回落 ' + regress + ' 处'}`
  );
  check(
    `${label}：终值正确 = ${r.expected}`,
    r.finalShown === r.expected,
    `实得 ${r.finalShown}`
  );
});

console.log('\n[2] 分片到达必跳变（不得吞掉更新）');
const seq = base.timeline.map(x => x.shown).join(' → ');
check(
  '阶梯与真实分片边界一致（首屏 50，之后每片 +500/+310）',
  base.timeline.every((x, i) => x.shown === x.loaded),
  seq
);
// 观测点构成：初始(0) + 首屏 latest(50) + 8 个分片 = 10
check(
  '跳变次数 = 分片数 + 2（初始 + 首屏 + 8 片）',
  base.timeline.length === CHUNKS.length + 2,
  `${base.timeline.length} 次观测（期望 ${CHUNKS.length + 2}）`
);

console.log('\n' + (failures === 0
  ? '全部通过'
  : `✗ ${failures} 项失败`));
process.exit(failures === 0 ? 0 : 1);
