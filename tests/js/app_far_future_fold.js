// 前端 computed 行为测试（2026-10-13）：方案 B「两段式排序 + 远期折叠」。
//
// 背景
// ----
// 用户诉求：① 超过 7 天的待开始讲座折叠成提示条（默认折叠、点击就地展开、刷新不丢）；
//          ② 24h 内的讲座置顶（按最先开始顺序优先展示）。
// DeepSeek 方案 A 只改 filtered 排序——不够：pagedGroups 组间排序原是日期倒序，
// 分组后远期组会凭日期最大弹回首屏，两段式被层间抵消（test_03 守这个漏洞）。
//
// 本测试锁定：
//   test_01 filtered 两段式排序（未来升序在前 / 已结束倒序在后 / 无时间垫底）
//   test_02 24h 内置顶（未来段升序的自然结果）
//   test_03 pagedGroups 组间跟随 filtered 顺序（防层间抵消）
//   test_04 foldedUnits：≥3 条 >7 天才折成 far-fold 容器（位置=未来区末尾、恒占 1 位）
//   test_04b 1-2 条 >7 天不折叠（FAR_FOLD_MIN=3 边界）：原样渲染、顺序与不折叠路径一致
//   test_05 无 >7 天时不产生折叠容器
//   test_06 fold 组：折叠态 items 空、展开态 items=被折 units（原样走普通渲染分支）
//   test_07 分页按 foldedUnits 计（展开/收起不改页数）
//   test_08 toggleFarFuture + localStorage 持久化
//
// 运行：node tests/js/app_far_future_fold.js（纯 node，无浏览器无网络）
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');
const INDEX_HTML = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
const PARTS = (INDEX_HTML.match(/<script[^>]*\ssrc=["'](app[^"']*?\.js)(?:\?[^"']*)?["']/g) || [])
  .map(tag => tag.match(/src=["'](app[^"']*?\.js)/)[1]);

const noop = () => {};
const store = {};
// 确定性时钟（2026-10-13 用户规则要求边界可测）：vm 内所有 Date.now()/new Date()
// 都走 FakeDate，基准固定为 2026-10-13 12:30（对应用户截图时刻）。
// ⚠ iso()/FIXED 相关计算在宿主的真实 Date 上做，只有 sandbox.Date 被替换。
let FIXED_MS = new Date(2026, 9, 13, 12, 30, 0).getTime();
class FakeDate extends Date {
  constructor(...args) {
    if (args.length === 0) super(FIXED_MS);
    else super(...args);
  }
  static now() { return FIXED_MS; }
}
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
sandbox.Date = FakeDate;
sandbox.Vue = { createApp: () => ({ config: {}, mount: noop }) };

const ctx = vm.createContext(sandbox);
for (const f of PARTS) {
  vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf-8'), ctx, { filename: f });
}

const computed = vm.runInContext('APP_COMPUTED', ctx);
const methods = vm.runInContext('APP_METHODS', ctx);
const CONSTS = vm.runInContext(
  '({ FAR_FUTURE_DAYS, FAR_FOLD_KEY })', ctx);

const errs = [];
const ok = (cond, msg) => { if (!cond) errs.push(msg); };

// ---------- 测试数据（时刻基于 FIXED_MS，与运行时刻/时区无关） ----------
const DAY = 86400000;
function iso(msOffset) {
  const d = new Date(FIXED_MS + msOffset);
  const p2 = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}:00`;
}
// 一场「待开始」讲座：off=距 FIXED 的毫秒数；day 用于生成不同的 sourceUrl（避免同日折叠）
function future(off, day, title) {
  return {
    sourceUrl: `http://x.scnu.edu.cn/f${day}`, title,
    lectureStart: iso(off), topic: '', location: '地点' + day,
    speaker: '讲者' + day, speakerKeys: [], college: '文学院', campus: '石牌',
    lectureIndex: 1,
  };
}
function past(dayBack, title) {
  return {
    sourceUrl: `http://x.scnu.edu.cn/p${dayBack}`, title,
    lectureStart: iso(-dayBack * DAY), topic: '', location: '地点' + dayBack,
    speaker: '讲者' + dayBack, speakerKeys: [], college: '文学院', campus: '石牌',
    lectureIndex: 1,
  };
}
// 指定本地时刻的一场讲座（确定性边界用例用）：'2026-10-13 10:30' 形式
function at(localIso, day, title, end) {
  return {
    sourceUrl: `http://x.scnu.edu.cn/a${day}`, title,
    lectureStart: localIso, lectureEnd: end || undefined,
    topic: '', location: '地点' + day,
    speaker: '讲者' + day, speakerKeys: [], college: '文学院', campus: '石牌',
    lectureIndex: 1,
  };
}

// 物化 computed 链（框架缓存由我们手动模拟，同 app_forum_and_clamp.js 的教训：
// filtered/units 在真实运行时是计算属性，直接调下游函数会读到 undefined）。
// ⚠ methods 必须一并挂上：pagedGroups 内部要调 this.dayKey（app.display.js）。
function makeCtx(allData, over) {
  const cm = Object.assign(vm.runInContext('APP_STATE()', ctx), methods, {
    all: allData, campus: '', college: '', year: '', query: '', searchField: '',
    speaker: '', showLikedOnly: false, likedUrls: new Set(),
    pageSize: 25, currentPage: 1,
  }, over || {});
  Object.defineProperty(cm, 'filtered', {
    get: () => computed.filtered.call(cm), configurable: true });
  Object.defineProperty(cm, 'units', {
    get: () => computed.units.call(cm), configurable: true });
  Object.defineProperty(cm, 'foldedUnits', {
    get: () => computed.foldedUnits.call(cm), configurable: true });
  Object.defineProperty(cm, 'pagedUnits', {
    get: () => computed.pagedUnits.call(cm), configurable: true });
  Object.defineProperty(cm, 'pagedGroups', {
    get: () => computed.pagedGroups.call(cm), configurable: true });
  Object.defineProperty(cm, 'totalPages', {
    get: () => computed.totalPages.call(cm), configurable: true });
  return cm;
}

// ---------- test_01/02：两段式排序与 24h 置顶 ----------
{
  // 三条未来（+8 天 / +2 天 / +3 小时）+ 两条已结束（-1 天 / -5 天）
  const data = [
    past(1, '已结束近'), past(5, '已结束远'),
    future(8 * DAY, 1, '远期'), future(2 * DAY, 2, '中期'), future(3 * 3600000, 3, '24h内'),
  ];
  const cm = makeCtx(data);
  const order = cm.filtered.map(l => l.title);
  ok(order[0] === '24h内', `test_01 24h 内应置顶，实际首位=${order[0]}`);
  ok(order[1] === '中期', `test_01 未来段应按迫近度升序，实际=${order}`);
  ok(order[2] === '远期', `test_01 >7 天应排未来段末尾，实际=${order}`);
  ok(order[3] === '已结束近' && order[4] === '已结束远',
     `test_01 已结束段应维持日期倒序，实际=${order}`);
}

// ---------- test_03：pagedGroups 组间跟随顺序（防层间抵消） ----------
{
  // 全部未来条目都在 7 天内（不触发折叠），验证未来天组整体在已结束组之前、
  // 且未来组内部按迫近度（不是日期倒序）
  const data = [
    past(1, 'a'), past(5, 'b'),
    future(2 * DAY, 2, 'c'), future(1 * DAY, 1, 'd'), future(3 * DAY, 3, 'e'),
  ];
  const cm = makeCtx(data);
  // 每组的代表条目（单卡组取 recs[0]；本数据不成论坛卡）
  const seq = cm.pagedGroups.map(g => g.items[0].recs[0].title);
  ok(seq.length === 5, `test_03 应有 5 个天组，实际 ${seq.length}：${seq}`);
  // 旧行为（组间日期倒序）会给出 [e, c, d, a, b]；新行为必须跟随 filtered：
  // 未来升序在前（d +1d → c +2d → e +3d），已结束倒序在后（a -1d → b -5d）
  ok(seq.join(',') === 'd,c,e,a,b',
     `test_03 组间顺序应为「未来升序 + 已结束倒序」（旧倒序行为为 e,c,d,a,b），实际=${seq}`);
}

// ---------- test_04/05：foldedUnits 折叠行为 ----------
{
  const data = [
    past(1, 'a'),
    future(2 * DAY, 2, 'b'), future(8 * DAY, 3, 'c'), future(20 * DAY, 4, 'd'), future(30 * DAY, 5, 'e'),
  ];
  const cm = makeCtx(data);
  const units = cm.units;
  const folded = cm.foldedUnits;
  const fold = folded.filter(u => u.type === 'far-fold');
  ok(fold.length === 1, `test_04 应恰有 1 个 far-fold 容器，实际=${fold.length}`);
  ok(fold[0].key === CONSTS.FAR_FOLD_KEY, `test_04 fold key 应为 ${CONSTS.FAR_FOLD_KEY}`);
  ok(fold[0].recs.length === 3, `test_04 应折入 3 场（+8/+20/+30 天，≥FAR_FOLD_MIN=3），实际=${fold[0].recs.length}`);
  // 折叠守恒：foldedUnits 数 = units 数 - 被折数 + 1
  ok(folded.length === units.length - 3 + 1,
     `test_04 折叠后单元数应守恒，units=${units.length} folded=${folded.length}`);
  // 位置：fold 容器应紧跟在最后一个 ≤7 天未来单元之后（未来区末尾）
  const foldIdx = folded.findIndex(u => u.type === 'far-fold');
  const before = folded.slice(0, foldIdx);
  ok(before.every(u => {
    const s = u.type === 'forum' ? u.head.lectureStart : u.recs[0].lectureStart;
    return new Date(s.replace(' ', 'T')).getTime() <= Date.now() + 7 * DAY;
  }), 'test_04 fold 容器前的单元必须都 ≤7 天（容器落在未来区末尾）');
  // 无 >7 天 → 不产生容器
  const cm2 = makeCtx([past(1, 'a'), future(2 * DAY, 2, 'b')]);
  ok(cm2.foldedUnits.filter(u => u.type === 'far-fold').length === 0,
     'test_05 无 >7 天时不得产生 far-fold 容器');
  ok(cm2.foldedUnits.length === cm2.units.length, 'test_05 无折叠时 foldedUnits 应与 units 等长');
}

// ---------- test_04b：1-2 条远期不折叠（FAR_FOLD_MIN=3 边界，2026-10-13 用户调整） ----------
{
  // 1 条远期：直接渲染，无折叠容器；foldedUnits 与 units 等长（不减位）
  const cm1 = makeCtx([past(1, 'a'), future(2 * DAY, 2, 'b'), future(8 * DAY, 3, 'c')]);
  ok(cm1.foldedUnits.filter(u => u.type === 'far-fold').length === 0,
     'test_04b 仅 1 条远期不得折叠');
  ok(cm1.foldedUnits.length === cm1.units.length,
     'test_04b 1 条远期时 foldedUnits 应与 units 等长');
  // 未折叠的远期单元必须仍在渲染链里（不被隐藏）
  ok(cm1.pagedUnits.some(u => u.recs[0].title === 'c'),
     'test_04b 未折叠的远期单元必须仍在 pagedUnits 中');
  // 顺序与 filtered 完全一致（未来升序 b,c + 已结束 a 垫底）
  ok(cm1.foldedUnits.map(u => u.recs[0].title).join(',') === 'b,c,a',
     `test_04b 1 条远期不折时顺序应为 b,c,a，实际=${cm1.foldedUnits.map(u => u.recs[0].title).join(',')}`);
  // 2 条远期：仍不折叠（<3）
  const cm2 = makeCtx([past(1, 'a'), future(2 * DAY, 2, 'b'), future(8 * DAY, 3, 'c'), future(20 * DAY, 4, 'd')]);
  ok(cm2.foldedUnits.filter(u => u.type === 'far-fold').length === 0,
     'test_04b 2 条远期仍不得折叠');
  ok(cm2.foldedUnits.length === cm2.units.length,
     'test_04b 2 条远期时 foldedUnits 应与 units 等长');
  ok(cm2.foldedUnits.map(u => u.recs[0].title).join(',') === 'b,c,d,a',
     `test_04b 2 条远期不折时顺序应为 b,c,d,a，实际=${cm2.foldedUnits.map(u => u.recs[0].title).join(',')}`);
}

// ---------- test_06/07：fold 组渲染数据与分页稳定性 ----------
{
  const data = [
    past(1, 'a'),
    future(2 * DAY, 2, 'b'), future(8 * DAY, 3, 'c'), future(20 * DAY, 4, 'd'), future(30 * DAY, 5, 'e'),
  ];
  const cm = makeCtx(data);
  const foldGroup = cm.pagedGroups.find(g => g.key === CONSTS.FAR_FOLD_KEY);
  ok(!!foldGroup, 'test_06 pagedGroups 应含 far-fold 组');
  ok(foldGroup.count === 3, `test_06 fold 组 count 应为 3，实际=${foldGroup.count}`);
  ok(foldGroup.items.length === 0, 'test_06 折叠态 fold 组 items 应为空');
  // 展开态：items = 被折 units（原样，供模板 forum/single 分支复用）
  const cmOpen = makeCtx(data, { farFutureOpen: true });
  const foldGroupOpen = cmOpen.pagedGroups.find(g => g.key === CONSTS.FAR_FOLD_KEY);
  ok(foldGroupOpen.items.length === 3, 'test_06 展开态 fold 组 items 应为被折的 3 个单元');
  ok(foldGroupOpen.items.every(u => u.type === 'single'),
     'test_06 展开态 items 应保留原 unit 类型');
  // 分页：展开前后 foldedUnits 长度一致 → 页数一致
  ok(cm.totalPages === cmOpen.totalPages,
     `test_07 展开/收起不得改变页数（${cm.totalPages} vs ${cmOpen.totalPages}）`);
  // isFarFoldGroup 实例方法（模板判定走它，不直接引用常量）
  ok(methods.isFarFoldGroup.call(cm, { key: CONSTS.FAR_FOLD_KEY }) === true,
     'test_06 isFarFoldGroup 对 fold 组应为 true');
  ok(methods.isFarFoldGroup.call(cm, { key: '2026-10-13 周二' }) === false,
     'test_06 isFarFoldGroup 对日期组应为 false');
}

// ---------- test_08：localStorage 持久化 ----------
{
  const cm = makeCtx([past(1, 'a')]);
  ok(cm.farFutureOpen === false, 'test_08 初值应为折叠');
  methods.loadFarFuture.call(cm);
  ok(cm.farFutureOpen === false, 'test_08 无存储时应为折叠');
  methods.toggleFarFuture.call(cm);
  ok(cm.farFutureOpen === true, 'test_08 toggle 后应展开');
  ok(store['lecture_far_future_open_v1'] === '1', 'test_08 展开态应写入 localStorage');
  methods.toggleFarFuture.call(cm);
  ok(store['lecture_far_future_open_v1'] === '0', 'test_08 收起态应写入 localStorage');
  const cm2 = makeCtx([past(1, 'a')]);
  store['lecture_far_future_open_v1'] = '1';
  methods.loadFarFuture.call(cm2);
  ok(cm2.farFutureOpen === true, 'test_08 loadFarFuture 应从 localStorage 恢复展开态');
}

// ---------- test_09：活段同日按时刻升序（用户 12:37 截图反馈） ----------
{
  // 10-14 15:00（title 带「第十九届…第3期」）与 10-14 10:30 同日。
  // 旧口径同日掉「系列编号倒序」分支 → 返回 0 塌成输入顺序（15:00 在前，错）。
  const data = [
    at('2026-10-14 15:00:00', 1, '【培养】第十九届"勷勤论坛"活动预告（第3期）'),
    at('2026-10-14 10:30:00', 2, '勷勤数学·专家报告-王超'),
  ];
  const cm = makeCtx(data);
  const order = cm.filtered.map(l => l.lectureStart);
  ok(order[0] === '2026-10-14 10:30:00' && order[1] === '2026-10-14 15:00:00',
     `test_09 活段同日应按时刻升序（10:30 在前），实际=${order}`);
  // 组内顺序同样升序
  const g = cm.pagedGroups.find(g => g.items.length);
  ok(g.items[0].recs[0].lectureStart === '2026-10-14 10:30:00',
     'test_09 组内顺序也应按时刻升序');
}

// ---------- test_10：statusOf 半日边界（用户 12:35 规则） ----------
{
  const st = l => methods.statusOf.call(makeCtx([l]), l);
  const atFIXED = ms => { FIXED_MS = ms; };
  const restore = new Date(2026, 9, 13, 12, 30, 0).getTime();
  try {
    // ① 只有开始时间：+2h 越过 12:00 → 12:00 结束（用户截图场景：10:30 场 12:00 散场）
    ok(st(at('2026-10-13 10:30:00', 1)) === 'ended', 'test_10 10:30 无结束时间应按 12:00 边界判 ended');
    ok(st(at('2026-10-13 11:30:00', 2)) === 'ended', 'test_10 11:30 无结束时间应按 12:00 边界判 ended');
    // ② +2h 不越界（12:00 起）→ 正常 2 小时；12:30 正在进行中
    ok(st(at('2026-10-13 12:00:00', 3)) === 'ongoing', 'test_10 12:00 起默认 2 小时，12:30 应 ongoing');
    ok(st(at('2026-10-13 14:00:00', 4)) === 'upcoming', 'test_10 14:00 起应 upcoming');
    // ③ 下午边界：临时把 now 移到 18:30，16:30 场 +2h=18:30 越过 18:00 → 18:00 结束
    atFIXED(new Date(2026, 9, 13, 18, 30, 0).getTime());
    ok(st(at('2026-10-13 16:30:00', 5)) === 'ended', 'test_10 16:30 无结束时间应按 18:00 边界判 ended');
    // ④ 晚间边界：now 22:30，21:00 场 +2h=23:00 越过 22:00 → 22:00 结束
    atFIXED(new Date(2026, 9, 13, 22, 30, 0).getTime());
    ok(st(at('2026-10-13 21:00:00', 6)) === 'ended', 'test_10 21:00 无结束时间应按 22:00 边界判 ended');
    // ⑤ 夜场 23:00 不适用任何边界（start 晚于全部边界）→ 仍按 +2h
    atFIXED(new Date(2026, 9, 13, 23, 30, 0).getTime());
    ok(st(at('2026-10-13 23:00:00', 7)) === 'ongoing', 'test_10 23:00 夜场不截断，+2h 内 ongoing');
    // ⑥ 写明结束时间：按实际 end，不截断（16:30-19:30，now 19:00 仍 ongoing）
    atFIXED(new Date(2026, 9, 13, 19, 0, 0).getTime());
    ok(st(at('2026-10-13 16:30:00', 8, '', '2026-10-13 19:30:00')) === 'ongoing',
       'test_10 写明 end 应按实际结束时间（19:30），不触发 18:00 截断');
    atFIXED(new Date(2026, 9, 13, 20, 0, 0).getTime());
    ok(st(at('2026-10-13 16:30:00', 9, '', '2026-10-13 19:30:00')) === 'ended',
       'test_10 写明 end 后 now>end 判 ended');
  } finally {
    FIXED_MS = restore;
  }
}

// ---------- test_11：同日混合拆组（用户 12:33 截图反馈） ----------
{
  // 10-13 当天：06:00 已结束（+2h=08:00）+ 18:00 待开始；外加一个纯已结束日 10-12。
  const data = [
    at('2026-10-13 06:00:00', 1, '今天上午已散场'),
    at('2026-10-13 18:00:00', 2, '今天晚上'),
    at('2026-10-12 09:00:00', 3, '昨天'),
  ];
  const cm = makeCtx(data);
  const labels = cm.pagedGroups.map(g => g.label);
  // 期望：10-13 活组 → 10-13 死组（拆出、带标注）→ 10-12 纯已结束组（无标注）
  ok(labels.length === 3, `test_11 应有 3 组，实际=${labels}`);
  ok(labels[0].startsWith('2026-10-13') && !labels[0].includes('已结束'),
     `test_11 首组应是 10-13 活组（无标注），实际=${labels[0]}`);
  ok(labels[1] === '2026-10-13 周二 · 已结束' || /2026-10-13.*已结束/.test(labels[1]),
     `test_11 第二组应是 10-13 死组且带「· 已结束」标注，实际=${labels[1]}`);
  ok(labels[2].startsWith('2026-10-12') && !labels[2].includes('已结束'),
     `test_11 纯已结束日不得加标注，实际=${labels[2]}`);
  // 死组里装的是 06:00 那条，活组里装 18:00 那条
  const dead = cm.pagedGroups[1].items[0].recs[0];
  const live = cm.pagedGroups[0].items[0].recs[0];
  ok(dead.lectureStart === '2026-10-13 06:00:00', 'test_11 死组应含 06:00 场');
  ok(live.lectureStart === '2026-10-13 18:00:00', 'test_11 活组应含 18:00 场');
}

// ---------- test_12：进行中归活段（statusOf 联动的排序一致性） ----------
{
  // now=15:00：A=10:00-16:00（写明 end）正在进行；B=12:00-13:00 已结束。
  // 旧口径（裸 start>=now 判段）会把 A 也算已结束 → 全死段倒序 [B, A]；
  // 新口径 A 归活段 → [A, B]。⚠ A 的 start 必须早于 B，否则旧口径靠倒序巧合同序。
  const save = FIXED_MS;
  FIXED_MS = new Date(2026, 9, 13, 15, 0, 0).getTime();
  try {
    const data = [
      at('2026-10-13 10:00:00', 1, '正在进行', '2026-10-13 16:00:00'),
      at('2026-10-13 12:00:00', 2, '午后已结束', '2026-10-13 13:00:00'),
    ];
    const cm = makeCtx(data);
    const order = cm.filtered.map(l => l.lectureStart);
    ok(order[0] === '2026-10-13 10:00:00',
       `test_12 进行中的场应排活段最前（旧口径会给 [12:00, 10:00]），实际=${order}`);
    ok(order[1] === '2026-10-13 12:00:00',
       `test_12 已结束场应在后，实际=${order}`);
  } finally {
    FIXED_MS = save;
  }
}

// ---------- test_13：当天组内时刻待定排最后（用户 13:25 截图反馈） ----------
{
  // 活段：10-14 00:00（时刻待定）+ 10-14 09:00 + 10-14 15:00 同日。
  // 旧口径活段同日按 lectureStart 升序 → 00:00 占位天然排最前，把真实时刻
  // 挤后（截图实证：研究生院「时间待定」排在物理学院 09:00 前面）。
  const data = [
    at('2026-10-14 00:00:00', 1, '研究生院·时间待定'),
    at('2026-10-14 15:00:00', 2, '下午场'),
    at('2026-10-14 09:00:00', 3, '上午场'),
  ];
  const cm = makeCtx(data);
  const order = cm.filtered.map(l => l.lectureStart);
  ok(order[0] === '2026-10-14 09:00:00' && order[1] === '2026-10-14 15:00:00'
     && order[2] === '2026-10-14 00:00:00',
     `test_13a 活段同日 tbd 应排最后，实际=${order}`);
}
{
  // 已结束段：now 推到 10-15，10-14 三条全 ended。tbd 置后须显式生效——
  // 00:00 占位在倒序里恰好落末尾是巧合；08:00 占位会排到 07:00 真实场之前，
  // 专门放一条 08:00 占位防「靠巧合通过」。
  const save = FIXED_MS;
  FIXED_MS = new Date(2026, 9, 15, 12, 0, 0).getTime();
  try {
    const data = [
      at('2026-10-14 00:00:00', 1, '昨日·待定'),
      at('2026-10-14 08:00:00', 2, '昨日·待定2'),
      at('2026-10-14 07:00:00', 3, '昨日·真实场'),
    ];
    const cm = makeCtx(data);
    const order = cm.filtered.map(l => l.lectureStart);
    ok(order[0] === '2026-10-14 07:00:00',
       `test_13b 已结束段真实场应最前，实际=${order}`);
    ok(order[2] === '2026-10-14 00:00:00',
       `test_13b 已结束段 tbd 应置后（00:00 在 08:00 后），实际=${order}`);
  } finally {
    FIXED_MS = save;
  }
}

// ---------- 汇总 ----------
if (errs.length) {
  console.log(`[FAIL] ${errs.length} 项`);
  errs.forEach(e => console.log(' - ' + e));
  process.exit(1);
}
console.log('[PASS] 两段式排序/24h 置顶/组间顺序/远期折叠/分页稳定/localStorage 持久化/tbd 置后全部通过');
