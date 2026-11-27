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
//   T8 fetchAll 分页形态（order=url&limit=1000&offset=0，2026-10-18 路线二）
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');

const SITE = path.join(__dirname, '..', '..', 'site');

function makeCtx(fetchImpl) {
  const calls = [];
  const timers = new Map();
  let nextTimer = 1;
  const wrapped = (url, opts) => { calls.push({ url, opts }); return fetchImpl(url, opts); };
  const sandbox = {
    console, fetch: wrapped,
    location: { hostname: 'example.com' },
    AbortController,
    setTimeout: (fn, ms) => {
      const id = nextTimer++;
      timers.set(id, { fn, ms });
      return id;
    },
    clearTimeout: id => timers.delete(id),
  };
  sandbox.globalThis = sandbox;
  const expireTimers = () => {
    const pending = Array.from(timers.values());
    timers.clear();
    pending.forEach(t => t.fn());
  };
  return { ctx: vm.createContext(sandbox), calls, timers, expireTimers };
}

function jsonResp(body, ok = true, status = 200) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(body) });
}

function loadCloud(fetchImpl) {
  const { ctx, calls, timers, expireTimers } = makeCtx(fetchImpl);
  vm.runInContext(fs.readFileSync(path.join(SITE, 'app.cloud.js'), 'utf-8'), ctx,
    { filename: 'app.cloud.js' });
  // const 声明不挂 sandbox 对象，但进入 context 的 global 词法环境，
  // 后续 runInContext 表达式可读取（与现有测试取 APP_METHODS 同手法）。
  return { cloud: vm.runInContext('LectureCloud', ctx), calls, timers, expireTimers };
}

// 跨 vm realm 的对象不能直接 deepStrictEqual（prototype 不同源会假红，
// 与 tests/js/stats_quota_guard.js 同因），比对序列化结果——只关心字段值语义。
function sameJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function flushMicrotasks() {
  return new Promise(resolve => setImmediate(resolve));
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

  // T3：批量读解析（含脏行过滤与数值规整；2026-10-20 起全表含 cal_downloads 列）
  {
    const rows = [
      { url: 'http://a/1', visits: '9', likes: 3, wants: 1, cal_downloads: '2' },
      { url: 'http://a/2', visits: 0, likes: 0, wants: 0, cal_downloads: 0 },
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
      'http://a/1': { visits: 9, likes: 3, wants: 1, cal_downloads: 2 },   // '9'/'2' 字符串规整为数字
      'http://a/2': { visits: 0, likes: 0, wants: 0, cal_downloads: 0 },
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

  // T8：fetchAll 分页形态（2026-10-18 路线二）——单次 limit 再大也会被服务端
  // Max Rows（默认 1000）静默截断，故每批恰取 1000 + offset 翻页 + order=url
  // 保证稳定排序；行数触顶后部分讲座计数静默归零的问题由此根治。
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
    assert.ok(seenUrl && seenUrl.includes('order=url') && seenUrl.includes('limit=1000')
              && seenUrl.includes('offset=0'),
              'T8 fetchAll 首页须为 order=url&limit=1000&offset=0，实得: ' + seenUrl);
    console.log('T8 fetchAll 分页形态（order/limit/offset）  pass');
  }

  // T9：配置请求网络层挂起 → 定时中止并降级，不永久卡住调用方。
  {
    let signal = null;
    const { cloud, calls, timers, expireTimers } = loadCloud((url, opts) => {
      signal = opts.signal;
      return new Promise(() => {});
    });
    const pending = cloud.bump('like', 'http://a/1', 1);
    await flushMicrotasks();
    assert.strictEqual(calls.length, 1, 'T9 应已发出配置请求');
    assert.strictEqual(timers.size, 1, 'T9 应设置超时');
    assert.strictEqual(Array.from(timers.values())[0].ms, 8000, 'T9 超时预算应为 8 秒');
    expireTimers();
    assert.strictEqual(await pending, null, 'T9 配置挂起必须降级 null');
    assert.strictEqual(signal.aborted, true, 'T9 超时必须 abort 原请求');
    assert.strictEqual(timers.size, 0, 'T9 结束后必须清定时器');
    console.log('T9 配置请求挂起超时降级  pass');
  }

  // T10：响应头已到但 JSON body 永久挂起，同样受同一预算保护。
  {
    const { cloud, timers, expireTimers } = loadCloud(() =>
      Promise.resolve({ ok: true, status: 200, json: () => new Promise(() => {}) }));
    const pending = cloud.fetchAll();
    await flushMicrotasks();
    assert.strictEqual(timers.size, 1, 'T10 body 挂起仍应有超时');
    expireTimers();
    assert.strictEqual(await pending, null, 'T10 body 挂起必须降级 null');
    console.log('T10 JSON body 挂起超时降级  pass');
  }

  // T11：RPC 写请求挂起 → 返回 null；调用方据此走本机降级，不自动重试写。
  {
    let signal = null;
    const { cloud, timers, expireTimers } = loadCloud((url, opts) => {
      if (url === 'supabase-config.json') {
        return jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' });
      }
      signal = opts.signal;
      return new Promise(() => {});
    });
    const pending = cloud.bump('want', 'http://a/1', 1);
    await flushMicrotasks();
    await Promise.resolve();
    assert.strictEqual(timers.size, 1, 'T11 RPC 应设置超时');
    expireTimers();
    assert.strictEqual(await pending, null, 'T11 RPC 挂起必须降级 null');
    assert.strictEqual(signal.aborted, true, 'T11 RPC 超时必须 abort');
    console.log('T11 RPC 挂起超时降级且不重试  pass');
  }

  // T12：REST 任一批挂起 → 整体 null，不返回半套计数。
  {
    const { cloud, timers, expireTimers } = loadCloud((url) => {
      if (url === 'supabase-config.json') {
        return jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' });
      }
      return new Promise(() => {});
    });
    const pending = cloud.fetchAll();
    await flushMicrotasks();
    await Promise.resolve();
    assert.strictEqual(timers.size, 1, 'T12 REST 应设置超时');
    expireTimers();
    assert.strictEqual(await pending, null, 'T12 REST 挂起必须整体降级 null');
    console.log('T12 REST 挂起整体降级  pass');
  }

  // T13：成功路径必须清掉定时器，避免请求完成后仍触发无效 abort。
  {
    const { cloud, calls, timers } = loadCloud((url) => url === 'supabase-config.json'
      ? jsonResp({ url: 'https://x.supabase.co', anonKey: 'k' })
      : jsonResp({ visits: 1, likes: 1, wants: 0 }));
    assert.ok(await cloud.bump('like', 'http://a/1', 1), 'T13 成功请求应有返回值');
    assert.strictEqual(timers.size, 0, 'T13 成功后不得残留定时器');
    assert.strictEqual(calls[1].opts.signal.aborted, false, 'T13 成功后不得 abort');
    console.log('T13 成功路径清理超时定时器  pass');
  }

  console.log('cloud_stats: 13/13 pass');
}

run().catch(e => { console.error(e); process.exit(1); });
