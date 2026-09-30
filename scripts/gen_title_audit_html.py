# -*- coding: utf-8 -*-
"""扫描「标题被海报识别结果改写」的存量记录，生成可点击人工核实清单。

背景（2026-09-30）：用户决策方案A —— title 恒取自源页列表条目名（listTitle），
海报/OCR 读到的主题只写入 topic。该规则已作用于新抓页面（parsers._apply_vlm_to_result
的覆盖分支已移除），但存量记录不会自动回改，故生成本清单供人工定夺。

三组口径：
  A 建议回改     title 明显是海报系列名/装饰文字，listTitle 才是源页条目名 → title ← listTitle
  B 建议保留     同一 listTitle 被多个不同源页共用（源站列表标题本身不区分条目/有误），
                 此时的 title 反而比 listTitle 准（如 swc「扬帆教学论坛」各周次页在列表里都写「第二周」）
  C 无参照       缺 listTitle 字段，无法判定与回改

输出：reports/title-audit-<日期>.html
"""
import json
import os
import re
import html
import datetime
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'data', 'lectures.json')
OUT = os.path.join(ROOT, 'reports',
                   'title-audit-%s.html' % datetime.datetime.now().strftime('%Y%m%d'))

data = json.load(open(SRC, encoding='utf-8'))
recs = data['data'] if isinstance(data, dict) and 'data' in data else data


def norm(s):
    s = re.sub(r'[\s\u200b\u3000]', '', str(s or ''))
    return re.sub(r'^[●·•\-\*\|丨【】\[\]]+', '', s)


def is_poster_parsed(l):
    return (l.get('imageParseMethod') in ('vlm', 'ocr')
            or str(l.get('splitMode', '')).startswith(('vlm', 'ocr')))


def site_of(u):
    m = re.search(r'//([^/]+)', str(u or ''))
    return m.group(1) if m else '?'


# listTitle → 出现它的不同源页数：>1 说明该列表标题不区分条目（源站列表有问题）
lt_urls = defaultdict(set)
for l in recs:
    lt, u = norm(l.get('listTitle')), l.get('sourceUrl')
    if lt and u:
        lt_urls[lt].add(u)

# 系列通称词：出现在 title 里说明 title 只是活动系列名、未含具体讲座题目
SERIES_KEYWORDS = ('大讲堂', '前沿论坛', '讲坛', '工作坊', '研修班', '系列讲座', '沙龙', '学术论坛')

groups = {'A1': [], 'A2': [], 'B': [], 'C': []}
for l in recs:
    if not is_poster_parsed(l):
        continue
    lt, t = norm(l.get('listTitle')), norm(l.get('title'))
    if not lt:
        groups['C'].append(l)
        continue
    if not t or t == lt or t in lt or lt in t:
        continue                      # 子串关系 = 普通清洗差异，不在本次范围
    if len(lt_urls[lt]) > 1:
        groups['B'].append(l)         # 列表标题被多页共用 → 保留 title
    elif any(k in t for k in SERIES_KEYWORDS):
        groups['A1'].append(l)        # title 仅为系列通称名 → 取列表条目名
    else:
        groups['A2'].append(l)        # title 已是实质题目 → 回改是负收益


def e(s):
    return html.escape(str(s if s is not None else ''))


GROUP_META = [
    ('A1', '已回改：title 原为系列通称名',
     '这些记录的 title 是海报/正文上的系列活动名（如「木棉生命科学前沿论坛」），'
     '未含本场讲座题目。已按方案A 口径改为源页列表条目名（listTitle）；'
     '各场真题目仍在 topic 字段，信息不丢失。清洗脚本 scripts/fix_title_from_listtitle.py。'),
    ('A2', '建议保留：title 已是实质题目',
     '这些 title 本身就是具体讲座题目，与 listTitle 的差异只是标点/大小写/源站错别字'
     '（如源站列表把「双受精」写成「双受镜」、「sympatric」写成「symparric」、'
     '括号截断等），或 listTitle 只是系列名（回改会让同源多条撞成同一标题）。'
     '在此情形下回改是负收益，建议保留现有 title。'),
    ('B', '建议保留：源站列表标题本身不区分条目',
     '同一 listTitle 被多个不同源页共用（源站列表把若干条目写成同一个名字，'
     '如「扬帆教学论坛」各周次页在列表里都写「第二周」）。此时现有 title 反而比 listTitle 准确，'
     '若强行回改会引入错误。建议保留，或另行修正源站列表配置。'),
    ('C', '无参照：记录内缺 listTitle 字段',
     '这些记录没有 listTitle 字段（多为 listTitle 字段/抓取链路完善前入库的老记录，'
     '此后未被重抓刷新；见 scraper.py 抓取路径会无条件写入 listTitle）。'
     '记录内无从比对，故无法回改。注：这不代表源站列表页当初没有条目名。'),
]

parts = []
for code, title, desc in GROUP_META:
    rows = groups[code]
    if not rows:
        # A1 已在本轮对齐完毕，无剩余行；仍保留标题与口径说明，便于对照
        if code == 'A1':
            parts.append(f'\n<h2>{e(title)} <span class="badge">0</span></h2>\n'
                         f'<p class="gdesc">{e(desc)}</p>')
        continue
    trs = []
    for i, l in enumerate(rows, 1):
        u = l.get('sourceUrl', '')
        trs.append(
            f'<tr>'
            f'<td class="num"><input type="checkbox" class="pick" data-url="{e(u)}" '
            f'data-title="{e(l.get("title"))}" data-list="{e(l.get("listTitle"))}" '
            f'data-group="{code}"></td>'
            f'<td class="num">{i}</td>'
            f'<td><a href="{e(u)}" target="_blank">{e(u)}</a>'
            f'<div class="dim">{e(site_of(u))}'
            f'{" · 多场" if l.get("isMultiLecture") else ""}'
            f'{" · " + e(l.get("imageParseMethod")) if l.get("imageParseMethod") else ""}'
            f'{" · " + e(l.get("splitMode")) if l.get("splitMode") else ""}</div></td>'
            f'<td class="cur">{e(l.get("title"))}</td>'
            f'<td class="new">{e(l.get("listTitle")) if l.get("listTitle") else "<span class=dim>（缺）</span>"}</td>'
            f'<td class="dim">{e(l.get("topic")) or "—"}</td>'
            f'<td class="dim">{e(l.get("speaker")) or "—"}</td>'
            f'<td class="dim">{e(l.get("lectureStart")) or "—"}</td>'
            f'</tr>')
    parts.append(f'''
<h2>{e(title)} <span class="badge">{len(rows)}</span></h2>
<p class="gdesc">{e(desc)}</p>
<table>
<thead><tr><th class="num">选</th><th class="num">#</th><th>源页链接 / 来源</th>
<th>当前 title</th><th>源页 listTitle</th><th>topic</th><th>主讲人</th><th>时间</th></tr></thead>
<tbody>
{chr(10).join(trs)}
</tbody>
</table>''')

now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
total = sum(len(v) for v in groups.values())

html_out = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>标题来源存量审计 · 人工核实清单</title>
<style>
  body {{ font-family: "Microsoft YaHei", "微软雅黑", sans-serif; background:#f7f8fa;
         color:#1f2328; margin:0; padding:32px 20px; }}
  .wrap {{ max-width:1280px; margin:0 auto; background:#fff; padding:28px 32px;
           border-radius:10px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  h1 {{ font-size:22px; margin:0 0 6px; }}
  .sub {{ color:#6b7280; font-size:13px; margin-bottom:18px; }}
  h2 {{ font-size:16px; margin:30px 0 8px; padding-left:9px; border-left:4px solid #2563eb; }}
  .badge {{ display:inline-block; background:#eef2ff; color:#4338ca; font-size:12px;
            padding:1px 8px; border-radius:10px; margin-left:6px; }}
  .gdesc {{ color:#4b5563; font-size:13px; line-height:1.7; margin:0 0 10px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ border:1px solid #e5e7eb; padding:7px 10px; text-align:left; vertical-align:top; }}
  th {{ background:#f3f4f6; font-weight:600; white-space:nowrap; }}
  td.num, th.num {{ width:42px; text-align:center; color:#6b7280; }}
  tr:nth-child(even) td {{ background:#fafbfc; }}
  a {{ color:#2563eb; text-decoration:none; word-break:break-all; }}
  a:hover {{ text-decoration:underline; }}
  .dim {{ color:#9ca3af; font-size:12px; }}
  .cur {{ color:#1f2328; }}
  .new {{ color:#b91c1c; }}
  .note {{ background:#fffbeb; border:1px solid #fde68a; padding:12px 14px;
           border-radius:6px; font-size:13px; margin:16px 0; line-height:1.7; }}
  .bar {{ position:sticky; top:0; background:#fff; padding:10px 0; border-bottom:1px solid #e5e7eb;
          margin-bottom:6px; display:flex; gap:10px; align-items:center; flex-wrap:wrap; z-index:9; }}
  button {{ font-family:inherit; font-size:13px; padding:5px 12px; border-radius:6px;
            border:1px solid #d1d5db; background:#fff; cursor:pointer; }}
  button:hover {{ background:#f3f4f6; }}
  button.primary {{ background:#2563eb; color:#fff; border-color:#2563eb; }}
  button.primary:hover {{ background:#1d4ed8; }}
  #out {{ width:100%; height:120px; font-family:Consolas, monospace; font-size:12px;
          margin-top:8px; display:none; }}
</style>
</head>
<body>
<div class="wrap">
<h1>标题来源存量审计 · 人工核实清单</h1>
<div class="sub">生成于 {now} · 共 {total} 条 · 数据源 data/lectures.json（{len(recs)} 条记录）</div>
<div class="note">
<b>为什么有这个清单</b>：新规则（方案A）已改为「title 恒取自源页列表条目名，海报/OCR 只写 topic」，
即刻作用于新抓页面。但<b>存量记录不会自动回改</b>，下表列出全部仍在「title 被海报内容改写」状态的记录。
每条都附源页链接，可点开对照。<b>勾选 = 你同意按建议动作处理</b>（A 组回改、B 组保留、C 组暂不动）。
</div>
<div class="bar">
  <button onclick="toggleAll(true)">全选</button>
  <button onclick="toggleAll(false)">清空</button>
  <button class="primary" onclick="exportPicked()">导出已勾选</button>
  <span id="cnt" class="dim">已勾选 0 条</span>
</div>
<textarea id="out" readonly></textarea>
{''.join(parts)}
</div>
<script>
function picks() {{ return Array.from(document.querySelectorAll('.pick')); }}
function toggleAll(v) {{ picks().forEach(function(c) {{ c.checked = v; }}); update(); }}
function update() {{
  var n = picks().filter(function(c) {{ return c.checked; }}).length;
  document.getElementById('cnt').textContent = '已勾选 ' + n + ' 条';
}}
function exportPicked() {{
  var out = picks().filter(function(c) {{ return c.checked; }}).map(function(c) {{
    return {{ group: c.dataset.group, url: c.dataset.url,
             titleNow: c.dataset.title, listTitle: c.dataset.list }};
  }});
  var t = document.getElementById('out');
  t.style.display = 'block';
  t.value = JSON.stringify(out, null, 2);
  t.select();
  document.execCommand('copy');
  document.getElementById('cnt').textContent = '已勾选 ' + out.length + ' 条（已复制到剪贴板）';
}}
document.addEventListener('change', function(ev) {{
  if (ev.target && ev.target.classList.contains('pick')) update();
}});
</script>
</body>
</html>'''

open(OUT, 'w', encoding='utf-8').write(html_out)
print('已生成:', OUT)
for code, title, _ in GROUP_META:
    print('  %s组 %-30s %d 条' % (code, title.split('：')[0], len(groups[code])))
print('  合计:', total)
