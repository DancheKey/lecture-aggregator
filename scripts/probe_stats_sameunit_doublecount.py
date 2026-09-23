# -*- coding: utf-8 -*-
"""探针：验证「同单位跨源融合」后，统计页矩阵是否会把同一讲座在该单位算两次。

直接复刻 scripts/generate_frontend_data.py 里 _build_stats 的核心去重循环
（lines 168-178），用合成数据跑，打印每个单位的计数，证明：
  - 同单位融合记录 → 该单位只 +1（不会翻倍）
  - 跨单位融合记录 → 每个相关单位各 +1（符合既有口径）
  - 两条独立同单位记录 → 该单位 +2（真实的同日多场，正确）
  - 顶部「来源通知总数」按 sourceCount 求和（同单位融合=2，属正常）

本探针只验证统计计数口径，不改任何代码。
"""
import json
from collections import defaultdict


def year_of(item):
    ls = item.get('lectureStart') or ''
    return ls[:4] if ls[:4].isdigit() else '其他'


def build_matrix(data):
    """复刻 generate_frontend_data.py 的矩阵去重逻辑。"""
    matrix = defaultdict(lambda: defaultdict(int))
    source_notice_count = 0
    for item in data:
        y = year_of(item)
        sources = item.get('sources') or [item]
        sc = item.get('sourceCount')
        s_count = sc if sc is not None else (len(sources) or 1)
        source_notice_count += s_count

        primary_college = item.get('college') or '未分类'
        units = []
        seen_unit = set()
        for c in [primary_college] + [s.get('college') for s in sources if s.get('college')]:
            if c and c not in seen_unit:
                seen_unit.add(c)
                units.append(c)
        for c in units:
            matrix[c][y] += 1
    return dict(matrix), source_notice_count, len(data)


def show(title, data):
    matrix, snc, lc = build_matrix(data)
    print('=' * 70)
    print('场景:', title)
    print('-' * 70)
    for c, ym in sorted(matrix.items()):
        total = sum(ym.values())
        print(f'  单位 {c!r}: 计数 = {total}  (分年 {dict(ym)})')
    print(f'  来源通知总数 sourceNoticeCount = {snc}')
    print(f'  唯一讲座总数 lectureCount = {lc}')
    print()


def make_merged(primary_college, source_colleges, source_count, year='2025'):
    sources = [{'sourceUrl': f'http://x/{i}', 'college': cc, 'campus': '', 'title': '同一讲座'}
               for i, cc in enumerate(source_colleges)]
    return {
        'sourceUrl': 'http://x/0',
        'college': primary_college,
        'lectureStart': f'{year}-10-13 09:00:00',
        'sources': sources,
        'merged': True,
        'sourceCount': source_count,
    }


if __name__ == '__main__':
    print('注：以下计数口径完全复刻 generate_frontend_data.py 的矩阵去重循环。\n')

    # 场景1：同单位融合（物理学院先发 + 物理学院后发同一讲座）
    show('1) 同单位融合（主=物理，来源=物理）',
         [make_merged('物理学院', ['物理学院', '物理学院'], 2)])

    # 场景2：跨单位融合（主=物理，来源=化学）→ 两个单位各+1
    show('2) 跨单位融合（主=物理，来源=化学）',
         [make_merged('物理学院', ['物理学院', '化学学院'], 2)])

    # 场景3：两条独立的同单位讲座（真实的同日多场，如李红岩上午/下午）
    show('3) 两条独立同单位记录（同日多场，应各+1 → 该单位+2）',
         [
             {'sourceUrl': 'http://a/1', 'college': '历史文化学院',
              'lectureStart': '2025-10-13 09:00:00'},
             {'sourceUrl': 'http://a/2', 'college': '历史文化学院',
              'lectureStart': '2025-10-13 15:00:00'},
         ])

    # 场景4：同单位融合 + 一条独立同单位记录 → 融合那条+1、独立那条+1
    show('4) 同单位融合 + 一条独立同单位记录',
         [
             make_merged('物理学院', ['物理学院', '物理学院'], 2),
             {'sourceUrl': 'http://b/9', 'college': '物理学院',
              'lectureStart': '2025-11-15 14:00:00'},
         ])
