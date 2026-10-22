# -*- coding: utf-8 -*-
"""一次性修复脚本：重解析 11210 主讲人单位/职称（2026-10-22 经管 11210）。

背景：bio 反推兜底 _infer_affiliation 旧实现把 bio 开头「姓名,学历学校,现职单位」
形态里的学历学校（北京师范大学）当现职单位，污染 speakerAffiliation；host 守卫
又把模型 A 给出的裸主办校名（华南师范大学）一律拒填。修复 hybrid._infer_affiliation
学历守卫 + host bio 佐证闸门后，本脚本只重解析该条记录的目标字段
（speakerAffiliation / speakerTitle），经 write_lectures 原子写盘，不动其它字段。

⚠ 本地专用：依赖网络抓取，不入库（AGENTS.md §1）。
"""
import sys, os, json

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
os.chdir(os.path.join(ROOT, 'scraper'))

import parsers as P
from lectures_writer import write_lectures

URL = 'http://em.scnu.edu.cn/a/20261022/11210.html'
YEAR = 2026
COLLEGE = '经济与管理学院'
CAMPUS = '大学城'


def main():
    import urllib.request
    req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'})
    html = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'replace')

    rec = P.parse_detail(html, URL, COLLEGE, CAMPUS, YEAR, skip_news_filter=True)
    print('new speakerAffiliation:', repr(rec.get('speakerAffiliation')))
    print('new speakerTitle     :', repr(rec.get('speakerTitle')))
    print('speaker              :', repr(rec.get('speaker')))
    print('bio head             :', repr((rec.get('speakerBio') or '')[:60]))

    data_path = os.path.join(ROOT, 'data', 'lectures.json')
    with open(data_path, encoding='utf-8') as f:
        doc = json.load(f)

    changed = 0
    for item in doc['data']:
        if item.get('sourceUrl') == URL:
            old_aff = item.get('speakerAffiliation')
            old_title = item.get('speakerTitle')
            if rec.get('speakerAffiliation'):
                item['speakerAffiliation'] = rec['speakerAffiliation']
            if rec.get('speakerTitle'):
                item['speakerTitle'] = rec['speakerTitle']
            print(f'{URL}: affiliation {old_aff!r} -> {item.get("speakerAffiliation")!r}, '
                  f'title {old_title!r} -> {item.get("speakerTitle")!r}')
            changed += 1
    assert changed == 1, f'expect exactly 1 record, got {changed}'
    write_lectures(doc, tag='reparse-11210-affil')
    print('written OK')


if __name__ == '__main__':
    main()
