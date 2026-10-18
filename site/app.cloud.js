// 云端共享计数层（2026-10-17 上线）：讲座 URL 维度的 访问/点赞/想听 全局累计。
// 目标：Supabase REST API，裸 fetch 零 SDK 依赖（保持本站零 CDN 架构）。
//
// 配置来源：site/supabase-config.json（随站发布，2026-10-18 起入库），形如
//   { "url": "https://<项目ref>.supabase.co", "anonKey": "eyJ..." }
// anon key 为公开设计（见下安全模型）。缺失 / 空值 / 读取失败 / 本机开发环境
// （见 isLocalDev）= 云端禁用，调用方照旧走 /api 与本机降级链，行为与本层上线前完全一致。
//
// 安全模型：anon key 公开是 Supabase 的设计定位（同 reCAPTCHA site key），
// 真正的闸门在数据库侧——RLS + RPC 增量函数内钳值域（见迁移 SQL 注释），
// 前端只能 +/-1、写不了任意值；显示封顶在 app.social.js 的 capDisplay。
const LectureCloud = (function () {
    let configPromise = null;

    // 本地开发环境短路（2026-10-18）：本机（localhost / 127.0.0.1 / file:）默认不打云端，
    // 防止本地反复刷新把公网真实计数刷虚高。短路时 loadConfig 直接 resolve(null)，
    // 与「配置缺失 / 读取失败」完全同路——调用方照旧走 /api 与本机降级链，
    // 行为与云端层上线前一致（见文件头注释）。
    // 逃生门：localStorage.forceCloud === '1' 时强制走云端（验证真实写入效果用）。
    // 判据只认 hostname / 协议；公网域名（*.github.io 等）永不经此分支。
    function isLocalDev() {
        var local = typeof location !== 'undefined' &&
            (location.protocol === 'file:' ||
             /^(localhost|127\.0\.0\.1)$/.test(location.hostname || ''));
        if (!local) return false;
        try { return localStorage.getItem('forceCloud') !== '1'; }
        catch (e) { return true; }   // 隐私模式等 localStorage 异常：保守短路，宁可少写也不污染云端
    }

    function loadConfig() {
        if (!configPromise) {
            configPromise = isLocalDev()
                ? Promise.resolve(null)   // 本地开发：一个云端请求都不发
                : fetch('supabase-config.json', { cache: 'no-store' })
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
        // 分页循环（2026-10-18）：Supabase 服务端有 Max Rows 全局上限（默认 1000），
        // 单次 limit 写多大都被静默截到该值——云端守卫上线前曾被灌出垃圾行，
        // 且讲座计数行随访问持续增长，迟早触顶（触顶后部分讲座计数静默归零）。
        // 改为每批 PAGE=1000（≤默认 Max Rows，任何配置下都不截断）+ offset 递增
        // 翻到尾批不满即收工，从此不依赖 Dashboard 的 Max Rows 设置。
        // order=url：url 是主键唯一，offset 翻页稳定（无重复无遗漏）。
        // MAX_PAGES：服务端异常时永远返满页的死循环硬顶（5 万行，远超真实量级）。
        // 任一批失败 → 整次返回 null，调用方照旧走 /api → 本机降级链；
        // 不做部分采用——半套计数比没有计数更坏（数字会对不上）。
        fetchAll() {
            return loadConfig().then(cfg => {
                if (!cfg) return null;
                const PAGE = 1000;
                const MAX_PAGES = 50;
                const out = {};
                const base = cfg.url + '/rest/v1/lecture_stats'
                    + '?select=url,visits,likes,wants&order=url&limit=' + PAGE + '&offset=';
                const onePage = (offset) =>
                    fetch(base + offset, { cache: 'no-store', headers: headers(cfg) })
                        .then(r => { if (!r.ok) throw new Error('stats-' + r.status); return r.json(); })
                        .then(rows => {
                            const list = rows || [];
                            for (const row of list) {
                                // `__` 前缀是保留键（__site__=全站 PV、历史探测行），
                                // 不是讲座 URL，过滤掉防止混入统计与讲座 map。
                                if (row && row.url && !/^__/.test(row.url)) {
                                    out[row.url] = {
                                        visits: +row.visits || 0,
                                        likes: +row.likes || 0,
                                        wants: +row.wants || 0,
                                    };
                                }
                            }
                            return list.length;
                        });
                // 满页（=PAGE）才可能有下一页；尾批不满即收工。
                const loop = (offset, pages) =>
                    onePage(offset).then(n => (n >= PAGE && pages < MAX_PAGES)
                        ? loop(offset + PAGE, pages + 1)
                        : out);
                return loop(0, 1).catch(() => null);
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
