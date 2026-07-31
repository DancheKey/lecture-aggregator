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
import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
SITE_LECTURES_PATH = os.path.join(ROOT, 'site', 'lectures.json')
SITE_DIR = os.path.join(ROOT, 'site', 'lectures')
LATEST_SIZE = 50
UNKNOWN_YEAR = '其他'
# 首页首屏（latest.json）只需要列表卡片展示字段，长文本按首页 truncate 长度截断，
# 让首屏秒开；详情字段仍保留在 lite.json 中，确保展开/查看时与本地一致。
LATEST_PREVIEW_LEN = 220


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


def atomic_write_text(path, content):
    """文本文件的原子写入（用于改写 HTML 等）。"""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(tmp, path)


def _short_hash(path, length=10):
    """返回文件内容的短 hash，用作静态资源缓存破坏版本号。"""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()[:length]


def stamp_script_version(html_name, js_name):
    """给 html 中引用 js_name 的 <script> 标签打上「基于文件内容 hash」的版本号：
    <script defer src="stats.js"></script> -> <script defer src="stats.js?v=abc123"></script>

    作用：GitHub Pages 对未哈希的静态资源会长期缓存。若只改 JS 逻辑而不改文件名，
    回访用户的浏览器会继续跑旧 JS（例如统计页仍加载 5.7MB 全量数据而非 291KB 切片），
    表现为「改动已推送但体验没变 / 仍很慢」。按内容 hash 打版本号后，JS 一改版本号即变，
    浏览器必然重新拉取；JS 未变时版本号不变，不产生无谓改动。幂等（重复运行不会叠加 ?v）。
    """
    html_path = os.path.join(ROOT, 'site', html_name)
    js_path = os.path.join(ROOT, 'site', js_name)
    if not (os.path.exists(html_path) and os.path.exists(js_path)):
        return
    ver = _short_hash(js_path)
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    # 匹配 src="js_name" 或 src="js_name?v=xxxx"（无论单/双引号），统一替换为带新版本号
    pat = re.compile(r'src=(["\'])' + re.escape(js_name) + r'(?:\?v=[0-9a-fA-F]+)?\1')
    new_html = pat.sub(r'src="%s?v=%s"' % (js_name, ver), html)
    if new_html != html:
        atomic_write_text(html_path, new_html)
        print(f'[done] {html_name}: {js_name} 缓存版本号 = {ver}')


def strip_fields(item):
    """保留全部字段，确保公网静态版（lite/latest）与本地 /api/lectures 卡片内容一致。
    历史上曾在这里剥离 abstract、speakerBio 以减小体积，但导致公网卡片比本地少「简介/内容摘要」。
    """
    return dict(item)


def latest_preview(item):
    """生成首屏 latest.json 的轻量条目：保留列表必要字段，长文本截断。
    与 lite.json 字段完全一致，只是 abstract/speakerBio 被截断，不损失功能只损失未展开长度。"""
    preview = dict(item)
    for key in ('abstract', 'speakerBio'):
        val = preview.get(key)
        if val and len(val) > LATEST_PREVIEW_LEN:
            preview[key] = val[:LATEST_PREVIEW_LEN]
    return preview


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


def load_excluded():
    """读取全局排除名单 data/excluded_urls.json。

    该名单既被爬虫用于增量抓取时跳过，也必须在展示端过滤——凡是列入的 URL
    不应出现在聚合页面 / 统计中（否则 excluded 形同虚设，非讲座会反复回潮）。
    """
    p = os.path.join(ROOT, 'data', 'excluded_urls.json')
    if not os.path.exists(p):
        return set()
    try:
        with open(p, 'r', encoding='utf-8') as f:
            lst = json.load(f)
        return set(lst) if isinstance(lst, list) else set()
    except Exception:
        return set()


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
        # 累加来源通知总数。注意：显式 sourceCount=0（多讲座拆分出的非首条）应被尊重，
        # 故不能写 `item.get('sourceCount') or ...`（0 会被 or 吞掉）。
        sc = item.get('sourceCount')
        s_count = sc if sc is not None else (len(sources) or 1)
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


def with_unit(item, url_dates):
    """为单页多讲座拆分记录标注 unitType，供前端区分「场」与「期」：

    - 同一 sourceUrl 组内所有记录的讲座日期完全相同（同一天多场次）
      -> 'session'（第x场，同一活动的某一场）
    - 同一 sourceUrl 组内记录跨了不同日期（系列讲座分期）
      -> 'issue'（第x期，不同日期的若干期）

    仅对含 lectureIndex 的记录附加该字段；其它记录原样透传，不污染主数据。
    """
    it = dict(item)
    if item.get('lectureIndex') is not None:
        dates = url_dates.get(item.get('sourceUrl') or '', set())
        it['unitType'] = 'session' if len(dates) == 1 else 'issue'
    return it


def main():
    os.makedirs(SITE_DIR, exist_ok=True)
    data, updated_at = load_lectures()
    if not data:
        print('[warn] 没有讲座数据，跳过生成')
        return

    # 全局排除名单：凡是列入的 URL 不应出现在聚合页面（与爬虫端跳过抓取保持一致）。
    # 这是根治「排除过的非讲座又回来」的关键——之前 excluded 只被爬虫用，展示端从不过滤。
    excluded = load_excluded()
    if excluded:
        before = len(data)
        data = [r for r in data if (r.get('sourceUrl') or '') not in excluded]
        print(f'[filter] 排除名单过滤: {before} -> {len(data)} (移除 {before - len(data)} 条)')

    # 构建 sourceUrl -> 讲座日期集合，用于区分「同一活动的多场」（同天=场）
    # 与「系列讲座分期」（跨天=期）。
    url_dates = {}
    for item in data:
        u = item.get('sourceUrl') or ''
        d = (item.get('lectureStart') or '')[:10]
        url_dates.setdefault(u, set())
        if d:
            url_dates[u].add(d)

    sorted_data = sort_for_latest(data)
    latest = [latest_preview(with_unit(item, url_dates)) for item in sorted_data[:LATEST_SIZE]]
    lite = [with_unit(item, url_dates) for item in data]
    stats = build_stats(data, updated_at)

    # 同时写入 site/lectures.json 与切片，全部使用原子写入，确保首页与统计页版本一致
    # 全量切片同样附加 unitType（供本地 /api/lectures 与 GitHub Pages 回退），与 lite 一致
    atomic_write(SITE_LECTURES_PATH, {'updatedAt': updated_at, 'data': [with_unit(item, url_dates) for item in data]})
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

    # 给前端脚本打内容 hash 版本号，避免浏览器长期缓存旧 JS（见 stamp_script_version 注释）。
    stamp_script_version('stats.html', 'stats.js')
    stamp_script_version('index.html', 'app.js')


if __name__ == '__main__':
    main()
