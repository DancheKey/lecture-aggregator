"""为前端生成优化后的数据切片。

从 data/lectures.json 生成：
  - site/lectures/lite.json：全量数据，但去掉首页用不到的大字段（abstract、speakerBio），
    用于 GitHub Pages 首屏快速渲染 + 完整筛选。
  - site/lectures/latest.json：仅保留最新 50 条（首页第一页），字段与 lite.json 一致，
    体积最小，用于"先渲染第一页，后台再加载完整数据"的渐进体验。

运行：python scripts/generate_frontend_data.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
SITE_DIR = os.path.join(ROOT, 'site', 'lectures')
LATEST_SIZE = 50


def strip_fields(item):
    """去掉首页列表用不到的大字段。"""
    return {k: v for k, v in item.items() if k not in ('abstract', 'speakerBio')}


def load_lectures():
    with open(DATA_PATH, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    if isinstance(raw, dict) and 'data' in raw:
        return raw.get('data', []) or [], raw.get('updatedAt', '')
    return raw if isinstance(raw, list) else [], ''


def sort_for_latest(data):
    """按 lectureStart 降序，缺失时间排最后。"""
    def key(item):
        start = item.get('lectureStart') or ''
        return ('0' if start else '1', start)
    return sorted(data, key=key, reverse=True)


def main():
    os.makedirs(SITE_DIR, exist_ok=True)
    data, updated_at = load_lectures()
    if not data:
        print('[warn] 没有讲座数据，跳过生成')
        return

    sorted_data = sort_for_latest(data)
    latest = [strip_fields(item) for item in sorted_data[:LATEST_SIZE]]
    lite = [strip_fields(item) for item in data]

    wrapper = {'updatedAt': updated_at, 'data': latest}
    with open(os.path.join(SITE_DIR, 'latest.json'), 'w', encoding='utf-8') as f:
        json.dump(wrapper, f, ensure_ascii=False, separators=(',', ':'))

    wrapper = {'updatedAt': updated_at, 'data': lite}
    with open(os.path.join(SITE_DIR, 'lite.json'), 'w', encoding='utf-8') as f:
        json.dump(wrapper, f, ensure_ascii=False, separators=(',', ':'))

    latest_bytes = os.path.getsize(os.path.join(SITE_DIR, 'latest.json'))
    lite_bytes = os.path.getsize(os.path.join(SITE_DIR, 'lite.json'))
    print(f'[done] latest.json: {len(latest)} 条 ({latest_bytes / 1024:.1f} KB)')
    print(f'[done] lite.json: {len(lite)} 条 ({lite_bytes / 1024:.1f} KB)')


if __name__ == '__main__':
    main()
