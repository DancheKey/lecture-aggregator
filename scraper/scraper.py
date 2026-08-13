"""华南师范大学讲座采集器：列表页 -> 详情页 -> 去重 -> data/lectures.json。"""
import os
import re
import sys
import json
import time
import yaml
import datetime
import requests
import charset_normalizer
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parsers import parse_detail, is_lecture, is_news_record  # noqa: E402

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SCNULectureAggregator/0.1)'}
TIMEOUT = 15
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _atomic_write_json(path, obj, indent=2):
    """原子写 JSON：先写 .tmp 再 os.replace，避免中途崩溃/被杀留下截断 JSON。"""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


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
            # 若标题本身明显是讲座（含讲座关键词，如「学者讲坛第2讲丨全英课程学习策略」），
            # 不受 EXCLUDE_TITLE_KW 误杀——避免"课程"等词把真讲座不当作讲座过滤掉。
            # 仅当标题不含任何讲座关键词时，才用 EXCLUDE_TITLE_KW 拦截通知/招聘/培训等。
            if not is_lecture(txt):
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
        r.get('speakerAffiliation'), r.get('topic'), r.get('speakerBio'),
        r.get('abstract')
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

    同单位（同学院）系列分期特例：
      同一张系列海报分期发布（如钱捷《人文精神概论》5 讲，首期页预告全部、后续各期
      发同一张海报的当期预告）。这些记录同主讲、同日、同单位，本就是「同一报告的
      系列预告/更新」，应强制合并为一条（不依赖文本相似度，避免通用系列名 vs 具体
      题目相似度不足而漏合并留下重复），且主记录优先选「listTitle 期号与自身场次
      匹配」的那条（即该页正是讲这期），使每讲归属其当期页、标题正确；不标记 merged。
    """
    _CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7,
               '八': 8, '九': 9, '十': 10, '零': 0, '两': 2}

    def _session_num(text):
        if not text:
            return None
        # 第X讲 / 第X期 / 第X场
        m = re.search(r'第\s*([0-9]+|[一二三四五六七八九十零两]+)\s*[讲期场]', text or '')
        if m:
            tok = m.group(1)
            return int(tok) if tok.isdigit() else _CN_NUM.get(tok)
        # 讲座X（不带「第」，如「系列讲座一」） / （第X期）
        m = re.search(r'讲座\s*([0-9]+|[一二三四五六七八九十零两]+)', text or '')
        if m:
            tok = m.group(1)
            return int(tok) if tok.isdigit() else _CN_NUM.get(tok)
        m = re.search(r'（第\s*([0-9]+|[一二三四五六七八九十零两]+)\s*期）', text or '')
        if m:
            tok = m.group(1)
            return int(tok) if tok.isdigit() else _CN_NUM.get(tok)
        return None

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

    # 已被合并进其他记录的 (sourceUrl, lectureIndex) 对（需从最终列表中移除）。
    # 使用 (url, li) 元组而非裸 url：多讲座页面的不同期号共享同一 sourceUrl，
    # 裸 url 会导致同页其他未被合并的期号被连带删除（致命丢数据）。
    merged_urls = set()
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
                # 同单位（同学院）系列分期：同一张系列海报分期发布，同主讲同日、
                # 且题目足够相似（≥0.25）才视为同一报告的系列预告/更新而合并。
                # ⚠️ 不再「无脑强制合并」：同单位同主讲同日可能是两场不同讲座
                # （如 bd/66 地理信息科学展望 vs bd/67 青年教师如何做好科研），
                # 强制合并会误删不同讲座。故同单位也必须过相似度阈值。
                if ri.get('college', '') and ri.get('college') == rj.get('college'):
                    # 同单位系列分期强信号：同主讲、同日、同 lectureIndex（如钱捷
                    # 《人文精神概论》各期被多个 URL 重复发布，标题分别为完整系列名
                    # 与「第N讲丨具体题目」，文本相似度可能低于 0.25，但显然同一期）。
                    # 用 lectureIndex 相同作为强制合并条件，避免误删同单位同主讲同日
                    # 的*不同*讲座（bd/66 与 bd/67 lectureIndex 均 None，不会触发）。
                    li_a = ri.get('lectureIndex')
                    li_b = rj.get('lectureIndex')
                    if li_a is not None and li_a == li_b:
                        union(i, j)
                        continue
                    ti_a = ri.get('topic', '') or ri.get('title', '')
                    ti_b = rj.get('topic', '') or rj.get('title', '')
                    sim_u = max(
                        _topic_similarity(ri.get('title', ''), rj.get('title', '')),
                        _topic_similarity(ti_a, ti_b),
                    )
                    if sim_u >= 0.25:
                        union(i, j)
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

            # 选主记录：字段完整度优先；若 title 已包含 topic，再加分，
            # 避免 Qian Jie 系列等多源合并后主记录 title 与实际讲座不符。
            def _primary_score(r):
                score = _completeness(r)
                topic = r.get('topic', '')
                title = r.get('title', '')
                if topic and topic in title:
                    score += 3
                # abstract 越长越优：合并（含已合并记录的二次合并）时优先保留
                # 摘要更完整的记录，避免长摘要被短摘要覆盖（如 physics/803 长摘要被 iqm/92 短摘要替代）
                score += min(len(r.get('abstract') or '') // 120, 6)
                # 同单位系列分期：优先选 listTitle 期号与自身场次匹配的记录
                # （该页正是讲这期），使每讲归属其当期页、标题不被张冠李戴。
                sn = _session_num(r.get('listTitle', '')) or _session_num(r.get('sessionNumber', ''))
                own = _session_num(r.get('sessionNumber', ''))
                if own is None and r.get('isMultiLecture') and r.get('lectureIndex') is not None:
                    own = r.get('lectureIndex')
                if sn and own and sn == own:
                    score += 5
                return score

            primary_idx = max(members, key=lambda idx: (
                _primary_score(group[idx]),
                len(group[idx].get('abstract') or ''),
                len(group[idx].get('speakerBio') or ''),
            ))
            primary = group[primary_idx]

            primary_college = primary.get('college', '')
            seen_colleges = set()
            sources_list = []
            for idx in members:
                if idx == primary_idx:
                    continue
                other = group[idx]
                oc = other.get('college', '')
                # 同 URL 已在前面排除；这里允许同学院不同 URL 的重复合并。
                # 跨院/同学院从记录：合并进主记录，从最终列表移除
                _li = other.get('lectureIndex')
                merged_urls.add((other.get('sourceUrl', '').rstrip('/'), _li))
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

            # 补全策略：用从记录的非空字段填补主记录的空字段（同/跨院都做）
            for field in ['speakerTitle', 'speakerAffiliation', 'location',
                          'speakerBio', 'organizer', 'abstract']:
                if not primary.get(field):
                    for src_rec in [group[idx] for idx in members if idx != primary_idx]:
                        val = src_rec.get(field)
                        if val:
                            primary[field] = val
                            break

            # 去掉自我合并/重复后已无可合并的跨院来源 → 本簇不作为跨源合并
            if not sources_list:
                merge_count += len(members) - 1
                continue

            # 跨院合并才标记 merged / 记录 sources（真正的多信息源）；
            # 同单位（同学院）的系列分期（同一张系列海报分期发布）属于正常系列更新，
            # 只保留主记录、剔除冗余的同期预告，不标记「多来源提醒」（用户 2026-07-27 明确）。
            cross_college = any(s['college'] != primary_college for s in sources_list)
            if not cross_college:
                merge_count += len(members) - 1
                print(f'[MERGE-SAME-UNIT] {key[0]} @ {key[1]} → {len(members)} 条合并为 1 '
                      f'(同单位 {primary_college}，不标记多来源)')
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
            merge_count += len(members) - 1
            print(f'[MERGE] {key[0]} @ {key[1]} → {len(members)} 条合并为 1 '
                  f'(主记录: {primary["college"]} | 来源: {", ".join(s["college"] for s in sources_list)})')

    if merge_count:
        print(f'[MERGE] 跨源去重完成，共 {merge_count} 条被合并')

    # 过滤掉已被合并的从记录
    result = [r for r in records
              if (r.get('sourceUrl', '').rstrip('/'), r.get('lectureIndex')) not in merged_urls]
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
        # 多讲座拆分：同 sourceUrl 多条（lectureIndex 不同）必须视为不同讲座，
        # 否则会被当成「同 URL 真重复」压成 1 条（如 cs 5708 一页两场）。
        li = rec.get('lectureIndex')
        multi_key = ('#' + str(li)) if (rec.get('isMultiLecture') and li is not None) else ''
        key = (rec.get('college', ''), ntitle, ls, url + multi_key)
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


def _norm_url(u):
    """归一化来源 URL：去尾斜杠，用于增量合并时基底/新增的键比对。"""
    return str(u or '').rstrip('/')


def _cross_source_dup_with_existing(rec, existing_index):
    """判断 rec 是否与基底 existing 中某条跨源重复。

    用于增量模式：命中则新增记录不追加（避免重复），existing 基底保持原样
    （包括其 merged 状态不变）。判定与 cross_source_dedup 一致：
      - 同单位（同学院）同主讲同日不同 URL → 强制视为重复；
      - 跨单位同主讲同日 + topic/title 相似度 ≥ 0.25 → 视为重复。
    """
    spk = _normalize_speaker(rec.get('speaker') or '')
    if not _is_valid_speaker_name(spk):
        return False
    date = (rec.get('lectureStart') or '')[:10]
    if not date or date.startswith('0000'):
        return False
    rivals = existing_index.get((spk, date), [])
    u1 = _norm_url(rec.get('sourceUrl'))
    ti_a = rec.get('topic', '') or rec.get('title', '')
    for r2 in rivals:
        if _norm_url(r2.get('sourceUrl')) == u1:
            continue  # 同 URL 由 seen 处理，不在此判
        if rec.get('college', '') and rec.get('college') == r2.get('college'):
            return True  # 同单位同主讲同日（系列分期重复发布）
        ti_b = r2.get('topic', '') or r2.get('title', '')
        sim = max(
            _topic_similarity(rec.get('topic', ''), r2.get('topic', '')),
            _topic_similarity(rec.get('title', ''), r2.get('title', '')),
            _topic_similarity(ti_a, ti_b),
            _topic_similarity(rec.get('topic', ''), r2.get('title', '')),
            _topic_similarity(rec.get('title', ''), r2.get('topic', '')),
        )
        if sim >= 0.25:
            return True
    return False


# 增量时间门：水位线 = since（最近一次增量抓取时间）。
# 一条新抓到的讲座算「近期/未来」⇔ 发布时间 ≥ 水位线 或 事件时间(lectureStart) ≥ 水位线；
# 两者都早于水位线才丢弃，杜绝历史旧讲座冒充当新事件。无宽限（避免拍脑袋魔法数字）。


def _parse_iso(s):
    """解析 ISO 时间字符串为 datetime；失败返回 None。"""
    if not s:
        return None
    s = str(s).strip()
    try:
        return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def incremental_merge(existing, new_records):
    """增量模式合并：existing 基底原样锁定（不删除/不重组已精修记录），
    仅对新增记录做去重后追加。

    修复（2026-07-31）：此前为保护精修而完全跳过跨源去重，导致每日增量
    会反复追加「与基底已有讲座跨源重复」的新 URL 记录（即不同学院发布同一
    讲座），日积月累产生重复（钱捷系列、刘学兰等即因此冗余）。现改为：
      1) new_records 内部先做同源去重 + 跨源去重，避免本次批量抓取多 URL
         同讲座互相重复；
      2) new_records 与 existing 基底做跨源比对，命中则跳过该新增记录
         （不重复追加），existing 保持原样、其 merged 状态不变。
    这样未来增量不再产生重复，且存量数据由人工/--full 清理统一处理。
    """
    base_map = {}
    for r in existing:
        u = _norm_url(r.get('sourceUrl'))
        if not u:
            continue
        li = r.get('lectureIndex')
        key = u + ('#' + str(li) if li is not None else '')
        base_map[key] = r
    seen = set(base_map.keys())

    # 新增记录：先同源去重，再跨源去重（new 内部合并，避免互相重复）
    new_deduped = cross_source_dedup(dedup(new_records))

    # 基底跨源索引：用于判断 new 是否与已有讲座跨源重复
    existing_index = {}
    for r in existing:
        spk = _normalize_speaker(r.get('speaker') or '')
        if not _is_valid_speaker_name(spk):
            continue
        date = (r.get('lectureStart') or '')[:10]
        if not date or date.startswith('0000'):
            continue
        existing_index.setdefault((spk, date), []).append(r)

    final_new = []
    skip = 0
    for r in new_deduped:
        key = _norm_url(r.get('sourceUrl')) + ('#' + str(r.get('lectureIndex')) if r.get('lectureIndex') is not None else '')
        if key in seen:
            continue
        if _cross_source_dup_with_existing(r, existing_index):
            skip += 1
            continue
        seen.add(key)
        final_new.append(r)
    if skip:
        print(f'[INCREMENTAL] 跳过 {skip} 条与基底跨源重复的新增记录（不重复追加；基底保持原样）')
    return list(base_map.values()) + final_new


def _process_source(src, year, existing_urls, is_incremental, global_exclude=None):
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
                    if is_incremental and (href_norm, None) in existing_urls:
                        continue
                    if href_norm in exclude_urls or (global_exclude and href_norm in global_exclude):
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
        # 2026-08-05 体检修复（严重-3）：把失败上报给调用方。此前异常仅打印后吞掉，
        # main() 无从知晓哪些源失败，水位照常推进 → 失败时段内发布的讲座永久漏抓。
        # 已抓到的部分结果仍返回（不浪费），但本源水位不得推进。
        return local, f'{name}: {e}'
    return local, None


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
        except Exception as e:
            print(f'[ABORT] 读取 data/lectures.json 失败：{e}。为避免覆盖丢失数据，已中止。', file=sys.stderr)
            return
    # 已抓 URL 集合：(sourceUrl, lectureIndex) 元组。
    # 多讲座记录（isMultiLecture=True 且有 lectureIndex）只加入 (url, li) 而不加 (url, None)；
    # 非多讲座记录加入 (url, None)。增量模式下只检查 (url, None) 是否在集合中，
    # 这样多讲座页面不会被跳过，允许解析器改进后重新检测补拆漏期。
    existing_urls = set()
    for r in existing:
        u = str(r.get('sourceUrl', '')).rstrip('/')
        if not u:
            continue
        if r.get('isMultiLecture') and r.get('lectureIndex') is not None:
            existing_urls.add((u, r.get('lectureIndex')))
        else:
            existing_urls.add((u, None))

    lectures = {}
    if is_incremental:
        # 增量：以已有记录为基底，只补充新 URL（不重新解析旧条目）
        for r in existing:
            u = r.get('sourceUrl')
            if u:
                # 多讲座拆分：同 sourceUrl 多条用 (url, lectureIndex) 区分，
                # 避免两条互相覆盖只剩 1 条（与 _process_source 的 key 保持一致）
                li = r.get('lectureIndex')
                key = u + (('#' + str(li)) if li is not None else '')
                lectures[key] = r

    sources = cfg['sources']
    if args.source:
        sources = [s for s in sources if s.get('name') == args.source]
        if not sources:
            print(f'[ERROR] 未找到信息源「{args.source}」', file=sys.stderr)
            return

    # 全局排除名单：被人工确认删除的非讲座/新闻类 URL，cron 增量与全量均跳过，避免污染。
    # 由数据清洗时把「本地已删、cron 曾误加回」的 URL 写入 data/excluded_urls.json 生成。
    global_excluded = set()
    _ge_path = os.path.join(ROOT, 'data', 'excluded_urls.json')
    if os.path.exists(_ge_path):
        try:
            _ge = json.load(open(_ge_path, encoding='utf-8'))
            if isinstance(_ge, list):
                global_excluded = {str(u).rstrip('/') for u in _ge}
            elif isinstance(_ge, dict) and 'urls' in _ge:
                global_excluded = {str(u).rstrip('/') for u in _ge['urls']}
            if global_excluded:
                print(f'[INFO] 已加载全局排除名单 {len(global_excluded)} 条')
        except Exception as e:
            print(f'[WARN] 加载全局排除名单失败：{e}', file=sys.stderr)

    # 并发抓取各信息源：源与源之间独立，大幅缩短 GitHub Actions 全量/增量耗时
    max_workers = 1 if args.source else 5
    all_fetched = []  # 收集所有源抓回的记录（增量模式用于追加，不覆盖基底）
    failed_sources = []  # 体检修复（严重-3）：本次抓取失败的源，水位不得推进
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_src = {executor.submit(_process_source, src, year, existing_urls, is_incremental, global_excluded): src for src in sources}
        for future in as_completed(future_to_src):
            src_name = future_to_src[future].get('name')
            try:
                local, err = future.result()
                if err:
                    failed_sources.append(err)
                for url, rec in local.items():
                    lectures[url] = rec
                    all_fetched.append(rec)
            except Exception as e:
                failed_sources.append(f'{src_name}: {e}')
                print(f'[ERROR] 合并信息源「{src_name}」结果失败：{e}', file=sys.stderr)

    # 同源去重：同一学院标题相似的只保留一条
    raw = list(lectures.values())
    # 局部修复模式（--source，且无 --out）：保留其他学院已有记录，仅替换指定学院的记录
    if args.source and not args.out:
        other_existing = [r for r in existing if r.get('college') != args.source]
        # 按源安全保护：列表页抓取失败/网络超时会使本源新结果骤减甚至为空，
        # 此时不应清掉该源已有数据。新产出 < 旧产出 50% 时保留旧该源数据，避免误删。
        old_src = [r for r in existing if r.get('college') == args.source]
        new_src = [r for r in raw if r.get('college') == args.source]
        if old_src and len(new_src) < len(old_src) * 0.5:
            print(f'[WARN] --source {args.source} 新产出 {len(new_src)} 条 < 旧 {len(old_src)} 条的 50%，'
                  f'疑似列表页抓取失败，保留该源旧数据不覆盖。', file=sys.stderr)
            raw = raw + old_src
        raw = other_existing + raw
        # 安全拦截：--source 模式绝不应让总条数大幅缩水，否则大概率是 existing
        # 加载失败（json.load 异常被静默吞掉 → existing=[]）导致用单源覆盖全量。
        # 一旦产出 < 现有条数 50%，拒绝覆盖，避免误删其他学院数据。
        if existing and len(raw) < len(existing) * 0.5:
            print(f'[ABORT] --source 模式产出 {len(raw)} 条 < 现有 {len(existing)} 条的 50%，'
                  f'疑似现有数据未正确合并，拒绝覆盖 data/lectures.json。', file=sys.stderr)
            return
    if is_incremental and not args.source and not args.out:
        # 增量时间门（2026-08-01 全局修复）：
        # 此前 `since` 只用于设置 is_incremental 布尔，从未过滤讲座，导致增量退化成
        # 「URL 不在 existing 就抓」的全量追加——列表页新翻到的任何历史 URL（含 2014/
        # 2021 等远古旧讲座）都被当新事件灌入主数据，CI 每跑一次数据就变。
        # 修复：增量新增的讲座，其有效时间必须 ≥ since，否则丢弃（不计入增量），
        # 既保留「近期/未来讲座补入」能力，又杜绝历史旧讲座冒充当新事件。
        # 全量模式（--full）不走此分支，仍抓全历史建库。
        cutoff = _parse_iso(since) if since else None
        if cutoff is not None:
            # 统一时区：last_scrape 可能带时区(如 +08:00)，讲座时间均为 naive 本地时间，
            # 比较前均去时区，避免 offset-naive/offset-aware 比较抛 TypeError 致 CI 崩溃。
            cutoff = cutoff.replace(tzinfo=None)
            # 水位线 = since（最近一次增量抓取时间）。OR 规则：一条新抓到的讲座
            # 算「近期/未来」⇔ 发布时间 ≥ 水位线 或 事件时间(lectureStart) ≥ 水位线；
            # 两者都早于水位线才丢弃，杜绝历史旧讲座冒充当新事件。无宽限：
            #   - 发布早于水位线的远古/过期旧讲座 → 在 publishTime 这一维被排除；
            #   - 事件在未来(≥水位线)的真实 upcoming 讲座 → 永不被漏掉；
            #   - 已进主数据的 URL 由 incremental_merge 的 key 锁定，不会重复加入。
            kept, dropped = [], 0
            for r in all_fetched:
                pub = _parse_iso(r.get('publishTime'))
                lec = _parse_iso(r.get('lectureStart'))
                if pub is not None and pub.tzinfo is not None:
                    pub = pub.replace(tzinfo=None)
                if lec is not None and lec.tzinfo is not None:
                    lec = lec.replace(tzinfo=None)
                is_recent = (pub is not None and pub >= cutoff) or (lec is not None and lec >= cutoff)
                if not is_recent:
                    dropped += 1
                    print(f'[SKIP-OLD] 增量跳过历史旧讲座(早于时间门) {r.get("sourceUrl")} | '
                          f'lectureStart={r.get("lectureStart")} | publishTime={r.get("publishTime")}')
                    continue
                kept.append(r)
            all_fetched = kept
            if dropped:
                print(f'[INCREMENTAL] 时间门过滤 {dropped} 条历史旧讲座（不计入增量，避免旧数据当新事件）')
        # 增量模式：基底(existing)原样锁定，仅对新增记录做同源去重后追加，
        # 不再对全量重跑 cross_source_dedup（避免每日增量退化/重组已有精修数据）。
        out = incremental_merge(existing, all_fetched)
    else:
        out = dedup(raw)
        # 跨源去重：不同学院发布的同一讲座合并为一条（同主讲+同日期+topic相似）。
        # --out 模式由各源独立写出、最后由驱动脚本统一合并，故此处跳过跨源去重避免重复。
        if not args.out:
            out = cross_source_dedup(out)
    out.sort(key=lambda x: x.get('lectureStart') or '', reverse=True)
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
    _atomic_write_json(os.path.join(data_dir, 'lectures.json'),
                       {'updatedAt': now_iso, 'data': out})
    # 局部修复模式不更新 last_scrape.json，避免影响下一次全量/定时增量调度
    if not args.source:
        if failed_sources:
            # 体检修复（严重-3）：存在失败源时绝不推进水位。否则水位越过失败时段，
            # 这些源在该时段内发布的讲座在后续增量中永远补不回（「增量陷阱」）。
            # 保留旧水位并记录失败源：下一次运行以同一区间重抓自动补回。
            # 首次运行即失败（无旧水位）时不写 last_scrape 字段 → 下次自动全量重建。
            payload = {'mode': 'incremental' if is_incremental else 'full',
                       'attempted_at': now_iso,
                       'failed_sources': failed_sources}
            if since:
                payload['last_scrape'] = since
            _atomic_write_json(last_scrape_path, payload)
            print(f'[WARN] 本次 {len(failed_sources)} 个信息源失败，水位未推进（下次自动重试）：'
                  + '；'.join(failed_sources), file=sys.stderr)
        else:
            _atomic_write_json(last_scrape_path,
                               {'last_scrape': now_iso, 'mode': 'incremental' if is_incremental else 'full'})
    print(f'[DONE] total {len(out)} lectures -> data/lectures.json  '
          f'(mode={"incremental" if is_incremental else "full"}, source={args.source or "all"}, since={since})')


if __name__ == '__main__':
    main()
