// 门禁：趋势页 KPI 实时校正链路（2026-11-04）。
//
// 背景：台账快照每日只抓一次，首页/统计页页脚是实时值，趋势页 KPI 曾挂
// 旧快照值一整天，三页数字不一致（用户实测 2027 vs 2019）。
// 2026-11-05 同源改造：页脚与 KPI 共用 footer-counter.js 的
// window.LectureSitePv.get()（全页记忆化的同一次取值），不再各自 fetch，
// 权威值同步进 KPI 首卡 + 近 7 条/日均两张静态卡、图表今日点位、明细最新行。
//
// 本门禁锁四件事（相对现有 visits_trend_render.js 多锁的语义）：
//   ① 共享值 > 快照值 → KPI 值/副文案/近 7 条/日均、SNAPS 末条、明细最新行同步；
//   ② 副文案增量口径 = 共享值 − 今日凌晨快照（不是 − 上一条快照，评审 #2）；
//   ③ 共享值 ≤ 快照值 / null → 静默保留快照值（不得回退、不得写 0）；
//   ④ 无 LectureSitePv 环境（本机降级/沙箱）→ 不抛错，页面照常渲染。
// 这三类都是零报错的「静默错」，浏览器里看不出异常但数字错，必须锁。
//
// 运行：node tests/js/visits_trend_live_pv.js（纯 node + vm，无浏览器无网络）
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SITE = path.join(__dirname, '..', '..', 'site');
const js = fs.readFileSync(path.join(SITE, 'visits-trend.js'), 'utf-8');
const data = JSON.parse(
  fs.readFileSync(path.join(SITE, 'visits-trend-data.json'), 'utf-8'));
const errs = [];

const snapPv = Number(data.snaps[data.snaps.length - 1].site_pv) || 0;
if (!snapPv) {
  console.log('[FAIL] 快照数据异常：末条 site_pv 为 0');
  process.exit(1);
}

function mkNode(tag) {
  return {
    tagName: tag, children: [], attrs: {}, _text: '', className: '',
    style: {}, hidden: false,
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {},
    get firstChild() { return this.children[0] || null; },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this.children = []; },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { this.children = this.children.filter(x => x !== c); },
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    appendChildNS(ns, t) { const n = mkNode(t); this.children.push(n); return n; },
    querySelectorAll() { return []; },
  };
}

// 起一个沙箱跑完脚本、排空微任务。livePv 为共享 Promise 桩的返回值；
// hasShared=false 时不注入 window.LectureSitePv（场景 ④）；delayData=true 时
// 数据文件 fetch 延迟一轮（模拟场景 ⑤：共享值先到）。
async function runScenario(name, { livePv, hasShared, delayData }) {
  const byId = {};
  ['chart', 'tbody', 'tcount', 'toggleRows', 'load-err',
   'kpi_pv_value', 'kpi_pv_delta',
   'kpi_recent7_value', 'kpi_perday_value'].forEach(id => { byId[id] = mkNode('div'); });
  // 模板静态渲染：KPI 卡由 gen_visits_trend.py 直接写进 HTML，沙箱里预置
  // 同款文本，否则「保留快照值」类断言对着空元素无意义。后两张卡预置一个
  // 不可能的哨兵值，用来证明 recomputeKpis 确实改写了它们（评审 #2 联动）。
  byId['kpi_pv_value']._text = snapPv.toLocaleString('en-US');
  byId['kpi_pv_delta']._text = '较前次 +1';
  byId['kpi_recent7_value']._text = '__STATIC__';
  byId['kpi_perday_value']._text = '__STATIC__';
  // ⚠ data 必须每场景深拷贝：applyLivePv 会写 SNAPS 末条（last.site_pv = n），
  // 共享引用会让场景①的写入污染后续场景的「快照基线」（实测踩过：
  // 场景②③④全拿到已被改写的 2,027，断言全假红）。
  const dataClone = JSON.parse(JSON.stringify(data));
  const sandbox = {
    console,
    document: {
      getElementById: id => byId[id] || null,
      createElement: mkNode,
      createElementNS: (ns, t) => mkNode(t),
      createDocumentFragment: () => mkNode('#fragment'),
      createTextNode: t => ({ nodeType: 3, textContent: String(t) }),
      querySelectorAll: () => [],
      addEventListener() {},
      documentElement: { style: {} },
    },
    window: { addEventListener() {} },
    requestAnimationFrame: () => 0,
    fetch: () => Promise.resolve({
      ok: true, status: 200,
      json: () => (delayData
        ? new Promise(res => setTimeout(() => res(dataClone), 0))
        : Promise.resolve(dataClone)),
    }),
    Date, Math, Number, String, JSON, Array, Object, parseInt, parseFloat,
    isNaN, Set, Map, Infinity, NaN, Promise,
  };
  if (hasShared) {
    sandbox.window.LectureSitePv = {
      get: () => Promise.resolve(livePv),
    };
  }
  vm.createContext(sandbox);
  vm.runInContext(js, sandbox, { filename: 'visits-trend.js' });
  for (let i = 0; i < (delayData ? 30 : 6); i++) await Promise.resolve();
  await new Promise(res => setTimeout(res, 0));
  for (let i = 0; i < 10; i++) await Promise.resolve();
  return byId;
}

(async () => {
  // ---------- ① 实时值 > 快照值：三处同步更新 ----------
  const live = snapPv + 8;
  const a = await runScenario('实时大于快照', { livePv: live, hasShared: true });
  const fmtLive = live.toLocaleString('en-US');
  if (a['kpi_pv_value']._text !== fmtLive) {
    errs.push(`KPI 值应为 ${fmtLive}，实得 "${a['kpi_pv_value']._text}"`);
  }
  if (!/实时/.test(a['kpi_pv_delta']._text)) {
    errs.push(`KPI 副文案应标注「实时」，实得 "${a['kpi_pv_delta']._text}"`);
  }
  const trA = a['tbody'].children;
  if (!trA.length) {
    errs.push('明细表无行');
  } else {
    // 明细表按新→旧排列（renderTable 里 rows.reverse()），最新快照在第一行。
    const firstRow = trA[0];
    const pvCell = firstRow.children[1] && firstRow.children[1]._text;
    if (pvCell !== fmtLive) {
      errs.push(`明细表首行（最新）累计值应为 ${fmtLive}，实得 "${pvCell}"`
                + '——KPI 与明细不一致是用户可直接看出的矛盾');
    }
  }
  const svgA = a['chart'].children.filter(c => c.tagName === 'svg').length;
  if (svgA !== 1) errs.push(`校正后图表应重渲染恰好 1 个 svg，实得 ${svgA}`);

  // ---------- ② 增量口径 + 两张静态 KPI 联动（评审 #2） ----------
  if (a['kpi_pv_delta']._text !== `较今日快照 +${live - snapPv} · 实时`) {
    errs.push(`KPI 副文案应为「较今日快照 +${live - snapPv} · 实时」，`
              + `实得 "${a['kpi_pv_delta']._text}"——旧口径 n−上一条快照会让 +N 对不上累计值`);
  }
  const prevPv = Number(data.snaps[data.snaps.length - 2].site_pv) || 0;
  const win = data.snaps.slice(-8);
  let expRecent7 = 0;
  for (const s2 of win.slice(1)) expRecent7 += Math.max(0, Number(s2.pv_delta) || 0);
  // 今日那条在 applyLivePv 里被改成 live−prevPv，故用原始值换算出期望
  expRecent7 += (live - prevPv) - Math.max(0, Number(win[win.length - 1].pv_delta) || 0);
  if (a['kpi_recent7_value']._text !== expRecent7.toLocaleString('en-US')) {
    errs.push(`近 7 条快照新增应为 ${expRecent7}，实得 "${a['kpi_recent7_value']._text}"`
              + '——静态卡未联动，同屏数字自相矛盾');
  }
  const firstPv = Number(data.snaps[0].site_pv) || 0;
  const spanDays = (Date.parse(data.snaps[data.snaps.length - 1].date + 'T00:00:00Z')
                    - Date.parse(data.snaps[0].date + 'T00:00:00Z')) / 86400000;
  const expPerDay = Math.round(Math.max(live - firstPv, 0) / spanDays);
  if (a['kpi_perday_value']._text !== expPerDay.toLocaleString('en-US')) {
    errs.push(`日均新增应为 ${expPerDay}，实得 "${a['kpi_perday_value']._text}"`
              + '——静态卡未联动');
  }

  // ---------- ② 实时值 ≤ 快照值：保留快照值，不回退 ----------
  const fmtSnap = snapPv.toLocaleString('en-US');
  for (const [label, v] of [['小于快照', snapPv - 1], ['等于快照', snapPv], ['null', null]]) {
    const b = await runScenario(label, { livePv: v, hasShared: true });
    if (b['kpi_pv_value']._text !== fmtSnap) {
      errs.push(`[${label}] KPI 应保留快照值 ${fmtSnap}，实得 "${b['kpi_pv_value']._text}"`);
    }
    const trB = b['tbody'].children;
    const pvCell = trB.length ? (trB[0].children[1] || {})._text : null;
    if (pvCell !== fmtSnap) {
      errs.push(`[${label}] 明细表首行（最新）应保留快照值 ${fmtSnap}，实得 "${pvCell}"`);
    }
  }

  // ---------- ④ 无 LectureSitePv：不抛错，快照值照常渲染 ----------
  const c = await runScenario('无共享层', { hasShared: false });
  const svgC = c['chart'].children.filter(x => x.tagName === 'svg').length;
  if (svgC !== 1) errs.push(`无 LectureSitePv 时渲染应不受影响，svg=${svgC}`);
  if (c['load-err'].hidden === false && c['load-err']._text) {
    errs.push('无 LectureSitePv 时走进了错误分支：' + c['load-err']._text);
  }
  if (c['kpi_pv_value']._text !== fmtSnap) {
    errs.push(`[无云端层] KPI 应保留快照值 ${fmtSnap}，实得 "${c['kpi_pv_value']._text}"`);
  }

  // ---------- ⑤ 竞态：共享值先到、数据文件后到 ----------
  // 2026-11-04 实测坑：取值先 resolve 时 SNAPS 还是空数组，早期实现
  // 直接 return，校正静默跳过（KPI 停在旧值，零报错）。现实现先缓存 _livePv、
  // 数据到达后补挂——本场景锁住该收口，防回潮。
  const d = await runScenario('实时先到', { livePv: live, hasShared: true, delayData: true });
  if (d['kpi_pv_value']._text !== fmtLive) {
    errs.push(`[实时先到] KPI 应为 ${fmtLive}，实得 "${d['kpi_pv_value']._text}"`
              + '——竞态收口失效，校正被静默跳过');
  }
  const svgD = d['chart'].children.filter(x => x.tagName === 'svg').length;
  if (svgD !== 1) errs.push(`[实时先到] 图表应渲染恰好 1 个 svg，实得 ${svgD}`);

  console.log(`snapPv=${snapPv} livePv=${live} · 场景①②③④⑤`
    + (errs.length ? '' : ' 全部通过'));
  if (errs.length) {
    console.log('[FAIL]');
    errs.forEach(e => console.log(' -', e));
    process.exit(1);
  }
  console.log('[PASS] 趋势页同源校正：KPI/静态卡/今日点位/明细最新行同步，降级静默保留快照');
})();
