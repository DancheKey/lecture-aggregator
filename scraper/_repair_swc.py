"""全量重解析汕尾校区公共课教学部（swc）记录。

用法：python scraper/_repair_swc.py
对 data/lectures.json 中 college=='汕尾校区公共课教学部' 的全部记录重新下载详情页并调用
最新 parse_detail 解析（新解析器已增强：地点内嵌时间分离、「教学工作坊」后缀剔除、OCR 抗噪日期、
主讲人标签识别等），从而统一消除旧数据里残留的时间待定 / 地点带时间 / 主讲人误抓等问题。
解析失败（返回 None）则保留原记录；其它学院的记录不触碰。
"""
import json
import os
import sys
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parsers import parse_detail

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
COLLEGE = '汕尾校区公共课教学部'
CAMPUS = '汕尾'


def main():
    with open(DATA_PATH, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    data = raw['data'] if isinstance(raw, dict) else raw
    updated = 0
    skipped = 0
    for i, item in enumerate(data):
        if item.get('college') != COLLEGE:
            continue
        url = item.get('sourceUrl')
        if not url:
            continue
        try:
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
            r.encoding = 'utf-8'
        except Exception as e:
            print('fetch fail', url, e)
            skipped += 1
            continue
        rec = parse_detail(r.text, url, COLLEGE, CAMPUS)
        if rec is None:
            print('rejected(None):', url)
            skipped += 1
            continue
        # 保留原始 merged/sources 等聚合信息（swc 单源，通常为空）
        rec['merged'] = item.get('merged', False)
        rec['sources'] = item.get('sources', [])
        rec['sourceCount'] = item.get('sourceCount', 1)
        data[i] = rec
        updated += 1
        print(f'updated {url} -> {rec.get("lectureStart")} loc={rec.get("location")!r}')
    if updated:
        raw['updatedAt'] = __import__('datetime').datetime.now().isoformat(sep=' ')
        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(raw, f, ensure_ascii=False, indent=1)
        print(f'[done] updated={updated} skipped={skipped}')
    else:
        print('[done] nothing to repair')


if __name__ == '__main__':
    main()
