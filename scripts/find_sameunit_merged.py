# -*- coding: utf-8 -*-
"""扫描 data/lectures.json，找出「同单位多源融合」的真实记录并附链接。

两类：
  A. merged=True 且主记录 college 与某个 source 的 college 相同（增量新逻辑产物）
  B. 有 sources 但 merged!=True，且主记录 college 与某个 source college 相同
     （dedup_existing.py 对同单位合并的写法：同单位不打 merged 标记）
"""
import json

PATH = 'data/lectures.json'


def main():
    d = json.load(open(PATH, encoding='utf-8'))
    data = d['data']
    print(f'库总条数: {len(data)}\n')

    a_hits, b_hits = [], []

    for r in data:
        primary = r.get('college', '')
        sources = r.get('sources') or []
        src_colleges = [s.get('college', '') for s in sources if s.get('college')]
        same_in_src = [c for c in src_colleges if c and c == primary]

        if r.get('merged') and same_in_src:
            a_hits.append(r)
        elif (not r.get('merged')) and sources and same_in_src:
            b_hits.append(r)

    print('=' * 78)
    print(f'类型 A：merged=True 且来源含同单位  →  {len(a_hits)} 条')
    print('=' * 78)
    for r in a_hits:
        _show(r)

    print('=' * 78)
    print(f'类型 B：有 sources、merged 空、来源含同单位（dedup_existing 同单位合并写法）  →  {len(b_hits)} 条')
    print('=' * 78)
    for r in b_hits:
        _show(r)

    if not a_hits and not b_hits:
        print('（库中当前无任何同单位融合记录）')


def _show(r):
    primary = r.get('college', '')
    sources = r.get('sources') or []
    print(f"\n主记录 college = {primary}  | merged={r.get('merged')}  | sourceCount={r.get('sourceCount')}")
    print(f"  topic : {r.get('topic') or r.get('title')}")
    print(f"  主链接: {r.get('sourceUrl')}")
    print(f"  来源列表({len(sources)}):")
    for s in sources:
        tag = '  ← 同单位' if s.get('college') == primary else ''
        print(f"    - {s.get('college')}: {s.get('sourceUrl')}{tag}")


if __name__ == '__main__':
    main()
