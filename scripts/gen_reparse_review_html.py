# -*- coding: utf-8 -*-
"""把重解析差异报告里「需人工判断」的几类生成可点击 HTML 清单。

分类取 scripts/reparse_diff_report.py 的口径：
  split_reverted   多场回退为单条（重点：可能是回归）
  split_new        旧1条→新拆多场（升级收益）
  split_count_change 已拆页场次数变化
  parse_empty      新解析判空（全量重爬会丢失）
输出：reports/reparse-review-<日期>.html
"""
import json
import os
import datetime
import html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, 'reports', 'reparse-diff-20260926.json')
OUT = os.path.join(ROOT, 'reports', 'reparse-review-20260930.html')

data = json.load(open(REPORT, encoding='utf-8'))
recs = data['records'] if isinstance(data, dict) and 'records' in data else data


def by_cat(c):
    return [r for r in recs if r.get('cat') == c]


def e(s):
    return html.escape(str(s or ''))


def topics(ts):
    return '<br>'.join(f'· {e(t[1])} <span class="dim">{e(t[2] or "")}</span>'
                       for t in ts) or '<span class="dim">—</span>'


def old_topics(ts):
    return '<br>'.join(f'· {e(t[1])}' for t in ts) or '<span class="dim">—</span>'


sections = [
    ('split_reverted', '多场回退为单条（需人工判断是否回归）',
     lambda r: f'<td>{r.get("old")} → {r.get("new")}</td>'
               f'<td>{old_topics(r.get("old_topics", []))}</td>'
               f'<td class="new">{e(r.get("new_topic", ""))}</td>',
     '<th>场次</th><th>旧各场题目</th><th>新题目</th>'),
    ('split_new', '旧 1 条 → 新拆多场（升级收益）',
     lambda r: f'<td>{r.get("old")} → {r.get("new")}</td>'
               f'<td class="new">{topics(r.get("new_topics", []))}</td>',
     '<th>场次</th><th>新各场题目 / 时间</th>'),
    ('split_count_change', '已拆页场次数变化',
     lambda r: f'<td>{r.get("old")} → {r.get("new")}'
               f'{" ⚠人工拆分页" if r.get("manual") else ""}</td>'
               f'<td class="new">{topics(r.get("new_topics", []))}</td>',
     '<th>场次</th><th>新各场题目 / 时间</th>'),
    ('parse_empty', '新解析判空（全量重爬会丢失）',
     lambda r: f'<td>{r.get("old")} → 0</td><td class="warn">重解析未产出记录</td>',
     '<th>场次</th><th>说明</th>'),
    ('unreachable', '源页不可达（本次未取到 HTML）',
     lambda r: f'<td>{r.get("old")}</td><td class="dim">404 / 超时，无法重解析</td>',
     '<th>旧条数</th><th>说明</th>'),
]

counts = {c: len(by_cat(c)) for c, _, _, _ in sections}
total = sum(counts.values())

parts = []
for cat, title, rowfn, head in sections:
    rows = by_cat(cat)
    if not rows:
        continue
    body = '\n'.join(
        f'<tr><td class="num">{i}</td>'
        f'<td><a href="{e(r["url"])}" target="_blank">{e(r["url"])}</a></td>'
        f'{rowfn(r)}</tr>'
        for i, r in enumerate(rows, 1))
    parts.append(f'''
<h2>{e(title)} <span class="badge">{len(rows)}</span></h2>
<table>
<thead><tr><th class="num">#</th><th>源页链接</th>{head}</tr></thead>
<tbody>
{body}
</tbody>
</table>''')

now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
html_out = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>重解析差异 · 人工核实清单</title>
<style>
  body {{ font-family: "Microsoft YaHei", "微软雅黑", sans-serif; background:#f7f8fa;
         color:#1f2328; margin:0; padding:32px 20px; }}
  .wrap {{ max-width:1180px; margin:0 auto; background:#fff; padding:28px 32px;
           border-radius:10px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  h1 {{ font-size:22px; margin:0 0 6px; }}
  .sub {{ color:#6b7280; font-size:13px; margin-bottom:18px; }}
  h2 {{ font-size:16px; margin:28px 0 10px; padding-left:9px; border-left:4px solid #2563eb; }}
  .badge {{ display:inline-block; background:#eef2ff; color:#4338ca; font-size:12px;
            padding:1px 8px; border-radius:10px; margin-left:6px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ border:1px solid #e5e7eb; padding:7px 10px; text-align:left; vertical-align:top; }}
  th {{ background:#f3f4f6; font-weight:600; white-space:nowrap; }}
  td.num, th.num {{ width:42px; text-align:center; color:#6b7280; }}
  tr:nth-child(even) td {{ background:#fafbfc; }}
  a {{ color:#2563eb; text-decoration:none; word-break:break-all; }}
  a:hover {{ text-decoration:underline; }}
  .dim {{ color:#9ca3af; }}
  .new {{ color:#b91c1c; }}
  .warn {{ color:#b45309; }}
  .note {{ background:#fffbeb; border:1px solid #fde68a; padding:12px 14px;
           border-radius:6px; font-size:13px; margin:16px 0; line-height:1.7; }}
</style>
</head>
<body>
<div class="wrap">
<h1>重解析差异 · 人工核实清单</h1>
<div class="sub">生成于 {now} · 共 {total} 条待核实 · 数据源 reports/reparse-diff-20260926.json（3527 页全量重解析）</div>
<div class="note">
<b>口径说明</b>：本次重解析是<b>纯规则层</b>（关闭 LLM/VLM），与生产抓取口径不同。
因此 <code>field_diff</code> 中的差异混杂「通道差异」（旧值来自 LLM/VLM 富化，重解析抽不到）
与「规则升级差异」，<b>不能直接作为落库依据</b>，故本清单只列拆分/判空/失联四类需人工定夺的项。
标注 <b>⚠人工拆分页</b> 的记录按项目约定不作为落库建议。
</div>
{''.join(parts)}
</div>
</body>
</html>'''

open(OUT, 'w', encoding='utf-8').write(html_out)
print('已生成:', OUT)
for c, n in counts.items():
    print(f'  {c:20s} {n}')
