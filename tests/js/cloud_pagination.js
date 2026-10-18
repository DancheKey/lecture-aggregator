// LectureCloud fetchAll 分页循环行为门禁（2026-10-18 路线二）。
//
// 背景：Supabase 服务端 Max Rows 默认 1000，单次 limit 写多大都被静默截断——
// 旧实现 limit=5000 只是心理兜底，行数触顶后部分讲座计数静默归零。现实现
// 每批 1000 + offset 翻页循环，本套件锁死翻页语义（cloud_stats.js 的 T8
// 只锁首页 URL 形态，翻页循环本身在此覆盖）：
//   G1 满页继续：首批 1000 条 → 必须发第二批 offset=1000
//   G2 尾批不满即停：末批 499 条 → 不再发请求（无多余空翻）
//   G3 整倍数边界：两批各 1000 后第三批返 [] → 正常收尾（多一次空请求可接受）
//   G4 多批合并：1003 行跨两批全部进 map，无重无漏
//   G5 任一批失败 → 整体 null（不部分采用——半套计数比没有更坏）
//   G6 死循环硬顶：服务端永远返满页 → 最多 50 批后强制收尾
//   G7 稳定排序：请求必须带 order=url（offset 翻页无重复无遗漏的前提）
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');

const SITE = path.join(__dirname, '..', '..', 'site');

function makeCtx(fetchImpl) {
  const calls = [];
  const wrapped = (url, opts) => { calls.push({ url, opts }); return fetchImpl(url, opts); };
  const sandbox = {
    console, fetch: wrapped,
    location: { hostname: 'example.com' },
  };
  sandbox.globalThis = sandbox;
  return { ctx: vm.createContext(sandbox), calls };
}

function jsonResp(body, ok = true, status = 200) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(body) });
}

// 按 offset 分页的 mock：pages 为「每批返回的行数」数组，用尽后抛错
// （防测试自己逻辑出错时静默多发请求）。
function pagedFetch(pages) {
  let i = 0;
  return (url) => {
    if (url === 'supabase-config.json') {
      return jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' });
    }
    const m = /offset=(\d+)$/.exec(url);
    assert.ok(m, '分页请求须带 offset，实得: ' + url);
    const idx = Number(m[1]) / 1000;
    assert.strictEqual(idx, i, `offset 批次错位：期望第 ${i} 批，实得 offset=${m[1]}`);
    if (idx >= pages.length) throw new Error('超出预期批次数: ' + url);
    i += 1;
    return jsonResp(pages[idx]);
  };
}

function fullPage(tag, n) {
  return Array.from({ length: n }, (_, k) =>
    ({ url: `http://${tag}/${k}`, visits: 1, likes: 0, wants: 0 }));
}

// 跨 vm realm 的对象不能直接 deepStrictEqual（prototype 不同源会假红，
// 同 cloud_stats.js / stats_quota_guard.js），比对序列化结果。
function sameJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

async function run() {
  // 数据请求 = 除配置外的全部 fetch（config 只加载一次，混入计数会错）
  const dataCalls = (calls) => calls.filter(c => c.url !== 'supabase-config.json');

  // G1+G2+G4：两批（1000 满页 + 499 尾批）→ 共 2 次数据请求，1499 行进 map
  {
    const rowsA = fullPage('a', 1000);
    const rowsB = fullPage('b', 499);
    const { ctx, calls } = makeCtx(pagedFetch([rowsA, rowsB]));
    vm.runInContext(fs.readFileSync(path.join(SITE, 'app.cloud.js'), 'utf-8'), ctx,
      { filename: 'app.cloud.js' });
    const cloud = vm.runInContext('LectureCloud', ctx);
    const got = await cloud.fetchAll();
    const d = dataCalls(calls);
    assert.strictEqual(d.length, 2, `G1 应恰 2 次数据请求（满页续翻+尾批即停），实得 ${d.length}`);
    assert.ok(d[1].url.includes('offset=1000'), 'G1 第二批 offset 应为 1000: ' + d[1].url);
    assert.strictEqual(Object.keys(got).length, 1499, `G4 合并应 1499 行，实得 ${Object.keys(got).length}`);
    assert.ok(sameJson(got['http://a/0'], { visits: 1, likes: 0, wants: 0 }), 'G4 首批行进 map');
    assert.ok(sameJson(got['http://b/498'], { visits: 1, likes: 0, wants: 0 }), 'G4 尾批行进 map');
    console.log('G1+G2+G4 满页续翻/尾批即停/多批合并  pass');
  }

  // G3：整倍数边界——两批各 1000，第三批返 [] 收尾（共 3 次数据请求）
  {
    const rows = fullPage('a', 1000);
    const { ctx, calls } = makeCtx(pagedFetch([rows, rows, []]));
    vm.runInContext(fs.readFileSync(path.join(SITE, 'app.cloud.js'), 'utf-8'), ctx,
      { filename: 'app.cloud.js' });
    const cloud = vm.runInContext('LectureCloud', ctx);
    const got = await cloud.fetchAll();
    assert.strictEqual(dataCalls(calls).length, 3,
      `G3 整倍数应 3 次数据请求（含一次空翻收尾），实得 ${dataCalls(calls).length}`);
    assert.strictEqual(Object.keys(got).length, 1000, 'G3 合并应 1000 行');
    console.log('G3 整倍数边界（尾批满→空翻收尾）  pass');
  }

  // G5：第二批 500 → 整体 null（不部分采用）
  {
    const rows = fullPage('a', 1000);
    const { ctx } = makeCtx((url) => {
      if (url === 'supabase-config.json') {
        return jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' });
      }
      return /offset=1000$/.test(url) ? jsonResp(null, false, 500) : jsonResp(rows);
    });
    vm.runInContext(fs.readFileSync(path.join(SITE, 'app.cloud.js'), 'utf-8'), ctx,
      { filename: 'app.cloud.js' });
    const cloud = vm.runInContext('LectureCloud', ctx);
    assert.strictEqual(await cloud.fetchAll(), null, 'G5 任一批失败必须整体 null（禁部分采用）');
    console.log('G5 任一批失败→整体 null（禁部分采用）  pass');
  }

  // G6：死循环硬顶——服务端永远返满页，最多 50 批后强制收尾
  {
    const rows = fullPage('a', 1000);
    let hits = 0;
    const { ctx } = makeCtx((url) => {
      if (url === 'supabase-config.json') {
        return jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' });
      }
      hits += 1;
      return jsonResp(rows);
    });
    vm.runInContext(fs.readFileSync(path.join(SITE, 'app.cloud.js'), 'utf-8'), ctx,
      { filename: 'app.cloud.js' });
    const cloud = vm.runInContext('LectureCloud', ctx);
    const got = await cloud.fetchAll();
    assert.strictEqual(hits, 50, `G6 满页不止时应恰 50 批硬顶，实得 ${hits}`);
    assert.strictEqual(Object.keys(got).length, 1000, 'G6 硬顶后仍返回已拉部分');
    console.log('G6 死循环硬顶（MAX_PAGES=50）  pass');
  }

  // G7：稳定排序——请求必须带 order=url
  {
    let seenUrl = null;
    const { ctx } = makeCtx((url) => {
      if (url === 'supabase-config.json') {
        return jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' });
      }
      seenUrl = url;
      return jsonResp([]);
    });
    vm.runInContext(fs.readFileSync(path.join(SITE, 'app.cloud.js'), 'utf-8'), ctx,
      { filename: 'app.cloud.js' });
    const cloud = vm.runInContext('LectureCloud', ctx);
    await cloud.fetchAll();
    assert.ok(seenUrl.includes('order=url'),
              'G7 分页请求必须带 order=url（offset 翻页稳定性的前提）: ' + seenUrl);
    console.log('G7 稳定排序 order=url  pass');
  }

  console.log('cloud_pagination: 7/7 pass');
}

run().catch(e => { console.error(e); process.exit(1); });
