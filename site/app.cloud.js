// 云端共享计数层（2026-10-17 上线）：讲座 URL 维度的 访问/点赞/想听 全局累计。
// 目标：Supabase REST API，裸 fetch 零 SDK 依赖（保持本站零 CDN 架构）。
//
// 配置来源：site/supabase-config.json（仅本机存在、刻意不入库），形如
//   { "url": "https://<项目ref>.supabase.co", "anonKey": "eyJ..." }
// 缺失 / 空值 / 读取失败 = 云端禁用，调用方照旧走 /api 与本机降级链，
// 行为与本层上线前完全一致（公网用户无感）。
//
// 安全模型：anon key 公开是 Supabase 的设计定位（同 reCAPTCHA site key），
// 真正的闸门在数据库侧——RLS + RPC 增量函数内钳值域（见迁移 SQL 注释），
// 前端只能 +/-1、写不了任意值；显示封顶在 app.social.js 的 capDisplay。
const LectureCloud = (function () {
    let configPromise = null;

    function loadConfig() {
        if (!configPromise) {
            configPromise = fetch('supabase-config.json', { cache: 'no-store' })
                .then(r => { if (!r.ok) throw new Error('config-' + r.status); return r.json(); })
                .then(j => (j && j.url && j.anonKey)
                    ? { url: String(j.url).replace(/\/+$/, ''), anonKey: String(j.anonKey) }
                    : null)
                .catch(() => null);   // 404 / 解析失败 / 空值 = 静默禁用
        }
        return configPromise;
    }

    function headers(cfg) {
        return {
            'Content-Type': 'application/json',
            'apikey': cfg.anonKey,
            'Authorization': 'Bearer ' + cfg.anonKey,
        };
    }

    return {
        // 拿配置（Promise<config|null>；null=云端禁用）。内部缓存，重复调用不发请求。
        config: loadConfig,
        // 批量读全表 → { url: { visits, likes, wants } }；任一环节失败返回 null。
        fetchAll() {
            return loadConfig().then(cfg => {
                if (!cfg) return null;
                return fetch(cfg.url + '/rest/v1/lecture_stats?select=url,visits,likes,wants',
                    { cache: 'no-store', headers: headers(cfg) })
                    .then(r => { if (!r.ok) throw new Error('stats-' + r.status); return r.json(); })
                    .then(rows => {
                        const out = {};
                        for (const row of (rows || [])) {
                            if (row && row.url) {
                                out[row.url] = {
                                    visits: +row.visits || 0,
                                    likes: +row.likes || 0,
                                    wants: +row.wants || 0,
                                };
                            }
                        }
                        return out;
                    })
                    .catch(() => null);
            });
        },
        // 增量调用：kind ∈ like|want|visit，delta=+1/-1。
        // 成功返回数据库权威行 { visits, likes, wants }；失败/禁用返回 null。
        bump(kind, url, delta) {
            return loadConfig().then(cfg => {
                if (!cfg) return null;
                return fetch(cfg.url + '/rest/v1/rpc/increment_' + kind, {
                    method: 'POST', cache: 'no-store', headers: headers(cfg),
                    body: JSON.stringify({ p_url: url, p_delta: delta }),
                })
                    .then(r => { if (!r.ok) throw new Error('rpc-' + r.status); return r.json(); })
                    .then(j => (j && typeof j === 'object') ? j : null)
                    .catch(() => null);
            });
        },
    };
})();
