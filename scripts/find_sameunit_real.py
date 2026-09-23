# -*- coding: utf-8 -*-
"""用 scraper.py 生产环境的真实检测逻辑，扫全库找出「同单位会被融合」的真实组。

直接复用：
  - _normalize_speaker / _is_valid_speaker_name
  - _cross_source_dup_with_existing(rec, existing_index, no_speaker_index)
  - 索引构建与 incremental_merge 完全一致
这样列出来的就是上线后增量阶段「真的会融合」的对象，而非粗筛误报。
"""
import json
import sys

sys.path.insert(0, 'scraper')
import scraper as S


def build_indexes(data):
    existing_index = {}
    no_speaker_index = {}
    for r in data:
        spk = S._normalize_speaker(r.get('speaker') or '')
        date = (r.get('lectureStart') or '')[:10]
        if S._is_valid_speaker_name(spk) and date and not date.startswith('0000'):
            existing_index.setdefault((spk, date), []).append(r)
        else:
            # 无有效主讲人但有单位 → 进 no_speaker_index
            if r.get('college') and date and not date.startswith('0000'):
                no_speaker_index.setdefault((r['college'], date), []).append(r)
    return existing_index, no_speaker_index


def main():
    d = json.load(open('data/lectures.json', encoding='utf-8'))
    data = d['data']
    existing_index, no_speaker_index = build_indexes(data)

    print(f'库总条数: {len(data)}')
    print(f'有主讲人索引组: {len(existing_index)}  无主讲人索引组: {len(no_speaker_index)}\n')

    # 对每条记录，检测它是否会被判为与库内某条同单位重复
    pairs = []  # (rec, matched)
    seen_keys = set()
    for r in data:
        key = (r.get('sourceUrl'),)
        matched = S._cross_source_dup_with_existing(r, existing_index, no_speaker_index)
        if matched is not None and matched.get('sourceUrl') != r.get('sourceUrl'):
            # 只保留同单位的（我们要看同单位融合）
            if matched.get('college') == r.get('college'):
                pairs.append((r, matched))

    # 去重并分组展示
    shown = set()
    group_id = 0
    for r, matched in pairs:
        a_url = r.get('sourceUrl')
        b_url = matched.get('sourceUrl')
        sig = tuple(sorted([a_url, b_url]))
        if sig in shown:
            continue
        shown.add(sig)
        group_id += 1
        print('=' * 78)
        print(f'【同单位融合组 {group_id}】单位={r.get("college")}')
        for x in (matched, r):
            print(f"   链接: {x.get('sourceUrl')}")
            print(f"   标题: {x.get('title')}")
            print(f"   题目: {x.get('topic') or '(空)'}")
            print(f"   时间: {x.get('lectureStart')}  地点: {x.get('location') or '(空)'}")
            print(f"   主讲: {x.get('speaker') or '(空)'}")
            print()
    if group_id == 0:
        print('（按生产逻辑，库内当前无「同单位会被融合」的记录）')
        print('  说明：现有同单位记录要么是不同的多场讲座（不同主讲/日期/题目），')
        print('        要么已经被旧逻辑 skip 丢弃从未进库，所以库里没有待融合的同单位对。')


if __name__ == '__main__':
    main()
