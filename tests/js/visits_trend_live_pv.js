// 门禁：趋势页 KPI 实时校正链路（2026-11-04）。
//
// 背景：台账快照每日只抓一次，首页/统计页页脚是实时值，趋势页 KPI 曾挂
// 旧快照值一整天，三页数字不一致（用户实测 2027 vs 2019）。修复后
// visits-trend.js 经 LectureCloud.fetchSitePv 只读云端 __site__ 行，把实时
// 权威值同步进三处：KPI 卡、图表今日点位、明细表最后一行。
//
// 本门禁锁三件事（相对现有 visits_trend_render.js 多锁的语义）：
//   ① 实时值 > 快照值 → KPI 值/副文案、SNAPS 末条、明细表末行同步更新；
//   ② 实时值 ≤ 快照值 / null → 静默保留快照值（不得回退、不得写 0）；
//   ③ 无 LectureCloud 环境（本机降级/沙箱）→ 不抛错，页面照常渲染。
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

// 起一个沙箱跑完脚本、排空微任务。livePv 为 fetchSitePv 桩的返回值；
// hasCloud=false 时不注入 LectureCloud（场景 ③）；delayData=true 时数据
// 文件 fetch 延迟一轮（模拟场景 ④：实时值先到）。
async function runScenario(name, { livePv, hasCloud, delayData }) {
  const byId = {};
  ['chart', 'tbody', 'tcount', 'toggleRows', 'load-err',
   'kpi_pv_value', 'kpi_pv_delta'].forEach(id => { byId[id] = mkNode('div'); });
  // 模板静态渲染：KPI 卡的快照值/副文案由 gen_visits_trend.py 直接写进 HTML，
  // 沙箱里预置同款文本，否则「保留快照值」类断言对着空元素无意义。
  byId['kpi_pv_value']._text = snapPv.toLocaleString('en-US');
  byId['kpi_pv_delta']._text = '较前次 +1';
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
  if (hasCloud) {
    sandbox.LectureCloud = {
      fetchSitePv: () => Promise.resolve(livePv),
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
  const a = await runScenario('实时大于快照', { livePv: live, hasCloud: true });
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

  // ---------- ② 实时值 ≤ 快照值：保留快照值，不回退 ----------
  const fmtSnap = snapPv.toLocaleString('en-US');
  for (const [label, v] of [['小于快照', snapPv - 1], ['等于快照', snapPv], ['null', null]]) {
    const b = await runScenario(label, { livePv: v, hasCloud: true });
    if (b['kpi_pv_value']._text !== fmtSnap) {
      errs.push(`[${label}] KPI 应保留快照值 ${fmtSnap}，实得 "${b['kpi_pv_value']._text}"`);
    }
    const trB = b['tbody'].children;
    const pvCell = trB.length ? (trB[0].children[1] || {})._text : null;
    if (pvCell !== fmtSnap) {
      errs.push(`[${label}] 明细表首行（最新）应保留快照值 ${fmtSnap}，实得 "${pvCell}"`);
    }
  }

  // ---------- ③ 无 LectureCloud：不抛错，快照值照常渲染 ----------
  const c = await runScenario('无云端层', { hasCloud: false });
  const svgC = c['chart'].children.filter(x => x.tagName === 'svg').length;
  if (svgC !== 1) errs.push(`无 LectureCloud 时渲染应不受影响，svg=${svgC}`);
  if (c['load-err'].hidden === false && c['load-err']._text) {
    errs.push('无 LectureCloud 时走进了错误分支：' + c['load-err']._text);
  }
  if (c['kpi_pv_value']._text !== fmtSnap) {
    errs.push(`[无云端层] KPI 应保留快照值 ${fmtSnap}，实得 "${c['kpi_pv_value']._text}"`);
  }

  // ---------- ④ 竞态：实时值先到、数据文件后到 ----------
  // 2026-11-04 实测坑：fetchSitePv 先 resolve 时 SNAPS 还是空数组，早期实现
  // 直接 return，校正静默跳过（KPI 停在旧值，零报错）。现实现先缓存 _livePv、
  // 数据到达后补挂——本场景锁住该收口，防回潮。
  const d = await runScenario('实时先到', { livePv: live, hasCloud: true, delayData: true });
  if (d['kpi_pv_value']._text !== fmtLive) {
    errs.push(`[实时先到] KPI 应为 ${fmtLive}，实得 "${d['kpi_pv_value']._text}"`
              + '——竞态收口失效，校正被静默跳过');
  }
  const svgD = d['chart'].children.filter(x => x.tagName === 'svg').length;
  if (svgD !== 1) errs.push(`[实时先到] 图表应渲染恰好 1 个 svg，实得 ${svgD}`);

  console.log(`snapPv=${snapPv} livePv=${live} · 场景①②③④`
    + (errs.length ? '' : ' 全部通过'));
  if (errs.length) {
    console.log('[FAIL]');
    errs.forEach(e => console.log(' -', e));
    process.exit(1);
  }
  console.log('[PASS] 趋势页实时校正：KPI/今日点位/明细末行同步，降级静默保留快照');
})();
