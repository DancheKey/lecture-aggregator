// 长文本（简介/摘要）加载流程门禁（2026-09-30 修订）
//
// 背景：bio+abstract 占数据 68%，2026-09-28 被剥离出主分片、改存 16 个 detail 桶。
// 剥离后曾出现两种体验问题：
//   ① 首屏 50 条之外，卡片上的「简介/摘要」整块不显示，直到搜索或逐条展开才补；
//   ② 搜索时先渲染未含长文本的部分结果，桶到达后条数跳变、摘要逐条冒出。
//
// 现行策略（2026-09-30 用户定）：
//   · 首屏一渲染即在后台静默预取全部长文本（不等任何交互）—— 见 app.data._loadStaticLatest；
//   · 搜索【永不】等待长文本：未就绪时直接用已到数据出结果，桶到达后 _longText 变更
//     触发响应式自动补全（先出结果、到齐静默补全）。原 searchPending 等待提示已删除。
//
// 本门禁用真实分片 + fetch 桩覆盖上述策略的全部关键分支，纯 node、无浏览器、无网络。
// 用法：node tests/js/app_longtext_flow.js
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');
const BUCKETS = 16;

let fetchLog = [];
let manifestOk = true;     // false → 模拟 detail 清单 404/500
let failBucket = null;     // 'NN' → 该桶第一次请求失败，用于验证「缺桶不阻塞 + 后续补拉」

const ctx = {
  console,
  setTimeout,
  fetch: async (url) => {
    fetchLog.push(url);
    if (String(url).includes('manifest.json')) {
      if (!manifestOk) return { ok: false, status: 500 };
      return {
        ok: true, status: 200,
        json: async () => ({
          files: Array.from({ length: BUCKETS }, (_, i) =>
            'lectures/detail/detail_' + String(i).padStart(2, '0') + '.json'),
        }),
      };
    }
    const m = String(url).match(/detail_(\d+)\.json/);
    const i = m ? m[1] : '00';
    if (m && failBucket === m[1]) {
      failBucket = null;   // 只失败一次，下一轮即可补齐
      return { ok: false, status: 503 };
    }
    return {
      ok: true, status: 200,
      json: async () => ({ data: [{ key: 'https://x/' + i + '#', speakerBio: 'bio-' + i, abstract: 'abs-' + i }] }),
    };
  },
};
ctx.window = ctx;
ctx.document = { addEventListener() {}, querySelectorAll() { return []; } };
ctx.Vue = { createApp: () => ({}) };
vm.createContext(ctx);

const PARTS = ['app.core.js', 'app.state.js', 'app.computed.js', 'app.data.js', 'app.social.js'];
for (const f of PARTS) {
  vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf8'), ctx, { filename: f });
}

const METHODS = vm.runInContext('APP_METHODS', ctx);
const COMPUTED = vm.runInContext('APP_COMPUTED', ctx);
const STATE = vm.runInContext('APP_STATE()', ctx);

// 造一个「组件实例」：方法来自分片容器，状态字段来自 APP_STATE()，可被用例覆写
function newFake(over) {
  const f = Object.assign(Object.create(null), METHODS, STATE, over || {});
  Object.setPrototypeOf(f, METHODS);
  return f;
}
const countDetailCalls = () => fetchLog.filter(u => u.includes('detail_') && !u.includes('manifest')).length;
const settle = (ms) => new Promise(r => setTimeout(r, ms || 30));

let fail = 0;
function check(label, got, want) {
  const ok = got === want;
  if (!ok) fail++;
  console.log((ok ? 'PASS' : 'FAIL') + ' | ' + label + ' | got=' + got + ' want=' + want);
}

(async () => {
  console.log('--- 1) 首屏加载完 latest 后自动后台预取（不等任何交互）---');
  const s0 = newFake({});
  s0._applyLectureData = () => {};   // 桩：不吸收首屏数据
  s0._loadStaticFull = () => {};     // 桩：不触发分片加载
  s0.bumpCount = () => {};
  let autoFired = 0;
  s0._ensureLongTextAll = () => { autoFired++; };
  s0._loadStaticLatest();
  await settle(40);
  check('首屏流程自动触发预取 1 次', autoFired, 1);

  console.log('\n--- 2) 搜索永不等待长文本（未就绪即用已到数据出结果）---');
  const fq = newFake({
    query: 'abs-03', searchField: 'abstract',
    all: [{ sourceUrl: 'https://x/03', lectureIndex: null, title: 't' }],
  });
  check('未就绪时立即可算（0 命中，不阻塞、不等待）', COMPUTED.filtered.call(fq).length, 0);
  check('searchPending 已彻底移除', COMPUTED.searchPending, undefined);

  console.log('\n--- 3) 预取：拉齐 16 桶并填入旁路存储 ---');
  const f1 = newFake({ query: '' });
  fetchLog = [];
  await f1._prefetchLongText();
  check('拉到 16 个桶', countDetailCalls(), BUCKETS);
  check('_ltAllLoaded 置位', f1._ltAllLoaded, true);
  check('长文本条目数 = 16', Object.keys(f1._longText).length, BUCKETS);
  check('桶全部标记已加载', Object.keys(f1._ltLoaded).length, BUCKETS);
  check('进行中标志已复位', f1._ltSearching, false);

  console.log('\n--- 4) 幂等：已就绪时重复触发零请求 ---');
  fetchLog = [];
  f1._ensureLongTextAll();
  f1._ensureLongTextAll();
  await f1._prefetchLongText();
  check('零新增请求', fetchLog.length, 0);

  console.log('\n--- 5) 并发防重入：预取进行中再触发不重复发起 ---');
  const f2 = newFake({ query: '' });
  fetchLog = [];
  const p1 = f2._prefetchLongText();
  const p2 = f2._prefetchLongText();          // 应被 _ltSearching 短路
  check('第二次调用被短路', f2._ltSearching, true);
  await Promise.all([p1, p2]);
  check('只拉一轮 16 桶', countDetailCalls(), BUCKETS);

  console.log('\n--- 6) 清单不可用：标记后不卡死（搜索退回已到数据）---');
  manifestOk = false;
  const f3 = newFake({
    query: '李',
    all: [{ sourceUrl: 'https://x/03', lectureIndex: null, title: '李讲座' }],
  });
  await f3._prefetchLongText();
  check('_ltUnavailable 置位', f3._ltUnavailable, true);
  check('进行中标志已复位', f3._ltSearching, false);
  check('搜索照常可用（命中已到数据）', COMPUTED.filtered.call(f3).length, 1);
  manifestOk = true;

  console.log('\n--- 7) 滚动触发预取（停在顶部不触发）---');
  const f4 = newFake({ query: '' });
  let started = 0;
  f4._ensureLongTextAll = () => { started++; };
  ctx.window.scrollY = 0;
  f4.onScroll.call(f4);
  check('停在顶部不触发', started, 0);
  ctx.window.scrollY = 300;
  f4.onScroll.call(f4);
  check('滚动后触发', started, 1);

  console.log('\n--- 8) 就绪后摘要可被搜索命中（长文本能力未丢 + 到齐自动补全）---');
  const f5 = newFake({
    query: 'abs-03', searchField: 'abstract',
    all: [{ sourceUrl: 'https://x/03', lectureIndex: null, title: 't' }],
  });
  check('未就绪时无命中', COMPUTED.filtered.call(f5).length, 0);
  await f5._prefetchLongText();
  check('就绪后命中 1 条（响应式补全）', COMPUTED.filtered.call(f5).length, 1);

  console.log('\n--- 9) 单桶失败：搜索照常 + 后续交互补拉 ---');
  failBucket = '05';
  const f6 = newFake({ query: '李' });
  await f6._prefetchLongText();
  check('搜索结果不受残缺桶影响（不等待）', COMPUTED.filtered.call(f6).length, 0);
  check('_ltIncomplete 置位', f6._ltIncomplete, true);
  check('已加载桶数 = 15', Object.keys(f6._ltLoaded).length, BUCKETS - 1);
  await f6._prefetchLongText();               // 下一次交互触发补拉
  check('补拉后桶数 = 16', Object.keys(f6._ltLoaded).length, BUCKETS);
  check('补拉后 _ltIncomplete 复位', f6._ltIncomplete, false);
  fetchLog = [];
  await f6._prefetchLongText();
  check('补齐后不再空跑', countDetailCalls(), 0);

  console.log('\n' + (fail ? '[FAIL] ' + fail + ' 项未通过' : '[PASS] 长文本加载流程全部用例通过'));
  process.exit(fail ? 1 : 0);
})();
