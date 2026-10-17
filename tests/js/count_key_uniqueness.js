// 计数键门禁（2026-10-18）：讲座级计数键 ck 必须存在、全局唯一、规则正确。
//
// 背景（用户 2026-10-18 报的 bug）：公网点一条讲座的赞，统计页显示成 3。
// 根因是计数键用源页 URL——`yjsy.scnu.edu.cn/a/20261012/10265.html` 这一页
// 含 3 场讲座（数据里 3 条记录共用同一 sourceUrl），云端只存 1 份 likes=1，
// 而 stats.js 按讲座条目逐条累加 → 1+1+1=3；首页上 3 张卡片也会同时 +1。
//
// 修法：计数键下沉到讲座级（ck = sourceUrl#lectureIndex，单场则等于 sourceUrl），
// 由 scripts/generate_frontend_data.py::count_key 单点产出，前端只消费不拼装。
//
// 本门禁锁三件事：
//   ① 每条讲座都有 ck，且**全局唯一**（重复即回到放大 bug）；
//   ② 规则正确：无 lectureIndex 时 ck 必须逐字等于 sourceUrl——这是存量云端
//      计数零迁移的前提（90% 的讲座是单场，键不变才能继续读到旧数据）；
//   ③ 模板不得再用 u.url / l.sourceUrl 走计数调用（防回潮；href 用真 URL 是合法的）。
//
// 运行：node tests/js/count_key_uniqueness.js
const fs = require('fs');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');
const errs = [];

// ---------- ① 分片数据：ck 存在且全局唯一 ----------
const chunkDir = path.join(SITE, 'lectures');
const chunks = fs.readdirSync(chunkDir)
  .filter(f => /^chunk_\d+\.json$/.test(f))
  .sort();
if (!chunks.length) errs.push('site/lectures/ 下无 chunk_*.json（未出片？）');

const seen = new Map();   // ck -> sourceUrl
let total = 0;
let missing = 0;
let ruleBad = 0;
for (const f of chunks) {
  const rows = JSON.parse(fs.readFileSync(path.join(chunkDir, f), 'utf-8'));
  const list = Array.isArray(rows) ? rows : (rows.data || []);
  for (const r of list) {
    total++;
    const ck = r.ck;
    if (!ck) { missing++; continue; }
    // ② 规则：单场 ck === sourceUrl；多场 ck === sourceUrl~lectureIndex
    //    分隔符锁定为 `~`（URL 无保留字符）。曾误用 `#`：PostgREST 的
    //    url=eq.<键> 会把 # 后内容当片段丢弃 → 写入与查询落到不同的行。
    const want = (r.lectureIndex === undefined || r.lectureIndex === null)
      ? (r.sourceUrl || '')
      : `${r.sourceUrl}~${r.lectureIndex}`;
    if (ck !== want) ruleBad++;
    const prev = seen.get(ck);
    if (prev !== undefined) {
      errs.push(`ck 重复：${ck}（出现于 ${prev} 与 ${r.sourceUrl}）——`
                + '同一源页的多场键未分开，统计页会把一次点赞累加多份');
    } else {
      seen.set(ck, r.sourceUrl);
    }
  }
}
if (total === 0) errs.push('分片数据为空（扫到 0 条，门禁等价于没跑）');
if (missing) errs.push(`${missing} 条讲座缺 ck 字段`);
if (ruleBad) errs.push(`${ruleBad} 条讲座的 ck 与规则不符（单场须等于 sourceUrl，多场须为 sourceUrl#序号）`);

// ---------- ② 统计索引：k 与 ck 同规则且唯一 ----------
const statsPath = path.join(chunkDir, 'stats.json');
if (fs.existsSync(statsPath)) {
  const st = JSON.parse(fs.readFileSync(statsPath, 'utf-8'));
  const L = st.lectures || [];
  const ks = new Set();
  let kMissing = 0;
  L.forEach(x => {
    if (!x.k) { kMissing++; return; }
    ks.add(x.k);
  });
  if (kMissing) errs.push(`stats.json 索引有 ${kMissing} 条缺 k 字段`);
  if (ks.size !== L.length) {
    errs.push(`stats.json 的 k 不唯一：${ks.size} / ${L.length}——统计页会重复累加`);
  }
} else {
  errs.push('site/lectures/stats.json 不存在（统计页索引缺失）');
}

// ---------- ③ 模板防回潮：计数调用不得再用裸 URL ----------
// href / safeUrl 用真网址是合法的，故只禁计数类调用。
const html = fs.readFileSync(path.join(SITE, 'index.html'), 'utf-8');
for (const [label, bad] of [
  ['recordVisit', /recordVisit\((?:u\.url|l\.sourceUrl)\)/],
  ['toggleLike', /toggleLike\((?:u\.url|l\.sourceUrl)/],
  ['toggleWant', /toggleWant\((?:u\.url|l\.sourceUrl)/],
  ['hasLiked/hasWanted', /has(?:Liked|Wanted)\((?:u\.url|l\.sourceUrl)\)/],
  ['likeCount/wantCount', /(?:like|want)Count\((?:u\.url|l\.sourceUrl)\)/],
]) {
  if (bad.test(html)) {
    errs.push(`index.html 仍在用裸 URL 走${label}——计数键须用 ck（讲座级）`);
  }
}

// ---------- ④ 分隔符必须 URL 安全 ----------
// 多场键形如 `源页URL~序号`。若分隔符是 URL 保留字符（# ? & = + / : 等），
// PostgREST 的 `url=eq.<键>` 会把它当语法吃掉、静默落到别的行上。
// 实测过的反例：`#` → 写入 a.html#1，查询返回的却是 a.html 那一行。
const UNSAFE = /[#?&=+/:%@[\]]/;
let unsafeSep = 0;
seen.forEach((_src, ck) => {
  const tail = ck.split('/').pop() || '';
  const m = tail.match(/^(.+\.\w+)(.)(.+)$/);   // 文件名 ~ 序号
  if (m && UNSAFE.test(m[2])) unsafeSep++;
});
if (unsafeSep) {
  errs.push(`${unsafeSep} 个计数键用了 URL 保留字符作分隔符——`
            + 'PostgREST 查询会静默截断，须改用 ~ 等无保留字符');
}

console.log(`讲座 ${total} 条 · 唯一 ck ${seen.size} · 分片 ${chunks.length}`);
if (errs.length) {
  console.log('[FAIL]');
  errs.forEach(e => console.log(' -', e));
  process.exit(1);
}
console.log('[PASS] 计数键：全量唯一、规则正确、模板无裸 URL 调用');
