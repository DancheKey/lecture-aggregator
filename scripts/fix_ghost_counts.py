# -*- coding: utf-8 -*-
"""幽灵计数修复：拆分页 index/count 与库内实际条数失配的存量清洗。

背景（2026-09-26 全局审计）：拆分编号后，MS5 逐条回顾稿过滤会剔除部分场次，
但 lectureIndex/lectureCount 未重算——库里只剩 M(<N) 条却标着「第K期/共N期」
（xz/252 标 5/5 仅存 1 条、ibc/2779 标 2/2 仅存 1 条、ai/163 编号 3-6 共 4 条等）。
解析器已在 parsers.py MS5 过滤点同步修复（剔除后重排），本脚本清洗存量。

规范化策略（与解析器一致）：
- M≥2：按原编号顺序重排 1..M，lectureCount=M；
- M==1：视为单场——isMultiLecture=False、lectureIndex/lectureCount 置空
  （系列位次保留在 title/topic 文本，如「第5讲丨…」，信息不丢失）；
- 顺带删除 1 条历史残留键 forumConference（机制早已不存在，文档 §1.5 已废弃）。

用法：python scripts/fix_ghost_counts.py [--apply]
"""
import os
import sys
import json
import shutil
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')


def ghost_groups(recs):
    """返回 {(sourceUrl): [records]} —— index/count 与实际条数失配的拆分组。"""
    from collections import defaultdict
    bysrc = defaultdict(list)
    for r in recs:
        bysrc[r.get('sourceUrl')].append(r)
    ghosts = {}
    for u, v in bysrc.items():
        n = len(v)
        idxs = sorted(r.get('lectureIndex') for r in v
                      if r.get('lectureIndex') is not None)
        counts = {r.get('lectureCount') for r in v}
        bad = False
        if n > 1:
            if any(r.get('lectureIndex') is None for r in v):
                bad = True
            elif idxs != list(range(1, n + 1)):
                bad = True
            if counts != {n}:
                bad = True
        else:
            r = v[0]
            if (r.get('lectureCount') not in (None, 1)
                    or r.get('lectureIndex') is not None
                    or (r.get('isMultiLecture') and (r.get('lectureCount') or 1) <= 1)):
                bad = True
        if bad:
            ghosts[u] = v
    return ghosts


def main():
    apply_mode = '--apply' in sys.argv
    data = json.load(open(DATA_PATH, encoding='utf-8'))
    recs = data['data']
    ghosts = ghost_groups(recs)
    print(f'发现幽灵组 {len(ghosts)} 页:')
    for u, v in sorted(ghosts.items()):
        idxs = [(r.get('lectureIndex'), r.get('lectureCount')) for r in v]
        print(f'  {len(v)}条 现标{idxs[:3]}{"..." if len(v) > 3 else ""}  {u}')

    n_fixed = 0
    for u, v in ghosts.items():
        m = len(v)
        order = sorted(v, key=lambda r: (r.get('lectureIndex') is None,
                                         r.get('lectureIndex') or 0,
                                         r.get('lectureStart') or ''))
        if m >= 2:
            for i, r in enumerate(order, 1):
                r['lectureIndex'] = i
                r['lectureCount'] = m
                r['isMultiLecture'] = True
        else:
            r = order[0]
            r['isMultiLecture'] = False
            r.pop('lectureIndex', None)
            r.pop('lectureCount', None)
            r.pop('sessionNumber', None)
        n_fixed += 1

    n_fc = 0
    for r in recs:
        if 'forumConference' in r:
            r.pop('forumConference')
            n_fc += 1

    print(f'\n规范化 {n_fixed} 页；删除 forumConference 残留 {n_fc} 条')
    if not apply_mode:
        print('演练结束（未落库）。加 --apply 落库。')
        return

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = f'{DATA_PATH}.bak-{ts}-ghostfix'
    shutil.copyfile(DATA_PATH, bak)
    print(f'已备份 → {bak}')
    data['updatedAt'] = datetime.datetime.now().isoformat(timespec='seconds')
    with open(DATA_PATH, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'落库完成，共 {len(recs)} 条')


if __name__ == '__main__':
    main()
