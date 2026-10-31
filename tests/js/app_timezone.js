// 「讲座时刻按北京时间解析」的回归锁（2026-10-31）。
//
// ## 为什么需要
//
// 数据里 lectureStart/lectureEnd 是**北京时间字面量**（'2026-10-31 14:00:00'），
// 不带时区偏移。写成 `new Date(s)` 会按**浏览器本地时区**解析：
// 纽约用户把北京 14:00 当成自己的 14:00，状态/倒计时/排序全错。
//
// 实测（CDP Emulation.setTimezoneOverride，同一时刻同一份数据）：
//   Asia/Shanghai     待开始 5 / 进行中 1，「最近一场」14:00，倒计时 2 时 47 分
//   America/New_York  待开始 7 / 进行中 0，「最近一场」10:00，倒计时 10 时 47 分
// 纽约用户看到的「下一场」其实已开始 1 小时 13 分。
//
// app.calendar.js 早已写明这条铁律（文件头 ①）并用 Date.UTC(h-8) 实现，
// 但显示层漏改。本次把解析抽到 app.core.js::_bjMs，显示层全部改走它。
//
// ## 本门禁的关键设计
//
// 只断言「_bjMs('...14:00') === Date.UTC(...,6)」在 UTC+8 下**抓不住回归**——
// 因为 new Date(s) 在 +8 下恰好也等于同一个值。所以本测试用 process.env.TZ
// 切到多个时区（Node 22 运行时改 TZ 立即生效），逐时区断言结论不变；
// 并用一条**反例断言**证明「如果退回 new Date，本测试确实会红」。
//
// 运行：node tests/js/app_timezone.js（纯 node，无浏览器无网络）

const fs = require('fs');
const vm = require('vm');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const SITE = path.join(ROOT, 'site');

// ---------- 加载前端分片（与 app_time_placeholder.js 同一套桩） ----------
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

const errs = [];
const p2 = n => String(n).padStart(2, '0');

// ---------- 0) 反例自检：证明本测试真的能抓住回归 ----------
// 纽约时区下 new Date('2026-10-31T14:00') 应是 UTC 18:00（错 12 小时）。
// 若这里等于正确值，说明 Node 的 TZ 切换没生效，整份测试就是空转。
process.env.TZ = 'America/New_York';
const naiveNy = new Date('2026-10-31T14:00').getTime();
const CORRECT = Date.UTC(2026, 9, 31, 6, 0, 0);   // 北京 14:00 = UTC 06:00
if (naiveNy === CORRECT) {
  errs.push('测试自身失效：纽约时区下 new Date() 仍等于正确值，无法区分回归'
    + '（Node 的 process.env.TZ 切换可能未生效）');
}

// ---------- 1) _bjMs 在任何时区下都必须恒定 ----------
const TZS = ['Asia/Shanghai', 'America/New_York', 'Europe/London', 'Asia/Tokyo', 'UTC'];
const SAMPLE = '2026-10-31 14:00:00';
for (const tz of TZS) {
  process.env.TZ = tz;
  const got = M._bjMs(SAMPLE);
  if (got !== CORRECT) {
    errs.push(`_bjMs 在 ${tz} 下得 ${got}，应为 ${CORRECT}`
      + `（${new Date(got).toISOString()} vs ${new Date(CORRECT).toISOString()}）`);
  }
  // 逆运算：_bjParts 必须还原出北京时间的 14:00
  const p = M._bjParts(CORRECT);
  const want = { y: 2026, mo: 10, day: 31, hh: 14, mm: 0, wd: 6 };
  for (const k of Object.keys(want)) {
    if (p[k] !== want[k]) errs.push(`_bjParts 在 ${tz} 下 ${k}=${p[k]}，应为 ${want[k]}`);
  }
}

// ---------- 2) 显示文案在任何时区下都必须一致 ----------
const REC_FMT = { lectureStart: SAMPLE, timeUnknown: false };
for (const tz of TZS) {
  process.env.TZ = tz;
  const fmt = M.fmtDateTime(REC_FMT);
  if (fmt !== '10月31日 周六 14:00') {
    errs.push(`fmtDateTime 在 ${tz} 下得「${fmt}」，应为「10月31日 周六 14:00」`);
  }
  const dk = M.dayKey(SAMPLE);
  if (dk !== '2026-10-31 周六') {
    errs.push(`dayKey 在 ${tz} 下得「${dk}」，应为「2026-10-31 周六」`);
  }
  // 08:00 占位判定与 14:30 非占位判定都不能随时区翻转
  if (M.isTimeTBD({ lectureStart: '2026-10-31 08:00:00' }) !== true) {
    errs.push(`isTimeTBD 在 ${tz} 下把 08:00 判成非占位`);
  }
  if (M.isTimeTBD({ lectureStart: '2026-10-31 14:30:00' }) !== false) {
    errs.push(`isTimeTBD 在 ${tz} 下把 14:30 判成占位`);
  }
}

// ---------- 3) statusOf 在任何时区下结论一致（核心回归锁） ----------
// 用「相对当前时刻」构造样本，避免写死时间导致测试过期。
const bjLiteral = (ms) => {
  const d = new Date(ms + 8 * 3600000);   // 北京时间分量
  return `${d.getUTCFullYear()}-${p2(d.getUTCMonth() + 1)}-${p2(d.getUTCDate())}`
    + ` ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}:00`;
};
const NOW = Date.now();
const H = 3600000;
const STATUS_CASES = [
  // 显式给 lectureEnd，避免半日边界截断干扰
  { why: '1 小时后开始', rec: { lectureStart: bjLiteral(NOW + H), lectureEnd: bjLiteral(NOW + 2 * H) }, want: 'upcoming' },
  { why: '进行中（start-1h ~ end+1h）', rec: { lectureStart: bjLiteral(NOW - H), lectureEnd: bjLiteral(NOW + H) }, want: 'ongoing' },
  { why: '已结束 4 小时', rec: { lectureStart: bjLiteral(NOW - 5 * H), lectureEnd: bjLiteral(NOW - 4 * H) }, want: 'ended' },
  { why: '时间待定', rec: { lectureStart: '' }, want: 'tbd' },
];
for (const tz of TZS) {
  process.env.TZ = tz;
  for (const c of STATUS_CASES) {
    const got = M.statusOf(c.rec);
    if (got !== c.want) {
      errs.push(`statusOf「${c.why}」在 ${tz} 下得 ${got}，应为 ${c.want}`
        + `（lectureStart=${c.rec.lectureStart}）`);
    }
    // isNotStarted 与 statusOf 必须同源：upcoming 恒为 true
    const ns = M.isNotStarted(c.rec, NOW);
    if (c.want === 'upcoming' && ns !== true) {
      errs.push(`isNotStarted「${c.why}」在 ${tz} 下得 ${ns}，应为 true`);
    }
    if (c.want === 'ended' && ns !== false) {
      errs.push(`isNotStarted「${c.why}」在 ${tz} 下得 ${ns}，应为 false`);
    }
  }
}

// ---------- 4) 倒计时文案（statusInfo）跨时区一致 ----------
// statusInfo 读 this.tick（组件 state，秒级心跳）。这里手工注入。
M.tick = NOW;
for (const tz of TZS) {
  process.env.TZ = tz;
  // 25 小时后开始 → 走「还有 N 天」分支，日历日差必须是 1（北京时间口径）
  const rec = { lectureStart: bjLiteral(NOW + 25 * H), lectureEnd: bjLiteral(NOW + 26 * H) };
  const si = M.statusInfo(rec);
  if (!/还有 \d+ 天/.test(si.label)) {
    errs.push(`statusInfo 在 ${tz} 下文案异常：「${si.label}」`);
  } else {
    const gap = parseInt((si.label.match(/还有 (\d+) 天/) || [])[1], 10);
    if (gap !== 1) {
      errs.push(`statusInfo 在 ${tz} 下日历日差得 ${gap}，应为 1（25 小时后即次日）`);
    }
  }
}

// ---------- 5) 日历导出不受影响 + 与 core 口径不分叉 ----------
// app.calendar.js::_calUtcMs 刻意保持独立实现（calendar_ics.js 只单独加载它做隔离
// 测试），因此这里逐样本比对两条实现，任一侧改歪即红。
const CAL_SAMPLES = [
  '2026-10-31 14:00:00',   // 常规
  '2026-10-31 03:00:00',   // 跨日（北京 03:00 = 前一天 UTC 19:00）
  '2026-01-01 00:00',      // 无秒位
  '2026-12-31 23:59:59',   // 年末
  '',                       // 空
  '不是时间',                // 非法
];
for (const tz of TZS) {
  process.env.TZ = tz;
  if (M._calUtcMs(SAMPLE) !== CORRECT) {
    errs.push(`_calUtcMs 在 ${tz} 下得 ${M._calUtcMs(SAMPLE)}，应为 ${CORRECT}`);
  }
  if (M._calUtcMs('') !== null) {
    errs.push(`_calUtcMs 在 ${tz} 下对空串应返回 null，实得 ${M._calUtcMs('')}`);
  }
  if (M._calUtcMs('不是时间') !== null) {
    errs.push(`_calUtcMs 在 ${tz} 下对非法串应返回 null`);
  }
  for (const s of CAL_SAMPLES) {
    const core = M._bjMs(s);
    const coreNorm = Number.isFinite(core) ? core : null;
    const cal = M._calUtcMs(s);
    if (coreNorm !== cal) {
      errs.push(`_bjMs 与 _calUtcMs 口径分叉（${tz}）：「${s}」`
        + `得 ${coreNorm} / ${cal}`);
    }
  }
}

// 复原，避免影响同进程后续（本文件是独立进程，保险而已）
process.env.TZ = 'Asia/Shanghai';

if (errs.length) {
  console.log('[FAIL]');
  errs.slice(0, 20).forEach(e => console.log(' -', e));
  if (errs.length > 20) console.log(`  ... 另 ${errs.length - 20} 条`);
  process.exit(1);
}
console.log(`[PASS] 北京时间口径跨 ${TZS.length} 个时区恒定`
  + `（_bjMs/_bjParts/fmtDateTime/dayKey/isTimeTBD/statusOf/isNotStarted/statusInfo/_calUtcMs）`);
