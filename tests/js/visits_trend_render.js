// 门禁：站点访问量趋势报告（site/visits-trend.html）的渲染管线与 CSP 收紧（2026-10-02）。
//
// 背景：① 该页此前用 innerHTML 字符串拼接渲染 SVG 与表格，而快照的 note 是唯一
//   来自台账的自由文本（经 json.dumps 内嵌进内联脚本），含 </script> 或
//   <img onerror=…> 即构成存储型 XSS → 已全部改为 createElement/textContent。
// ② 脚本与数据整体内联，迫使 CSP 放行 script-src 'unsafe-inline' → 已拆为
//   独立 visits-trend.js / visits-trend-data.json / visits-trend.css。
//
// 本门禁锁四件事：静态无 innerHTML、CSP 无 unsafe-inline、外链齐全、DOM 渲染仍正常。
//
// 运行：node tests/js/visits_trend_render.js（纯 node + vm，无浏览器无网络）
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SITE = path.join(__dirname, '..', '..', 'site');
const html = fs.readFileSync(path.join(SITE, 'visits-trend.html'), 'utf-8');
const errs = [];

// ---------- ① 静态：不得再有 innerHTML 赋值 ----------
// 2026-10-18 起明细表已删「备注」列，note 不再进 DOM；禁令保留防回潮——
// 谁把 note 重新渲染出来，这条就是第一道闸（且 payload 里 note 仍在）。
if (/\.innerHTML\s*=/.test(html)) {
  const at = html.search(/\.innerHTML\s*=/);
  const line = html.slice(0, at).split('\n').length;
  errs.push(`visits-trend.html:${line} 仍用 .innerHTML = 渲染 —— `
            + '台账 note 是自由文本，须走 createElement/textContent');
}

// ---------- ② CSP 必须已去掉 unsafe-inline ----------
const cspM = html.match(/Content-Security-Policy"\s+content="([^"]+)"/);
if (!cspM) {
  errs.push('页面无 CSP meta');
} else if (/script-src[^;]*unsafe-inline/.test(cspM[1])) {
  errs.push('CSP 的 script-src 仍含 unsafe-inline —— 脚本应已拆为独立 .js');
} else if (/style-src[^;]*unsafe-inline/.test(cspM[1])) {
  errs.push('CSP 的 style-src 仍含 unsafe-inline —— 样式应已拆为独立 .css');
}

// ---------- ③ 外链齐全，且页面自身无内联脚本/样式 ----------
for (const ref of ['visits-trend.js', 'visits-trend.css']) {
  if (!html.includes(ref)) errs.push(`页面未引用 ${ref}`);
  if (!fs.existsSync(path.join(SITE, ref))) errs.push(`${ref} 不存在（未随页面出片）`);
}
if (!fs.existsSync(path.join(SITE, 'visits-trend-data.json'))) {
  errs.push('visits-trend-data.json 不存在');
}
if (/<script>/.test(html)) errs.push('页面仍有内联 <script>（无 src）');
if (/<style[\s>]/.test(html)) errs.push('页面仍有内联 <style>');

// ---------- ⑤ 页面导航闭环（2026-10-08 补） ----------
// 背景：本页此前**没有任何**返回链接——首页(index.html)菜单里有「讲座统计/
// 访问量趋势」，stats.html 顶部也有「访问量趋势/返回首页」，唯独本页面是死胡同，
// 手机上（无后退键可见）用户只能自己改地址栏。此处锁住两个返回入口，
// 防止后续重构把导航又删掉。
// 2026-10-22：入口文案与顺序与 stats.html 统一（另一个页面在前、返回首页在后）。
// 原为「返回首页 / 去统计页」且导航在左上角，与统计页右上角的写法不一致。
const needNav = [['stats.html', '讲座统计'], ['./', '返回首页']];
for (const [href, label] of needNav) {
  const re = new RegExp(`<a[^>]+href=["']${href.replace('.', '\\.')}["'][^>]*>\\s*${label}\\s*</a>`);
  if (!re.test(html)) {
    errs.push(`页面缺少「${label}」导航链接（href=${href}）——`
              + '三页导航闭环：首页 ↔ 统计页 ↔ 趋势页');
  }
}
// 导航样式须走外链 CSS（CSP 的 style-src 为 'self'，不允许内联 style）
if (/<nav class="topnav"[^>]*\sstyle=/.test(html)) {
  errs.push('导航用了内联 style —— style-src 为 \'self\'，样式须写在 visits-trend.css');
}
const css = fs.readFileSync(path.join(SITE, 'visits-trend.css'), 'utf-8');
if (!/\.topnav\b/.test(css)) errs.push('visits-trend.css 缺 .topnav 样式（导航会无样式）');
// 2026-10-22：三页导航位置统一——趋势页原先左对齐（内容区左上角），而统计页的
// 入口在 header 右侧。此处锁住右对齐，防止后续改样式时又退回左上角。
if (!/\.topnav\{[^}]*justify-content:flex-end/.test(css)) {
  errs.push('趋势页导航未右对齐（.topnav 缺 justify-content:flex-end）——'
            + '与统计页右上角的入口位置不一致');
}

// ---------- ⑥ 产物必须与生成脚本模板一致（2026-10-09 补） ----------
// 背景：site/visits-trend.{html,css} 都是 scripts/gen_visits_trend.py 的**产物**
// （main() 里 _write(OUT_PATH/CSS)），每次 CI 跑该脚本都会被整体重写。
// 2026-10-08 直接改了产物文件，当日部署成功，**次日 10-09 的自动提交就把导航覆盖
// 掉了**，公网随之失效——改动落错了地方，且没有任何报错。此处锁住「模板里有」：
// 模板缺导航 → 产物迟早被覆盖 → 门禁提前报红。
const genSrc = fs.readFileSync(
  path.join(SITE, '..', 'scripts', 'gen_visits_trend.py'), 'utf-8');
for (const [label, needle] of [['讲座统计', '<a href="stats.html">讲座统计</a>'],
                               ['返回首页', '<a href="./">返回首页</a>'],
                               ['.topnav 样式', '.topnav{'] /* CSS 常量内 */]) {
  if (!genSrc.includes(needle)) {
    errs.push(`scripts/gen_visits_trend.py 模板缺「${label}」——`
              + '直接改 site/ 产物会被下一次 CI 覆盖（10-08 → 10-09 已发生过一次）');
  }
}

// ---------- ④ 行为：用最小 DOM 桩跑一遍渲染 ----------
// 数据改为 fetch 载入，故桩里提供可控的 fetch（返回本地 JSON 文件内容）。
const js = fs.readFileSync(path.join(SITE, 'visits-trend.js'), 'utf-8');
const data = JSON.parse(
  fs.readFileSync(path.join(SITE, 'visits-trend-data.json'), 'utf-8'));

// ---------- ⑦ UV 已下线，不得回潮（2026-10-18） ----------
// 计数源迁到自建 Supabase 后 UV（独立访客数）无法复现（原依赖 busuanzi 服务端
// 按 IP 去重），已从趋势页全部移除：明细表删掉「累计访客数 / 日新增访客」两列，
// 图表主轴改为访问次数。此处锁住「展示层不再读 UV 字段」——页面若重新消费
// site_uv/uv_delta，它们在新快照里恒为 null，表格会变成整列「—」的死数据。
// 注：页脚说明里解释「为什么不统计访客数」是允许的，故只禁字段引用与表头列名。
for (const [label, src, bad] of [
  ['趋势页 HTML 表头', html, /<th>(累计|日新增)访客/],
  ['visits-trend.js 渲染', js, /s\.site_uv|s\.uv_delta/],
  ['生成脚本模板', genSrc, /fmt\(s\.site_uv\)|fmt\(s\.uv_delta\)|<th>累计访客数/],
]) {
  if (bad.test(src)) errs.push(`${label} 仍在体现 UV（${bad}）——UV 已于 2026-10-18 下线`);
}

// ---------- ⑦b 明细表恰三列，「备注」列不得回潮（2026-10-18） ----------
// 应用户要求删「备注」列（内容是一次性事件流水，无决策价值）；note 字段仍
// 留在台账与 payload 存档。锁四处：产物表头列数、产物无备注字样、模板表头、
// JS 渲染侧无备注单元格。放在此处是为了让 genSrc/js 已定义（避开 TDZ）。
{
  // ⚠ `<th(?:\s[^>]*)?>` 不能写 `<th[^>]*>`——后者会把 `<thead>` 也匹配上
  // （th 是 thead 前缀，实测 3 列被数成 4 列；断言必须自带边界）。
  const ths = [...html.matchAll(/<th(?:\s[^>]*)?>/g)].map(m => m[0]);
  if (ths.length !== 3) {
    errs.push(`明细表应恰 3 列（日期/累计访问次数/日新增访问），实得 ${ths.length}`
              + '——「备注」列回潮了？');
  }
  if (/备注/.test(html)) errs.push('产物 HTML 仍含「备注」字样——该列 2026-10-18 已删');
  if (/<th[^>]*>备注/.test(genSrc)) {
    errs.push('scripts/gen_visits_trend.py 模板仍有「备注」表头——直接改产物会被 CI 覆盖');
  }
  if (/el\('td'[^)]*'l'\)|el\('span'[^)]*'note'\)/.test(js)) {
    errs.push('visits-trend.js 仍在渲染备注单元格/span——删列应落到 JS 侧');
  }
  // 页脚说明只保留一行（用户 2026-10-18 明确要求），锁「无 <br> 堆叠」语义
  const footM = html.match(/<div class="foot">([\s\S]*?)<\/div>/);
  if (!footM) {
    errs.push('页面缺 .foot 说明块');
  } else if (/<br/i.test(footM[1])) {
    errs.push('.foot 又变回多行长文——应只保留一行口径说明');
  }
}

function mkNode(tag) {
  return {
    tagName: tag, children: [], attrs: {}, _text: '', className: '',
    style: {}, hidden: false,
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {},
    get firstChild() { return this.children[0] || null; },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this.children = []; },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { this.children = this.children.filter(x => x !== c); },
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    appendChildNS(ns, t) { const n = mkNode(t); this.children.push(n); return n; },
    querySelectorAll() { return []; },
  };
}
const byId = {};
['chart', 'tbody', 'tcount', 'toggleRows', 'load-err'].forEach(id => { byId[id] = mkNode('div'); });

const sandbox = {
  console,
  document: {
    getElementById: id => byId[id] || null,
    createElement: mkNode,
    createElementNS: (ns, t) => mkNode(t),
    createDocumentFragment: () => mkNode('#fragment'),
    createTextNode: t => ({ nodeType: 3, textContent: String(t) }),
    querySelectorAll: () => [],
    addEventListener() {},
    documentElement: { style: {} },
  },
  window: { addEventListener() {} },
  requestAnimationFrame: () => 0,
  fetch: () => Promise.resolve({
    ok: true, status: 200, json: () => Promise.resolve(data),
  }),
  Date, Math, Number, String, JSON, Array, Object, parseInt, parseFloat,
  isNaN, Set, Map, Infinity, NaN, Promise,
};
vm.createContext(sandbox);

(async () => {
  try {
    vm.runInContext(js, sandbox, { filename: 'visits-trend.js' });
    // 数据是 fetch 异步载入，等微任务队列排空后再断言
    for (let i = 0; i < 5; i++) await Promise.resolve();
  } catch (e) {
    console.log('[FAIL] 脚本执行异常：' + e.message);
    process.exit(1);
  }

  const chart = byId['chart'].children;
  const svgCount = chart.filter(c => c.tagName === 'svg').length;
  const capCount = chart.filter(c => c.tagName === 'p').length;
  const trCount = byId['tbody'].children.length;

  if (byId['load-err'].hidden === false && byId['load-err']._text) {
    errs.push('渲染走入了错误分支：' + byId['load-err']._text);
  }
  if (svgCount !== 1) errs.push(`应渲染恰好 1 个 <svg>，实得 ${svgCount}`);
  if (capCount !== 1) errs.push(`应渲染恰好 1 个说明段 <p>，实得 ${capCount}`);
  if (trCount <= 0) errs.push(`表格应至少 1 行，实得 ${trCount}`);
  if (svgCount === 1) {
    const svg = chart.find(c => c.tagName === 'svg');
    if (svg.children.length === 0) {
      errs.push('svg 内无任何图形元素——DOM 化时可能漏了 appendChild');
    }
  }

  console.log(`svg=${svgCount} 说明段=${capCount} 表格行=${trCount} `
              + `tcount=${byId['tcount']._text}`);
  if (errs.length) {
    console.log('[FAIL]');
    errs.forEach(e => console.log(' -', e));
    process.exit(1);
  }
  console.log('[PASS] 趋势报告：无 innerHTML、CSP 已收紧、外链齐全、渲染正常');
})();