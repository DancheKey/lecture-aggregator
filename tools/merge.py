"""合并重复讲座为单张卡片，同时保留所有来源单位标签与原文链接。

判定规则（用户确认）：时间相同 + 主讲人相同 + 题目相似（≥80%）。
合并后：
  - 主卡保留信息最全者的字段，并用其他来源的非空字段补全缺失项；
  - 主卡新增 `sources`（深拷贝各来源完整记录，去嵌套）与 `merged`/`sourceCount`；
  - 首页按去重后的主卡展示（总数=去重讲座数）；
  - 统计页遍历 `sources` 展开计数，使「各学院/部处之和」= 原始发布条数，
    并保留「去重讲座数」与「来源通知数」两个口径。
  - 顺带把被合并来源的本地访问/点赞统计并入主卡 url，保证统计连续。

幂等：重跑前先按 `sources` 展开还原，再重新聚类，可反复执行。
"""
import json
import os
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
STATS_PATH = os.path.join(ROOT, 'data', 'lecture_stats.json')

# 合并时优先补全的字段
FILL_FIELDS = [
    'topic', 'location', 'speaker', 'speakerAffiliation',
    'speakerBio', 'abstract', 'lectureStart', 'lectureEnd',
]


def norm_speaker(s):
    """提取主讲人姓名核心，去掉职称与括号内容。"""
    if not s:
        return ''
    s = s.strip()
    s = re.sub(r'[（\(].*?[）\)]', '', s)  # 去掉括号
    s = re.sub(r'\s+', '', s)
    s = re.sub(
        r'(教授|副教授|研究员|副研究员|讲师|博士|硕士|主任|院长|所长|'
        r'同学|先生|女士|老师|特聘|访问学者|博士后|院士)\s*$', '', s)
    return s.strip()


def speaker_core(l):
    return norm_speaker(l.get('speaker', ''))


def title_text(l):
    return (l.get('topic') or l.get('title') or '').strip()


def norm_text(s):
    """去掉所有空白与标点，仅保留中英文数字，用于相似度比较。"""
    return re.sub(r'[\s\W_]+', '', s or '')


def similar(a, b, threshold=0.8):
    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return False
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def same_day(a, b):
    if not a or not b:
        return False
    return str(a)[:10] == str(b)[:10]


def expand_sources(data):
    """幂等还原：把已合并主卡展开为原始来源列表。"""
    out = []
    for l in data:
        srcs = l.get('sources')
        if srcs:
            for s in srcs:
                rec = {k: v for k, v in s.items() if k not in ('sources', 'merged')}
                out.append(rec)
        else:
            out.append(l)
    return out


def pick_primary(cluster):
    """选择字段最丰富的成员作为主卡。"""
    def score(l):
        return sum(1 for k in FILL_FIELDS if str(l.get(k) or '').strip())
    return max(cluster, key=score)


def merge_records(cluster):
    primary = dict(pick_primary(cluster))
    # 用其他来源的非空字段补全主卡缺失项
    for k in FILL_FIELDS:
        if not str(primary.get(k) or '').strip():
            for m in cluster:
                v = str(m.get(k) or '').strip()
                if v:
                    primary[k] = m[k]
                    break
    # 来源记录：深拷贝各成员，去掉嵌套字段避免膨胀
    sources = []
    for m in cluster:
        rec = {k: v for k, v in m.items() if k not in ('sources', 'merged')}
        sources.append(rec)
    primary['sources'] = sources
    primary['merged'] = len(cluster) > 1
    primary['sourceCount'] = len(cluster)
    return primary


def merge_local_stats(merged):
    """把被合并来源的本地访问/点赞统计并入主卡 url。"""
    if not os.path.exists(STATS_PATH):
        return
    try:
        stats = json.load(open(STATS_PATH, encoding='utf-8'))
    except Exception:
        return
    urlmap = {}
    for m in merged:
        for s in m.get('sources', []):
            su = s.get('sourceUrl')
            mu = m.get('sourceUrl')
            if su and mu and su != mu:
                urlmap[su] = mu
    if not urlmap:
        return
    changed = False
    for old, new in urlmap.items():
        if old in stats and old != new:
            st = stats.pop(old)
            target = stats.setdefault(new, {'visits': 0, 'likes': 0})
            target['visits'] = (target.get('visits', 0) + st.get('visits', 0))
            target['likes'] = (target.get('likes', 0) + st.get('likes', 0))
            changed = True
    if changed:
        json.dump(stats, open(STATS_PATH, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        print('[merge] 已合并本地统计文件中的来源 url 到主卡。')


def main():
    with open(DATA_PATH, encoding='utf-8') as f:
        raw = json.load(f)
    meta = {k: v for k, v in raw.items() if k != 'data'} if isinstance(raw, dict) else {}
    data = raw['data'] if isinstance(raw, dict) else raw

    # 幂等：先展开还原
    data = expand_sources(data)
    original_count = len(data)

    used = [False] * len(data)
    merged = []
    for i, li in enumerate(data):
        if used[i]:
            continue
        used[i] = True
        cluster = [li]
        si = speaker_core(li)
        for j in range(i + 1, len(data)):
            if used[j]:
                continue
            lj = data[j]
            sj = speaker_core(lj)
            # 规则：主讲人相同 + 时间相同 + 题目相似
            if not si or not sj or si != sj:
                continue
            if not same_day(li.get('lectureStart'), lj.get('lectureStart')):
                continue
            if not similar(title_text(li), title_text(lj)):
                continue
            used[j] = True
            cluster.append(lj)
        merged.append(merge_records(cluster))

    # 按讲座时间倒序
    merged.sort(key=lambda l: str(l.get('lectureStart') or ''), reverse=True)

    out = dict(meta)
    out['data'] = merged
    from datetime import datetime, timezone, timedelta
    out['updatedAt'] = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%dT%H:%M:%S+08:00')

    with open(DATA_PATH, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    merge_local_stats(merged)

    n_merged = sum(1 for l in merged if l.get('merged'))
    print(f'合并完成：原始 {original_count} 条 → 去重后 {len(merged)} 条；'
          f'其中 {n_merged} 场为多来源（已保留 {original_count} 个来源通知）。')


if __name__ == '__main__':
    main()
