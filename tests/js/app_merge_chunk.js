// _mergeChunk 覆盖语义门禁（2026-10-02）：执行**真实函数体**。
//
// 为什么必须真实执行
// ------------------
// 首页总数正确性的地基就在这里：首屏 latest.json 的 50 条是全 3810 条的**子集**，
// 第 1 片包含这 50 条。_mergeChunk 必须按key**覆盖**而非追加，否则全站会显示
// 3860 条（真实 3810 + 重复 50）。这个「3810 而非 3860」的语义此前只由
// tests/js/app_count_animation.js:63-109 用**简化版 merge**断言——
// 即测试自己重写了一遍合并逻辑，生产代码改了它不会红；而 app_longtext_flow.js
// 更是把 _mergeChunk 直接桩掉了。真正的函数体**零覆盖**。
//
// 本门禁直接调用 site/app.data.js 里的真实 _mergeChunk，覆盖三类语义：
//   ① 重叠条目**覆盖**不追加（核心）
//   ② 非重叠条目正常追加
//   ③ 长文本被抽离到旁路 _longText、条目本体不再携带（分片剥离的前提）
//
// 运行：node tests/js/app_merge_chunk.js（纯 node + vm，无浏览器无网络）
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

const errs = [];
const ok = (cond, msg) => { if (!cond) errs.push(msg); };

// 造一个够用的实例：_mergeChunk 会调用 _absorbLongText，后者再调 _ltKey，
// 所以三个都要给**真实函数体**（给桩就等于绕开了被测链路）。
function newState() {
  return {
    all: [],
    _longText: {},
    _ltFull: {},
    _ltPending: {},
    _ltLoaded: {},
    _absorbLongText: M._absorbLongText,
    _mergeChunk: M._mergeChunk,
    _ltKey: M._ltKey,
  };
}

const rec = (url, start, title, idx, extra) => Object.assign({
  sourceUrl: url, lectureStart: start, title, lectureIndex: idx,
}, extra || {});

// ---------- ① 核心：重叠条目覆盖而非追加 ----------
{
  const st = newState();
  // 首屏 3 条「预览版」（abstract 被截断），id 用 title 区分
  st.all = [
    rec('u1', '2026-01-01 10:00', 'T1', 1, { abstract: '预览A' }),
    rec('u2', '2026-01-01 11:00', 'T2', 1, { abstract: '预览B' }),
    rec('u3', '2026-01-01 12:00', 'T3', 1, { abstract: '预览C' }),
  ];
  const before = st.all.length;
  // 分片里包含同样 3 条（完整版）+ 2 条新的
  st._mergeChunk([
    rec('u1', '2026-01-01 10:00', 'T1', 1, { abstract: '完整A' }),
    rec('u2', '2026-01-01 11:00', 'T2', 1, { abstract: '完整B' }),
    rec('u3', '2026-01-01 12:00', 'T3', 1, { abstract: '完整C' }),
    rec('u4', '2026-01-01 13:00', 'T4', 1, { abstract: '新D' }),
    rec('u5', '2026-01-01 14:00', 'T5', 1, { abstract: '新E' }),
  ]);
  ok(st.all.length === 5,
     `重叠 3 条 + 新增 2 条应得 5 条，实得 ${st.all.length}`
     + `（若为 ${before + 5} 说明退化成了追加，首页会多显示 50 条）`);
  ok(st.all.length < before + 5, '重叠条目被追加而非覆盖');
  // 覆盖后应取分片里的值（更完整）
  const u1 = st.all.find(r => r.sourceUrl === 'u1');
  ok(u1 && u1.abstract === undefined,
     'abstract 应被抽离到 _longText 旁路，不留在条目本体上');
}

// ---------- ② 真实规模核对：50 条首屏 + 含这 50 条的第 1 片 ----------
{
  const st = newState();
  const mkN = (n, tag) => {
    const out = [];
    for (let i = 0; i < n; i++) {
      out.push(rec(`s${i}`, `2026-01-01 ${String(i % 24).padStart(2, '0')}:00`,
        `T${i}`, 1, { abstract: tag }));
    }
    return out;
  };
  st.all = mkN(50, 'preview');
  st._mergeChunk(mkN(500, 'full'));     // 第1 片 500 条，含首屏那 50 条
  ok(st.all.length === 500,
     `50 条首屏 + 500 条分片（第1片含首屏）应得 500 条，实得 ${st.all.length}`
     + '（追加而非覆盖会得 550，正是「3860 vs 3810」那类偏差的来源）');
}

// ---------- ③ 长文本抽离 ----------
{
  const st = newState();
  st._mergeChunk([
    rec('x1', '2026-02-01 10:00', 'X1', 1,
      { speakerBio: '简介内容', abstract: '摘要内容' }),
  ]);
  ok(st.all.length === 1, '单条应正常追加');
  ok(st.all[0].speakerBio === undefined, 'speakerBio 应已抽离（条目本体不再携带）');
  ok(st.all[0].abstract === undefined, 'abstract 应已抽离');
  const lt = st._longText['x1#1'];
  ok(lt && lt.abstract === '摘要内容' && lt.speakerBio === '简介内容',
     `长文本应落到 _longText['x1#1']，实得 ${JSON.stringify(lt)}`);
}

// ---------- ④ 键的四个组成部分都参与（防止有人「简化」键） ----------
{
  const st = newState();
  st.all = [rec('u', '2026-01-01 10:00', 'TA', 1)];
  // 同一 url 但 lectureStart 不同 → 应视为不同条目
  st._mergeChunk([rec('u', '2026-01-02 10:00', 'TA', 1)]);
  ok(st.all.length === 2, 'lectureStart 不同应视为两条（键含 lectureStart）');

  const st2 = newState();
  st2.all = [rec('u', '2026-01-01 10:00', 'TA', 1)];
  // 同一 url/start 但 title 不同 → 视为不同条目
  st2._mergeChunk([rec('u', '2026-01-01 10:00', 'TB', 1)]);
  ok(st2.all.length === 2, 'title 不同应视为两条（键含 title）');

  const st3 = newState();
  st3.all = [rec('u', '2026-01-01 10:00', 'TA', 1)];
  // 同一 url/start/title 但 lectureIndex 不同（多场拆分）→ 视为不同条目
  st3._mergeChunk([rec('u', '2026-01-01 10:00', 'TA', 2)]);
  ok(st3.all.length === 2, 'lectureIndex 不同应视为两条（键含 lectureIndex）');

  const st4 = newState();
  st4.all = [rec('u', '2026-01-01 10:00', 'TA', 0)];
  // lectureIndex=0 与缺失（''）不同 → 不能被 || 吞掉
  st4._mergeChunk([rec('u', '2026-01-01 10:00', 'TA')]);
  ok(st4.all.length === 2, 'lectureIndex=0 与缺失应视为两条（键不得用 || 吞 0）');
}

if (errs.length) {
  console.log('[FAIL]');
  errs.forEach(e => console.log(' -', e));
  process.exit(1);
}
console.log('[PASS] _mergeChunk：重叠覆盖不追加、真实规模 500 条正确、长文本抽离、'
  + '键四要素与 lectureIndex=0 均正确');