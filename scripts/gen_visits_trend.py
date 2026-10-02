# -*- coding: utf-8 -*-
"""生成「站点访问量趋势」报告（自包含单文件 HTML）。

数据来源：
    data/visits_history.json —— 由 scripts/fetch_visits_snapshot.py 每日在 CI 中
    从 busuanzi 取快照累积而成（用 GET，不计数）。这是公网访问量的**唯一本地备份**：
    busuanzi 是外部免费服务，一旦它失效，累计访问量只在这个台账里还活着。

与旧的 gen_visits_report.py 的区别（勿混淆）：
    gen_visits_report.py  ← data/visits.json（**本地 localhost** 访问，server.py 写的）
    本脚本                ← data/visits_history.json（**公网**访问，CI 从 busuanzi 取的）

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

图表设计（2026-09-14 改版）：
    手绘内联 SVG 双轴组合图 —— 累计访客折线（左轴，按数据 min/max 自适应）
    + 日增访客柱（右轴）。缺失快照的日期保留在横轴上，不假装连续。
    范围切换与表格展开由前端 JS 完成，故 file:// 直接打开也能交互。

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
*{box-sizing:border-box}
body{margin:0;padding:36px 20px 48px;background:#F7F7F5;color:#2C2C2A;
  font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;
  font-size:15px;line-height:1.7;-webkit-font-smoothing:antialiased}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:22px;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}
.sub{color:#888780;font-size:13.5px;margin:0}
header{margin-bottom:24px}
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
th,td{padding:11px 15px;text-align:right;border-bottom:1px solid #F1F0EC;white-space:nowrap}
th{position:sticky;top:0;background:#FAFAF8;font-weight:600;color:#5F5E5A;font-size:14px;z-index:1}
td:first-child,th:first-child{text-align:left}
td.l,th.l{text-align:left}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:#FCFCFB}
.note{color:#B45309;font-size:13.5px}
.foot{margin-top:28px;color:#888780;font-size:13.5px;line-height:1.8}
.foot b{color:#5F5E5A;font-weight:600}
"""

# 2026-10-02：JS 不再内联数据（META/SNAPS 改由 fetch 载入同目录的
# visits-trend-data.json）。目的是让页面能去掉 CSP 的 script-src 'unsafe-inline'。
JS = """'use strict';
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
// 本页唯一来自台账的自由文本是快照的 note 字段（其余全是数字与固定日期），
// 它经 json.dumps 内嵌进页面脚本——若 note 里含 </script> 或 <img onerror=…>，
// innerHTML 拼接即构成存储型 XSS（CI 抓来的数据进公网页面）。
// 这里改用 createElement + textContent：文本一律按纯文本落地，不解释标记。
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
      '仅 1 条快照（' + s0.date + '）：累计访问次数 ' + fmt(s0.site_pv)
      + ' 次 / 累计访客数 ' + fmt(s0.site_uv) + ' 人。'));
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

  var uvs = snaps.map(function (s) { return Number(s.site_uv) || 0; });
  var yT = niceTicks(Math.min.apply(null, uvs), Math.max.apply(null, uvs), 3);
  var yLo = yT[0], yHi = yT[yT.length - 1];
  function yL(v) { return B - (v - yLo) / ((yHi - yLo) || 1) * ph; }

  var dmax = Math.max.apply(null, snaps.map(function (s) {
    return Math.max(0, Number(s.uv_delta) || 0);
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
    var v = Math.max(0, Number(s.uv_delta) || 0);
    if (!v) return;
    var x = xOf(s) - bw / 2, y = yR(v), h = B - y;
    var rect = sv('rect', { x: x.toFixed(1), y: y.toFixed(1), width: bw.toFixed(1),
                            height: h.toFixed(1), rx: 3, fill: '#C9DDF3' });
    rect.appendChild(sv('title', {}, s.date + ' 日新增访客 ' + v + ' 人'));
    g.appendChild(rect);
  });

  g.appendChild(sv('polyline', {
    points: snaps.map(function (s) {
      return xOf(s).toFixed(1) + ',' + yL(Number(s.site_uv) || 0).toFixed(1);
    }).join(' '),
    fill: 'none', stroke: '#185FA5', 'stroke-width': 2.5,
    'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));

  var showVal = snaps.length <= 8;
  snaps.forEach(function (s, i) {
    var x = xOf(s), y = yL(Number(s.site_uv) || 0);
    var c = sv('circle', { cx: x.toFixed(1), cy: y.toFixed(1), r: 4,
                           fill: '#ffffff', stroke: '#185FA5', 'stroke-width': 2.5 });
    c.appendChild(sv('title', {}, s.date + ' 累计访客数 ' + fmt(s.site_uv) + ' 人'));
    g.appendChild(c);
    if (showVal) {
      var anchor = 'middle', lx = x;
      if (i === 0) { anchor = 'start'; lx = x + 9; }
      else if (i === snaps.length - 1) { anchor = 'end'; lx = x - 9; }
      g.appendChild(sv('text', { x: lx.toFixed(1), y: (y - 12).toFixed(1), 'font-size': 12,
                                 fill: '#185FA5', 'text-anchor': anchor },
                       fmt(s.site_uv)));
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
                         'aria-label': '累计访客数折线与日新增访客柱状组合趋势图' });
  svg.appendChild(sv('title', {}, '站点访问量趋势'));
  svg.appendChild(sv('desc', {}, snaps[0].date + ' 至 '
    + snaps[snaps.length - 1].date + ' 的累计访客数折线与每日新增访客柱状图。'));
  svg.appendChild(g);
  box.appendChild(svg);

  var cap = el('p', null, 'cap');
  cap.appendChild(document.createTextNode('覆盖 '));
  cap.appendChild(el('b', span + 1));
  cap.appendChild(document.createTextNode(' 天 · 快照 '));
  cap.appendChild(el('b', snaps.length));
  cap.appendChild(document.createTextNode(' 条 · 左轴累计访客数（人），右轴日新增访客'
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
    tr.appendChild(el('td', fmt(s.site_uv)));
    tr.appendChild(el('td', fmt(s.pv_delta)));
    tr.appendChild(el('td', fmt(s.uv_delta)));
    // note 是本页唯一的自由文本（来自抓取脚本写入的台账），一律按纯文本落地。
    var last = el('td', null, 'l');
    if (s.note) last.appendChild(el('span', s.note, 'note'));
    tr.appendChild(last);
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
  })
  .catch(function (e) { showLoadError(e && e.message ? e.message : String(e)); });
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
    first, last = snaps[0], snaps[-1]
    d0, d1 = parse_date(first.get('date')), parse_date(last.get('date'))
    span_days = (d1 - d0).days if (d0 and d1) else 0
    uv0, uv1 = int(first.get('site_uv') or 0), int(last.get('site_uv') or 0)
    per_day = round((uv1 - uv0) / span_days) if span_days > 0 else None

    def delta(v):
        return f'较前次 +{int(v or 0):,}'

    cards = [
        ('累计访问次数', f"{int(last.get('site_pv') or 0):,}", delta(last.get('pv_delta'))),
        ('累计访客数', f'{uv1:,}', delta(last.get('uv_delta'))),
        ('日均新增访客', f'{per_day:,}' if per_day is not None else '—',
         '按首末快照间隔计' if span_days > 0 else '间隔不足，暂不可算'),
        ('时间跨度', f'{span_days + 1} 天',
         f'快照 {len(snaps)} 条' + (
             f'，缺 {span_days + 1 - len(snaps)} 天' if span_days + 1 > len(snaps) else '')),
    ]
    return ''.join(
        f'<div class="kpi"><div class="kpi-k">{k}</div>'
        f'<div class="kpi-v">{v}</div><div class="kpi-d">{d}</div></div>'
        for k, v, d in cards)


def main():
    snaps = load_snapshots()
    if not snaps:
        print('[skip] 台账为空，未生成报告（先跑 fetch_visits_snapshot.py）')
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    first, last = snaps[0], snaps[-1]
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

    # 2026-10-02：数据与脚本各自出片为独立文件，页面只留外链引用。
    # 这样做的直接收益是 CSP 的 script-src 可从 "'self' 'unsafe-inline'"
    # 收紧为 "'self'"——内联脚本无法满足 script-src 'self'。
    payload = {
        'meta': {
            'source': 'busuanzi（公网）',
            'siteUrl': 'https://danchekey.github.io/lecture-aggregator/',
            'generatedAt': now,
        },
        'snaps': snaps,
    }

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<!-- CSP（2026-10-02）：本报告页此前是全站唯一无 CSP 的页面（2026-09-30 评审 P5），
     补 CSP 时因脚本与数据整体内联而被迫放行 script-src 'unsafe-inline'。
     同日两项改动使其可以完全收紧：
       ① 全部 innerHTML 拼接改为 createElement/textContent（渲染不再解析任何文本标记）；
       ② 脚本拆为独立 visits-trend.js、数据拆为 visits-trend-data.json（本次）。
     故 script-src 与 style-src 均只需 'self'（style 走外链 visits-trend.css）。
     遗留代价：页面不再支持 file:// 直接打开（fetch 受同源限制），
     本地预览请用 start_local.bat 起本地 server。 -->
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'" />
<title>站点访问量趋势</title>
<link rel="stylesheet" href="visits-trend.css">
</head>
<body><div class="wrap">

<header>
  <h1>站点访问量趋势</h1>
  <p class="sub">数据来源 busuanzi（公网）每日快照 · 覆盖
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
      <span><i class="sw-line"></i>累计访客数</span>
      <span><i class="sw-bar"></i>日新增访客</span>
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
    <thead><tr><th>日期</th><th>累计访问次数</th><th>累计访客数</th>
    <th>日新增访问</th><th>日新增访客</th><th class="l">备注</th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<div class="foot">
  <b>累计访问次数</b>：每打开一次页面就计 1 次，同一人多次访问会重复累计。<br>
  <b>累计访客数</b>：按访客去重，同一访客重复访问不再累加。<br>
  「日新增」= 本次快照 − 上一条记录；负值一律记 0 并标注（意味着对方计数器重置）。
  每日由 CI 用 GET 取数（不计数，避免污染统计）。<br>
  横轴按<b>真实日期等距</b>绘制，缺失快照的日期不跳过、不补 0，避免把断档画成连续趋势；
  左轴按数据范围自适应（累计值不从 0 起算，否则曲线会被压平贴顶）。
</div>

<p id="load-err" class="empty" hidden></p>
</div>
<script src="visits-trend.js" defer></script>
</body></html>
"""

    def _write(path, content):
        """原子写出（先 .tmp 再 replace），避免 GitHub Pages 读到半份文件。"""
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp, path)

    _write(OUT_PATH, html)
    _write(JS_PATH, JS)
    _write(DATA_PATH,
           json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    css = os.path.join(OUT_DIR, CSS_NAME)
    _write(css, CSS)
    for p in (OUT_PATH, JS_PATH, DATA_PATH, css):
        print(f'[done] {os.path.relpath(p, ROOT)}'
              f'（{os.path.getsize(p) / 1024:.1f} KB）')
    print(f'       {len(snaps)} 条快照，生成于 {now}')


if __name__ == '__main__':
    main()
