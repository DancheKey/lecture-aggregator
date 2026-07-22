"""华南师范大学讲座采集器：列表页 -> 详情页 -> 去重 -> data/lectures.json。"""
import os
import re
import sys
import json
import time
import yaml
import requests
import charset_normalizer
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def fetch(url, _retries=3):
    """下载页面并鲁棒解码。

    网络层异常（连接重置 ECONNRESET / 超时 / 断流 等 requests.RequestException）
    做指数退避重试，避免偶发抖动被误判为死链而漏抓；死链（HTTP 4xx/5xx，
    requests 不抛异常）直接返回解码文本，不重试。重试耗尽仍失败返回 None
    （调用方按死链处理）。
    """
    import random
    last_err = None
    for _i in range(_retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            return _decode_html(r.content)
        except requests.exceptions.RequestException as e:
            last_err = e
            if _i < _retries - 1:
                time.sleep(min(1.5 * (2 ** _i), 8) + random.uniform(0, 0.4))
                continue
            print(f'[WARN] fetch failed {url}: {last_err}', file=sys.stderr)
            return None
        except Exception as e:  # 非网络异常（如极端解码情况）：不重试，直接放弃
            print(f'[WARN] fetch error {url}: {e}', file=sys.stderr)
            return None
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
    """记录字段完整度：非空关键字段越多越「完整」，去重/合并时优先保留。"""
    return sum(1 for v in [
        r.get('lectureStart'), r.get('location'), r.get('speaker'),
        r.get('speakerAffiliation'), r.get('topic'), r.get('speakerBio')
    ] if v)


def _normalize_speaker(speaker):
    """主讲人归一化：去掉职称后缀，用于跨源匹配。"""
    if not speaker:
        return ''
    s = speaker.strip()
    # 去掉常见职称
    for suffix in ['教授', '副教授', '讲师', '研究员', '副研究员',
                   '院士', '博士', '博士后', '博士生导师', '硕士生导师']:
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[:-len(suffix)]
    return s.strip()


def _is_valid_speaker_name(name):
    """判断主讲人是否为真实姓名（排除「联系方式」「我校生命」等解析噪声）。

    合法姓名特征：2~10 字符，不含明显非人名词汇。
    """
    if not name or len(name) < 2 or len(name) > 15:
        return False
    # 明确非法词（解析器常见噪声）
    invalid_keywords = [
        '联系方式', '报名', '投稿', '截止', '审核', '发布', '更新', '修改',
        '创建', '我校', '学院', '中心', '研究院', '实验室', '办公室', '秘书处',
        '组委会', '筹备组', '主办方', '承办方', '协办方', '通知', '公告',
        '欢迎', '敬请', '详情', '咨询', '联系',
    ]
    name_lower = name.lower()
    for kw in invalid_keywords:
        if kw in name:
            return False
    # 纯数字或纯标点
    if re.match(r'^[\d\s\W]+$', name):
        return False
    return True


def _tokenize(text):
    """中文文本分词（字符级 bigram + 长词兜底），用于相似度计算。

    对中文文本比按标点切分更鲁棒：能容忍标点差异（如「以格局铸根基,以传统文化」
    vs 「以格局铸根基以传统文化」），因为共享的字符序列仍会产生重叠 bigram。
    """
    if not text:
        return set()
    # 先去标点，保留纯文字
    clean = re.sub(r'[\s,，。、；；：:！!？?·…—\-()（）\[\]【】""\'\'《》<>""／/]+', '', text)
    if len(clean) < 2:
        return set()
    # 字符级 bigram（相邻两字一组）
    bigrams = {clean[i:i+2] for i in range(len(clean) - 1)}
    # 额外保留 4+ 字符的长片段作为补充信号
    long_tokens = {m.group() for m in re.finditer(r'.{4,}', clean)}
    return bigrams | long_tokens


def _topic_similarity(a, b):
    """两段文本的关键词重叠度（Jaccard 系数）。返回 0~1。"""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def cross_source_dedup(records):
    """跨源去重：不同学院发布的同一讲座合并为一条。

    判定规则（两层）：
      必须条件：同一主讲人（归一化后） + 同一讲座日期（精确到天）
      充分条件：标题或 topic 的关键词 Jaccard 重叠度 ≥ 0.25

    合并策略：
      - 保留字段更完整的记录作为主记录（primary）
      - 其他记录变为 sources 数组中的条目：{sourceUrl, college, campus, title}
      - 主记录设置 merged=True, sourceCount=len(sources)+1（含自身）

    为什么用「主讲人+日期」而不是纯标题相似：
      同一讲座在不同学院的标题差异极大（如 psy 用短标题"5月31日 林崇德教授砺儒讲坛"，
      skc 用正式长标题"华南师范大学砺儒讲坛第146讲：…"），但主讲人和日期一定一致。
    """
    # 第一轮：按 (speaker_normalized, date) 分组（仅合法姓名参与）
    groups = {}
    for rec in records:
        spk = _normalize_speaker(rec.get('speaker') or '')
        if not _is_valid_speaker_name(spk):
            continue
        date = (rec.get('lectureStart') or '')[:10]
        if not date or date.startswith('0000'):
            continue
        key = (spk, date)
        groups.setdefault(key, []).append(rec)

    merged_urls = set()   # 已被合并进其他记录的 sourceUrl（需从最终列表中移除）
    merge_count = 0

    for key, group in groups.items():
        if len(group) < 2:
            continue

        # 组内两两比较：找可合并的对
        n = len(group)
        # union-find 简化版：parent[i]=i 表示独立，否则指向主记录索引
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for i in range(n):
            for j in range(i + 1, n):
                if find(i) == find(j):
                    continue
                ri, rj = group[i], group[j]
                # 不同源才考虑合并（同源重复已由 dedup 处理）
                if ri.get('sourceUrl', '').rstrip('/') == rj.get('sourceUrl', '').rstrip('/'):
                    continue
                # topic 或 title 相似度（含跨字段交叉比较：
                # 有的源把实质内容放 topic、有的放 title，需四向全比）
                ti_a = ri.get('topic', '') or ri.get('title', '')
                ti_b = rj.get('topic', '') or rj.get('title', '')
                sim = max(
                    _topic_similarity(ri.get('topic', ''), rj.get('topic', '')),
                    _topic_similarity(ri.get('title', ''), rj.get('title', '')),
                    _topic_similarity(ti_a, ti_b),          # A有效文本 vs B有效文本
                    _topic_similarity(ri.get('topic', ''), rj.get('title', '')),  # A topic vs B title
                    _topic_similarity(ri.get('title', ''), rj.get('topic', '')),  # A title vs B topic
                )
                if sim >= 0.25:
                    union(i, j)

        # 按 find 结果聚簇执行合并
        clusters = {}
        for i in range(n):
            root = find(i)
            clusters.setdefault(root, []).append(i)

        for root_idx, members in clusters.items():
            if len(members) < 2:
                continue

            # 选字段最完整的做主记录
            primary_idx = max(members, key=lambda idx: _completeness(group[idx]))
            primary = group[primary_idx]

            primary_college = primary.get('college', '')
            seen_colleges = set()
            sources_list = []
            for idx in members:
                if idx == primary_idx:
                    continue
                other = group[idx]
                oc = other.get('college', '')
                # 同学院的自我合并：跳过，保留为独立记录（不合并、不移除）
                if oc == primary_college:
                    continue
                # 跨院从记录：合并进主记录，从最终列表移除
                merged_urls.add(other.get('sourceUrl', '').rstrip('/'))
                # 折叠重复学院（如社科处把同一场讲座发了两次 → 只计一次来源）
                if oc in seen_colleges:
                    continue
                seen_colleges.add(oc)
                sources_list.append({
                    'sourceUrl': other.get('sourceUrl', ''),
                    'college': oc,
                    'campus': other.get('campus', ''),
                    'title': other.get('title', ''),
                })

            # 去掉自我合并/重复后已无可合并的跨院来源 → 本簇不作为跨源合并
            if not sources_list:
                continue

            # 如果主记录已有 sources（来自同源去重阶段），合并进去并去重
            existing_sources = primary.get('sources') or []
            all_sources = []
            _seen = set()
            for s in existing_sources + sources_list:
                c = s.get('college', '')
                if c == primary_college or c in _seen:
                    continue
                _seen.add(c)
                all_sources.append(s)
            primary['sources'] = all_sources
            primary['merged'] = True
            primary['sourceCount'] = len(all_sources) + 1  # 含自身
            # 补全策略：用从记录的非空字段填补主记录的空字段
            for field in ['speakerTitle', 'speakerAffiliation', 'location',
                          'speakerBio', 'organizer']:
                if not primary.get(field):
                    for src_rec in [group[idx] for idx in members if idx != primary_idx]:
                        val = src_rec.get(field)
                        if val:
                            primary[field] = val
                            break
            merge_count += len(members) - 1
            print(f'[MERGE] {key[0]} @ {key[1]} → {len(members)} 条合并为 1 '
                  f'(主记录: {primary["college"]} | 来源: {", ".join(s["college"] for s in sources_list)})')

    if merge_count:
        print(f'[MERGE] 跨源去重完成，共 {merge_count} 条被合并')

    # 过滤掉已被合并的从记录
    result = [r for r in records
              if r.get('sourceUrl', '').rstrip('/') not in merged_urls]
    return result


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


def _process_source(src, year, existing_urls, is_incremental):
    """处理单个信息源，返回 {url: rec} 字典。"""
    name = src['name']
    campus = src.get('campus', '')
    base = src['base']
    src_list_norm = set()
    for lu in src.get('list_urls', []):
        u = lu['url'] if isinstance(lu, dict) else lu
        src_list_norm.add(str(u).rstrip('/'))
    exclude_urls = {str(u).rstrip('/') for u in src.get('exclude_urls', [])}
    seen = set()
    visited_pages = set()
    local = {}
    try:
        for lu in src.get('list_urls', []):
            if isinstance(lu, dict):
                list_url = lu['url']
                collect_mode = lu.get('collect_mode', 'auto')
            else:
                list_url = lu
                collect_mode = 'auto'
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
                    if is_incremental and href_norm in existing_urls:
                        continue
                    if href_norm in exclude_urls:
                        print(f'[SKIP] {name} exclude {href}')
                        continue
                    if href_norm in src_list_norm:
                        continue
                    d = fetch(href)
                    if not d:
                        continue
                    try:
                        recs = parse_detail(d, href, name, campus, year, list_title=txt,
                                            skip_news_filter=src.get('skip_news_filter', False))
                    except Exception as e:
                        print(f'[WARN] parse failed {href}: {e}', file=sys.stderr)
                        continue
                    if recs is None:
                        print(f'[SKIP-NEWS] {name} | {txt} | {href}')
                        continue
                    if not isinstance(recs, list):
                        recs = [recs]
                    for r in recs:
                        r['listTitle'] = txt
                        # 多讲座拆分后多条共享同一 sourceUrl，需用 期号 区分 key 防覆盖
                        key = r['sourceUrl'] + (('#' + str(r['lectureIndex'])) if r.get('lectureIndex') else '')
                        local[key] = r
                        tag = f' (第{r["lectureIndex"]}期)' if r.get('lectureIndex') else ''
                        print(f'[OK] {name} | {r.get("lectureStart")} | {txt}{tag}')
                nxt = _next_page_url(html, base) if html else None
                sequential = False
                if not nxt and new_count > 0:
                    cand = _sequential_candidate(cur)
                    if cand:
                        nxt = cand
                        sequential = True
                if (not nxt or nxt.rstrip('/') in visited_pages
                        or nxt.rstrip('/') == cur.rstrip('/')):
                    break
                if sequential and new_count == 0:
                    break
                cur = nxt
                if len(visited_pages) > 300:
                    print(f'[WARN] {name} 分页超过 300 页，停止跟随')
                    break
        time.sleep(1)
    except Exception as e:
        print(f'[ERROR] 信息源「{name}」抓取失败：{e}', file=sys.stderr)
    return local


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--since', help='仅抓取该 ISO 时间之后的新信息（增量模式）')
    parser.add_argument('--full', action='store_true', help='全量抓取，忽略增量')
    parser.add_argument('--source', help='仅抓取指定名称的信息源（用于局部修复/测试）')
    parser.add_argument('--out', help='将本源结果写入指定路径（而非合并进 data/lectures.json），'
                                      '用于「并行多进程分批重抓 + 最后统一合并」的场景，避免空库并发写竞争')
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

    # 并发抓取各信息源：源与源之间独立，大幅缩短 GitHub Actions 全量/增量耗时
    max_workers = 1 if args.source else 5
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_src = {executor.submit(_process_source, src, year, existing_urls, is_incremental): src for src in sources}
        for future in as_completed(future_to_src):
            src_name = future_to_src[future].get('name')
            try:
                local = future.result()
                for url, rec in local.items():
                    lectures[url] = rec
            except Exception as e:
                print(f'[ERROR] 合并信息源「{src_name}」结果失败：{e}', file=sys.stderr)

    # 同源去重：同一学院标题相似的只保留一条
    raw = list(lectures.values())
    # 局部修复模式（--source，且无 --out）：保留其他学院已有记录，仅替换指定学院的记录
    if args.source and not args.out:
        other_existing = [r for r in existing if r.get('college') != args.source]
        raw = other_existing + raw
        # 安全拦截：--source 模式绝不应让总条数大幅缩水，否则大概率是 existing
        # 加载失败（json.load 异常被静默吞掉 → existing=[]）导致用单源覆盖全量。
        # 一旦产出 < 现有条数 50%，拒绝覆盖，避免误删其他学院数据。
        if existing and len(raw) < len(existing) * 0.5:
            print(f'[ABORT] --source 模式产出 {len(raw)} 条 < 现有 {len(existing)} 条的 50%，'
                  f'疑似现有数据未正确合并，拒绝覆盖 data/lectures.json。', file=sys.stderr)
            return
    out = dedup(raw)
    # 跨源去重：不同学院发布的同一讲座合并为一条（同主讲+同日期+topic相似）。
    # --out 模式由各源独立写出、最后由驱动脚本统一合并，故此处跳过跨源去重避免重复。
    if not args.out:
        out = cross_source_dedup(out)
    out.sort(key=lambda x: x.get('lectureStart') or '', reverse=True)
    import datetime
    # 用北京时间（Asia/Shanghai）记录更新时间，避免 GitHub Runner 默认 UTC 导致日期差一天
    try:
        from zoneinfo import ZoneInfo
        now_iso = datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    except Exception:
        # 回退：UTC+8 小时
        now_iso = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).isoformat(timespec='seconds')
    # 写入带更新时间戳的包裹格式：{updatedAt, data}；前端与后端均兼容旧版纯数组。
    if args.out:
        # 并行分批重抓：本源结果独立落盘，最后由驱动脚本汇总合并
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump({'updatedAt': now_iso, 'data': out}, f, ensure_ascii=False, indent=2)
        print(f'[DONE] source={args.source} -> {args.out} ({len(out)} records)')
        return
    data_dir = os.path.join(ROOT, 'data')
    os.makedirs(data_dir, exist_ok=True)
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
