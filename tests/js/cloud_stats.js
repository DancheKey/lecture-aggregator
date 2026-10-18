// LectureCloud 云端共享计数层单测（2026-10-17）。
// 覆盖 site/app.cloud.js：配置加载与缓存、批量读解析、RPC 增量请求形态、
// 全失败路径静默降级（返回 null，调用方走既有 /api → 本机链）。
//
// 场景：
//   T1 配置文件 404（公网/未配置）→ bump/fetchAll 均 resolve null
//   T2 配置文件存在但空值 → null（视为禁用）
//   T3 配置有效 + REST 200 → fetchAll 解析全表（脏行过滤 + 数值规整）
//   T4 配置有效 + RPC 200 → bump 返回数据库权威行
//   T5 REST 401（墙/无权限）→ fetchAll resolve null（软失败降级）
//   T6 请求形态：URL /rest/v1/rpc/increment_<kind>、双 header、body {p_url,p_delta}
//   T7 配置只加载一次（Promise 缓存，重复调用不发重复请求）
//   T8 fetchAll 带 limit=5000（全表拉取兜底，2026-10-18 S1）
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

function loadCloud(fetchImpl) {
  const { ctx, calls } = makeCtx(fetchImpl);
  vm.runInContext(fs.readFileSync(path.join(SITE, 'app.cloud.js'), 'utf-8'), ctx,
    { filename: 'app.cloud.js' });
  // const 声明不挂 sandbox 对象，但进入 context 的 global 词法环境，
  // 后续 runInContext 表达式可读取（与现有测试取 APP_METHODS 同手法）。
  return { cloud: vm.runInContext('LectureCloud', ctx), calls };
}

// 跨 vm realm 的对象不能直接 deepStrictEqual（prototype 不同源会假红，
// 与 tests/js/stats_quota_guard.js 同因），比对序列化结果——只关心字段值语义。
function sameJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

async function run() {
  // T1：配置 404 → 禁用
  {
    const { cloud } = loadCloud((url) => {
      if (url === 'supabase-config.json') return jsonResp(null, false, 404);
      throw new Error('不应再发请求: ' + url);
    });
    assert.strictEqual(await cloud.fetchAll(), null, 'T1a fetchAll 应 null');
    assert.strictEqual(await cloud.bump('like', 'http://a/1', 1), null, 'T1b bump 应 null');
    console.log('T1 配置 404 = 云端禁用（静默）  pass');
  }

  // T2：配置存在但缺字段 → 禁用
  {
    const { cloud } = loadCloud((url) =>
      url === 'supabase-config.json' ? jsonResp({ url: '' }) : Promise.reject(new Error('不应请求')));
    assert.strictEqual(await cloud.fetchAll(), null, 'T2 fetchAll 应 null');
    console.log('T2 配置空值 = 云端禁用（静默）  pass');
  }

  // T3：批量读解析（含脏行过滤与数值规整）
  {
    const rows = [
      { url: 'http://a/1', visits: '9', likes: 3, wants: 1 },
      { url: 'http://a/2', visits: 0, likes: 0, wants: 0 },
      { visits: 5 },                       // 无 url → 丢弃
    ];
    const { cloud } = loadCloud((url) => {
      if (url === 'supabase-config.json') {
        return jsonResp({ url: 'https://x.supabase.co/', anonKey: 'k' });
      }
      return jsonResp(rows);
    });
    const got = await cloud.fetchAll();
    assert.ok(sameJson(got, {
      'http://a/1': { visits: 9, likes: 3, wants: 1 },   // '9' 字符串规整为数字
      'http://a/2': { visits: 0, likes: 0, wants: 0 },
    }), 'T3 fetchAll 解析结果不符: ' + JSON.stringify(got));
    console.log('T3 批量读解析（脏行过滤+数值规整）  pass');
  }

  // T4：RPC 增量返回权威行
  {
    const { cloud } = loadCloud((url) => {
      if (url === 'supabase-config.json') {
        return jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' });
      }
      return jsonResp({ visits: 10, likes: 5, wants: 2 });
    });
    const got = await cloud.bump('like', 'http://a/1', 1);
    assert.ok(sameJson(got, { visits: 10, likes: 5, wants: 2 }), 'T4 权威行不符: ' + JSON.stringify(got));
    console.log('T4 bump 返回数据库权威行  pass');
  }

  // T5：REST 401（被墙/无权限）→ 软失败
  {
    const { cloud } = loadCloud((url) => {
      if (url === 'supabase-config.json') {
        return jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' });
      }
      return jsonResp(null, false, 401);
    });
    assert.strictEqual(await cloud.fetchAll(), null, 'T5a fetchAll 401 应 null');
    assert.strictEqual(await cloud.bump('visit', 'http://a/1', 1), null, 'T5b bump 401 应 null');
    console.log('T5 REST 401 = 软失败降级  pass');
  }

  // T6：请求形态（URL / headers / body / method）
  {
    let seen = null;
    const { cloud } = loadCloud((url, opts) => {
      if (url === 'supabase-config.json') {
        return jsonResp({ url: 'https://x.supabase.co', anonKey: 'secret-key' });
      }
      seen = { url, opts };
      return jsonResp({ visits: 1, likes: 1, wants: 0 });
    });
    await cloud.bump('want', 'http://a/9', -1);
    assert.strictEqual(seen.url, 'https://x.supabase.co/rest/v1/rpc/increment_want', 'T6 URL');
    assert.strictEqual(seen.opts.method, 'POST', 'T6 method');
    assert.ok(sameJson(seen.opts.headers, {
      'Content-Type': 'application/json',
      apikey: 'secret-key',
      Authorization: 'Bearer secret-key',
    }), 'T6 headers 不符: ' + JSON.stringify(seen.opts.headers));
    assert.strictEqual(seen.opts.body, JSON.stringify({ p_url: 'http://a/9', p_delta: -1 }), 'T6 body');
    console.log('T6 RPC 请求形态（URL/headers/body）  pass');
  }

  // T7：配置只加载一次（后续调用不发重复配置请求）
  {
    let configHits = 0;
    const { cloud } = loadCloud((url) => {
      if (url === 'supabase-config.json') {
        configHits += 1;
        return jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' });
      }
      return jsonResp([]);
    });
    await cloud.fetchAll();
    await cloud.fetchAll();
    await cloud.bump('like', 'http://a/1', 1);
    assert.strictEqual(configHits, 1, `T7 配置请求应只发 1 次，实际 ${configHits}`);
    console.log('T7 配置 Promise 缓存（只加载一次）  pass');
  }

  // T8：fetchAll 必须带 limit（2026-10-18 审查报告 S1）——全表拉取无分页
  // 上限，行数被灌爆（空键/超长键建行）时单次响应体会拖慢每页统计加载。
  {
    let seenUrl = null;
    const { cloud } = loadCloud((url) => {
      if (url === 'supabase-config.json') {
        return jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' });
      }
      seenUrl = url;
      return jsonResp([]);
    });
    await cloud.fetchAll();
    assert.ok(seenUrl && seenUrl.includes('limit=5000'),
              'T8 fetchAll 的请求须带 limit=5000 兜底，实得: ' + seenUrl);
    console.log('T8 fetchAll 带 limit=5000（全表拉取兜底）  pass');
  }

  console.log('cloud_stats: 8/8 pass');
}

run().catch(e => { console.error(e); process.exit(1); });
