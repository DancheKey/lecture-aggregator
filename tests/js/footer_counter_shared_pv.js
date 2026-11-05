// 门禁：页脚与趋势页 KPI 共用一次取值（2026-11-05，评审 #1）。
//
// 背景：旧链路里页脚走「5 分钟缓存 + 条件 bump」，KPI 另调
// LectureCloud.fetchSitePv 单独读云端，两条链路时点不同 → 同一屏可能显示
// 2027 / 2028 两个数字，且缓存命中时 KPI 仍读到更新的值。用户拍板：
// 「页脚与 KPI 共用一次 fetch」，计数口径不变（全站 5 分钟防刷、趋势页不单独 +1）。
//
// 本门锁的静默错（零报错但数字错 / 计数错）：
//   ① 多次订阅触发多次 bump（重复计数）或多次读快照；
//   ② get() 每次返回新 Promise（页脚与 KPI 拿到的值可能不同）；
//   ③ 缓存有效时仍发请求（重复计数）；
//   ④ 云端失败不降级、或降级后页脚与 KPI 不同值；
//   ⑤ 无 LectureCloud 时脚本抛错。
//
// 运行：node tests/js/footer_counter_shared_pv.js（纯 node + vm，无浏览器无网络）
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SITE = path.join(__dirname, '..', '..', 'site');
const js = fs.readFileSync(path.join(SITE, 'footer-counter.js'), 'utf-8');
const errs = [];

function mkEl() {
  return {
    _text: '—',
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
  };
}

async function runScenario(opts) {
  const o = opts || {};
  const store = {};
  if (o.cached !== undefined) {
    store['lecture_site_count_value'] = String(o.cached);
    store['lecture_site_count_ts'] = String(Date.now());
  }
  const byId = { site_pv_value: mkEl() };
  const counts = { bump: 0, fetch: 0 };
  const sandbox = {
    console,
    Promise, Date, Math, Number, String, JSON, parseInt, isNaN,
    window: {},
    document: {
      readyState: 'complete',       // init() 立即执行，不依赖 DOMContentLoaded
      getElementById: id => byId[id] || null,
      addEventListener() {},
    },
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: k => { delete store[k]; },
    },
    fetch: () => {
      counts.fetch++;
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ site_pv: o.archivePv === undefined ? 2019 : o.archivePv }),
      });
    },
  };
  if (!o.noCloud) {
    sandbox.LectureCloud = {
      bump: () => {
        counts.bump++;
        return Promise.resolve(o.bumpResult === undefined ? { visits: 2027 } : o.bumpResult);
      },
    };
  }
  vm.createContext(sandbox);
  vm.runInContext(js, sandbox, { filename: 'footer-counter.js' });
  for (let i = 0; i < 12; i++) await Promise.resolve();
  await new Promise(res => setTimeout(res, 0));
  for (let i = 0; i < 12; i++) await Promise.resolve();
  return { sandbox, byId, counts, store };
}

(async () => {
  // ① 首次访问：只 bump 一次；页脚与「KPI 订阅者」拿到同一个 Promise、同一个值
  const a = await runScenario({ bumpResult: { visits: 2027 } });
  if (a.counts.bump !== 1) errs.push(`首次访问应恰好 bump 1 次，实得 ${a.counts.bump}`);
  const api = a.sandbox.window && a.sandbox.window.LectureSitePv;
  if (!api || typeof api.get !== 'function') {
    errs.push('未暴露 window.LectureSitePv.get()——趋势页 KPI 无处订阅共享值');
  } else {
    const p1 = api.get(), p2 = api.get(), p3 = api.get();
    if (p1 !== p2 || p2 !== p3) {
      errs.push('多次 get() 未返回同一个记忆化 Promise——会重复计数/重复读快照');
    }
    const vals = await Promise.all([p1, p2, p3]);
    if (vals.some(v => v !== 2027)) {
      errs.push(`共享值应恒为 2027，实得 ${JSON.stringify(vals)}`);
    }
    if (String(vals[vals.length - 1]) !== a.byId.site_pv_value._text) {
      errs.push(`页脚与 KPI 值不一致：页脚 "${a.byId.site_pv_value._text}" vs 共享值 ${vals[vals.length - 1]}`);
    }
    if (a.counts.bump !== 1) {
      errs.push(`多次订阅后 bump 变成 ${a.counts.bump} 次——重复计数`);
    }
  }
  if (a.byId.site_pv_value._text !== '2027') {
    errs.push(`页脚应显示权威值 2027，实得 "${a.byId.site_pv_value._text}"`);
  }
  if (a.store['lecture_site_count_value'] !== '2027') {
    errs.push('bump 成功后未写入 localStorage 缓存（5 分钟防刷失效）');
  }

  // ② 缓存有效：零请求，共享值就是缓存值（KPI 不会读到更新的实时值）
  const b = await runScenario({ cached: 2020 });
  if (b.counts.bump !== 0 || b.counts.fetch !== 0) {
    errs.push(`缓存命中不应发请求，实得 bump=${b.counts.bump} fetch=${b.counts.fetch}`);
  }
  const bv = await b.sandbox.window.LectureSitePv.get();
  if (bv !== 2020) errs.push(`缓存命中共享值应为 2020，实得 ${bv}`);
  if (b.byId.site_pv_value._text !== '2020') {
    errs.push(`缓存命中页脚应显示 2020，实得 "${b.byId.site_pv_value._text}"`);
  }
  if (String(bv) !== b.byId.site_pv_value._text) {
    errs.push('缓存命中时页脚与 KPI 仍可能不同值（评审 #1 未闭环）');
  }

  // ③ 云端失败：降级读静态快照一次，页脚与共享值同为快照值
  const c = await runScenario({ bumpResult: null, archivePv: 2019 });
  if (c.counts.fetch !== 1) {
    errs.push(`云端失败应降级读快照 1 次，实得 ${c.counts.fetch}`);
  }
  const cv1 = c.sandbox.window.LectureSitePv.get();
  const cv2 = c.sandbox.window.LectureSitePv.get();
  if (cv1 !== cv2) errs.push('降级路径多次 get() 未复用同一个 Promise');
  const cv = await cv1;
  if (cv !== 2019) errs.push(`降级共享值应为 2019，实得 ${cv}`);
  if (c.byId.site_pv_value._text !== '2019') {
    errs.push(`降级页脚应显示 2019，实得 "${c.byId.site_pv_value._text}"`);
  }
  if (c.counts.fetch !== 1) errs.push('降级路径多次订阅触发了重复读快照');
  if ('lecture_site_count_ts' in c.store) {
    errs.push('降级快照不得写入 localStorage 缓存（否则云端恢复后回不到实时值）');
  }

  // ④ 无 LectureCloud：不抛错，仍能降级
  const d = await runScenario({ noCloud: true, archivePv: 2019 });
  if (d.counts.bump !== 0 || d.counts.fetch !== 1) {
    errs.push(`无云端层应只降级读快照 1 次，实得 bump=${d.counts.bump} fetch=${d.counts.fetch}`);
  }
  if (d.byId.site_pv_value._text !== '2019') {
    errs.push(`无云端层页脚应显示快照 2019，实得 "${d.byId.site_pv_value._text}"`);
  }

  console.log('场景 ①首次bump ②缓存零请求 ③云端降级 ④无云端层'
    + (errs.length ? '' : ' 全部通过'));
  if (errs.length) {
    console.log('[FAIL]');
    errs.forEach(e => console.log(' -', e));
    process.exit(1);
  }
  console.log('[PASS] 页脚与 KPI 共用一次取值：同 Promise、同值、缓存零请求、降级不计数');
})();
