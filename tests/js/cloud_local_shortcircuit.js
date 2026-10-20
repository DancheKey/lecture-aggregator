// LectureCloud 本机开发环境短路门禁（2026-10-18 方案 B）。
//
// 背景：本地起 server.py 验证修改效果时，页面每次刷新都经 LectureCloud 实打实
// 写 Supabase，把公网真实计数刷虚高。现实现：hostname 为 localhost / 127.0.0.1
// 或 file: 协议时 loadConfig 直接 resolve(null)（=云端禁用），调用方照旧走
// /api 与本机降级链；localStorage.forceCloud === '1' 为逃生门（强制走云端）。
//
// 本套件锁死短路语义（cloud_pagination.js 只覆盖公网路径，本地分支在此覆盖）：
//   L1 公网域名（*.github.io）不短路：bump 照常打 rpc 并返回权威行
//   L2 localhost 短路：bump 返 null 且零 fetch（连 supabase-config.json 都不读）
//   L3 127.0.0.1 短路：同 L2
//   L4 file: 协议短路：同 L2（hostname 为空也不误判）
//   L5 逃生门：localhost + forceCloud='1' → 强制走云端（rpc 打到）
//   L6 localStorage 异常（隐私模式）：保守短路，宁可少写也不污染云端
//   L7 公网 fetchAll 不短路：照常拉全表进 map
//   L8 localhost fetchAll 短路：null 且零 fetch
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');

const SITE = path.join(__dirname, '..', '..', 'site');

// env: { protocol, hostname, store, throwOnGetItem }
function makeCtx(fetchImpl, env) {
  env = env || {};
  const calls = [];
  const wrapped = (url, opts) => { calls.push({ url, opts }); return fetchImpl(url, opts); };
  const store = env.store || {};
  const sandbox = {
    console, fetch: wrapped,
    // vm 沙箱默认无 Promise 全局；短路分支的 Promise.resolve(null) 在 vm 内执行，必须显式提供。
    Promise,
    location: { protocol: env.protocol || 'https:', hostname: env.hostname || 'example.com' },
    localStorage: {
      getItem: (k) => {
        if (env.throwOnGetItem) throw new Error('localStorage disabled');
        return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
      },
    },
  };
  sandbox.globalThis = sandbox;
  return { ctx: vm.createContext(sandbox), calls };
}

function jsonResp(body, ok = true, status = 200) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(body) });
}

// 公网 fetch 桩：config 一次 + rpc 一次（bump）；rest/v1（fetchAll）。
function cloudFetch(url) {
  if (url === 'supabase-config.json') {
    return jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' });
  }
  if (/\/rpc\/increment_/.test(url)) {
    return jsonResp({ url: 'http://a/1', visits: 5, likes: 0, wants: 0, cal_downloads: 0 });
  }
  if (/\/rest\/v1\/lecture_stats/.test(url)) {
    return jsonResp([{ url: 'http://a/1', visits: 5, likes: 0, wants: 0, cal_downloads: 0 }]);
  }
  throw new Error('未预期的云端请求: ' + url);
}

// 跨 vm realm 的对象不能直接 deepStrictEqual（prototype 不同源会假红，
// 同 cloud_pagination.js / cloud_stats.js），比对序列化结果。
function sameJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function loadCloud(ctx) {
  vm.runInContext(fs.readFileSync(path.join(SITE, 'app.cloud.js'), 'utf-8'), ctx,
    { filename: 'app.cloud.js' });
  return vm.runInContext('LectureCloud', ctx);
}

async function run() {
  // L1：公网域名不短路——bump 打 rpc 且返回数据库权威行
  {
    const { ctx, calls } = makeCtx(cloudFetch, { hostname: 'danchekey.github.io' });
    const cloud = loadCloud(ctx);
    const row = await cloud.bump('visit', 'http://a/1', 1);
    assert.ok(row && sameJson(row, { url: 'http://a/1', visits: 5, likes: 0, wants: 0, cal_downloads: 0 }),
      'L1 公网域名 bump 应返回权威行，实得: ' + JSON.stringify(row));
    assert.strictEqual(calls.length, 2, `L1 应恰 2 次请求（config+rpc），实得 ${calls.length}`);
    assert.ok(calls[1].url.includes('/rpc/increment_visit'), 'L1 第二次应为 rpc 增量: ' + calls[1].url);
    console.log('L1 公网域名不短路（bump 打 rpc）  pass');
  }

  // L2/L3/L4：本机三形态短路——bump 返 null 且零 fetch
  for (const [name, env] of [
    ['L2 localhost', { hostname: 'localhost' }],
    ['L3 127.0.0.1', { hostname: '127.0.0.1' }],
    ['L4 file: 协议', { protocol: 'file:', hostname: '' }],
  ]) {
    const { ctx, calls } = makeCtx(cloudFetch, env);
    const cloud = loadCloud(ctx);
    const row = await cloud.bump('visit', 'http://a/1', 1);
    assert.strictEqual(row, null, name + ' bump 应返 null（云端禁用）');
    assert.strictEqual(calls.length, 0,
      name + ' 零 fetch（连 supabase-config.json 都不读），实得 ' + calls.length);
    console.log(name + ' 短路（零请求）  pass');
  }

  // L5：逃生门——localhost + forceCloud='1' 强制走云端
  {
    const { ctx, calls } = makeCtx(cloudFetch, { hostname: 'localhost', store: { forceCloud: '1' } });
    const cloud = loadCloud(ctx);
    const row = await cloud.bump('like', 'http://a/1', 1);
    assert.ok(row && sameJson(row, { url: 'http://a/1', visits: 5, likes: 0, wants: 0, cal_downloads: 0 }),
      'L5 逃生门开启时 bump 应返回权威行，实得: ' + JSON.stringify(row));
    assert.ok(calls.some(c => /\/rpc\/increment_like/.test(c.url)),
      'L5 逃生门应放行 rpc 请求，实得调用: ' + JSON.stringify(calls.map(c => c.url)));
    console.log('L5 逃生门 forceCloud=1 强制走云端  pass');
  }

  // L6：localStorage 异常（隐私模式等）——保守短路，不读 config 不打 rpc
  {
    const { ctx, calls } = makeCtx(cloudFetch, { hostname: 'localhost', throwOnGetItem: true });
    const cloud = loadCloud(ctx);
    const row = await cloud.bump('visit', 'http://a/1', 1);
    assert.strictEqual(row, null, 'L6 localStorage 异常应保守短路');
    assert.strictEqual(calls.length, 0, 'L6 零 fetch，实得 ' + calls.length);
    console.log('L6 localStorage 异常保守短路  pass');
  }

  // L7：公网 fetchAll 不短路——照常拉全表进 map
  {
    const { ctx, calls } = makeCtx(cloudFetch, { hostname: 'danchekey.github.io' });
    const cloud = loadCloud(ctx);
    const got = await cloud.fetchAll();
    assert.ok(got && sameJson(got['http://a/1'], { visits: 5, likes: 0, wants: 0, cal_downloads: 0 }),
      'L7 公网 fetchAll 应返回全表 map，实得: ' + JSON.stringify(got));
    assert.ok(calls.some(c => /\/rest\/v1\/lecture_stats/.test(c.url)),
      'L7 应发 rest 读表请求，实得调用: ' + JSON.stringify(calls.map(c => c.url)));
    console.log('L7 公网 fetchAll 不短路  pass');
  }

  // L8：localhost fetchAll 短路——null 且零 fetch
  {
    const { ctx, calls } = makeCtx(cloudFetch, { hostname: 'localhost' });
    const cloud = loadCloud(ctx);
    const got = await cloud.fetchAll();
    assert.strictEqual(got, null, 'L8 localhost fetchAll 应返 null');
    assert.strictEqual(calls.length, 0, 'L8 零 fetch，实得 ' + calls.length);
    console.log('L8 localhost fetchAll 短路  pass');
  }

  console.log('cloud_local_shortcircuit: 8/8 pass');
}

run().catch(e => { console.error(e); process.exit(1); });
