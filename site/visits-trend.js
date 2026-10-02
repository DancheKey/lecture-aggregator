'use strict';
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
