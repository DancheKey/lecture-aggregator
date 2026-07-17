"""华南师范大学讲座采集器：列表页 -> 详情页 -> 去重 -> data/lectures.json。"""
import os
import re
import sys
import json
import time
import yaml
import requests
import charset_normalizer
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parsers import parse_detail, is_lecture, is_news_record  # noqa: E402

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SCNULectureAggregator/0.1)'}
TIMEOUT = 15
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _decode_html(raw):
    """鲁棒解码 HTML：优先 <meta charset> 声明，其次 UTF-8 严格，再次 GB18030 兜底。

    华师站点编码混杂（现代站 UTF-8、老站 GBK/GB2312）。仅用 charset_normalizer 易把
    GBK 误判为 UTF-8（页面混有 ASCII 时），导致中文乱码、日期解析错位（如 ibc 站点把
    「2025年12月30日」丢失，侧边栏 ASCII 日期被误当讲座时间）。故增加 meta 声明优先 +
    GB18030 超集兜底，覆盖绝大多数中文站点。
    """
    # 1) <meta charset> / <meta http-equiv=Content-Type> 显式声明优先
    try:
        head = raw[:2048].decode('latin-1', errors='ignore')
        m = re.search(r'charset\s*=\s*[\'"]?\s*([a-z0-9\-_]+)', head, re.I)
        if m:
            enc = m.group(1).strip().lower()
            if enc in ('gb2312', 'gbk', 'gb18030', 'gbk2312'):
                enc = 'gb18030'
            elif enc in ('big5', 'big5hkscs'):
                enc = 'big5'
            if enc not in ('utf-8', 'utf8', 'us-ascii', 'ascii', 'iso-8859-1'):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    pass
    except Exception:
        pass
    # 2) UTF-8 严格优先（现代站点主流）
    try:
        raw.decode('utf-8')
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        pass
    # 3) GB18030 兜底（GBK/GB2312 超集，覆盖老站点）
    try:
        return raw.decode('gb18030')
    except UnicodeDecodeError:
        pass
    # 4) charset_normalizer 最终兜底
    best = charset_normalizer.from_bytes(raw).best()
    if best:
        return str(best)
    return raw.decode('utf-8', errors='replace')


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        return _decode_html(r.content)
    except Exception as e:
        print(f'[WARN] fetch failed {url}: {e}', file=sys.stderr)
        return None


NAV_KW = ['首页', '主页', '上一页', '下一页', '尾页', '返回', '更多', '>>',
          'home', 'about', 'contact', 'rss', 'sitemap']
# all_items 模式下，列表标题命中这些词直接视为非讲座（通知/招聘/比赛/培训等），
# 不再下载详情页解析。用于「学术讲座栏目但列表标题即讲座名」的院系（如砺儒论坛、勷勤数学）。
EXCLUDE_TITLE_KW = ['通知', '招聘', '答辩', '公示', '大赛', '初赛', '复赛', '决赛',
                    '培训', '宣讲', '招募', '报名', '征稿', '评奖', '获奖', '喜报',
                    '放假', '就业', '职路', '生涯', '课程', '安排', '年会', '夏令营',
                    '实习', '调剂', '复试', '录取', '考试', '成果获', '研究成果', '论文', '发表',
                    '论点摘编', '出版', '立项', '结项', '获批', '荣获']
# 常见 CMS 内容页 URL 特征：/a/20260616/348.html 或 /xueshujiangzuo/2026/0628/74.html
_CONTENT_URL_RE = re.compile(r'/((a/\d{8}/\d+\.html)|(\d{4}/\d{4}/\d+\.html)|(\d{4}/\d{2}/\d{2}/.*\.html))', re.I)


def _abs_url(href, base):
    if href.startswith('http'):
        return href
    if href.startswith('/'):
        return base.rstrip('/') + href
    return base.rstrip('/') + '/' + href


def collect_links(html, base, list_url=None, collect_mode='auto'):
    """从列表页提取详情页链接。

    collect_mode:
      - auto: 用 is_lecture 标题关键词过滤（默认）。
      - all_items: 不过滤标题关键词，直接抓取列表中看起来像内容项的链接，
                   用于「列表页本身就是讲座列表」的栏目（如行知书院讲座预约）。
    """
    if not html:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    list_url_norm = list_url.rstrip('/') if list_url else None
    for a in soup.find_all('a'):
        txt = a.get_text(strip=True)
        href = a.get('href')
        if not href or not txt:
            continue
        # 跳过 javascript、锚点、当前列表页自身
        if href.startswith('javascript') or href == '#' or href.startswith('#'):
            continue
        # 跳过占位/包装链接：外层 <a> 里还包着真实 <a>，它的 href 通常是 content.html / # 等
        if a.find('a'):
            continue
        url = _abs_url(href, base)
        if list_url_norm and url.rstrip('/') == list_url_norm:
            continue
        # 跳过明显无意义的占位文件名（如 content.html 本身是包装，index.html 是栏目首页）
        path = url.rstrip('/').split('?')[0].lower()
        if path.endswith('/content.html') or path.endswith('/index.html'):
            continue
        if collect_mode == 'all_items':
            # 排除明显导航词
            tlow = txt.lower()
            if any(k in tlow for k in NAV_KW):
                continue
            # 排除标题命中负向关键词的非讲座条目（通知/招聘/比赛/培训等）
            if any(k in txt for k in EXCLUDE_TITLE_KW):
                continue
            # 排除过短文本（导航常见）
            if len(txt) < 4:
                continue
            # all_items 用于「列表页即讲座列表」的栏目，只保留看起来像内容页的链接
            if not _CONTENT_URL_RE.search(url):
                continue
            links.append((url, txt))
        else:
            if not is_lecture(txt):
                continue
            links.append((url, txt))
    return links


_NEXT_PAGE_KW = ('下一页', '下页', '下一頁', '下一页»', '»', '>>', 'Next', 'next', 'NEXT')


def _next_page_url(html, base):
    """从列表页提取「下一页」链接的绝对地址；无则 None。

    用于自动跟随分页：从首列表页开始，沿「下一页」依次抓取，
    直到末页（「下一页」缺失 / 指向自身 / 已访问）为止。避免像
    tongzhigonggao/2.html 那样手工罗列每个分页地址。
    """
    if not html:
        return None
    soup = BeautifulSoup(html, 'html.parser')
    for a in soup.find_all('a'):
        t = a.get_text(strip=True)
        if t not in _NEXT_PAGE_KW:
            continue
        h = a.get('href')
        if not h or h.startswith('javascript') or h == '#' or h.startswith('#'):
            continue
        return _abs_url(h, base)
    return None


def _sequential_candidate(cur_url):
    """JS 渲染分页的兜底：列表页静态 HTML 无「下一页」链接（分页器由 JS 注入，
    形如 <div class="pages"></div>），但分页地址遵循 `根目录/N.html` 规律
    （如 /xueshujiangzuo/2.html、/ae/Colloquium/3.html）。根据当前页 URL 推导下一页。

    仅作为 _next_page_url 返回 None 时的兜底；是否采用由调用方结合本页是否
    确有内容（new_count>0）来判断，避免对单页源误翻页。
    """
    if not cur_url:
        return None
    # .../xueshujiangzuo/2.html -> .../xueshujiangzuo/3.html
    m = re.search(r'/(\d+)\.html$', cur_url)
    if m:
        return cur_url[:m.start()] + '/' + str(int(m.group(1)) + 1) + '.html'
    # 根目录形式：.../xueshujiangzuo/ -> .../xueshujiangzuo/2.html
    if cur_url.endswith('/'):
        return cur_url.rstrip('/') + '/2.html'
    return None


def _normalize_title(title):
    """标题归一化：去空白、去常见前后缀与末尾日期，用于同源去重比对。"""
    s = re.sub(r'\s+', '', title.strip())
    # 去掉末尾常见的来源标注
    s = re.sub(r'[（(][^）)]*[）)]$', '', s)
    # 去掉末尾常见的日期（CMS 列表页常把日期拼在标题后）：2026-06-25、2026/06/25、20260625
    s = re.sub(r'(20\d{2}[-/]?\d{2}[-/]?\d{2}|20\d{6})$', '', s)
    return s


def _completeness(r):
    """记录字段完整度：非空关键字段越多越「完整」，去重时优先保留。"""
    return sum(1 for v in [
        r.get('lectureStart'), r.get('location'), r.get('speaker'),
        r.get('speakerAffiliation'), r.get('topic'), r.get('speakerBio')
    ] if v)


def dedup(records):
    """同源去重：同一讲座只保留一条（保留字段更完整的）。

    ⚠️ 判定「同一讲座」必须 4 要素同时相同：
        (college, 归一化标题, 讲座日期, 来源 URL)
    只要 sourceUrl 不同，就视为不同讲座——即便标题撞车
    （例如多期「学术报告通知」仅日期不同、列表标题被 _clean_title 去掉
    日期前缀后都变成「学术报告通知」），也绝不合并且丢弃，否则会把
    大量真实的不同讲座静默删掉。

    这样设计：同一 URL 被不同列表页/不同次抓取重复收录时仍能正确合并
    （同 URL 必然同 key），而不同 URL 的真实讲座永不被误删。
    """
    groups = {}
    for rec in records:
        url = str(rec.get('sourceUrl', '')).rstrip('/')
        ntitle = _normalize_title(rec.get('title', ''))
        ls = (rec.get('lectureStart') or '')[:10]   # 仅取日期部分，忽略具体时分
        key = (rec.get('college', ''), ntitle, ls, url)
        if key not in groups:
            groups[key] = []
        groups[key].append(rec)

    kept = []
    dup_count = 0
    for key, group in groups.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        # 同 URL 真重复：按字段完整度排序，保留最完整的一条
        group.sort(key=_completeness, reverse=True)
        kept.append(group[0])
        dup_count += len(group) - 1
        print(f'[DEDUP] {key[0]} | {group[0]["title"][:40]} → 保留1条, 去重{len(group)-1}条')
    if dup_count:
        print(f'[DEDUP] 总共去除 {dup_count} 条重复记录')
    return kept


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--since', help='仅抓取该 ISO 时间之后的新信息（增量模式）')
    parser.add_argument('--full', action='store_true', help='全量抓取，忽略增量')
    parser.add_argument('--source', help='仅抓取指定名称的信息源（用于局部修复/测试）')
    args = parser.parse_args()

    cfg_path = os.path.join(ROOT, 'scraper', 'sources.yaml')
    with open(cfg_path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    year = time.localtime().tm_year

    # 决定抓取模式：有 --since 且非 --full → 增量；否则全量
    last_scrape_path = os.path.join(ROOT, 'data', 'last_scrape.json')
    since = args.since
    if not since and not args.full and os.path.exists(last_scrape_path):
        try:
            since = json.load(open(last_scrape_path, encoding='utf-8')).get('last_scrape')
        except Exception:
            since = None
    is_incremental = bool(since) and not args.full

    # 读取现有记录：增量模式作为基底（合并写回）+ 已抓 URL 集合（跳过解析/OCR）
    data_path = os.path.join(ROOT, 'data', 'lectures.json')
    existing = []
    if os.path.exists(data_path):
        try:
            raw = json.load(open(data_path, encoding='utf-8'))
            # 兼容新版包裹格式 {updatedAt, data} 与旧版纯数组
            existing = raw.get('data', []) if isinstance(raw, dict) else raw
        except Exception:
            existing = []
    existing_urls = {str(r.get('sourceUrl', '')).rstrip('/') for r in existing if r.get('sourceUrl')}

    lectures = {}
    if is_incremental:
        # 增量：以已有记录为基底，只补充新 URL（不重新解析旧条目）
        for r in existing:
            u = r.get('sourceUrl')
            if u:
                lectures[u] = r

    sources = cfg['sources']
    if args.source:
        sources = [s for s in sources if s.get('name') == args.source]
        if not sources:
            print(f'[ERROR] 未找到信息源「{args.source}」', file=sys.stderr)
            return
    for src in sources:
        try:
            name = src['name']
            campus = src.get('campus', '')
            base = src['base']
            # 本源所有列表页 URL（归一化），用于跳过「栏目入口」类链接
            src_list_norm = set()
            for lu in src.get('list_urls', []):
                u = lu['url'] if isinstance(lu, dict) else lu
                src_list_norm.add(str(u).rstrip('/'))
            # 明确排除的非讲座 URL
            exclude_urls = {str(u).rstrip('/') for u in src.get('exclude_urls', [])}
            seen = set()
            visited_pages = set()
            for lu in src.get('list_urls', []):
                if isinstance(lu, dict):
                    list_url = lu['url']
                    collect_mode = lu.get('collect_mode', 'auto')
                else:
                    list_url = lu
                    collect_mode = 'auto'
                # 自动跟随分页：从本列表页开始，沿「下一页」依次抓取，直到末页
                cur = list_url
                while cur and cur.rstrip('/') not in visited_pages:
                    visited_pages.add(cur.rstrip('/'))
                    html = fetch(cur)
                    new_count = 0
                    for href, txt in collect_links(html, base, list_url=cur, collect_mode=collect_mode):
                        href_norm = href.rstrip('/')
                        if href_norm in seen:
                            continue
                        seen.add(href_norm)
                        new_count += 1
                        # 增量模式：已抓取过的 URL 直接跳过（不下载详情、不解析、不做 OCR）
                        if is_incremental and href_norm in existing_urls:
                            continue
                        # 跳过源配置中明确排除的 URL（如宣讲会、整体规划等非讲座页面）
                        if href_norm in exclude_urls:
                            print(f'[SKIP] {name} exclude {href}')
                            continue
                        # 跳过指向本源其他列表页的链接（栏目入口，不是讲座详情）
                        if href_norm in src_list_norm:
                            continue
                        d = fetch(href)
                        if not d:
                            continue
                        try:
                            rec = parse_detail(d, href, name, campus, year, list_title=txt)
                        except Exception as e:
                            print(f'[WARN] parse failed {href}: {e}', file=sys.stderr)
                            continue
                        # parse_detail 返回 None 表示命中新闻/回顾过滤，跳过不收录
                        if rec is None:
                            print(f'[SKIP-NEWS] {name} | {txt} | {href}')
                            continue
                        rec['listTitle'] = txt
                        lectures[href] = rec
                        print(f'[OK] {name} | {rec.get("lectureStart")} | {txt}')
                    # 翻到下一页；优先用静态「下一页」链接，缺失时回退到 /N.html 顺序翻页
                    nxt = _next_page_url(html, base) if html else None
                    sequential = False
                    if not nxt and new_count > 0:
                        # 本页确有内容但无「下一页」锚点：多为 JS 注入分页器，
                        # 尝试按 根目录/N.html 规律推导下一页（如 /xueshujiangzuo/2.html）。
                        cand = _sequential_candidate(cur)
                        if cand:
                            nxt = cand
                            sequential = True
                    if (not nxt or nxt.rstrip('/') in visited_pages
                            or nxt.rstrip('/') == cur.rstrip('/')):
                        break
                    # 顺序翻页越过末页后，下一页往往重定向回首页或为空页，
                    # 此时本页没有新链接，及时停止，避免空转。
                    if sequential and new_count == 0:
                        break
                    cur = nxt
                    # 安全阀：极端情况下避免无限翻页
                    if len(visited_pages) > 300:
                        print(f'[WARN] {name} 分页超过 300 页，停止跟随')
                        break
            time.sleep(1)
        except Exception as e:
            # 单源异常不应拖垮整次抓取：记录后继续下一个源，已采数据照常写入
            print(f'[ERROR] 信息源「{src.get("name")}」抓取失败：{e}', file=sys.stderr)
            continue

    # 同源去重：同一学院标题相似的只保留一条
    raw = list(lectures.values())
    # 局部修复模式（--source）：保留其他学院已有记录，仅替换指定学院的记录
    if args.source:
        other_existing = [r for r in existing if r.get('college') != args.source]
        raw = other_existing + raw
    out = dedup(raw)
    out.sort(key=lambda x: x.get('lectureStart') or '', reverse=True)
    data_dir = os.path.join(ROOT, 'data')
    os.makedirs(data_dir, exist_ok=True)
    import datetime
    # 用北京时间（Asia/Shanghai）记录更新时间，避免 GitHub Runner 默认 UTC 导致日期差一天
    try:
        from zoneinfo import ZoneInfo
        now_iso = datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    except Exception:
        # 回退：UTC+8 小时
        now_iso = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).isoformat(timespec='seconds')
    # 写入带更新时间戳的包裹格式：{updatedAt, data}；前端与后端均兼容旧版纯数组。
    with open(os.path.join(data_dir, 'lectures.json'), 'w', encoding='utf-8') as f:
        json.dump({'updatedAt': now_iso, 'data': out}, f, ensure_ascii=False, indent=2)
    # 局部修复模式不更新 last_scrape.json，避免影响下一次全量/定时增量调度
    if not args.source:
        with open(last_scrape_path, 'w', encoding='utf-8') as f:
            json.dump({'last_scrape': now_iso, 'mode': 'incremental' if is_incremental else 'full'},
                      f, ensure_ascii=False, indent=2)
    print(f'[DONE] total {len(out)} lectures -> data/lectures.json  '
          f'(mode={"incremental" if is_incremental else "full"}, source={args.source or "all"}, since={since})')


if __name__ == '__main__':
    main()
