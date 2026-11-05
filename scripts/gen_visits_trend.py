# -*- coding: utf-8 -*-
"""生成「站点访问量趋势」报告（自包含单文件 HTML）。

数据来源：
    data/visits_history.json —— 由 scripts/fetch_visits_snapshot.py 每日在 CI 中
    从 Supabase 取快照累积而成（GET 纯读）。这是公网访问量的**唯一本地备份**。

口径迁移（2026-10-18，勿混淆）：
    - 2026-09-10 ~ 2026-10-17：busuanzi.aspark.cc，含 PV 与 UV（独立访客）。
    - 现行：自建 Supabase 只有 PV。UV 依赖 busuanzi 服务端按 IP 去重，
      自建无法复现 → **本页已不再体现 UV**（页面文案、图表、明细表全部移除；
      台账里的历史 UV 字段保留作存档，但不再出片展示）。
      图表主轴为「累计访问次数」（台账历史 PV 数据完整，曲线全程连续无断点）。
      种子 1730 = busuanzi 切换时刻实时值，两段口径无缝衔接。

与旧的 gen_visits_report.py 的区别（勿混淆）：
    gen_visits_report.py  ← data/visits.json（**本地 localhost** 访问，server.py 写的）
    本脚本                ← data/visits_history.json（**公网**访问，CI 取的）

输出：
    site/visits-trend.html（2026-09-30 起从 reports/ 移入 site/）
    site/visits-trend.js（2026-10-02 拆出）+ site/visits-trend-data.json
    - **公网可见**：GitHub Pages 发布 site/ 目录，线上地址
      https://danchekey.github.io/lecture-aggregator/visits-trend.html
      （用户要求在公网直接看访问量趋势，故随 site/ 入库、随 Pages 部署）。
    - 2026-10-02 变更：脚本与数据**从内联拆为独立文件**。此前整页 JS（含数据）
      都在 <script> 里，迫使 CSP 放行 script-src 'unsafe-inline'；拆分后
      script-src 可收紧为 'self'。代价是**不再支持 file:// 直接打开**
      （fetch 受同源限制）——但该页的唯一用途是公网经 Pages 查看，
      本地预览用 start_local.bat 起 server 即可。数据仅约 3KB（gzip 691B），
      多一次请求的代价可忽略。
    - 报告页面无 CSP meta，内联 SVG/JS 可直接运行；唯一外链是回主站的 <a>。

图表设计（2026-10-18 改版：主轴 UV → PV；同日彻底移除 UV）：
    手绘内联 SVG 双轴组合图 —— 累计访问次数折线（左轴，按数据 min/max 自适应）
    + 每日新增访问柱（右轴）。缺失快照的日期保留在横轴上，不假装连续。
    范围切换与表格展开由前端 JS 完成。明细表仅 日期 / 累计访问次数 /
    日新增访问 三列（2026-10-18 应用户要求删「备注」列——该列内容是一次性
    事件流水，对读者无决策价值；note 字段仍留在台账与 payload 里存档，
    只是不再出片）。UV 相关列已删除。

用法：
    python scripts/gen_visits_trend.py
"""
import os
import json
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(ROOT, 'data', 'visits_history.json')
OUT_DIR = os.path.join(ROOT, 'site')
OUT_PATH = os.path.join(OUT_DIR, 'visits-trend.html')
# 2026-10-02 拆出：脚本 / 数据 / 样式各自成文件，使页面 CSP 能去掉 'unsafe-inline'。
# 命名与 index.html 的做法一致（站点根目录相对引用，随 site/ 一起部署）。
JS_NAME = 'visits-trend.js'
DATA_NAME = 'visits-trend-data.json'
CSS_NAME = 'visits-trend.css'
JS_PATH = os.path.join(OUT_DIR, JS_NAME)
DATA_PATH = os.path.join(OUT_DIR, DATA_NAME)

# 样式与脚本单独放常量：避免大括号与 f-string 冲突
CSS = """
/* 防点击劫持预隐藏（2026-10-14 P1-1）：与 index.html/stats.html 同一方案，
   但落点不同——本页 CSP 是 style-src 'self'（2026-10-02 收紧时禁掉
   'unsafe-inline'），head 内联 <style> 会被浏览器拒绝执行，故规则必须放
   本外链 CSS。<link> 是渲染阻塞资源，先于首帧生效，与内联 <style> 等效。
   还原动作在 visits-trend.js 顶部 IIFE（defer，DOM 解析后执行）确认顶层
   访问后移除 class。⚠ 三处拷贝（app.core.js/stats.js/visits-trend.js 的
   IIFE）与三处 .pre-hidden 规则改动须同步。 */
.pre-hidden{display:none}
*{box-sizing:border-box}
body{margin:0;padding:0;background:#f7f8fa;color:#2C2C2A;
  font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;
  font-size:15px;line-height:1.7;-webkit-font-smoothing:antialiased}
/* 顶部站点栏（2026-10-22）：与 index.html / stats.html 同一套视觉——白底、
   底部 1px 分隔线、左侧校徽+站名、右侧页面导航。此前本页没有站点栏，导航
   直接浮在灰色页面背景上，与另两页明显脱节（用户截图反馈「表头跟其他页面
   风格不搭」）。
   宽度取 1000px（= 960 内容 + 左右各 20 padding），使 logo 与正文左边缘对齐。 */
.sitebar{background:#fff;border-bottom:1px solid #E2E8F0}
.sitebar-in{max-width:1000px;margin:0 auto;padding:0 20px;height:56px;
  display:flex;align-items:center;gap:12px}
.brand{display:flex;align-items:center;gap:8px;flex-shrink:0;text-decoration:none}
.brand .emblem{width:36px;height:36px;border-radius:8px;background:#fff;
  box-shadow:0 1px 2px rgba(0,0,0,.05)}
.brand .wordmark{height:32px;width:auto;object-fit:contain}
.wrap{max-width:1000px;margin:0 auto;padding:24px 20px 48px}
h1{font-size:22px;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}
/* 顶部导航（2026-10-08 补，2026-10-09 修正落点；2026-10-22 统一位置与样式）：
   本页此前无任何返回链接，是首页/统计页/趋势页三页里唯一的死胡同。
   2026-10-22 起与 stats.html 对齐：右对齐到右上角、纯文字链接（去掉早期的胶囊
   按钮与边框），顺序统一为「另一个页面 → 返回首页」。
   字号/间距同 stats.html 的 text-12pt（12pt=16px）+ gap-2（8px）——此前本页写
   13.5px/16px，并排看会比统计页的导航明显小一号（用户截图反馈）。
   与 HTML 一并落在本模板常量里——site/visits-trend.css 同为本脚本产物
   （main() 里 _write(css, CSS)），直接改产物会被下一次 CI 覆盖。 */
.topnav{display:flex;align-items:center;gap:8px;margin:0 0 0 auto}
.topnav a{font-size:12pt;color:#475569;text-decoration:none}
.topnav a:hover{color:#1E293B}
.sub{color:#888780;font-size:13.5px;margin:0}
.wrap header{margin-bottom:24px}
.subline{color:#B4B2A9;font-size:13px;margin:4px 0 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));
  gap:12px;margin-bottom:22px}
.kpi{background:#F1F0EC;border-radius:10px;padding:15px 17px}
.kpi-k{font-size:13.5px;color:#888780;margin-bottom:6px}
.kpi-v{font-size:30px;font-weight:600;line-height:1.2;letter-spacing:-.02em}
.kpi-d{font-size:13px;color:#B4B2A9;margin-top:5px}
.card{background:#fff;border:1px solid #E8E6E0;border-radius:12px;
  padding:18px 20px 16px;margin-bottom:22px}
.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.ranges{display:flex;gap:8px}
.ranges button{font:inherit;font-size:14px;padding:5px 17px;border-radius:999px;
  border:1px solid #E1E0DA;background:#fff;color:#5F5E5A;cursor:pointer}
.ranges button:hover{border-color:#C9C7BE}
.ranges button.on{background:#E6F1FB;color:#185FA5;border-color:#B5D4F4}
/* 移动端（2026-10-29）：浅蓝底+中蓝字在手机反光/小字号下对比度不足，
   「全部」等选中标签文字发白看不清。仅在小屏把选中态改为实心深蓝底+白字加粗，
   对比度拉满；桌面端保持原浅蓝风格不变。 */
@media (max-width:640px){
  .ranges button.on{background:#185FA5;color:#fff;border-color:#185FA5;font-weight:600}
}
.legend{margin-left:auto;display:flex;gap:16px;font-size:13.5px;color:#888780}
.legend i{display:inline-block;vertical-align:middle;margin-right:6px}
.sw-line{width:16px;height:2.5px;background:#185FA5;border-radius:2px}
.sw-bar{width:10px;height:10px;background:#C9DDF3;border-radius:3px}
.cap{color:#888780;font-size:13.5px;margin:12px 0 0}
.cap b{color:#5F5E5A;font-weight:600}
.empty{color:#888780;padding:28px 0;text-align:center}
.tb-head{display:flex;align-items:center;gap:12px;margin:0 0 12px}
h2{font-size:16px;font-weight:600;margin:0}
.tb-head button{margin-left:auto;font:inherit;font-size:14px;padding:5px 14px;
  border-radius:8px;border:1px solid #E1E0DA;background:#fff;color:#5F5E5A;cursor:pointer}
.tb-head button:hover{border-color:#C9C7BE;background:#FAFAF8}
.tbl{max-height:560px;overflow:auto;border:1px solid #E8E6E0;border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:15px}
/* 2026-10-22：三列全部居中（此前日期左对齐、两列数字右对齐，中间留出大片
   空白，用户反馈「表中间空的地方太多」）。日期列是等宽字符，居中后纵向仍对齐。 */
th,td{padding:11px 15px;text-align:center;border-bottom:1px solid #F1F0EC;white-space:nowrap}
th{position:sticky;top:0;background:#FAFAF8;font-weight:600;color:#5F5E5A;font-size:14px;z-index:1}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:#FCFCFB}
.foot{margin-top:28px;color:#888780;font-size:13.5px;line-height:1.8}
.foot b{color:#5F5E5A;font-weight:600}
/* 底部信息栏（2026-10-28）：与 stats.html footer 同形式——全宽一条、居中、
   16px（= stats 的 text-12pt）、slate-400(#94a3b8) 文字。footer 标签上的
   Tailwind 类名在本页无效（不加载 Tailwind），仅作 theme-scnu.css 钩子；
   默认主题的真实样式全靠本规则。VI 模式由 theme-scnu.css 的
   footer.bg-white 覆盖为深藏青底白字。 */
.site-foot{
  border-top:1px solid #E8E6E0;background:#fff;text-align:center;
  font-size:16px;line-height:1.7;color:#94a3b8;padding:24px 16px}
"""

# 2026-10-02：JS 不再内联数据（META/SNAPS 改由 fetch 载入同目录的
# visits-trend-data.json）。目的是让页面能去掉 CSP 的 script-src 'unsafe-inline'。
JS = """'use strict';
// 防点击劫持（2026-10-14 P1-1）：与 index.html 的 app.core.js、stats.html 的
// stats.js 顶部 IIFE 是同款逻辑的**三份同源拷贝**之一，改动须三处同步。
// 公网 GitHub Pages 无法下发 X-Frame-Options / CSP frame-ancestors 响应头
// （meta 里的 frame-ancestors 会被浏览器忽略），故用「预隐藏 class + 顶层
// 放行」兜底：html.pre-hidden 由 visits-trend.css（渲染阻塞，先于首帧）
// 置为 display:none，被嵌入时页面不可见、不可点；顶层访问才移除还原。
// ⚠ 本 IIFE 必须位于 'use strict' **之后**：严格模式指令只对脚本首条语句
// 生效，若把 IIFE 提前，'use strict' 会沦为函数内的普通字符串表达式。
(function () {
  if (window.top === window.self) {
    var root = typeof document !== 'undefined' && document.documentElement;
    if (root && root.classList) root.classList.remove('pre-hidden');
    return;
  }
  try { window.top.location.replace(window.self.location); } catch (e) {}
})();
var META = {};
var SNAPS = [];

var L = 58, R = 636, T = 26, B = 206;
var RANGE = 7, EXPANDED = false;

function dn(s) {
  var a = s.split('-');
  return Date.UTC(+a[0], +a[1] - 1, +a[2]) / 86400000;
}
function iso(n) {
  return new Date(n * 86400000).toISOString().slice(0, 10);
}
function fmt(v) {
  if (v === null || v === undefined || v === '') return '—';
  return Number(v).toLocaleString('en-US');
}
function inRange(snaps, r) {
  if (!r || snaps.length < 2) return snaps.slice();
  var last = dn(snaps[snaps.length - 1].date);
  return snaps.filter(function (s) { return last - dn(s.date) <= r - 1; });
}
function niceTicks(lo, hi, seg) {
  var span = hi - lo;
  if (!(span > 0)) span = 1;
  var raw = span / seg;
  var mag = Math.pow(10, Math.floor(Math.log10(raw)));
  var n = raw / mag;
  var step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
  if (!(step > 0)) step = 1;
  var a = Math.floor(lo / step) * step;
  var b = Math.ceil(hi / step) * step;
  var out = [];
  for (var v = a; v <= b + step * 1e-6; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

// 文本节点安全写入：2026-10-02 起不再用 innerHTML 拼接用户可控文本。
// 快照的 note 字段（来自台账）仍随 payload 内嵌进页面数据，但 2026-10-18 起
// 明细表已删「备注」列，note 不再进 DOM——保留 el() 的 textContent 语义与
// json.dumps 的 </script> 转义，防未来重新展示 note 时直接踩存储型 XSS。
// 数值字段走 fmt()（toLocaleString 产物）且统一经 _num() 归一，非字符串不可注入。
function el(tag, text, cls) {
  var n = document.createElement(tag);
  if (text !== undefined && text !== null) n.textContent = String(text);
  if (cls) n.className = cls;
  return n;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function renderChart() {
  var box = document.getElementById('chart');
  var snaps = inRange(SNAPS, RANGE);
  if (!snaps.length) {
    clear(box);
    box.appendChild(el('div', '该时间范围内没有快照记录。', 'empty'));
    return;
  }
  if (snaps.length === 1) {
    var s0 = snaps[0];
    clear(box);
    var e = el('div', null, 'empty');
    e.appendChild(document.createTextNode(
      '仅 1 条快照（' + s0.date + '）：累计访问次数 ' + fmt(s0.site_pv) + ' 次。'));
    e.appendChild(el('br'));
    e.appendChild(document.createTextNode('至少需要 2 条快照才能绘制趋势线。'));
    box.appendChild(e);
    return;
  }

  var t0 = dn(snaps[0].date);
  var t1 = dn(snaps[snaps.length - 1].date);
  var span = Math.max(t1 - t0, 1);
  var pw = R - L, ph = B - T;
  function xOf(s) { return L + (dn(s.date) - t0) / span * pw; }

  // 2026-10-18 改版：主轴从「累计访客数(UV)」换为「累计访问次数(PV)」——
  // 计数源迁到自建 Supabase 后 UV 无法复现（曾依赖 busuanzi 服务端 IP 去重），
  // 而台账历史 PV 数据完整，曲线全程连续。UV 已从本页彻底移除（含明细表列）。
  var pvs = snaps.map(function (s) { return Number(s.site_pv) || 0; });
  var yT = niceTicks(Math.min.apply(null, pvs), Math.max.apply(null, pvs), 3);
  var yLo = yT[0], yHi = yT[yT.length - 1];
  function yL(v) { return B - (v - yLo) / ((yHi - yLo) || 1) * ph; }

  var dmax = Math.max.apply(null, snaps.map(function (s) {
    return Math.max(0, Number(s.pv_delta) || 0);
  }));
  var dT = niceTicks(0, dmax || 1, 2);
  var dHi = dT[dT.length - 1] || 1;
  function yR(v) { return B - v / dHi * ph; }

  var SVGNS = 'http://www.w3.org/2000/svg';
  function sv(tag, attrs, text) {
    var n = document.createElementNS(SVGNS, tag);
    for (var k in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) n.setAttribute(k, attrs[k]);
    }
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  var g = document.createDocumentFragment();

  yT.forEach(function (v) {
    var y = yL(v);
    g.appendChild(sv('line', { x1: L, y1: y.toFixed(1), x2: R, y2: y.toFixed(1),
                               stroke: (v === yLo ? '#D3D1C7' : '#E8E6E0'),
                               'stroke-width': 1 }));
    g.appendChild(sv('text', { x: L - 10, y: (y + 4).toFixed(1), 'font-size': 12,
                               fill: '#888780', 'text-anchor': 'end' }, fmt(v)));
  });
  dT.forEach(function (v) {
    if (v === 0) return;
    var y = yR(v);
    g.appendChild(sv('text', { x: R + 10, y: (y + 4).toFixed(1), 'font-size': 12,
                               fill: '#B4B2A9', 'text-anchor': 'start' }, fmt(v)));
  });

  var bw = Math.min(36, Math.max(1.5, pw / snaps.length * 0.7));
  snaps.forEach(function (s) {
    var v = Math.max(0, Number(s.pv_delta) || 0);
    if (!v) return;
    var x = xOf(s) - bw / 2, y = yR(v), h = B - y;
    var rect = sv('rect', { x: x.toFixed(1), y: y.toFixed(1), width: bw.toFixed(1),
                            height: h.toFixed(1), rx: 3, fill: '#C9DDF3' });
    rect.appendChild(sv('title', {}, s.date + ' 新增访问 ' + v + ' 次'));
    g.appendChild(rect);
  });

  g.appendChild(sv('polyline', {
    points: snaps.map(function (s) {
      return xOf(s).toFixed(1) + ',' + yL(Number(s.site_pv) || 0).toFixed(1);
    }).join(' '),
    fill: 'none', stroke: '#185FA5', 'stroke-width': 2.5,
    'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));

  var showVal = snaps.length <= 8;
  snaps.forEach(function (s, i) {
    var x = xOf(s), y = yL(Number(s.site_pv) || 0);
    var c = sv('circle', { cx: x.toFixed(1), cy: y.toFixed(1), r: 4,
                           fill: '#ffffff', stroke: '#185FA5', 'stroke-width': 2.5 });
    c.appendChild(sv('title', {}, s.date + ' 累计访问 ' + fmt(s.site_pv) + ' 次'));
    g.appendChild(c);
    if (showVal) {
      var anchor = 'middle', lx = x;
      if (i === 0) { anchor = 'start'; lx = x + 9; }
      else if (i === snaps.length - 1) { anchor = 'end'; lx = x - 9; }
      g.appendChild(sv('text', { x: lx.toFixed(1), y: (y - 12).toFixed(1), 'font-size': 12,
                                 fill: '#185FA5', 'text-anchor': anchor },
                       fmt(s.site_pv)));
    }
  });

  var tk = [];
  if (span <= 10) {
    for (var tDay = t0; tDay <= t1 + 1e-6; tDay++) tk.push(tDay);
  } else if (span <= 45) {
    for (var tWeek = t0; tWeek <= t1 + 1e-6; tWeek += 7) tk.push(tWeek);
    if (tk[tk.length - 1] < t1 - 1e-6) tk.push(t1);
  } else {
    var keyLen = span <= 400 ? 7 : 4;
    var seenKey = {};
    snaps.forEach(function (s) {
      var k = s.date.slice(0, keyLen);
      if (!seenKey[k]) { seenKey[k] = 1; tk.push(dn(s.date)); }
    });
  }
  tk.forEach(function (t) {
    var x = L + (t - t0) / span * pw;
    var has = snaps.some(function (s) { return Math.abs(dn(s.date) - t) < 0.5; });
    var lab = span <= 45 ? iso(t).slice(5) : span <= 400 ? iso(t).slice(0, 7) : iso(t).slice(0, 4);
    g.appendChild(sv('text', { x: x.toFixed(1), y: B + 22, 'font-size': 12,
                               fill: (has ? '#5F5E5A' : '#B4B2A9'),
                               'text-anchor': 'middle' }, lab));
  });

  var missing = [];
  for (var d = t0; d <= t1 + 1e-6; d++) {
    if (!snaps.some(function (s) { return Math.abs(dn(s.date) - d) < 0.5; })) missing.push(iso(d));
  }
  var missTxt = '';
  if (missing.length) {
    missTxt = ' · 无快照：' + (missing.length <= 4
      ? missing.join('、')
      : missing.slice(0, 3).join('、') + ' 等 ' + missing.length + ' 天');
  }

  clear(box);
  var svg = sv('svg', { viewBox: '0 0 680 250', width: '100%', role: 'img',
                         'aria-label': '累计访问次数折线与每日新增访问柱状组合趋势图' });
  svg.appendChild(sv('title', {}, '站点访问量趋势'));
  svg.appendChild(sv('desc', {}, snaps[0].date + ' 至 '
    + snaps[snaps.length - 1].date + ' 的累计访问次数折线与每日新增访问柱状图。'));
  svg.appendChild(g);
  box.appendChild(svg);

  var cap = el('p', null, 'cap');
  cap.appendChild(document.createTextNode('覆盖 '));
  cap.appendChild(el('b', span + 1));
  cap.appendChild(document.createTextNode(' 天 · 快照 '));
  cap.appendChild(el('b', snaps.length));
  cap.appendChild(document.createTextNode(' 条 · 左轴累计访问次数，右轴每日新增访问'
    + missTxt));
  box.appendChild(cap);
}

function renderTable() {
  var all = inRange(SNAPS, RANGE);
  var rows = all.slice().reverse();
  var limit = EXPANDED ? rows.length : Math.min(rows.length, 7);
  var tb = document.getElementById('tbody');
  clear(tb);
  rows.slice(0, limit).forEach(function (s) {
    var tr = el('tr');
    tr.appendChild(el('td', s.date));
    tr.appendChild(el('td', fmt(s.site_pv)));
    tr.appendChild(el('td', fmt(s.pv_delta)));
    tb.appendChild(tr);
  });
  var btn = document.getElementById('toggleRows');
  if (rows.length > 7) {
    btn.style.display = '';
    btn.textContent = EXPANDED ? '收起（仅显示最近 7 条）' : '展开全部 ' + rows.length + ' 条';
  } else {
    btn.style.display = 'none';
  }
  document.getElementById('tcount').textContent = rows.length;
}

document.querySelectorAll('.ranges button').forEach(function (b) {
  b.addEventListener('click', function () {
    RANGE = Number(b.getAttribute('data-range'));
    document.querySelectorAll('.ranges button').forEach(function (x) {
      x.classList.toggle('on', x === b);
    });
    renderChart();
    renderTable();
  });
});
document.getElementById('toggleRows').addEventListener('click', function () {
  EXPANDED = !EXPANDED;
  renderTable();
});

// 数据加载（2026-10-02：原先数据内联在页面脚本里，为收紧 CSP 的 script-src 改为 fetch）。
// 失败要**显式提示**而非静默留空——访客看到空图会以为「访问量没了」，
// 而真实原因多半是文件未随部署更新或被 CDN 缓存挡住。
function showLoadError(msg) {
  var box = document.getElementById('load-err');
  if (box) {
    box.textContent = '数据加载失败：' + msg
      + '（若直接用 file:// 打开本页也会失败——请经 HTTP 访问，或运行 start_local.bat 起本地服务）';
    box.hidden = false;
  }
  var chart = document.getElementById('chart');
  if (chart) clear(chart);
}

fetch('visits-trend-data.json', { cache: 'no-store' })
  .then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  })
  .then(function (d) {
    META = (d && d.meta) || {};
    SNAPS = (d && d.snaps) || [];
    if (!SNAPS.length) throw new Error('数据为空');
    renderChart();
    renderTable();
    // 实时校正先于数据文件到达时，applyLivePv 只缓存了 _livePv 未生效——
    // 此处补挂一次（竞态收口的另一半，见 applyLivePv 注释）。
    if (_livePv) {
      var pending = _livePv;
      _livePv = null;
      applyLivePv(pending);
    }
  })
  .catch(function (e) { showLoadError(e && e.message ? e.message : String(e)); });

// KPI 校正（2026-11-04 建链，2026-11-05 改为同源共用）：
// 台账快照每日一次，页脚是实时值，三页数字曾不一致（用户实测 2027 vs 2019）。
// 现在页脚与 KPI **共用 footer-counter.js 的 window.LectureSitePv.get()**：
// 那是全页记忆化的同一个 Promise（缓存命中零请求 / 否则一次 bump / 失败降级
// 静态快照），两处拿到的必然是同一个数字，不会因请求时点差分叉。
// ⚠ 只读不计数：页面打开的 +1 统一归 footer-counter 的全站 5 分钟防刷 bump，
// 趋势页不单独计数，也不再单独 fetch 云端。
// 拿到值后同步：KPI 首卡 + 近 7 条/日均两张静态卡、图表今日点位、明细最新行；
// 历史往日快照原样不动。失败 / 沙箱无 LectureSitePv → 静默保留快照值，
// 页面行为与未接链路时完全一致。
// ⚠ 竞态收口：共享 Promise 与数据文件 fetch 谁先到都可能——若值先到而
// SNAPS 还是空数组，直接 apply 会白做（门禁曾抓到：校正静默跳过）。故先缓存
// _livePv，数据加载完成后由 then 补挂一次；两条到达顺序任意都能收口。
var _livePv = null;
function recomputeKpis(n) {
  // 与 scripts/gen_visits_trend.py 的 build_kpis 同口径，避免四张卡校正后互相矛盾。
  var win = SNAPS.slice(-8);
  var recent7 = 0;
  for (var i = 1; i < win.length; i++) {
    recent7 += Math.max(0, Number(win[i].pv_delta) || 0);
  }
  var r7 = document.getElementById('kpi_recent7_value');
  if (r7) r7.textContent = recent7.toLocaleString('en-US');

  var first = SNAPS[0], last = SNAPS[SNAPS.length - 1];
  var d0 = Date.parse(first.date + 'T00:00:00Z');
  var d1 = Date.parse(last.date + 'T00:00:00Z');
  var span = (d1 - d0) / 86400000;
  if (span > 0) {
    var gain = Math.max(n - (Number(first.site_pv) || 0), 0);
    // Python round 是银行家舍入、JS Math.round 四舍五入：边界 .5 差 1，
    // 此处仅用于日均展示，量级极小且不会造成同屏数字打架，可接受。
    var pd = document.getElementById('kpi_perday_value');
    if (pd) pd.textContent = Math.round(gain / span).toLocaleString('en-US');
  }
}
function applyLivePv(n) {
  if (!n) return;
  if (!SNAPS.length) { _livePv = n; return; }
  var last = SNAPS[SNAPS.length - 1];
  var snapPv = Number(last.site_pv) || 0;
  if (n <= snapPv) return;   // 快照已是最新（或异常回退值），不动
  var prev = SNAPS.length > 1 ? (Number(SNAPS[SNAPS.length - 2].site_pv) || 0) : 0;
  last.site_pv = n;
  last.pv_delta = n - prev;   // 日新增：相对上一条（昨日）快照，供曲线/明细
  var kv = document.getElementById('kpi_pv_value');
  if (kv) kv.textContent = fmt(n);
  var kd = document.getElementById('kpi_pv_delta');
  // #2：旧文案「较前次快照 +N」实际算的是 n − 上一条快照，字面与数值不符，
  // 用户对照累计值 2027→2035 会发现 +17 对不上。改为相对「今日凌晨快照」，
  // 字面与实际一致：n − snapPv。
  if (kd) kd.textContent = '较今日快照 +' + (n - snapPv).toLocaleString('en-US')
    + ' · 实时';
  recomputeKpis(n);          // 近 7 条 / 日均两张静态卡联动重算
  renderChart();
  renderTable();
}
if (typeof window !== 'undefined' && window.LectureSitePv &&
    typeof window.LectureSitePv.get === 'function') {
  window.LectureSitePv.get().then(applyLivePv).catch(function () {});
}
"""


def load_snapshots():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, encoding='utf-8') as f:
            return json.load(f).get('snapshots') or []
    except (ValueError, OSError) as e:
        print(f'[warn] 台账读取失败：{type(e).__name__}: {e}')
        return []


def parse_date(s):
    try:
        return datetime.date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def build_kpis(snaps):
    first = snaps[0]
    last = snaps[-1]
    d0, d1 = parse_date(first.get('date')), parse_date(last.get('date'))
    span_days = (d1 - d0).days if (d0 and d1) else 0

    # 2026-10-18 改版：UV 停更后 KPI 从「访客」全部换为「访问次数(PV)」口径。
    pv1 = int(last.get('site_pv') or 0)
    pv0 = int(first.get('site_pv') or 0)
    total_gain = max(pv1 - pv0, 0)

    # 近 7 条快照的新增访问之和（不含首条自身，故取 8 条窗口）
    win = snaps[-8:]
    recent7 = sum(max(int(s.get('pv_delta') or 0), 0) for s in win[1:])

    per_day = round(total_gain / span_days) if span_days > 0 else None

    def delta(v):
        return f'较前次 +{int(v or 0):,}'

    cards = [
        ('累计访问次数', f'{pv1:,}', delta(last.get('pv_delta'))),
        ('近 7 条快照新增', f'{recent7:,}',
         f'覆盖 {win[0].get("date", "")} 起共 {len(win) - 1} 次快照'),
        ('日均新增访问', f'{per_day:,}' if per_day is not None else '—',
         '按首末快照间隔计' if span_days > 0 else '间隔不足，暂不可算'),
        ('时间跨度', f'{span_days + 1} 天',
         f'快照 {len(snaps)} 条' + (
             f'，缺 {span_days + 1 - len(snaps)} 天' if span_days + 1 > len(snaps) else '')),
    ]
    # 首卡（累计访问次数）值/副文案带 id，供 visits-trend.js 校正；
    # 第二/三卡的值也带 id（2026-11-05 #2）：实时值改写今日快照后，
    # 「近 7 条快照新增」与「日均新增」必须同步重算，否则同屏自相矛盾。
    # 第四卡「时间跨度」只与日期有关，不依赖 PV，无需 id。
    ids = [
        (' id="kpi_pv_value"', ' id="kpi_pv_delta"'),
        (' id="kpi_recent7_value"', ''),
        (' id="kpi_perday_value"', ''),
        ('', ''),
    ]
    return ''.join(
        f'<div class="kpi"><div class="kpi-k">{k}</div>'
        f'<div class="kpi-v"{ids[i][0]}>{v}</div>'
        f'<div class="kpi-d"{ids[i][1]}>{d}</div></div>'
        for i, (k, v, d) in enumerate(cards))


def main():
    snaps = load_snapshots()
    if not snaps:
        print('[skip] 台账为空，未生成报告（先跑 fetch_visits_snapshot.py）')
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    first, last = snaps[0], snaps[-1]
    # 2026-11-04 修：now 必须钉死东八区。原 datetime.datetime.now() 是 naive
    # 时间、取机器本地时区——本地跑是北京时间，CI runner（UTC）跑出来比北京
    # 早 8 小时，出现「生成于 2026-11-03 21:49 · 覆盖至 2026-11-04」的假矛盾
    # （实为北京 11-04 05:49）。快照 date/capturedAt 均为北京时间，生成时刻
    # 与之同区才自洽。
    now = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ).strftime('%Y-%m-%d %H:%M')

    # 2026-10-02：数据与脚本各自出片为独立文件，页面只留外链引用。
    # 这样做的直接收益是 CSP 的 script-src 可从 "'self' 'unsafe-inline'"
    # 收紧为 "'self'"——内联脚本无法满足 script-src 'self'。
    payload = {
        'meta': {
            'source': '自建 Supabase（2026-10-18 起；2026-09-10~10-17 为 busuanzi）',
            'siteUrl': 'https://danchekey.github.io/lecture-aggregator/',
            'generatedAt': now,
        },
        'snaps': snaps,
    }

    html = f"""<!DOCTYPE html>
<html lang="zh-CN" class="pre-hidden"><head><meta charset="utf-8">
<!-- 防点击劫持预隐藏（2026-10-14 P1-1）：与 index.html/stats.html 同方案。
     .pre-hidden 规则在 visits-trend.css、还原 IIFE 在 visits-trend.js 顶部
     （本页 CSP 禁 unsafe-inline，两者都不能内联进 head），详见两个常量注释。
     ⚠ 本 HTML 是产物：改动须落本模板，直接改 site/visits-trend.html 会被
     次日 CI 的 gen_visits_trend.py 覆盖（2026-10-08 已踩过坑）。 -->
<meta name="viewport" content="width=device-width,initial-scale=1">
<!-- 分享卡片：与 index.html 同一套，刻意前置（抓取器可能只读前 4KB）。 -->
<meta property="og:type" content="website">
<meta property="og:site_name" content="木铎金声">
<meta property="og:title" content="站点访问量趋势 · 木铎金声">
<meta property="og:description" content="木铎金声站点每日访问量与累计访问次数趋势。">
<meta property="og:url" content="https://danchekey.github.io/lecture-aggregator/visits-trend.html">
<meta property="og:image" content="https://danchekey.github.io/lecture-aggregator/og-cover-2.jpg">
<meta property="og:image:width" content="600">
<meta property="og:image:height" content="600">
<meta name="twitter:card" content="summary">
<meta name="twitter:image" content="https://danchekey.github.io/lecture-aggregator/og-cover-2.jpg">
<!-- CSP（2026-10-02）：本报告页此前是全站唯一无 CSP 的页面（2026-09-30 评审 P5），
     补 CSP 时因脚本与数据整体内联而被迫放行 script-src 'unsafe-inline'。
     同日两项改动使其可以完全收紧：
       ① 全部 innerHTML 拼接改为 createElement/textContent（渲染不再解析任何文本标记）；
       ② 脚本拆为独立 visits-trend.js、数据拆为 visits-trend-data.json（本次）。
     故 script-src 与 style-src 均只需 'self'（style 走外链 visits-trend.css）。
     2026-11-04 补、2026-11-05 更新：connect-src 放行 https://*.supabase.co
     —— 页脚 footer-counter.js 的云端计数（bump）需要连 Supabase REST；
     趋势页 KPI 不再单独 fetch，而是共用页脚那一次取值的结果。
     与 index/stats 两页同一域名白名单口径。
     遗留代价：页面不再支持 file:// 直接打开（fetch 受同源限制），
     本地预览请用 start_local.bat 起本地 server。 -->
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self' data:; connect-src 'self' https://*.supabase.co; object-src 'none'; base-uri 'self'; form-action 'self'" />
<title>站点访问量趋势 · 木铎金声</title>
<!-- 图标与分享卡片（2026-10-20）：与 index.html 同一套。本页是模板，改动会随
     每次 CI 重新生成进 site/visits-trend.html；直接改产物次日会被覆盖。 -->
<link rel="icon" href="scnu-emblem.svg" type="image/svg+xml">
<link rel="icon" href="favicon-32.png" sizes="32x32" type="image/png">
<link rel="icon" href="favicon-16.png" sizes="16x16" type="image/png">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<meta name="description" content="木铎金声站点每日访问量与累计访问次数趋势（数据来自自建计数快照）。">
<link rel="stylesheet" href="visits-trend.css">
  <!-- 讲座站点风格备选项（VI / 黑白 / 原版，由 site/_theme.json 后台切换，页面无 UI） -->
  <link id="vi-theme" rel="stylesheet" href="theme-scnu.css">
  <link id="bw-theme" rel="stylesheet" href="theme-bw.css" disabled>
  <script src="theme-loader.js"></script>
</head>
<body>

<!-- 顶部站点栏（2026-10-22）：与 index.html / stats.html 同一套视觉——白底、
     底部 1px 分隔线、左侧校徽+站名、右侧页面导航。此前本页没有站点栏，导航
     直接浮在灰色页面背景上，与另两页明显脱节（用户截图反馈）。

     导航入口（2026-10-08 补，2026-10-09 修正落点，2026-10-22 统一）：
     本页此前**没有任何**返回链接——首页与统计页都有「访问量趋势 / 返回首页」互跳，
     唯独本页面是死胡同，手机上（无可见后退键）只能自己改地址栏。
     2026-10-22：与 stats.html 对齐——右上角、纯文字链接、顺序统一为
     「另一个页面 → 返回首页」。

     ⚠ 改动必须落在这里（模板），不能直接改 site/visits-trend.html：
     该 HTML 是本脚本的**产物**，每次 CI 跑 gen_visits_trend.py 都会被整体重写。
     2026-10-08 那版直接改了产物文件，当日部署成功、但次日 10-09 的自动提交
     （chore: 自动更新讲座数据）就把它覆盖掉了，公网导航随之消失。 -->
<header class="sitebar">
  <div class="sitebar-in">
    <a href="./" class="brand">
      <img src="scnu-emblem.png" alt="华南师范大学" class="emblem">
      <img src="site-title.png" alt="木铎金声" class="wordmark">
    </a>
    <nav class="topnav">
      <a href="stats.html">讲座统计</a>
      <a href="./">返回首页</a>
    </nav>
  </div>
</header>

<div class="wrap">
<header>
  <h1>站点访问量趋势</h1>
  <p class="sub">数据来源：自建 Supabase 每日快照（2026-10-17 前为 busuanzi）· 覆盖
    {first.get('date', '')} 至 {last.get('date', '')}</p>
  <p class="subline">生成于 {now} · 台账 data/visits_history.json</p>
</header>

<div class="kpis">{build_kpis(snaps)}</div>

<div class="card">
  <div class="toolbar">
    <div class="ranges">
      <button type="button" data-range="7" class="on">近 7 天</button>
      <button type="button" data-range="30">近 30 天</button>
      <button type="button" data-range="0">全部</button>
    </div>
    <div class="legend">
      <span><i class="sw-line"></i>累计访问次数</span>
      <span><i class="sw-bar"></i>每日新增访问</span>
    </div>
  </div>
  <div id="chart"></div>
</div>

<div class="tb-head">
  <h2>快照明细</h2>
  <span class="sub">共 <span id="tcount">0</span> 条</span>
  <button type="button" id="toggleRows">展开全部</button>
</div>
<div class="tbl">
  <table>
    <thead><tr><th>日期</th><th>累计访问次数</th>
    <th>日新增访问</th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<div class="foot">
  <b>累计访问次数</b>：每打开一次页面就计 1 次，同一人多次访问会重复累计。 本页只统计<b>页面访问次数</b>。
</div>

<p id="load-err" class="empty" hidden></p>
</div><!-- /.wrap（2026-10-28 补闭合：此前模板漏写，footer 被困在 1000px 容器内不铺满） -->

<!-- 底部信息栏：与 stats.html footer 同形式（全宽、居中、16px、slate-400）。
     Tailwind 类名保留仅作 theme-scnu.css 的命中钩子（footer.bg-white /
     .text-slate-400，VI 模式染深藏青白字）；真实样式由本页 .site-foot 提供
     （本页不加载 Tailwind，2026-10-28 教训）。 -->
<footer class="site-foot border-t border-slate-200 text-center text-12pt text-slate-400 py-6 bg-white">
  数据来源于 Supabase，仅供参考。
  <span class="block sm:inline sm:ml-2 mt-1 sm:mt-0">
    · 本站总访问量 <span id="site_pv_value">—</span> 次
  </span>
</footer>

<!-- 云端计数接线（2026-11-04 建链，2026-11-05 同源改造）：与 index.html /
     stats.html 同一套——app.cloud.js 提供 LectureCloud，footer-counter.js
     负责页脚「本站总访问量」的云端计数（全站共享 5 分钟 localStorage 防刷，
     同一浏览器 5 分钟内无论开哪个页面只计 1 次），并暴露
     window.LectureSitePv.get()：全页记忆化的同一次取值。趋势页 KPI 订阅它，
     不再单独读云端，页脚与 KPI 必然同值；三页页脚数字永远同源一致。
     ⚠ defer 顺序不可乱：app.cloud → footer-counter → visits-trend，
     后两者都依赖前者先定义。裸引用（无 ?v=）是待打戳源形态，
     main() 里 stamp_script_version。 -->
<script src="app.cloud.js" defer></script>
<script src="footer-counter.js" defer></script>
<script src="visits-trend.js" defer></script>
</body></html>
"""

    def _write(path, content):
        """原子写出（先 .tmp 再 replace），避免 GitHub Pages 读到半份文件。

        ⚠ `newline='\n'` 不可省（2026-10-10 Windows 实测）：文本模式在
        Windows 把 `\n` 转 `\r\n`，本脚本产物是多行 HTML/JS/CSS，本地跑一次
        就把工作区变 CRLF → test_工作区与git换行一致 变红。CI（Linux）不复现。
        """
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)
        os.replace(tmp, path)

    _write(OUT_PATH, html)
    _write(JS_PATH, JS)
    _write(DATA_PATH,
           json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    css = os.path.join(OUT_DIR, CSS_NAME)
    _write(css, CSS)
    # 底部总访问量数据（2026-10-28 出片；2026-11-04 起页脚改由 footer-counter.js
    # 实时计数接管，visits-trend.js 不再 fetch 本文件——保留出片仅为
    # test_frontend_consistency 的 known_static 登记与潜在降级用途，
    # 数据仍随台账每日刷新。
    _write(os.path.join(OUT_DIR, 'visits.json'), json.dumps({
        'date': last.get('date', ''),
        'capturedAt': last.get('capturedAt', ''),
        'site_pv': int(last.get('site_pv') or 0),
    }, ensure_ascii=False))
    # 缓存戳（2026-10-10，由**产物所有者**在写入后立即打）。
    # ⚠ 为什么必须在这里打、而不是依赖 generate_frontend_data.py：
    #   daily.yml 的顺序是「generate_frontend_data（打戳）→ gen_visits_trend
    #   （整体重写本页）」，后者一跑，前者刚打的 ?v= 立刻被模板里的裸引用
    #   冲掉 → 提交无戳文件 → 回访用户永久缓存旧脚本。这与 2026-10-08 的
    #   「导航被次日 CI 覆盖」是同一类事故（模板与产物不同步）。
    #   修在模板所有者一侧后，无论两脚本以什么顺序执行，本页的戳都正确；
    #   generate 侧若再打一遍也是幂等的（同内容 hash 相同）。
    # 模板里的裸引用（468/533 行）保持不变：它是「待打戳的源形态」，
    # 正是 tests/test_frontend_schema.py::test_99 校验的输入。
    from generate_frontend_data import stamp_script_version
    stamp_script_version(os.path.basename(OUT_PATH), JS_NAME)
    stamp_script_version(os.path.basename(OUT_PATH), CSS_NAME)
    # 主题备选项资源（vi/bw/loader）同样由本脚本产出时打戳：模板里是裸引用
    # （与上面 visits-trend.js/css 同源机制），否则 trend 页的主题资源无 ?v=，
    # 会被 tests/test_frontend_schema.py::test_99 判为「未打戳」→ 门禁1 整班红
    # （2026-10-28 实测：daily.yml 排程里本脚本在 generate 之后跑，会把
    # generate 已打好的主题戳整体覆盖掉，趋势页只剩裸引用）。
    stamp_script_version(os.path.basename(OUT_PATH), 'theme-loader.js')
    stamp_script_version(os.path.basename(OUT_PATH), 'theme-scnu.css')
    stamp_script_version(os.path.basename(OUT_PATH), 'theme-bw.css')
    # 云端计数接线（2026-11-04）：模板挂载的 app.cloud.js / footer-counter.js
    # 同样由本脚本出片时打戳（test_frontend_schema::test_99 反向断言要求
    # 页面引用的本地资源全部带正确 ?v=，漏打即门禁红）。
    stamp_script_version(os.path.basename(OUT_PATH), 'app.cloud.js')
    stamp_script_version(os.path.basename(OUT_PATH), 'footer-counter.js')
    for p in (OUT_PATH, JS_PATH, DATA_PATH, css):
        print(f'[done] {os.path.relpath(p, ROOT)}'
              f'（{os.path.getsize(p) / 1024:.1f} KB）')
    print(f'       {len(snaps)} 条快照，生成于 {now}')


if __name__ == '__main__':
    main()
