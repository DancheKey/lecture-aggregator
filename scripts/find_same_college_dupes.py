# -*- coding: utf-8 -*-
"""在 data/lectures.json 中找出「同单位（college）+ 同归一化主讲人 + 同讲座日」的多条记录组。

用途：回答用户疑问——库里到底有没有「同单位无轮次标记的重复讲座」真实案例。
不修改任何数据，只读库。

运行：D:/Tools/Python 312/python.exe scripts/find_same_college_dupes.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from scraper.scraper import _normalize_speaker, _is_valid_speaker_name, _has_round_marker


def main():
    path = os.path.join(ROOT, 'data', 'lectures.json')
    d = json.load(open(path, encoding='utf-8'))
    recs = d.get('data', [])
    print(f'总记录数: {len(recs)}')

    # 按 (college, 归一化主讲, 讲座日) 分组
    groups = {}
    for i, r in enumerate(recs):
        spk = _normalize_speaker(r.get('speaker') or '')
        if not _is_valid_speaker_name(spk):
            continue
        date = (r.get('lectureStart') or '')[:10]
        if not date or date.startswith('0000'):
            continue
        c = r.get('college', '') or ''
        key = (c, spk, date)
        groups.setdefault(key, []).append((i, r))

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    print(f'同(college,主讲,日) 且组内>1条 的分组数: {len(dup_groups)}')

    shown = 0
    for (c, spk, date), items in sorted(dup_groups.items(), key=lambda kv: (kv[0][0], kv[0][2])):
        # 标记是否含轮次标记
        has_marker_any = any(_has_round_marker(r.get('title')) for _, r in items)
        merged_any = any(r.get('merged') for _, r in items)
        # 输出所有同 college 同日同主演的多条组（含同单位无标记场景）
        if shown >= 40:
            print('... (已截断，更多见脚本)')
            break
        print('=' * 72)
        print(f'college={c} | 主讲={spk} | 日期={date} | 组内条数={len(items)} | 含轮次标记={has_marker_any} | 已merged={merged_any}')
        for i, r in items:
            title = r.get('title', '')
            topic = r.get('topic', '')
            ls = (r.get('lectureStart') or '')[:16]
            src = r.get('sourceUrl', '')
            marker = '轮次标记' if _has_round_marker(title) else '无标记'
            print(f'  [{i}] {marker} ls={ls}')
            print(f'       title={title!r}')
            print(f'       topic={topic!r}')
            print(f'       src={src}')
        shown += 1


if __name__ == '__main__':
    main()
