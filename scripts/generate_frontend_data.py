"""为前端生成优化后的数据切片。

从 data/lectures.json 生成：
  - site/lectures.json：全量原始数据，供本地 /api/lectures 与 GitHub Pages 后台加载使用。
  - site/lectures/latest.json：仅保留最新 50 条（首页第一页），与 lectures.json 同字段集
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
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# 确保 scripts/ 下的共享模块（如 excluded_urls）可被 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 语义词表单一事实源（G4，2026-09-10）：speaker_keys 与 server.py 共用同一实现，
# 两端不再各抄一份（旧守卫测试要求两者严格一致，收敛后由构造保证）。
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
import field_vocab as _fv  # noqa: E402
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
SITE_LECTURES_PATH = os.path.join(ROOT, 'site', 'lectures.json')
SITE_DIR = os.path.join(ROOT, 'site', 'lectures')
# 站点绝对地址前缀（2026-10-20）：sitemap 与分享卡片（og:url / og:image）都要用
# 绝对 URL，爬虫不解析相对路径。GitHub Pages 项目页挂在子路径下，结尾必须有 `/`。
SITE_BASE_URL = 'https://danchekey.github.io/lecture-aggregator/'
LATEST_SIZE = 50
CHUNK_SIZE = 100        # 公网分片加载：每片 100 条（2026-10-14 用户定，原 500）。
# 背景：阶梯跳变形态下数字只在分片到达时更新（bumpCount 恒等于已加载真实条数，
# 无插值动画——插值方案 2026-09-30 两轮被否：观感「平滑上滚→长时间不动→猛跳」
# 且出现过负数）。500/片时 8 片大跳观感迟缓；100/片后 3830 条约 38 片，
# 数字以 4 页（pageSize=25）为粒度持续小步更新，总下载量不变、单请求更小
# （弱网友好），首片到达更快（50→150 秒现）。
UNKNOWN_YEAR = '其他'
# 首页首屏（latest.json）长文本截断长度：0 = 不截断（2026-09-29 起）。
# 历史做法是按 220 字符截断 abstract/speakerBio「让首屏秒开」，但首屏 50 条正是用户
# 第一眼看到的全部内容，截断点在宽屏下恰好排满 3 行 → 表现为「摘要写到一半、末尾无
# 省略号、以半个单词收尾」，只能被解读为数据缺失（实测被误认成 bug）。
# 实测代价（50 条带全量长文本）：raw 70KB→~128KB、gzip 23KB→~40KB，可忽略。
# 需要恢复截断时把该值改回正数即可（latest_preview 内分支保留）。
LATEST_PREVIEW_LEN = 0


def atomic_write_text(path, content):
    """文本文件的原子写入（用于改写 HTML 等）。

    ⚠ `newline='\n'` 不可省（2026-10-10 Windows 实测）：文本模式下 Python
    在 Windows 会把 `\n` 转成 `\r\n`，写出的站点文件与 git 索引（LF）逐字节
    不同 → `test_工作区与git换行一致` 当场变红，且 stamp 哈希工作区/索引两侧
    口径分裂。CI（Linux）不复现，故此坑只在本地跑生成脚本时暴露。
    """
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    os.replace(tmp, path)


def atomic_write_json(path, data):
    """JSON 文件的原子写入（用于改写切片等）。"""
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, separators=(',', ':')))


def _short_hash(path, length=10, git_first=True):
    """返回文件内容的短 hash，用作静态资源缓存破坏版本号。

    ⚠ 2026-10-02 修正（关键）：必须优先哈希 **Git 索引里那份内容**，而非工作区文件。

    本仓库 `site/*.js` 在工作区是 CRLF，入库时被 git 转成 LF
    （core.autocrlf / .gitattributes 未对 site/*.js 固定 eol）。于是：

        工作区 app.display.js (CRLF, 17511B) -> e72f3e2d48
        git 里 app.display.js  (LF,   17208B) -> fd8ba8ea49

    而 **GitHub Pages 下发的是 git 里那份**。原先哈希工作区文件，等于给
    一个 Pages 永远不会下发的字节序列算版本号 —— `?v=` 缓存破坏**完全失效**，
    回访用户持续加载旧 JS，且零报错（这正是该机制要防的事）。

    故这里用 `git show :<path>` 读**索引**（不是 HEAD）的内容：既能拿到 Pages
    将要下发的那份字节，又能覆盖「尚未提交的本地改动」（git add 后即在索引里）。

    取不到 git（未初始化仓库 / 非 git 目录）时退回工作区文件，保证脚本仍可用，
    但会打印警告——那种情况下算出的版本号仍可能与 Pages 不一致。
    """
    if git_first:
        import subprocess
        rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
        try:
            r = subprocess.run(['git', 'show', ':' + rel], cwd=ROOT,
                               capture_output=True, timeout=15)
            if r.returncode == 0 and r.stdout:
                return hashlib.sha256(r.stdout).hexdigest()[:length]
        except Exception:
            pass
        print(f'[warn] {os.path.basename(path)}: 无法从 Git 索引取内容，'
              f'退回工作区文件计算版本号——若该文件存在 CRLF/LF 转换，'
              f'版本号将与 Pages 实际下发的内容不符（缓存破坏失效）')
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()[:length]


def stamp_script_version(html_name, asset_name):
    """给 html 中引用 asset_name 的标签打上「基于文件内容 hash」的缓存破坏版本号：

        <script defer src="stats.js"></script>  -> src="stats.js?v=abc123"
        <link rel="stylesheet" href="x.css">      -> href="x.css?v=abc123"

    作用：GitHub Pages 对未哈希的静态资源会长期缓存。若只改 JS/CSS 逻辑而不改文件名，
    回访用户的浏览器会继续跑旧 JS（例如统计页仍加载 5.7MB 全量数据而非 291KB 切片），
    表现为「改动已推送但体验没变 / 仍很慢」。按内容 hash 打版本号后，资源一改版本号即变，
    浏览器必然重新拉取；未变时版本号不变，不产生无谓改动。幂等（重复运行不会叠加 ?v）。

    2026-10-10：原先只匹配 `src=`，**CSS 完全在缓存破坏机制之外**——
    `<link href="visits-trend.css">` 没有任何版本号，改样式对回访用户不生效。
    这是与「app.admin.js 漏打戳」同源的系统性缺口（见 docs 结论），故一并覆盖 href=。
    """
    html_path = os.path.join(ROOT, 'site', html_name)
    asset_path = os.path.join(ROOT, 'site', asset_name)
    if not (os.path.exists(html_path) and os.path.exists(asset_path)):
        return
    ver = _short_hash(asset_path)
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    # 匹配 src="x" / href="x"，无论单双引号，且已带任意 ?v= 的也要能命中并替换。
    # 版本号字符集必须宽松：历史上一处手写的 "?v=20260906g" 含非十六进制字符 'g'，
    # 被旧正则 (?:\?v=[0-9a-fA-F]+)? 整体失配 -> 该 script 版本号永久冻结（且静默无告警），
    # 回访用户持续跑旧 JS，正是本机制本该防住的事。故改用 [^"']* 兜住任意版本号形态，
    # 只以引号收边界。
    pat = re.compile(r'\b(src|href)=(["\'])' + re.escape(asset_name)
                     + r'(?:\?v=[^"\']*)?\2')
    if pat.search(html) is None:
        print(f'[warn] {html_name} 中未找到对 {asset_name} 的引用，版本号未更新'
              f'（请检查该资源是否被改名/删除）')
        return
    new_html = pat.sub(r'\1="%s?v=%s"' % (asset_name, ver), html)
    if new_html != html:
        atomic_write_text(html_path, new_html)
        print(f'[done] {html_name}: {asset_name} 缓存版本号 = {ver}')


def latest_preview(item):
    """生成首屏 latest.json 的条目：保留列表必要字段。
    LATEST_PREVIEW_LEN > 0 时才截断 abstract/speakerBio（默认 0 = 原样保留全文），
    因为截断点会以「无省略号的硬切」直接暴露给用户（见常量处说明）。"""
    preview = dict(item)
    if LATEST_PREVIEW_LEN > 0:
        for key in ('abstract', 'speakerBio'):
            val = preview.get(key)
            if val and len(val) > LATEST_PREVIEW_LEN:
                preview[key] = val[:LATEST_PREVIEW_LEN]
    return preview


def count_key(item):
    """讲座级计数键（2026-10-18）——访问/点赞/想听的云端主键。

    规则：有 lectureIndex（多场拆分的一页）时 = `sourceUrl~lectureIndex`，
    否则 = sourceUrl。

    ⚠ 分隔符必须是 URL 无保留字符（RFC 3986 unreserved），**不可用 `#`**——
    2026-10-18 实测：PostgREST 的 `url=eq.<键>` 会把 `#` 及其后内容当作 URL
    片段丢弃，写进去的是 `a.html#1`、查回来的却是 `a.html`（静默错行）。
    `~` 与字母数字同属 unreserved，拼在查询值里无需百分号编码。
    同理排除 `? & = + / :` 等保留字符。

    ⚠ 必须与前端不一致即事故：site/app.social.js 只消费本字段、不再自己拼键，
    故改规则只需改这里（tests/js/count_key_uniqueness.js 有门禁锁唯一性与规则）。

    兼容性取舍：单场讲座保持无后缀键，使改造前已写入云端的计数（30+ 行真实
    访问/点赞）零迁移继续可读；只有 94 个多场源页会分裂出讲座级键。
    """
    src = item.get('sourceUrl') or ''
    idx = item.get('lectureIndex')
    if idx is not None and src:
        return f'{src}~{idx}'
    return src


def year_of(item):
    """与 stats.js 保持一致的年份提取逻辑。"""
    if item.get('lectureStart'):
        return str(item['lectureStart'])[:4]
    m = (item.get('publishTime') or '').strip()[:4] or None
    if m and m.isdigit():
        return m
    t = (item.get('title') or '')
    m2 = re.search(r'(\d{4})', t)
    if m2:
        return m2.group(1)
    return UNKNOWN_YEAR


def load_lectures():
    with open(DATA_PATH, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    if isinstance(raw, dict) and 'data' in raw:
        return raw.get('data', []) or [], raw.get('updatedAt', '')
    return raw if isinstance(raw, list) else [], ''


# load_excluded 已迁移至 scripts/excluded_urls.py（scraper / generate / server 三点共用）
from excluded_urls import load_excluded, is_record_excluded  # noqa: E402,F811
# 前端下发字段白名单 + 长文本分离（generate / server 两点共用，见 frontend_fields.py 顶部决策记录）
from frontend_fields import (  # noqa: E402
    strip_frontend_fields, split_long_text, lt_bucket, lt_key, DETAIL_BUCKETS)


def sort_for_latest(data):
    """按 lectureStart 降序，缺失时间排最后。"""
    def key(item):
        start = item.get('lectureStart') or ''
        return ('1' if start else '0', start)
    return sorted(data, key=key, reverse=True)


def unique_source_urls(item):
    """该条记录关联的**唯一**来源通知 URL 集合。

    2026-10-08：统计口径由「逐条累加 sourceCount」改为「按唯一来源 URL 集合计数」。

    起因（问题 3 实测）：一页多场拆分后，每场都把同一张转发页登记进自己的 sources
    （xz/20240116/127 拆 3 场 → 3 条记录各挂 swc/collaborative/2024/0101/55），
    而转发页**本身没有独立记录入库**，只以 sources 形式存在 → 逐条累加等于把
    1 张转发页数了 3 次，「覆盖 N 条来源通知」虚高。

    注意口径边界：**只用于「来源通知总数」这一个数字**。筛选计数（按学院/校区）
    仍按讲座计，不去重——用户按「汕尾校区」筛就该看到那 3 场讲座（确实有 3 场），
    去重反而丢信息。两处口径不可混用。
    """
    urls = set()
    u = (item.get('sourceUrl') or '').strip()
    if u:
        urls.add(u.rstrip('/'))
    for s in (item.get('sources') or []):
        su = ((s or {}).get('sourceUrl') or '').strip()
        if su:
            urls.add(su.rstrip('/'))
    return urls


def build_stats(data, updated_at):
    """生成统计页专用 JSON：预计算矩阵 + 最小讲座索引。"""
    years_set = set()
    # 来源通知总数：按**唯一来源 URL 集合**计（见 unique_source_urls 的口径说明）。
    # 早前是逐条累加 sourceCount，多场拆分页会因同一转发页被多次引用而虚高。
    seen_notice_urls = set()
    # 学院 -> 年份 -> 来源通知数
    matrix = {}
    # 年份 -> 来源通知数
    year_totals = {}
    # 最小讲座索引：用于客户端结合 /api/lecture/stats 计算访问/点赞
    lectures = []
    # 页面级计数索引的原料（2026-10-19 B4）：u -> {split, plain, days, y, c, cs}。
    # 见循环后「页面级虚拟索引」说明。
    page_meta = {}
    # 学院 -> 校区（供统计页校区筛选）
    campus_map = {}
    # 学院 -> 实际举办校区集合（2026-10-14 P2-2）：校级等讲座按 location 推导的
    # 实际举办校区，供统计页按「归口 ∪ 实际举办」双集合筛选（与首页
    # app.computed.js filtered() 的 actualCampus 口径一致）。
    campus_actual = {}
    # 校区 -> 学院 -> 年份 -> 讲座数（2026-10-14 方案 A）：统计页选中校区后的
    # 计数矩阵。口径与首页 filtered() 完全一致——讲座命中校区 = 各归属单位的
    # 归口校区 ∪ actualCampus；再按「讲座-单位」展开（与 matrix 同口径，联合
    # 发布的讲座在每个相关单位各计一次）。此前统计页选中校区只过滤「显示哪些
    # 行」，行内数字仍是单位全部校区总数（社科处 116 vs 汕尾实际 1），两页数字
    # 对不上——用户 2026-10-14 报告并确认方案 A（生成器预计算）。
    campus_matrix = {}

    for item in data:
        y = year_of(item)
        if y:
            years_set.add(y)
        primary_url = item.get('sourceUrl') or ''
        sources = item.get('sources') or [item]
        seen_notice_urls |= unique_source_urls(item)
        # 预计算矩阵：按「该讲座归属的全部单位」计数。
        # 跨源合并的讲座（同一讲座被多个单位发布）应在每个相关单位各计一次，
        # 与首页筛选逻辑 Set([主学院, ...来源单位]) 完全一致，修复统计页漏算合并讲座的问题。
        # 注意：这是「讲座-单位归属计数」，联合发布的讲座会在各单位重复计入，故
        # 各单位计数之和会大于唯一讲座总数 lectureCount（属正常，统计页有说明文字）。
        primary_college = item.get('college') or '未分类'
        units = []
        seen_unit = set()
        for c in [primary_college] + [s.get('college') for s in sources if s.get('college')]:
            if c and c not in seen_unit:
                seen_unit.add(c)
                units.append(c)
        cell_year = y or UNKNOWN_YEAR
        for c in units:
            matrix.setdefault(c, {})
            matrix[c][cell_year] = matrix[c].get(cell_year, 0) + 1
            year_totals[cell_year] = year_totals.get(cell_year, 0) + 1
        # 学院 -> 校区映射：主学院优先；来源单位补全（保证合并讲座的来源单位也能按校区筛选）
        for c in units:
            if c in campus_map:
                continue
            if c == primary_college:
                campus_map[c] = item.get('campus') or ''
            else:
                src = next((s for s in sources if s.get('college') == c), None)
                campus_map[c] = (src or {}).get('campus') or ''
        # 学院 -> 实际举办校区集合（P2-2）：讲座归属的每个单位都记入该场的实际校区
        _ac = _fv.actual_campus(item)
        if _ac:
            for c in units:
                campus_actual.setdefault(c, set()).add(_ac)
        # 校区矩阵（方案 A）：讲座命中的校区 = 各归属单位的归口校区 ∪ actualCampus。
        # 注意 matched 是讲座级集合（对该讲座的所有归属单位相同），因此
        # campus_matrix[校区][单位][年份] = 该单位归属的、命中该校区的讲座数。
        matched_campuses = set()
        for c in units:
            if c == primary_college:
                uc = item.get('campus') or ''
            else:
                src = next((s for s in sources if s.get('college') == c), None)
                uc = (src or {}).get('campus') or ''
            if uc:
                matched_campuses.add(uc)
        if _ac:
            matched_campuses.add(_ac)
        for campus in matched_campuses:
            cm = campus_matrix.setdefault(campus, {})
            for c in units:
                cm.setdefault(c, {})
                cm[c][cell_year] = cm[c].get(cell_year, 0) + 1
        # 最小索引：用于客户端结合 /api/lecture/stats 计算访问/点赞。
        # cs = 该讲座归属的全部单位（含主学院与来源单位），供访问/点赞按单位展开归属；
        # c = 主学院（保留，供来源通知数等按主讲座口径统计）。
        # s = **本条**关联的唯一来源通知数（主页面 + 去重后的 sources）。
        #   2026-10-08 改：早前取 sourceCount，在 sources 含重复 URL 时会虚高
        #   （iqm/180 曾 sources 两个同一 URL、sc=2）。此处与顶层 sourceNoticeCount
        #   同一口径——同一条内的重复 URL 只算一次。
        _k = count_key(item)
        lectures.append({
            'u': primary_url,
            # 计数键（2026-10-18）：与分片数据的 ck 同一函数产出。统计页按 k 取
            # 云端计数；多场拆分后同一 u 会有多个不同 k，逐条累加不再重复计同一份。
            'k': _k,
            'y': y or UNKNOWN_YEAR,
            'c': primary_college,
            'cs': units,
            's': len(unique_source_urls(item)),
        })
        # 记录该页的键形态与「同日场次数」（B4，见循环后处理）。
        if primary_url:
            meta = page_meta.get(primary_url)
            if meta is None:
                meta = {'split': False, 'plain': False, 'days': {},
                        'y': y or UNKNOWN_YEAR, 'c': primary_college, 'cs': units}
                page_meta[primary_url] = meta
            if _k != primary_url:
                meta['split'] = True
            else:
                meta['plain'] = True
            _day = (item.get('lectureStart') or '')[:10]
            meta['days'][_day] = meta['days'].get(_day, 0) + 1

    # 页面级虚拟索引（2026-10-19 B4）：合并卡（同页同日 ≥3 场）的赞/想听/访问
    # 记在**无后缀** sourceUrl 上（app.computed.js units() 的 forum 分支 ck: u），
    # 而统计页只按 k 查 lectureStats，导致 30 组 / 225 场的页面级计数恒为 0。
    # 这里为这些页补一条 k = u 的索引，按 u 去重（同页多天多张合并卡共用同一个
    # 页面级键，只补 1 条，避免把一份页面级计数放大 N 倍）。归属单位/年份取该页
    # 首条记录；p=1 标记非真实讲座（前端不读，仅便于排查）。
    #
    # ⚠ 2026-10-19 修正（用户实测）：早前对**全部**多场页（94 个）补虚拟记录，
    # 但跨天拆分的页（如研究生院某页 3 场分 3 天）根本不渲染合并卡，页面上没有
    # 任何「整页赞」的入口，页面级键恒为 0/历史脏值，统计页却把它当一条记录计入
    # → 与首页「只看已赞」的条数对不上（用户：统计页 4 条 vs 首页筛出 1 条）。
    # 判定依据必须与 units() 的 MIN=3 完全同口径：该页存在任意一天场次 ≥3。
    # plain 页（本身 k == u）原记录已能查到页面级键，不再补。
    for _u, meta in page_meta.items():
        if not meta['split'] or meta['plain']:
            continue
        if max(meta['days'].values() or [0]) < 3:
            continue
        lectures.append({
            'u': _u, 'k': _u, 'y': meta['y'], 'c': meta['c'],
            'cs': meta['cs'], 's': 0, 'p': 1,
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
        # 唯一来源通知数（2026-10-08 口径变更，见 unique_source_urls）：
        # 由 seen_notice_urls 在遍历全部记录后统一得出——同一张转发页被多场拆分
        # 引用时只计 1 次。早前是逐条累加 sourceCount，会把那 1 张页数 N 次。
        'sourceNoticeCount': len(seen_notice_urls),
        'years': years,
        'matrix': matrix,
        'yearTotals': year_totals,
        'lectures': lectures,
        'campusMap': campus_map,
        # 实际举办校区集合（P2-2）：set 不可 JSON 序列化，转排序列表
        'campusActualMap': {c: sorted(v) for c, v in campus_actual.items()},
        # 校区矩阵（方案 A）：统计页选中校区后的计数来源（见上方注释）
        'campusMatrix': campus_matrix,
    }


def speaker_keys(name):
    """讲者归一化键（2026-09-06）：去空白/职称后缀、全角转半角；英文 lower。
    多主讲人（'A、B'）逐人拆分，返回键数组——同名同人判定为完全一致，
    跨语言（张三/Zhang San）暂不合键（避免同音误并），留作后续。

    实现已收敛到 scraper/field_vocab.speaker_keys()（G4，2026-09-10），
    与 server.py 的 _speaker_keys() 共用同一份代码。
    """
    return _fv.speaker_keys(name)


def with_unit(item, url_dates):
    """为单页多讲座拆分记录标注 unitType，供前端区分「场」与「期」：

    - 同一 sourceUrl 组内所有记录的讲座日期完全相同（同一天多场次）
      -> 'session'（第x场，同一活动的某一场）
    - 同一 sourceUrl 组内记录跨了不同日期（系列讲座分期）
      -> 'issue'（第x期，不同日期的若干期）

    仅对含 lectureIndex 的记录附加该字段；其它记录原样透传，不污染主数据。

    末步 strip_frontend_fields()：按白名单裁掉前端无消费者的内部字段
    （llmSelfExtract / qaRepaired / timeConfidence / speakerTitle 等，共约 5.9% 原始体积）。
    白名单与「勿删项」的决策记录见 scripts/frontend_fields.py 顶部注释。
    """
    it = dict(item)
    # ⚠ 以下字段**有意保留下发，勿剥离**（2026-09-10 决策，非疏漏）：
    #   images / hasPosterImage / imageParseMethod
    # 它们是「哪些讲座是海报图、用的哪种解析方式」的重处理索引——日后要
    # 针对海报类讲座重跑 OCR/VLM 时，靠这三个字段就能精准筛出目标集合，
    # 不必再回头全量重抓。体积占比很小（约 2.2%，见 code-review 报告 G3），
    # 前端虽不渲染，但作为处理台账随产物携带是刻意的取舍
    # （已登记进 scripts/frontend_fields.py 的 FRONTEND_KEEP 白名单）。
    if item.get('lectureIndex') is not None:
        dates = url_dates.get(item.get('sourceUrl') or '', set())
        it['unitType'] = 'session' if len(dates) == 1 else 'issue'
    # 实际举办校区（2026-10-13 用户拍板）：校级讲座按 location 推导，仅写 site 层
    ac = _fv.actual_campus(item)
    if ac:
        it['actualCampus'] = ac
    # 线上举办（2026-10-13 用户要求）：location 写的是参会方式即 True，仅 True 落字段
    if _fv.is_online_lecture(item):
        it['isOnline'] = True
    # 讲者归一化键（2026-09-06）：前端讲者聚合视图用，多人各一键
    it['speakerKeys'] = speaker_keys(item.get('speaker'))
    # 计数键（2026-10-18）：讲座级唯一键，访问/点赞/想听的云端主键。
    #   - 单场讲座（无 lectureIndex）：= sourceUrl，与改造前的键逐字一致，
    #     故全站 90% 的存量云端计数无需任何迁移。
    #   - 多场拆分（含 lectureIndex）：= sourceUrl~lectureIndex，
    #     同页的 N 场各自成键，修复「点一场 N 场同增」「统计页 1 次点赞累加 N 次」。
    #     该页整页级的计数（首页合并卡片单元）仍用无后缀的 sourceUrl，两者互不干扰。
    it['ck'] = count_key(item)
    return strip_frontend_fields(it)


def write_detail_chunks(buckets, updated_at):
    """写出长文本分片与清单。空桶不产出文件（省请求）。"""
    import glob
    detail_dir = os.path.join(SITE_DIR, 'detail')
    for old in glob.glob(os.path.join(detail_dir, 'detail_*.json')):
        os.remove(old)
    os.makedirs(detail_dir, exist_ok=True)
    files = []
    total = 0
    for i, rows in enumerate(buckets):
        if not rows:
            continue
        fname = 'detail_%02d.json' % i
        atomic_write_json(os.path.join(detail_dir, fname),
                          {'updatedAt': updated_at, 'data': rows})
        files.append('lectures/detail/' + fname)
        total += len(rows)
    manifest = {'updatedAt': updated_at, 'buckets': DETAIL_BUCKETS, 'files': files}
    atomic_write_json(os.path.join(detail_dir, 'manifest.json'), manifest)
    print(f'[done] 长文本分片 {len(files)}/{DETAIL_BUCKETS} 桶 (共 {total} 条, '
          f'{os.path.getsize(os.path.join(detail_dir, "manifest.json"))} B 清单)')
    return files


def write_chunks(data, updated_at):
    """将全量数据切片为 chunk_NNNN.json，并写入 chunks.json 清单。

    公网（GitHub Pages）下前端改为逐片加载：每片 ~200KB（100 条），比整文件 6MB 更抗弱网；
    任一片失败仅重试该片，不影响其它片；每片到达数字跳变到已加载真实条数
    （阶梯跳变，2026-10-14 起 100/片，数字以 4 页为粒度持续小步更新），
    彻底解决「手机端一次性拉 6MB 失败 -> 数字定格在 50」的问题。
    """
    # 清理旧分片：数据量减少时不残留（如 100 片缩到 50 片后旧的 chunk_0051+ 仍会被 git add 提交）
    import glob
    for old in glob.glob(os.path.join(SITE_DIR, 'chunk_*.json')):
        os.remove(old)
    chunks = []
    n = len(data)
    for i in range(0, n, CHUNK_SIZE):
        part = data[i:i + CHUNK_SIZE]
        idx = i // CHUNK_SIZE + 1
        fname = 'chunk_%04d.json' % idx
        atomic_write_json(os.path.join(SITE_DIR, fname), {'updatedAt': updated_at, 'data': part})
        chunks.append('lectures/' + fname)
    manifest = {
        'updatedAt': updated_at,
        'total': n,
        'chunkSize': CHUNK_SIZE,
        'chunks': chunks,
    }
    atomic_write_json(os.path.join(SITE_DIR, 'chunks.json'), manifest)
    print(f'[done] chunks.json + {len(chunks)} 片 (每片 {CHUNK_SIZE} 条, 共 {n} 条)')


def warn_uncommitted_scripts():
    """scripts/ 或 scraper/ 有未提交改动时提醒手动提交（CI 不代为提交脚本本身）。

    背景：本脚本会改写 site/index.html / site/stats.html（打 JS 版本号），而 CI 的
    git add 列表含这两个 HTML、不含脚本自身（见 docs/PARSING_RULES.md §3.3）。本地若
    只提交了 HTML、忘了提交脚本，公网会用旧脚本重跑出片且无人察觉。此处显式告警，
    消除「以为 CI 会自动处理」的误解。CI 工作树干净，故该提示只会在本地出现。
    """
    try:
        r = subprocess.run(['git', 'status', '--porcelain', '--', 'scripts/', 'scraper/'],
                           cwd=ROOT, capture_output=True, text=True, timeout=10)
    except Exception:
        return
    changed = [l for l in (r.stdout or '').splitlines() if l.strip()]
    if r.returncode == 0 and changed:
        print(f'[warn] scripts/ 或 scraper/ 存在 {len(changed)} 个未提交改动——CI 不会代为'
              f'提交脚本自身；若这些改动影响出片/解析，请手动 commit 后再推送')


def warn_stale_stamp_inputs():
    """site/*.js 有未暂存（未 git add）改动时打印醒目告警。

    背景（2026-10-12）：版本戳按 **git 索引版**内容计算（`git show :path`），改了
    site/*.js 但没 add 就跑本脚本，刷出的戳对应旧内容——提交后 CI test_99 立即红、
    部署被跳过。该形态只有本地能发现，在此处显式告警；与 .githooks/pre-commit
    （提交瞬间的硬拦截）互为补充，本告警覆盖「跑了脚本但没 add」这条前置链路。
    """
    try:
        r = subprocess.run(['git', 'diff', '--name-only', '--', 'site/*.js'],
                           cwd=ROOT, capture_output=True, text=True, timeout=10)
    except Exception:
        return
    changed = [l.strip() for l in (r.stdout or '').splitlines() if l.strip()]
    if r.returncode == 0 and changed:
        print('=' * 64)
        print('[warn] 以下 site/*.js 有未暂存改动（未 git add），版本戳正按'
              ' git 索引里的【旧内容】计算：')
        for p in changed:
            print(f'         - {p}')
        print('       直接提交会触发 CI 门禁红（test_99 版本戳不一致）。')
        print('       请先：git add -A site && 重新运行本脚本 && git add site')
        print('=' * 64)


def write_visits_snapshot():
    """把访问量台账的最后一条快照出片为 site/lectures/visits.json。

    用途：页脚在 busuanzi 失效时的**降级数据源**——公网是纯静态托管，页面只能
    读静态文件，所以降级值必须随站发布。台账由 scripts/fetch_visits_snapshot.py
    每日在 CI 中累积；本地若从未跑过该脚本则台账不存在，此时跳过（不影响其余
    产物，页脚仍走 busuanzi 实时值）。

    只出片**末值**，不含历史明细——历史明细归 data/ 台账与 reports/ 报告所有，
    避免把完整历史暴露在公网可访问的静态目录下。
    """
    history_path = os.path.join(ROOT, 'data', 'visits_history.json')
    if not os.path.exists(history_path):
        print('[skip] 无访问量台账，跳过 visits.json 出片')
        return
    try:
        with open(history_path, encoding='utf-8') as f:
            hist = json.load(f)
    except (ValueError, OSError) as e:
        print(f'[warn] 访问量台账读取失败，跳过出片：{type(e).__name__}: {e}')
        return

    snaps = hist.get('snapshots') or []
    if not snaps:
        print('[skip] 访问量台账为空，跳过 visits.json 出片')
        return

    last = snaps[-1]
    payload = {
        'date': last.get('date', ''),
        'capturedAt': last.get('capturedAt', ''),
        # 2026-10-18：UV 停更（自建计数无法按 IP 去重），site_uv 一并移出 payload——
        # 留着会退化为恒 0 的脏值，而它的唯一消费方 footer-counter.js 只读 site_pv。
        'site_pv': int(last.get('site_pv') or 0),
    }
    atomic_write_json(os.path.join(SITE_DIR, 'visits.json'), payload)
    print(f"[done] visits.json: 截至 {payload['date']} "
          f"累计 pv={payload['site_pv']}")


def write_sitemap(updated_at):
    """生成 site/sitemap.xml（2026-10-20 批次 A：分享与可发现性）。

    只有 3 个真实页面——讲座列表与筛选都是 SPA 内部状态、没有独立 URL，详情也不
    单独成页。所以 sitemap 恒为 3 条，**别指望它带来收录量**：本站内容全在 JSON
    里，百度不执行 JS，抓到的就是一个空壳 HTML（Google 能渲染 JS，但也要排队）。
    它的实际作用只有两个：给搜索引擎一个「更新频率」信号；满足 Search Console /
    Bing Webmaster 的 sitemap 提交入口。

    ⚠ 必须写**绝对 URL 且带 /lecture-aggregator/ 子路径**：GitHub Pages 项目页挂
    在域名子路径下，写相对路径或漏掉子路径会被判为无效条目。
    lastmod 直接复用数据更新时间（W3C datetime，与 lectures.json 的 updatedAt
    同源），保证「sitemap 声称的更新」与「数据真的更新」永远一致，不会出现
    手工维护导致的日期漂移。
    """
    urls = [
        ('', '1.0', 'daily'),
        ('stats.html', '0.8', 'daily'),
        ('visits-trend.html', '0.3', 'weekly'),
    ]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, prio, freq in urls:
        lines += ['  <url>',
                  f'    <loc>{SITE_BASE_URL}{path}</loc>',
                  f'    <lastmod>{updated_at}</lastmod>',
                  f'    <changefreq>{freq}</changefreq>',
                  f'    <priority>{prio}</priority>',
                  '  </url>']
    lines.append('</urlset>')
    atomic_write_text(os.path.join(ROOT, 'site', 'sitemap.xml'), '\n'.join(lines) + '\n')
    print(f'[done] sitemap.xml: {len(urls)} 条')


# ---------- 订阅源：RSS + iCalendar（2026-10-20 批次 B） ----------
# 两个 feed 解决的是不同问题，别混为一谈：
#   · feed.xml（RSS）  —— 「有新讲座了」：阅读器里看，不提醒
#   · feed.ics（日历） —— 「今天下午三点该去了」：进日程，到点提醒，改期自动同步
# 都只收「有时间、未标记时间未知、还没结束」的讲座，按开始时间升序取前 100 场。
# 理由：订阅者关心的是「接下来有什么」，不是「历史上都有什么」；100 条足够，
# 再多阅读器和日历都会嫌吵。
CST = timezone(timedelta(hours=8))
FEED_MAX = 100
_DAY_ABBR = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
_MON_ABBR = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _parse_cst(s):
    """'2026-10-31 14:00:00'（北京时间字面量）→ aware datetime；非法 → None。"""
    try:
        return datetime.strptime(str(s or '')[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=CST)
    except ValueError:
        return None


def _rfc822(dt):
    """RFC 822 日期（RSS 的 pubDate）。

    ⚠ **不能**用 `strftime('%a, %d %b %Y')`：`%a`/`%b` 是 locale 相关的，
    中文 locale 下会输出「星期六」「十月」，RSS 解析器直接判为非法日期。
    这里用写死的英文缩写表，与运行环境无关。
    """
    return (f'{_DAY_ABBR[dt.weekday()]}, {dt.day:02d} {_MON_ABBR[dt.month - 1]} '
            f'{dt.year} {dt:%H:%M:%S} +0800')


def _ics_stamp(dt):
    """iCalendar 的 UTC 时间戳。统一用 UTC（Z 结尾），任何时区的日历打开都是同一瞬间。"""
    return dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _ics_esc(s):
    """RFC 5545 转义：反斜杠、分号、逗号，换行写成字面量 \\n。"""
    return (str(s if s is not None else '')
            .replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
            .replace('\r\n', '\\n').replace('\n', '\\n').replace('\r', '\\n'))


def _ics_fold(line):
    """按 UTF-8 **字节**折行（≤75 字节），续行以单个空格开头。

    必须按字节折：中文一个字 3 字节，按字符折会写出超长行，按字节但切在
    汉字中间则得到乱码。这里逐字符累加字节数，永远切在字符边界上。
    """
    if len(line.encode('utf-8')) <= 75:
        return line
    out, cur, n = [], '', 0
    for ch in line:
        b = len(ch.encode('utf-8'))
        if n + b > 74:
            out.append(cur)
            cur, n = ' ', 1
        cur += ch
        n += b
    out.append(cur)
    return '\r\n'.join(out)


def _xml_esc(s):
    return (str(s if s is not None else '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;').replace("'", '&apos;'))


def feed_records(data):
    """挑出可订阅的讲座：(start, end, rec) 列表，按开始时间升序，最多 FEED_MAX 条。"""
    now = datetime.now(CST)
    rows = []
    for r in data:
        if str(r.get('timeUnknown')) == 'True':
            continue
        start = _parse_cst(r.get('lectureStart'))
        if not start:
            continue
        end = _parse_cst(r.get('lectureEnd'))
        if not end or end <= start:
            end = start + timedelta(hours=1)   # lectureEnd 只覆盖 49.7%，缺省 +1h
        if end < now:
            continue                            # 已结束的进订阅源没有意义
        rows.append((start, end, r))
    rows.sort(key=lambda x: x[0])
    return rows[:FEED_MAX]


def _feed_title(rec):
    t, topic = rec.get('title') or '', rec.get('topic') or ''
    if topic and topic != t:
        return f'{t}｜{topic}' if t else topic
    return t or '学术讲座'


def _feed_desc(rec):
    bits = []
    if rec.get('topic'):
        bits.append(f'题目：{rec["topic"]}')
    who = rec.get('speaker') or ''
    if who and rec.get('speakerAffiliation'):
        who = f'{who}（{rec["speakerAffiliation"]}）'
    if who:
        bits.append(f'主讲：{who}')
    if rec.get('location'):
        bits.append(f'地点：{rec["location"]}')
    if rec.get('college') or rec.get('organizer'):
        bits.append(f'主办：{rec.get("college") or rec.get("organizer")}')
    return '\n'.join(bits)


def write_feeds(data, updated_at):
    """生成 site/feed.xml（RSS 2.0）与 site/feed.ics（日历订阅）。"""
    rows = feed_records(data)
    if not rows:
        print('[warn] 没有可订阅的讲座，跳过 feed 生成')
        return
    built = _parse_cst(updated_at.replace('T', ' ')) or datetime.now(CST)
    base = SITE_BASE_URL
    title_site = '木铎金声 · 华师讲座聚合'
    desc_site = '汇集华南师范大学各学院与部处的学术讲座，自动抓取 53 个信息源，每天更新。'

    # ---- RSS 2.0 ----
    items = []
    for start, _end, rec in rows:
        link = rec.get('sourceUrl') or base
        guid = rec.get('ck') or link
        items.append('\n'.join([
            '    <item>',
            f'      <title>{_xml_esc(_feed_title(rec))}</title>',
            f'      <link>{_xml_esc(link)}</link>',
            f'      <guid isPermaLink="false">{_xml_esc(guid)}</guid>',
            f'      <pubDate>{_rfc822(start)}</pubDate>',
            f'      <category>{_xml_esc(rec.get("college") or "")}</category>',
            f'      <description>{_xml_esc(_feed_desc(rec))}</description>',
            '    </item>',
        ]))
    rss = '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        '  <channel>',
        f'    <title>{_xml_esc(title_site)}</title>',
        f'    <link>{base}</link>',
        f'    <description>{_xml_esc(desc_site)}</description>',
        '    <language>zh-CN</language>',
        f'    <lastBuildDate>{_rfc822(built)}</lastBuildDate>',
        f'    <atom:link href="{base}feed.xml" rel="self" type="application/rss+xml"/>',
        *items,
        '  </channel>',
        '</rss>',
    ]) + '\n'
    atomic_write_text(os.path.join(ROOT, 'site', 'feed.xml'), rss)

    # ---- iCalendar ----
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0',
             'PRODID:-//Muduo Jinsheng//Lecture Aggregator//CN',
             'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
             'X-WR-CALNAME:木铎金声 · 华师讲座', 'X-WR-TIMEZONE:Asia/Shanghai',
             'X-WR-CALDESC:' + _ics_esc(desc_site)]
    stamp = _ics_stamp(datetime.now(timezone.utc))
    for start, end, rec in rows:
        uid = (rec.get('ck') or rec.get('sourceUrl') or _feed_title(rec)) + '@danchekey.github.io'
        lines += ['BEGIN:VEVENT', 'UID:' + _ics_esc(uid), 'DTSTAMP:' + stamp,
                  'DTSTART:' + _ics_stamp(start), 'DTEND:' + _ics_stamp(end),
                  'SUMMARY:' + _ics_esc(_feed_title(rec))]
        if rec.get('location'):
            lines.append('LOCATION:' + _ics_esc(rec['location']))
        lines.append('DESCRIPTION:' + _ics_esc(_feed_desc(rec)))
        if rec.get('sourceUrl'):
            lines.append('URL:' + _ics_esc(rec['sourceUrl']))
        lines += ['BEGIN:VALARM', 'TRIGGER:-PT30M', 'ACTION:DISPLAY',
                  'DESCRIPTION:讲座即将开始', 'END:VALARM', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    ics = '\r\n'.join(_ics_fold(x) for x in lines) + '\r\n'
    atomic_write_text(os.path.join(ROOT, 'site', 'feed.ics'), ics)

    print(f'[done] feed.xml / feed.ics: {len(rows)} 场（未来讲座，按时间升序）')


def main():
    warn_uncommitted_scripts()
    warn_stale_stamp_inputs()
    os.makedirs(SITE_DIR, exist_ok=True)
    # 访问量快照出片与讲座数据无关，先独立执行——即使无讲座数据也应保留降级数据源
    write_visits_snapshot()
    data, updated_at = load_lectures()
    if not data:
        print('[warn] 没有讲座数据，跳过生成')
        return

    # 全局排除名单：凡是列入的 URL 不应出现在聚合页面（与爬虫端跳过抓取保持一致）。
    # 这是根治「排除过的非讲座又回来」的关键——之前 excluded 只被爬虫用，展示端从不过滤。
    excluded = load_excluded()
    if excluded:
        before = len(data)
        # 2026-10-02：改用记录级判定（excluded_urls.is_record_excluded）——
        # 此前只看主 sourceUrl，跨源合并记录的 sources[].sourceUrl 命中名单时
        # 整条仍会上站（前端「多来源」还能跳到那个已被否决的页面，排除形同虚设）。
        data = [r for r in data if not is_record_excluded(r, excluded)]
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
    # 长文本（简介/摘要）先剥离到独立分片，主分片因此只带结构化字段。
    # latest.json 例外：首屏 50 条的简介/摘要就地内联（本来就是截断预览），
    # 让「不点展开、只看首屏」的场景零额外请求；但仍须标出桶号 b，
    # 否则用户在这 50 条上点「展开更多」时前端不知道去哪一桶取全文。
    full_data = [with_unit(item, url_dates) for item in data]
    full_data, detail_buckets = split_long_text(full_data)
    latest_src = [with_unit(item, url_dates) for item in sorted_data[:LATEST_SIZE]]
    latest = []
    for it in latest_src:
        b = lt_bucket(it)
        if b is not None:
            it['b'] = b
        latest.append(latest_preview(it))
    stats = build_stats(data, updated_at)

    # 同时写入 site/lectures.json 与切片，全部使用原子写入，确保首页与统计页版本一致
    atomic_write_json(SITE_LECTURES_PATH, {'updatedAt': updated_at, 'data': full_data})
    atomic_write_json(os.path.join(SITE_DIR, 'latest.json'), {'updatedAt': updated_at, 'data': latest})
    atomic_write_json(os.path.join(SITE_DIR, 'stats.json'), stats)
    write_chunks(full_data, updated_at)
    write_detail_chunks(detail_buckets, updated_at)

    latest_bytes = os.path.getsize(os.path.join(SITE_DIR, 'latest.json'))
    stats_bytes = os.path.getsize(os.path.join(SITE_DIR, 'stats.json'))
    stats_lectures_count = len(stats['lectures'])
    print(f'[done] site/lectures.json: {len(data)} 条')
    print(f'[done] latest.json: {len(latest)} 条 ({latest_bytes / 1024:.1f} KB)')
    print(f'[done] stats.json: {stats_lectures_count} 条索引 ({stats_bytes / 1024:.1f} KB)')

    # 给前端脚本打内容 hash 版本号，避免浏览器长期缓存旧 JS（见 stamp_script_version 注释）。
    stamp_script_version('stats.html', 'stats.js')
    # app.js 已按职责分片，各分片都要打戳——漏掉的分片会永久冻结在旧版本上（静默无告警）。
    # 顺序与 index.html 中的加载顺序一致（app.js 组装文件放在最后）。
    # app.cloud.js：云端共享计数层，须先于 app.social.js 执行（const 声明，防 TDZ）。
    for _part in ('app.core.js', 'app.state.js', 'app.computed.js', 'app.display.js',
                  'app.cloud.js', 'app.social.js', 'app.calendar.js',
                  'app.data.js', 'app.admin.js'):
        stamp_script_version('index.html', _part)
    stamp_script_version('index.html', 'app.js')
    stamp_script_version('index.html', 'footer-counter.js')
    stamp_script_version('stats.html', 'app.cloud.js')
    stamp_script_version('stats.html', 'footer-counter.js')
    # 2026-10-10：访问量趋势页此前**完全在缓存破坏机制之外**——
    # visits-trend.js / visits-trend.css 是裸引用，既没注册打戳，也没进
    # tests/test_frontend_schema.py 的 PAGES 清单（只有 index/stats 两页），
    # 属结构性盲区：改了趋势页样式或脚本，回访用户仍跑旧版。
    # （git 历史已有两轮同源事故：f58e5fd 重打 app.admin.js 戳、daa11e1 趋势页
    #   导航改落生成脚本模板——都是「清单漏一处、零告警」。）
    stamp_script_version('visits-trend.html', 'visits-trend.js')
    stamp_script_version('visits-trend.html', 'visits-trend.css')

    # sitemap 与订阅源只依赖数据本身，放在最后：所有前端产物落盘后再写，即使这里
    # 出错也不影响首页/统计页可用（SEO 与订阅是附属物，不是功能件）。
    write_sitemap(updated_at)
    write_feeds(data, updated_at)


if __name__ == '__main__':
    main()
