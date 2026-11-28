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

// 造一个够用的实例：_mergeChunk 会调用 _absorbLongText，后者再调 _ltKey
// 与 _invalidateHay（2026-10-31 搜索缓存失效）；_loadChunks 收尾会调
// _warmHayCache（搜索缓存空闲预建）。这些都接**真实函数体**——
// 给桩就等于绕开了被测链路。all 为空时 _warmHayCache 直接返回，不会真跑预建。
function newState() {
  return {
    all: [],
    _longText: {},
    _ltFull: {},
    _ltPending: {},
    _ltLoaded: {},
    _ltManifest: null,
    _chunksManifest: null,
    _generation: '',
    _applyLectureData: M._applyLectureData,
    _absorbAll: M._absorbAll,
    _absorbLongText: M._absorbLongText,
    _absorbDetailRows: M._absorbDetailRows,
    _detailFileIndex: M._detailFileIndex,
    _invalidateHay: M._invalidateHay,
    _mergeChunk: M._mergeChunk,
    _ltKey: M._ltKey,
    _hayOf: M._hayOf,
    _warmHayCache: M._warmHayCache,
    _idle: M._idle,
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

// ---------- ⑤ 内容寻址分片/桶 URL 与 generation 混龄防护 ----------
// 事故：latest.json 是 no-store（恒新），分片却走 cache:'default' 命中 Pages 的
// max-age=600 旧副本；_mergeChunk 的覆盖设计让**旧分片把新 latest 记录覆盖回旧版**，
// 表现为「刚刷新有阿里云、加载完又没了」，且各设备因缓存龄不同表现不一。
// 修法：分片与长文本桶文件名按内容哈希，URL 仍保留 ?v=<清单 updatedAt>；
// latest/chunks/detail 的 generation 必须一致，旧清单不得覆盖新数据。
// 本节直接执行真实 _loadChunks / _loadFullSingle / _ltManifestJson / _loadDetailBucket。
(async () => {
  const VER = '2026-10-12T05:00:00+08:00';
  const GEN = 'a'.repeat(64);
  const OTHER_GEN = 'b'.repeat(64);
  const CHUNK_FILE = 'lectures/chunk_0001.' + '1'.repeat(24) + '.json';
  const DETAIL_FILE = 'lectures/detail/detail_00.' + '2'.repeat(24) + '.json';
  const DETAIL_MANIFEST = 'lectures/detail/manifest.' + '3'.repeat(24) + '.json';

  // —— _loadChunks：保存已接受清单，哈希文件名仍带 updatedAt 版本戳 ——
  const fetched = [];
  const chunkManifest = {
    updatedAt: VER, generation: GEN, total: 1, chunkSize: 100,
    chunks: [CHUNK_FILE], detailManifest: DETAIL_MANIFEST,
  };
  sandbox.fetch = async (url) => {
    fetched.push(String(url));
    if (String(url).includes('chunks.json')) {
      return { ok: true, json: async () => chunkManifest };
    }
    return { ok: true, json: async () => ({ data: [rec('c1', '2026-01-01 10:00', 'C1', 1)] }) };
  };
  const stChunks = Object.assign(newState(), {
    _generation: GEN, dataStage: 'loading', bumpCount: noop, _sleep: () => Promise.resolve(),
  });
  await M._loadChunks.call(stChunks);
  const chunkUrls = fetched.filter(u => u.includes('chunk_0001.'));
  ok(chunkUrls.length === 1, `哈希分片应被请求一次，实得 ${chunkUrls.length} 次：${JSON.stringify(fetched)}`);
  ok(chunkUrls[0].includes('?v=' + encodeURIComponent(VER)),
     `分片 URL 必须保留 ?v=<清单 updatedAt>，实得 ${chunkUrls[0]}`);
  ok(stChunks._chunksManifest === chunkManifest, '_loadChunks 必须保存已接受的 chunks 清单');
  ok(stChunks.all.length === 1 && stChunks.all[0].sourceUrl === 'c1', '同 generation 分片应正常合并');
  ok(stChunks.dataStage === 'full', '哈希分片应正常完成到 full');

  // —— latest 与 chunks generation 不同：不得请求/合并分片，改走整文件 ——
  const mismatchFetched = [];
  let fallbackCalls = 0;
  sandbox.fetch = async (url) => {
    mismatchFetched.push(String(url));
    return { ok: true, json: async () => ({ updatedAt: VER, generation: OTHER_GEN, chunks: [CHUNK_FILE] }) };
  };
  const stMismatch = Object.assign(newState(), {
    _generation: GEN, all: [rec('latest', '2026-01-01 09:00', 'Latest', 1)],
    dataStage: 'partial', bumpCount: noop, _sleep: () => Promise.resolve(),
    _loadFullSingle: () => { fallbackCalls++; return Promise.resolve(); },
  });
  await M._loadChunks.call(stMismatch);
  ok(fallbackCalls === 1, 'latest/chunks generation 不同必须回退 _loadFullSingle');
  ok(!mismatchFetched.some(u => u.includes('chunk_0001.')), 'generation 不同时不得请求任何分片');
  ok(stMismatch.all.length === 1 && stMismatch.all[0].sourceUrl === 'latest',
     'generation 不同时不得合并旧分片');

  // —— _applyLectureData 记录 generation；_loadFullSingle 也拒绝混龄响应 ——
  const stApply = newState();
  stApply._applyLectureData({ data: [], generation: GEN });
  ok(stApply._generation === GEN, '_applyLectureData 必须保存 resp.generation');
  const fullFetched = [];
  sandbox.fetch = async (url) => {
    fullFetched.push(String(url));
    return { ok: true, json: async () => ({
      generation: OTHER_GEN, data: [rec('old', '2026-01-01 08:00', 'Old', 1)],
    }) };
  };
  const stFull = Object.assign(newState(), {
    _generation: GEN, all: [rec('latest', '2026-01-01 09:00', 'Latest', 1)],
    dataStage: 'partial', loading: true,
  });
  await M._loadFullSingle.call(stFull);
  ok(fullFetched.length === 1, '整文件降级应仍请求 lectures.json');
  ok(stFull.all.length === 1 && stFull.all[0].sourceUrl === 'latest',
     '_loadFullSingle 不得用不同 generation 的整文件覆盖 latest');
  ok(stFull.dataStage === 'partial-error', '拒绝混龄整文件后应保留可重试错误态');

  // —— detail：优先 chunks 清单链接的哈希 manifest，桶名按哈希/旧名均可解析 ——
  const fetched2 = [];
  let manifestCacheOpts = null;
  sandbox.fetch = async (url, opts) => {
    fetched2.push(String(url));
    if (String(url).includes(DETAIL_MANIFEST)) {
      manifestCacheOpts = (opts && opts.cache) || '';
      return { ok: true, json: async () => ({
        updatedAt: VER, generation: GEN, files: [DETAIL_FILE],
      }) };
    }
    if (String(url).includes(DETAIL_FILE)) {
      return { ok: true, json: async () => ({ data: [
        { key: 'https://x/detail#', speakerBio: '新简介', abstract: '新摘要' },
      ] }) };
    }
    return { ok: false, status: 404 };
  };
  const stBucket = Object.assign(newState(), {
    _generation: GEN,
    _chunksManifest: { generation: GEN, detailManifest: DETAIL_MANIFEST },
    _ltManifest: null, _absorbDetailRows: M._absorbDetailRows,
    _ltManifestJson: M._ltManifestJson, _loadDetailBucket: M._loadDetailBucket,
  });
  await M._loadDetailBucket.call(stBucket, 0);
  const bucketUrls = fetched2.filter(u => u.includes('detail_00.'));
  ok(fetched2.some(u => u.includes(DETAIL_MANIFEST + '?t=')),
     `detail 必须优先请求 chunks 链接的 ${DETAIL_MANIFEST}，实得 ${JSON.stringify(fetched2)}`);
  ok(!fetched2.some(u => u.includes('lectures/detail/manifest.json?t=')),
     '有 detailManifest 链接时不应再请求固定 detail/manifest.json');
  ok(bucketUrls.length === 1 && bucketUrls[0].includes('?v=' + encodeURIComponent(VER)),
     `哈希长文本桶 URL 必须保留 ?v=<清单 updatedAt>，实得 ${JSON.stringify(fetched2)}`);
  ok(manifestCacheOpts === 'no-store',
     `detail 清单必须 no-store，实得 cache='${manifestCacheOpts}'`);
  ok(stBucket._longText['https://x/detail#'] &&
     stBucket._longText['https://x/detail#'].speakerBio === '新简介',
     '同 generation 的哈希 detail 桶应正常吸收');

  // —— detail generation 不同：拒绝缓存清单与桶数据，不能覆盖新长文本 ——
  const mismatchDetailFetched = [];
  sandbox.fetch = async (url) => {
    mismatchDetailFetched.push(String(url));
    if (String(url).includes('manifest')) {
      return { ok: true, json: async () => ({
        updatedAt: VER, generation: OTHER_GEN, files: [DETAIL_FILE],
      }) };
    }
    return { ok: true, json: async () => ({ data: [
      { key: 'https://x/detail#', speakerBio: '旧简介', abstract: '旧摘要' },
    ] }) };
  };
  const stDetailMismatch = Object.assign(newState(), {
    _generation: GEN,
    _chunksManifest: { generation: GEN, detailManifest: DETAIL_MANIFEST },
    _ltManifest: null,
    _longText: { 'https://x/detail#': { speakerBio: '新简介', abstract: '新摘要' } },
    _ltManifestJson: M._ltManifestJson, _loadDetailBucket: M._loadDetailBucket,
  });
  await M._loadDetailBucket.call(stDetailMismatch, 0);
  ok(!mismatchDetailFetched.some(u => u.includes(DETAIL_FILE)),
     'detail generation 不同时不得请求旧桶');
  ok(stDetailMismatch._ltManifest === null, '不同 generation 的 detail 清单不得进缓存');
  ok(stDetailMismatch._longText['https://x/detail#'].speakerBio === '新简介',
     '不同 generation 的旧长文本不得覆盖新值');

  // —— 旧 chunks 清单无 detailManifest：兼容回退固定清单与旧桶名 ——
  const legacyFetched = [];
  sandbox.fetch = async (url) => {
    legacyFetched.push(String(url));
    return { ok: true, json: async () => ({
      updatedAt: VER, generation: GEN, files: ['lectures/detail/detail_00.json'],
    }) };
  };
  const stLegacy = Object.assign(newState(), {
    _generation: GEN, _chunksManifest: { generation: GEN }, _ltManifest: null,
    _ltManifestJson: M._ltManifestJson, _loadDetailBucket: M._loadDetailBucket,
  });
  await M._loadDetailBucket.call(stLegacy, 0);
  ok(legacyFetched.some(u => u.includes('lectures/detail/manifest.json?t=')),
     '旧 chunks 清单必须回退固定 detail/manifest.json');
  ok(legacyFetched.some(u => u.includes('lectures/detail/detail_00.json?v=')),
     '旧式 detail_00.json 文件名必须继续可用');
  sandbox.fetch = () => Promise.resolve({ ok: false, json: () => ({}) });  // 还原
})().then(report).catch(e => { errs.push('⑤ 执行异常: ' + e); report(); });

function report() {
  if (errs.length) {
    console.log('[FAIL]');
    errs.forEach(e => console.log(' -', e));
    process.exit(1);
  }
  console.log('[PASS] _mergeChunk：重叠覆盖不追加、真实规模 500 条正确、长文本抽离、'
    + '键四要素与 lectureIndex=0 均正确、哈希分片/桶版本戳与 generation 混龄防护');
}