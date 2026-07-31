#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成清理结果清单 HTML：列出 cross_source_dedup 合并掉的冗余记录（按主讲+日期归组）。"""
import json, copy, sys, html
sys.path.insert(0, 'scraper')
import scraper as S

bak = json.load(open('data/lectures.json.bak_20260731', encoding='utf-8'))['data']
after_doc = json.load(open('data/lectures.json', encoding='utf-8'))
after = after_doc['data']

def key_of(r):
    u = (r.get('sourceUrl') or '').rstrip('/')
    li = r.get('lectureIndex')
    return u + ('#' + str(li) if li else '')

before_map = {key_of(r): r for r in bak}
after_keys = set(key_of(r) for r in after)
removed = [(k, before_map[k]) for k in before_map if k not in after_keys]

# after 按 (speaker,date) 索引
after_idx = {}
for r in after:
    spk = S._normalize_speaker(r.get('speaker') or '')
    if not S._is_valid_speaker_name(spk):
        continue
    d = (r.get('lectureStart') or '')[:10]
    if d and not d.startswith('0000'):
        after_idx.setdefault((spk, d), []).append(r)

groups = []
for k, r in removed:
    spk = S._normalize_speaker(r.get('speaker') or '')
    d = (r.get('lectureStart') or '')[:10]
    cands = after_idx.get((spk, d), [])
    if not cands:
        primary = None
    else:
        primary = max(cands, key=lambda c: max(
            S._topic_similarity(r.get('title', ''), c.get('title', '')),
            S._topic_similarity(r.get('topic', '') or r.get('title', ''), c.get('topic', '') or c.get('title', '')),
        ))
    groups.append((spk, d, r, primary))

# 按 (spk,date,primary_url) 归组
from collections import OrderedDict
grp = OrderedDict()
for spk, d, r, primary in groups:
    pkey = (spk, d, primary.get('sourceUrl') if primary else '???')
    grp.setdefault(pkey, {'primary': primary, 'removed': []})
    grp[pkey]['removed'].append(r)

rows = []
for (spk, d, purl), info in grp.items():
    p = info['primary']
    pcollege = p.get('college', '') if p else ''
    ptitle = (p.get('topic') or p.get('title'))[:60] if p else '（无主记录）'
    pmerged = 'merged=是' if (p and p.get('merged')) else 'merged=否'
    urls = '；'.join(html.escape(x.get('sourceUrl', '')) for x in info['removed'])
    rows.append((spk, d, pcollege, ptitle, pmerged, len(info['removed']), urls))

rows.sort(key=lambda x: (x[0], x[1]))

h = '''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>清理结果清单（合并掉 63 条冗余）</title>
<style>
* { box-sizing: border-box; } body { font-family: -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; margin:0; padding:24px; background:#f6f7fb; color:#1f2330; }
h1 { font-size:20px; margin:0 0 4px; } .meta { color:#666; font-size:13px; margin-bottom:16px; }
table { border-collapse:collapse; width:100%%; background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 1px 4px rgba(0,0,0,.08); }
th,td { text-align:left; padding:8px 10px; font-size:13px; border-bottom:1px solid #eef0f4; vertical-align:top; }
th { background:#2f3b52; color:#fff; font-weight:600; }
tr:hover td { background:#fafbff; }
.url { font-size:11px; color:#1a73e8; word-break:break-all; }
.mono { font-family:monospace; }
</style></head><body>
<h1>清理结果清单：合并掉 %d 条冗余记录</h1>
<div class="meta">数据源 data/lectures.json：3173 → 3110（删 %d 条）。下表按主讲人+日期归组，每组显示保留的主记录与并入的来源 URL。同单位合并不标 merged；跨单位转载标 merged=是。</div>
<table><thead><tr><th>主讲</th><th>日期</th><th>保留单位</th><th>保留标题/题目</th><th>状态</th><th>并入数</th><th>被并入的来源 URL</th></tr></thead><tbody>
''' % (len(removed), len(removed))

for spk, d, pcollege, ptitle, pmerged, n, urls in rows:
    h += '<tr><td>%s</td><td class="mono">%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td class="url">%s</td></tr>\n' % (
        html.escape(spk), html.escape(d), html.escape(pcollege), html.escape(ptitle),
        html.escape(pmerged), n, urls)

h += '</tbody></table></body></html>'
open('tmp/cleanup_report.html', 'w', encoding='utf-8').write(h)
print('[done] tmp/cleanup_report.html 组数=%d 总删=%d' % (len(rows), len(removed)))
