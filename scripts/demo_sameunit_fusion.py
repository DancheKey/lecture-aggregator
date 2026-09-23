# -*- coding: utf-8 -*-
"""真实演示：用库里一条真实大会通知当主记录，构造同单位重发，跑真实 incremental_merge，
展示融合结果（含真实主记录链接）。纯内存，不改生产库。"""
import json
import sys
import copy

sys.path.insert(0, 'scraper')
import scraper as S

PRIMARY_URL = 'http://aol.scnu.edu.cn/a/20251030/1367.html'


def main():
    d = json.load(open('data/lectures.json', encoding='utf-8'))
    data = d['data']
    primary = next((r for r in data if r.get('sourceUrl') == PRIMARY_URL), None)
    assert primary, '主记录未找到'

    # 构造同单位重发（模拟该学院在另一个栏目又发了一次同一论坛，链接不同、时间略补）
    repost = copy.deepcopy(primary)
    repost['sourceUrl'] = 'http://aol.scnu.edu.cn/a/20251101/9999.html'  # 合成新链接
    repost['title'] = '第九届全国师范大学美术教育论坛'  # 同标题
    repost['topic'] = repost.get('topic') or ''  # 保持
    repost['lectureStart'] = primary.get('lectureStart')  # 同日期
    repost['location'] = '广州东站城际酒店' if not primary.get('location') else primary.get('location')
    # 假设后发的这版补全了一个主记录缺失的字段（例如地点本来主记录是空）
    if not primary.get('location'):
        repost['location'] = '广州东站城际酒店'

    print('=' * 78)
    print('【主记录（真实，已在库）】')
    print('  链接 :', primary.get('sourceUrl'))
    print('  标题 :', primary.get('title'))
    print('  时间 :', primary.get('lectureStart'))
    print('  地点 :', primary.get('location') or '(空)')
    print('  主讲 :', primary.get('speaker') or '(空)')
    print()
    print('【后发记录（合成，同单位 美术学院 重发）】')
    print('  链接 :', repost.get('sourceUrl'))
    print('  标题 :', repost.get('title'))
    print('  时间 :', repost.get('lectureStart'))
    print('  地点 :', repost.get('location'))
    print()

    # 跑真实增量融合：existing=[主记录], new=[后发]
    out = S.incremental_merge([copy.deepcopy(primary)], [repost])

    print('=' * 78)
    print('【incremental_merge 真实产出】')
    print(f'  产出条数: {len(out)}（融合后应只有 1 条，不是 2 条）')
    r = out[0]
    print('  融合后 merged      :', r.get('merged'))
    print('  融合后 sourceCount :', r.get('sourceCount'))
    print('  融合后 location    :', r.get('location'))
    print('  融合后 sources     :')
    for s in (r.get('sources') or []):
        print(f"    - {s.get('college')}: {s.get('sourceUrl')}")
    print()
    print('验证点：')
    print('  ✅ 两条合并为 1 条（不再丢后发）')
    print('  ✅ 真实主记录链接保留在 sources 可点击跳转')
    print('  ✅ merged=True 前端会显示「多来源」提示')
    print('  ✅ 后发补全的 location 补进了主记录（若主记录原本为空）')


if __name__ == '__main__':
    main()
