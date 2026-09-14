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
    reports/visits-trend.html
    - 自包含：数据内联进页面，双击即可查看（file:// 也行），无需服务器、不依赖 CDN。
    - **入库**：.gitignore 只忽略旧的 visits-by-month.html，本报告可随仓库同步，
      便于在任意机器上 git pull 后直接查看。
    - 不会出现在公网页面上——GitHub Pages 只发布 site/ 目录。

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
OUT_DIR = os.path.join(ROOT, 'reports')
OUT_PATH = os.path.join(OUT_DIR, 'visits-trend.html')

# 样式与脚本单独放常量：避免大括号与 f-string 冲突
CSS = """
*{box-sizing:border-box}
body{margin:0;padding:36px 20px 48px;background:#F7F7F5;color:#2C2C2A;
  font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;
  font-size:13px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:920px;margin:0 auto}
h1{font-size:19px;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}
.sub{color:#888780;font-size:12px;margin:0}
header{margin-bottom:24px}
.subline{color:#B4B2A9;font-size:12px;margin:4px 0 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:12px;margin-bottom:22px}
.kpi{background:#F1F0EC;border-radius:10px;padding:14px 16px}
.kpi-k{font-size:12px;color:#888780;margin-bottom:6px}
.kpi-v{font-size:25px;font-weight:600;line-height:1.2;letter-spacing:-.02em}
.kpi-d{font-size:12px;color:#B4B2A9;margin-top:5px}
.card{background:#fff;border:1px solid #E8E6E0;border-radius:12px;
  padding:18px 20px 16px;margin-bottom:22px}
.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.ranges{display:flex;gap:8px}
.ranges button{font:inherit;font-size:12px;padding:4px 14px;border-radius:999px;
  border:1px solid #E1E0DA;background:#fff;color:#5F5E5A;cursor:pointer}
.ranges button:hover{border-color:#C9C7BE}
.ranges button.on{background:#E6F1FB;color:#185FA5;border-color:#B5D4F4}
.legend{margin-left:auto;display:flex;gap:16px;font-size:12px;color:#888780}
.legend i{display:inline-block;vertical-align:middle;margin-right:6px}
.sw-line{width:16px;height:2.5px;background:#185FA5;border-radius:2px}
.sw-bar{width:10px;height:10px;background:#C9DDF3;border-radius:3px}
.cap{color:#B4B2A9;font-size:12px;margin:12px 0 0}
.cap b{color:#7A7972;font-weight:600}
.empty{color:#888780;padding:28px 0;text-align:center}
.tb-head{display:flex;align-items:center;gap:12px;margin:0 0 12px}
h2{font-size:14px;font-weight:600;margin:0}
.tb-head button{margin-left:auto;font:inherit;font-size:12px;padding:4px 12px;
  border-radius:8px;border:1px solid #E1E0DA;background:#fff;color:#5F5E5A;cursor:pointer}
.tb-head button:hover{border-color:#C9C7BE;background:#FAFAF8}
.tbl{max-height:520px;overflow:auto;border:1px solid #E8E6E0;border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:9px 14px;text-align:right;border-bottom:1px solid #F1F0EC;white-space:nowrap}
th{position:sticky;top:0;background:#FAFAF8;font-weight:600;color:#5F5E5A;z-index:1}
td:first-child,th:first-child{text-align:left}
td.l,th.l{text-align:left}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:#FCFCFB}
.note{color:#B45309;font-size:12px}
.foot{margin-top:28px;color:#B4B2A9;font-size:12px;line-height:1.7}
.foot b{color:#7A7972;font-weight:600}
"""

JS = """var META = {};
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

function renderChart() {
  var box = document.getElementById('chart');
  var snaps = inRange(SNAPS, RANGE);
  if (!snaps.length) {
    box.innerHTML = '<div class="empty">该时间范围内没有快照记录。</div>';
    return;
  }
  if (snaps.length === 1) {
    var s0 = snaps[0];
    box.innerHTML = '<div class="empty">仅 1 条快照（' + s0.date + '）：累计访问 '
      + fmt(s0.site_pv) + ' 次 / 访客 ' + fmt(s0.site_uv)
      + ' 人。<br>至少需要 2 条快照才能绘制趋势线。</div>';
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

  var g = '';

  yT.forEach(function (v) {
    var y = yL(v);
    g += '<line x1="' + L + '" y1="' + y.toFixed(1) + '" x2="' + R + '" y2="' + y.toFixed(1)
      + '" stroke="' + (v === yLo ? '#D3D1C7' : '#E8E6E0') + '" stroke-width="1"/>';
    g += '<text x="' + (L - 10) + '" y="' + (y + 4).toFixed(1)
      + '" font-size="11" fill="#888780" text-anchor="end">' + fmt(v) + '</text>';
  });
  dT.forEach(function (v) {
    if (v === 0) return;
    var y = yR(v);
    g += '<text x="' + (R + 10) + '" y="' + (y + 4).toFixed(1)
      + '" font-size="11" fill="#B4B2A9" text-anchor="start">' + fmt(v) + '</text>';
  });

  var bw = Math.min(36, Math.max(1.5, pw / snaps.length * 0.7));
  snaps.forEach(function (s) {
    var v = Math.max(0, Number(s.uv_delta) || 0);
    if (!v) return;
    var x = xOf(s) - bw / 2, y = yR(v), h = B - y;
    g += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1)
      + '" height="' + h.toFixed(1) + '" rx="3" fill="#C9DDF3">'
      + '<title>' + s.date + ' 日增访客 ' + v + '</title></rect>';
  });

  g += '<polyline points="'
    + snaps.map(function (s) {
        return xOf(s).toFixed(1) + ',' + yL(Number(s.site_uv) || 0).toFixed(1);
      }).join(' ')
    + '" fill="none" stroke="#185FA5" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>';

  var showVal = snaps.length <= 8;
  snaps.forEach(function (s, i) {
    var x = xOf(s), y = yL(Number(s.site_uv) || 0);
    g += '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1)
      + '" r="4" fill="#ffffff" stroke="#185FA5" stroke-width="2.5">'
      + '<title>' + s.date + ' 累计访客 ' + fmt(s.site_uv) + '</title></circle>';
    if (showVal) {
      var anchor = 'middle', lx = x;
      if (i === 0) { anchor = 'start'; lx = x + 9; }
      else if (i === snaps.length - 1) { anchor = 'end'; lx = x - 9; }
      g += '<text x="' + lx.toFixed(1) + '" y="' + (y - 12).toFixed(1)
        + '" font-size="11" fill="#185FA5" text-anchor="' + anchor + '">'
        + fmt(s.site_uv) + '</text>';
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
    g += '<text x="' + x.toFixed(1) + '" y="' + (B + 22)
      + '" font-size="11" fill="' + (has ? '#5F5E5A' : '#B4B2A9')
      + '" text-anchor="middle">' + lab + '</text>';
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

  box.innerHTML = '<svg viewBox="0 0 680 250" width="100%" role="img"'
    + ' aria-label="累计访客折线与日增访客柱状组合趋势图">'
    + '<title>站点访问量趋势</title><desc>' + snaps[0].date + ' 至 '
    + snaps[snaps.length - 1].date + ' 的累计访客折线与每日新增访客柱状图。</desc>'
    + g + '</svg>'
    + '<p class="cap">覆盖 <b>' + (span + 1) + '</b> 天 · 快照 <b>' + snaps.length
    + '</b> 条 · 左轴累计访客（人），右轴日增访客' + missTxt + '</p>';
}

function renderTable() {
  var all = inRange(SNAPS, RANGE);
  var rows = all.slice().reverse();
  var limit = EXPANDED ? rows.length : Math.min(rows.length, 7);
  var h = '';
  rows.slice(0, limit).forEach(function (s) {
    var note = s.note ? '<span class="note">' + s.note + '</span>' : '';
    h += '<tr><td>' + s.date + '</td><td>' + fmt(s.site_pv) + '</td><td>' + fmt(s.site_uv)
      + '</td><td>' + fmt(s.pv_delta) + '</td><td>' + fmt(s.uv_delta)
      + '</td><td class="l">' + note + '</td></tr>';
  });
  document.getElementById('tbody').innerHTML = h;
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

renderChart();
renderTable();
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

    data_js = (
        'var META = ' + json.dumps({
            'source': 'busuanzi（公网）',
            'siteUrl': 'https://danchekey.github.io/lecture-aggregator/',
            'generatedAt': now,
        }, ensure_ascii=False) + ';\n'
        'var SNAPS = ' + json.dumps(snaps, ensure_ascii=False) + ';'
    )
    script = JS.replace('var META = {};\nvar SNAPS = [];', data_js)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>站点访问量趋势</title><style>{CSS}</style></head>
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
      <span><i class="sw-line"></i>累计访客</span>
      <span><i class="sw-bar"></i>日增访客</span>
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
    <thead><tr><th>日期</th><th>累计 PV</th><th>累计 UV</th>
    <th>日增 PV</th><th>日增 UV</th><th class="l">备注</th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<div class="foot">
  日增 = 本次快照 − 上一条记录；负值一律记 0 并标注（意味着对方计数器重置）。
  每日由 CI 用 GET 取数（不计数，避免污染统计）。<br>
  横轴按<b>真实日期等距</b>绘制，缺失快照的日期不跳过、不补 0，避免把断档画成连续趋势；
  左轴按数据范围自适应（累计值不从 0 起算，否则曲线会被压平贴顶）。
</div>

</div>
<script>{script}</script>
</body></html>
"""

    tmp = OUT_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(html)
    os.replace(tmp, OUT_PATH)
    print(f'[done] {os.path.relpath(OUT_PATH, ROOT)}（{len(snaps)} 条快照，'
          f'{len(html) / 1024:.1f} KB）')


if __name__ == '__main__':
    main()
