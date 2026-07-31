#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成讲座重复记录排查清单，排除同 URL 多场次拆分。"""
import json
import re
from collections import defaultdict
from html import escape

DATA = 'data/lectures.json'
OUT = 'tmp/duplicate_lectures_report.html'


def norm_title(t):
    if not t:
        return ''
    t = t.strip()
    # 去掉首尾常见书名号、引号
    t = re.sub(r'^[“""『《]+|[”""』》]+$', '', t)
    # 合并空白
    t = re.sub(r'\s+', ' ', t)
    return t


def norm_speaker(s):
    if not s:
        return ''
    s = s.strip()
    # 去掉头衔后缀，如 "教授"、"博士"、"（单位）"
    s = re.sub(r'[，,（(].*?[）)]$', '', s)
    s = re.sub(r'(教授|副教授|讲师|博士|研究员|助理研究员|院长|主任|书记)$', '', s)
    return s.strip()


def fmt_time(r):
    s = r.get('lectureStart') or ''
    if len(s) >= 16:
        return s[:16].replace('T', ' ')
    return s


def link(url):
    return f'<a href="{escape(url)}" target="_blank" rel="noopener">原页↗</a>'


def main():
    with open(DATA, 'r', encoding='utf-8') as f:
        d = json.load(f)
    recs = d.get('data', [])

    # 一、同源同标题同日但不同 URL
    same_src = defaultdict(list)
    for r in recs:
        key = (
            r.get('college') or '',
            norm_title(r.get('title') or r.get('topic') or ''),
            (r.get('lectureStart') or '')[:10]
        )
        if not key[1]:
            continue
        same_src[key].append(r)

    same_src_groups = []
    for key, items in same_src.items():
        urls = {it.get('sourceUrl') for it in items}
        if len(urls) > 1:
            same_src_groups.append((key, items))
    same_src_groups.sort(key=lambda x: (x[0][0], x[0][2], x[0][1]))

    # 二、跨单位同主讲人同日
    cross = defaultdict(list)
    for r in recs:
        sp = norm_speaker(r.get('speaker') or '')
        if not sp:
            continue
        key = (sp, (r.get('lectureStart') or '')[:10])
        cross[key].append(r)

    cross_groups = []
    for key, items in cross.items():
        colleges = {it.get('college') for it in items}
        if len(colleges) > 1:
            cross_groups.append((key, items))
    cross_groups.sort(key=lambda x: (x[0][1], x[0][0]))

    html = '''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>讲座重复记录排查清单</title>
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; margin: 0; padding: 24px; background:#f6f7fb; color:#1f2330; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 24px 0 8px; color:#2f3b52; }
.meta { color:#666; font-size: 13px; margin-bottom: 16px; }
table { border-collapse: collapse; width: 100%%; background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 1px 4px rgba(0,0,0,.08); margin-bottom:24px; }
th, td { text-align: left; padding: 8px 10px; font-size: 13px; border-bottom: 1px solid #eef0f4; vertical-align: top; }
th { background:#2f3b52; color:#fff; font-weight:600; }
tr.grp td { background:#eef2f8; font-weight:700; color:#2f3b52; padding:6px 10px; }
.title { max-width: 300px; }
.topic { color:#1a73e8; font-size:12px; }
.spk { width:140px; }
.sub { color:#888; font-size:12px; }
.time { width:130px; white-space:nowrap; }
.merged { width:90px; color:#666; font-size:12px; }
.college { width:100px; }
.link a { color:#1a73e8; text-decoration:none; white-space:nowrap; }
.link a:hover { text-decoration: underline; }
tr:hover td { background:#fafbff; }
</style></head>
<body>
<h1>讲座重复记录排查清单</h1>
<div class="meta">总记录数 %d。下面列出疑似重复/冗余的记录，供复查。同 URL 的多讲座拆分（同一通知里的第 N 期）已排除。</div>
''' % len(recs)

    html += '<h2>一、同源（同单位）同标题同日期但来源 URL 不同：%d 组，%d 条</h2>\n<table>\n' % (
        len(same_src_groups), sum(len(items) for _, items in same_src_groups)
    )
    html += '<thead><tr><th>单位</th><th>标题 / 主题</th><th>主讲人</th><th>时间</th><th>合并状态</th><th>来源</th></tr></thead>\n<tbody>\n'
    for (college, title, day), items in same_src_groups:
        html += '<tr class="grp"><td colspan="6">▸ %s | %s | %s（%d 条）</td></tr>\n' % (
            escape(college), escape(title), escape(day), len(items)
        )
        for r in sorted(items, key=lambda x: (x.get('lectureIndex') or 0, x.get('sourceUrl') or '')):
            topic = r.get('topic') or ''
            topic_html = '<br><span class="topic">↳ %s</span>' % escape(topic) if topic else ''
            spk = escape(r.get('speaker') or '')
            aff = r.get('speakerAffiliation') or ''
            aff_html = '<br><span class="sub">%s</span>' % escape(aff) if aff else ''
            merged = r.get('merged')
            sources = r.get('sources')
            merged_html = 'merged=%s<br>sources=%s' % (
                '是' if merged else '否',
                len(sources) if isinstance(sources, list) else '-'
            )
            html += '<tr><td class="college">%s</td><td class="title">%s%s</td><td class="spk">%s%s</td><td class="time">%s</td><td class="merged">%s</td><td class="link">%s</td></tr>\n' % (
                escape(r.get('college') or ''),
                escape(r.get('title') or ''),
                topic_html,
                spk, aff_html,
                escape(fmt_time(r)),
                merged_html,
                link(r.get('sourceUrl') or '')
            )
    html += '</tbody></table>\n'

    html += '<h2>二、跨单位同主讲人同日期：%d 组，%d 条</h2>\n<table>\n' % (
        len(cross_groups), sum(len(items) for _, items in cross_groups)
    )
    html += '<thead><tr><th>单位</th><th>标题 / 主题</th><th>主讲人</th><th>时间</th><th>合并状态</th><th>来源</th></tr></thead>\n<tbody>\n'
    for (speaker, day), items in cross_groups:
        html += '<tr class="grp"><td colspan="6">▸ %s @ %s（%d 条）</td></tr>\n' % (
            escape(speaker), escape(day), len(items)
        )
        for r in sorted(items, key=lambda x: (x.get('college') or '', x.get('sourceUrl') or '')):
            topic = r.get('topic') or ''
            topic_html = '<br><span class="topic">↳ %s</span>' % escape(topic) if topic else ''
            spk = escape(r.get('speaker') or '')
            aff = r.get('speakerAffiliation') or ''
            aff_html = '<br><span class="sub">%s</span>' % escape(aff) if aff else ''
            merged = r.get('merged')
            sources = r.get('sources')
            merged_html = 'merged=%s<br>sources=%s' % (
                '是' if merged else '否',
                len(sources) if isinstance(sources, list) else '-'
            )
            html += '<tr><td class="college">%s</td><td class="title">%s%s</td><td class="spk">%s%s</td><td class="time">%s</td><td class="merged">%s</td><td class="link">%s</td></tr>\n' % (
                escape(r.get('college') or ''),
                escape(r.get('title') or ''),
                topic_html,
                spk, aff_html,
                escape(fmt_time(r)),
                merged_html,
                link(r.get('sourceUrl') or '')
            )
    html += '</tbody></table>\n</body></html>'

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print('[done] %s: 一组 %d 组 %d 条，二组 %d 组 %d 条' % (
        OUT,
        len(same_src_groups), sum(len(items) for _, items in same_src_groups),
        len(cross_groups), sum(len(items) for _, items in cross_groups)
    ))


if __name__ == '__main__':
    main()
