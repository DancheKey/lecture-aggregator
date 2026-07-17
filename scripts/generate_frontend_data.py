"""为前端生成优化后的数据切片。

从 data/lectures.json 生成：
  - site/lectures.json：全量原始数据，供本地 /api/lectures 与 GitHub Pages 回退使用。
  - site/lectures/lite.json：全量数据（含 abstract、speakerBio），与本地 /api/lectures
    字段完全一致，用于 GitHub Pages 首屏快速渲染 + 完整筛选 + 完整卡片展示。
  - site/lectures/latest.json：仅保留最新 50 条（首页第一页），字段与 lite.json 一致
    （含 abstract、speakerBio），用于"先渲染第一页，后台再加载完整数据"的渐进体验。
  - site/lectures/stats.json：统计页专用，包含预计算的学院-年份矩阵、年份合计、
    以及用于动态访问/点赞数的最小讲座索引，避免统计页加载 2MB+ 全量数据。

所有文件均先写入 .tmp 临时文件，再原子重命名，确保首页与统计页在任何时刻
不会看到"半新半旧"的数据版本。

运行：python scripts/generate_frontend_data.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
SITE_LECTURES_PATH = os.path.join(ROOT, 'site', 'lectures.json')
SITE_DIR = os.path.join(ROOT, 'site', 'lectures')
LATEST_SIZE = 50
UNKNOWN_YEAR = '其他'


def atomic_write(path, content, mode='text'):
    """将内容写入 .tmp 文件，再用 os.replace 原子替换目标文件。
    避免写入过程中读者读到半份文件。"""
    tmp = path + '.tmp'
    if mode == 'text':
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(content, f, ensure_ascii=False, separators=(',', ':'))
    else:
        with open(tmp, 'wb') as f:
            f.write(content)
    os.replace(tmp, path)


def strip_fields(item):
    """保留全部字段，确保公网静态版（lite/latest）与本地 /api/lectures 卡片内容一致。
    历史上曾在这里剥离 abstract、speakerBio 以减小体积，但导致公网卡片比本地少「简介/内容摘要」。
    """
    return dict(item)


def year_of(item):
    """与 stats.js 保持一致的年份提取逻辑。"""
    if item.get('lectureStart'):
        return str(item['lectureStart'])[:4]
    m = (item.get('publishTime') or '').strip()[:4] or None
    if m and m.isdigit():
        return m
    t = (item.get('title') or '')
    m2 = __import__('re').search(r'(\d{4})', t)
    if m2:
        return m2.group(1)
    return UNKNOWN_YEAR


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


def build_stats(data, updated_at):
    """生成统计页专用 JSON：预计算矩阵 + 最小讲座索引。"""
    years_set = set()
    source_notice_count = 0
    # 学院 -> 年份 -> 来源通知数
    matrix = {}
    # 年份 -> 来源通知数
    year_totals = {}
    # 最小讲座索引：用于客户端结合 /api/lecture/stats 计算访问/点赞
    lectures = []
    # 学院 -> 校区（供统计页校区筛选）
    campus_map = {}

    for item in data:
        y = year_of(item)
        if y:
            years_set.add(y)
        primary_url = item.get('sourceUrl') or ''
        sources = item.get('sources') or [item]
        # 累加来源通知总数
        s_count = item.get('sourceCount') or len(sources) or 1
        source_notice_count += s_count
        # 预计算矩阵：按去重后讲座计数（每个 item 只计一次，按主学院/主年份）
        primary_college = item.get('college') or '未分类'
        matrix.setdefault(primary_college, {})
        cell_year = y or UNKNOWN_YEAR
        matrix[primary_college][cell_year] = matrix[primary_college].get(cell_year, 0) + 1
        year_totals[cell_year] = year_totals.get(cell_year, 0) + 1
        # 记录学院 -> 校区映射（取主学院）
        if primary_college not in campus_map:
            campus_map[primary_college] = item.get('campus') or ''
        # 最小索引：用于客户端结合 /api/lecture/stats 计算访问/点赞
        lectures.append({
            'u': primary_url,
            'y': y or UNKNOWN_YEAR,
            'c': primary_college,
            's': s_count,
        })

    # 年份排序：数字年份降序，"其他"放最后
    def year_key(y):
        return (0, y) if y.isdigit() else (1, y)

    years = sorted([y for y in years_set if y.isdigit()], key=lambda y: -int(y))
    if UNKNOWN_YEAR in years_set:
        years.append(UNKNOWN_YEAR)

    return {
        'updatedAt': updated_at,
        'lectureCount': len(data),
        'sourceNoticeCount': source_notice_count,
        'years': years,
        'matrix': matrix,
        'yearTotals': year_totals,
        'lectures': lectures,
        'campusMap': campus_map,
    }


def main():
    os.makedirs(SITE_DIR, exist_ok=True)
    data, updated_at = load_lectures()
    if not data:
        print('[warn] 没有讲座数据，跳过生成')
        return

    sorted_data = sort_for_latest(data)
    latest = [strip_fields(item) for item in sorted_data[:LATEST_SIZE]]
    lite = [strip_fields(item) for item in data]
    stats = build_stats(data, updated_at)

    # 同时写入 site/lectures.json 与切片，全部使用原子写入，确保首页与统计页版本一致
    atomic_write(SITE_LECTURES_PATH, {'updatedAt': updated_at, 'data': data})
    atomic_write(os.path.join(SITE_DIR, 'latest.json'), {'updatedAt': updated_at, 'data': latest})
    atomic_write(os.path.join(SITE_DIR, 'lite.json'), {'updatedAt': updated_at, 'data': lite})
    atomic_write(os.path.join(SITE_DIR, 'stats.json'), stats)

    latest_bytes = os.path.getsize(os.path.join(SITE_DIR, 'latest.json'))
    lite_bytes = os.path.getsize(os.path.join(SITE_DIR, 'lite.json'))
    stats_bytes = os.path.getsize(os.path.join(SITE_DIR, 'stats.json'))
    stats_lectures_count = len(stats['lectures'])
    print(f'[done] site/lectures.json: {len(data)} 条')
    print(f'[done] latest.json: {len(latest)} 条 ({latest_bytes / 1024:.1f} KB)')
    print(f'[done] lite.json: {len(lite)} 条 ({lite_bytes / 1024:.1f} KB)')
    print(f'[done] stats.json: {stats_lectures_count} 条索引 ({stats_bytes / 1024:.1f} KB)')


if __name__ == '__main__':
    main()
