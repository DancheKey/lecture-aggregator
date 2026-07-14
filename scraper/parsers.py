"""详情页字段解析：从华师各学院 CMS 详情页提取讲座标准字段。"""
import re
import io
import requests
from bs4 import BeautifulSoup
from timeparse import parse_cn_time, _year_from_text

def _clean_ocr_text(ocr_text):
    """清理图片 OCR 后常见的海报抬头、Logo、边框乱码等噪声。"""
    t = ocr_text
    # 合并连续空格
    t = re.sub(r'\s+', ' ', t).strip()
    # 常见顶部院校/机构抬头（行知书院、心理学院等海报常见）
    header_words = [
        '华南师范大学', '华南师大', '华师', '行知书院', '心理学院',
        '研究生会', '学生会', '学术讲座', '系列讲座', '讲座预告', 'LECTURE',
    ]
    # 多次贪婪去除：抬头通常在最前面且短
    for _ in range(3):
        changed = False
        for w in header_words:
            # 只去掉位于开头、前面无汉字的短词；避免误删正文
            pat = rf'^(?:[^\u4e00-\u9fa5]{{0,8}}){re.escape(w)}\s*'
            new_t = re.sub(pat, '', t)
            if new_t != t:
                t = new_t
                changed = True
        if not changed:
            break
    # 去除尾部常见边框乱码或装饰字符（如「曷」「号」孤立出现）
    t = re.sub(r'[\s]*[曷号]+$\s*', '', t).strip()
    # 去除孤立单个非中文字符（常见 OCR 噪声）
    t = re.sub(r'\s+[^\u4e00-\u9fa5a-zA-Z0-9]{1,2}\s*$', '', t).strip()
    return t


# 懒加载 easyocr Reader：仅在遇到图片讲座时才初始化
_OCR_READER = None


def _ocr_reader():
    global _OCR_READER
    if _OCR_READER is None:
        import easyocr
        _OCR_READER = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
    return _OCR_READER


def _img_to_text(img_url_or_bytes):
    """对讲座海报图片做 OCR，返回识别到的文本（行列表拼接）。"""
    try:
        if isinstance(img_url_or_bytes, bytes):
            img_bytes = img_url_or_bytes
        else:
            url = img_url_or_bytes
            if url.startswith('//'):
                url = 'https:' + url
            elif url.startswith('/statics.'):
                # 站点把 statics.scnu.edu.cn 以根路径形式引用，实际缺协议
                url = 'https:' + url
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
            img_bytes = r.content
        reader = _ocr_reader()
        lines = reader.readtext(img_bytes, detail=0)
        return ' '.join(lines)
    except Exception:
        return ''

LECTURE_KW = ['学术讲座', '讲座', '学术报告', '学术沙龙', '讲坛', '报告会', '前沿讲座']
EXCLUDE_KW = ['回顾', '总结', '新闻', '喜报', '招聘', '招生', '答辩', '公示', '报名', '获奖']


def is_lecture(title):
    if not any(k in title for k in LECTURE_KW):
        return False
    if any(k in title for k in EXCLUDE_KW):
        return False
    return True


def _extract_narrative(body_text, title):
    """无结构化标签的叙事体文章兜底提取：主题、地点、主讲人、摘要。"""
    result = {}
    if body_text:
        body_text = re.sub(r'\s+', ' ', body_text).strip()
    # 主题：优先从标题《...》书名号提取
    if title:
        m = re.search(r'《([^《》]{3,60})》', title)
        if m:
            result['topic'] = m.group(1).strip()
    if not body_text:
        return result

    # 地点：常见“在/于...楼/室/厅/校区...举行/举办/召开”，允许楼后带房间号
    loc_patterns = [
        r'在\s*([^。，；]{2,55}?(?:楼|室|厅|馆|校区|校园|中心|广场|会议室|教室|礼堂|报告厅|学术厅|综合楼|行政楼|教学楼|信息楼|院楼|大楼)(?:\s*[0-9]+)?)\s*(?:成功)?(?:举行|举办|召开|进行|开展)',
        r'于\s*([^。，；]{2,55}?(?:楼|室|厅|馆|校区|校园|中心|广场|会议室|教室|礼堂|报告厅|学术厅|综合楼|行政楼|教学楼|信息楼|院楼|大楼)(?:\s*[0-9]+)?)\s*(?:成功)?(?:举行|举办|召开|进行|开展)',
    ]
    for pat in loc_patterns:
        m = re.search(pat, body_text)
        if m:
            loc = m.group(1).strip()
            loc = re.sub(r'^[在于]\s*', '', loc)
            result['location'] = loc
            break

    # 主讲人：从包含“主讲/主持/带来”的子句中提取
    keywords = ['主讲', '主持', '带来']
    titles = ['教授', '副教授', '讲师', '博士', '老师', '副院长', '院长']
    prefixes = [
        '由学院新引进的', '由学院', '讲座由', '由',
        '院长兼', '副院长兼', '人工智能学院', '计算机学院', '学院',
        '新引进的', '青年拔尖人才', '拔尖人才',
    ]
    for kw in keywords:
        if kw not in body_text:
            continue
        left = body_text.split(kw, 1)[0].strip()
        left = re.split(r'[。，；]', left)[-1].strip()
        changed = True
        while changed:
            changed = False
            for p in prefixes:
                if left.startswith(p):
                    left = left[len(p):].strip()
                    changed = True
        for t in titles:
            if left.startswith(t):
                left = left[len(t):].strip()
        # name + optional title
        m = re.search(r'^([\u4e00-\u9fa5]{2,4})(?:\s*(?:' + '|'.join(titles) + r'))?', left)
        if m:
            name = m.group(1).strip()
            for t in titles:
                if name.endswith(t):
                    name = name[:-len(t)].strip()
            if name and len(name) >= 2:
                result['speaker'] = name
                break
        # just name
        if re.match(r'^[\u4e00-\u9fa5]{2,4}$', left):
            result['speaker'] = left
            break

    # 主题：若标题未提供，再尝试正文中“主讲《...》”
    if not result.get('topic'):
        cm = re.search(r'主讲\s*[《<]([^》>]{3,60})[》>]', body_text)
        if cm:
            result['topic'] = cm.group(1).strip()

    # 摘要：取正文第一句之后的 1-3 句，过滤图片说明
    sentences = [p.strip() for p in re.split(r'[。\n]+', body_text) if len(p.strip()) > 20]
    if sentences:
        start_idx = 0
        # 首句若只含时间/地点/主讲元信息，则跳过
        if (re.search(r'(?:月|日|晚|召开|举行|举办|主讲|由)', sentences[0]) and
                len(sentences) > 1):
            start_idx = 1
        abstract = '。'.join(sentences[start_idx:start_idx + 3]) + '。'
        abstract = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', abstract).strip()
        abstract = re.sub(r'图\s*\d+\s*[：:].*?(?=(?:图\s*\d+|$))', '', abstract).strip()
        abstract = re.sub(r'\s*图\s*\d+\s*.*$', '', abstract).strip()
        if len(abstract) > 15:
            result['abstract'] = abstract
    return result


def _clean_title(t):
    t = t.strip()
    if ' - ' in t:
        t = t.split(' - ')[0].strip()
    if '｜' in t:
        t = t.split('｜')[0].strip()
    return t


def _normalize_label_text(text):
    """去除常见字段标签中因 CMS 拆分 span 而混入的空格，如「题 目」「地 点」「主 讲 人」。"""
    labels = [
        # 主题/题目
        '题目', '主题', '讲座主题', '报告题目', '演讲题目', '报告主题',
        # 时间地点人物
        '地点', '时间', '主讲人', '主讲师', '报告人', '主讲嘉宾', '演讲人',
        # 简介/摘要/内容
        '主讲人简介', '主讲人简历', '简历', '简介',
        '摘要', '讲座内容', '讲座内容提要', '内容提要', '讲座摘要',
        '报告摘要', '内容摘要', '内容简介', '讲座简介', '报告内容', '讲座概要', '内容概要',
        # 发布信息
        '发布时间', '发布日期', '来源',
    ]
    # 先匹配长的复合标签，避免「讲座内容」把「讲座内容提要」先吃掉
    for label in sorted(labels, key=len, reverse=True):
        spaced = ''.join(c + r'\s*' for c in label)
        text = re.sub(spaced, label, text)
    return text


def _year_from_url(url):
    """从内容页 URL 路径提取年份。

    兼容：/a/20251201/xxx.html -> 2025
         /Resources_CN/2023/1207/59.html -> 2023
    """
    if not url:
        return None
    # 先匹配 /a/YYYYMMDD/ 这类紧凑日期路径
    m = re.search(r'/a/(20\d{2})(\d{2})(\d{2})/', url)
    if m:
        return int(m.group(1))
    # 再匹配 /YYYY/MM/ 或 /YYYY/MM/DD/ 路径
    m = re.search(r'/(20\d{2})/(?:\d{2})(?:/\d{2})?/', url)
    if m:
        return int(m.group(1))
    return None


def parse_detail(html, url, college, campus, default_year=None, list_title=None):
    soup = BeautifulSoup(html, 'html.parser')
    # 列表页标题通常就是干净的讲座标题，优先使用；否则回退到详情页 h1/title
    if list_title:
        title = _clean_title(list_title)
    else:
        h1 = soup.find('h1') or soup.find('h2')
        if h1:
            title = h1.get_text(strip=True)
        elif soup.title:
            title = soup.title.get_text(strip=True)
        else:
            title = ''
        title = _clean_title(title)

    text = soup.get_text(' ')
    text = re.sub(r'\s+', ' ', text).strip()
    text = _normalize_label_text(text)

    # 提前定位正文容器；若正文几乎为空但含图片（如行知书院讲座海报），对图片 OCR 提取文字
    content_div = (soup.find('div', class_='article-content')
                   or soup.find('div', class_='content')
                   or soup.find('div', class_='news-details-all')
                   or soup.find('div', class_='news-details-middle')
                   or soup.find('article')
                   or soup.find('div', class_='entry-content'))
    body_text = content_div.get_text(' ') if content_div else text
    body_text = re.sub(r'\s+', ' ', body_text).strip()
    body_text = _normalize_label_text(body_text)
    ocr_text = ''
    if content_div and len(body_text) < 50:
        base_url = url
        imgs = []
        for img in content_div.find_all('img'):
            src = img.get('src')
            if not src:
                continue
            if src.startswith('http'):
                pass
            elif src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                src = base_url.split('/')[0] + src
            else:
                src = base_url.rsplit('/', 1)[0] + '/' + src
            imgs.append(src)
        if imgs:
            ocr_text = ' '.join(_img_to_text(img) for img in imgs[:3])
            if ocr_text:
                # 清理 OCR 中常见的顶部/底部噪声
                ocr_text = _clean_ocr_text(ocr_text)
                body_text = (body_text + ' ' + ocr_text).strip()
                text = (text + ' ' + ocr_text).strip()

    # 发布时间：优先在正文区域内匹配，避免整页导航/版权把日期带偏。
    # 格式 1：发布：YYYY-MM-DD HH:MM[:SS]
    # 格式 2：YYYY-MM-DD HH:MM[:SS]（常见于 CMS 顶部，常接「来源：」）
    search_text = content_div.get_text(' ') if content_div else text
    search_text = re.sub(r'\s+', ' ', search_text).strip()
    publish_time = None
    # 支持：发布 / 发布时间 / 发布日期 / 发表时间 / 发布于 + YYYY-MM-DD [HH:MM[:SS]]
    PUB = r'(?:发布(?:时间|日期)?|发表时间|发布于)\s*[：:]?\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)'
    m = re.search(PUB, search_text)
    if not m:
        # CMS 顶部常见「2023-11-08 10:00 来源：」式时间戳
        m = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)', search_text)
    if not m:
        # 兜底：整页前部（正文为图片时 content_div 为空，发布时间只在整页 soup text 中）
        m = re.search(PUB, text)
    if not m:
        # 再兜底：CMS 顶部时间戳在整页中
        m = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)', text)
    publish_time = m.group(1).strip() if m else None

    # 从标题/URL 提取显式年份（标题兼容紧凑格式 20251204）
    title_year = _year_from_text(title) if title else None
    url_year = _year_from_url(url)

    result = {
        'sourceUrl': url,
        'college': college,
        'campus': campus,
        'title': title,
        'topic': '',
        'lectureStart': None,
        'lectureEnd': None,
        'location': '',
        'speaker': '',
        'speakerAffiliation': '',
        'speakerBio': '',
        'organizer': college,
        'publishTime': publish_time,
    }

    t = parse_cn_time(text, default_year, publish_time=publish_time, title_year=title_year, url_year=url_year)
    # 若解析结果与发布时间同一天，很可能解析到的是发布日期而非讲座日期；
    # 优先使用列表标题（通常包含真实讲座日期），其次尝试 OCR 中的明确「时间」标签。
    if t and publish_time and t['start'].strftime('%Y-%m-%d') == publish_time[:10]:
        if list_title:
            t_lt = parse_cn_time(list_title, default_year, publish_time=publish_time, title_year=title_year, url_year=url_year)
            if t_lt and t_lt['start'].strftime('%Y-%m-%d') != publish_time[:10]:
                t = t_lt
        # 图片 OCR 场景：正文存在「时间」标签时，优先用标签后片段
        if ocr_text:
            tm = re.search(r'(?:时间|时闻)[：:\s]*(.{0,70})', text)
            if tm:
                t2 = parse_cn_time(tm.group(1).strip(), default_year, publish_time=publish_time, title_year=title_year, url_year=url_year)
                if t2 and t2['start'].strftime('%Y-%m-%d') != publish_time[:10]:
                    t = t2
    if not t:
        # 兜底：部分站点（如心理学院）讲座日期只在列表标题里，正文仅有发布日期
        if list_title:
            t = parse_cn_time(list_title, default_year, publish_time=publish_time, title_year=title_year, url_year=url_year)
    if t:
        result['lectureStart'] = t['start'].isoformat(sep=' ')
        result['lectureEnd'] = t['end'].isoformat(sep=' ') if t['end'] else None

    # 字段标签前瞻——每个字段只取到下一个标签为止
    # 把「主题」「讲座内容提要」「摘要」等也纳入 STOP，避免 topic/地点/主讲人 把后续字段一起吃进去
    LABELS = (
        '地点|题目|主题|讲座主题|报告题目|演讲题目|报告主题|'
        '时间|主讲[人师]|报告人|主讲嘉宾|演讲人|'
        '摘要|讲座内容提要|内容提要|讲座内容摘要|内容摘要|内容简介|'
        '讲座内容|讲座简介|报告内容|讲座概要|内容概要|'
        '简历|主讲人简介|主讲人简历|简介|发布|来源'
    )
    STOP = rf'(?=\s*(?:{LABELS}|$))'

    # --- 题目/主题（兼容「题目/主题/讲座主题/报告题目/演讲题目/报告主题」）---
    topic_pat = rf'(?:题目|主题|讲座主题|报告题目|演讲题目|报告主题)[：:]\s*(.+?){STOP}'
    m = re.search(topic_pat, text)
    if m:
        t = m.group(1).strip()
        # 清除尾部粘连的「摘要」「主讲人」「预告」等非正文词
        t = re.sub(r'\s*(?:摘要|主讲人|报告人|预告)\s*[:：]?.*$', '', t).strip()
        result['topic'] = t

    # --- 地点 ---
    m = re.search(rf'地点[：:]\s*(.+?){STOP}', text)
    if m:
        loc = m.group(1).strip()
        # 地点值通常很短；如果超过 50 字符说明吃到了后续内容，截断到第一个句号/逗号
        if len(loc) > 60:
            loc = re.split(r'[。，;；\n]', loc)[0].strip()
        # 去除 OCR 尾部常见乱码或装饰字符（如「曷」「号」）
        loc = re.sub(r'[\s]*[曷号]+$\s*', '', loc).strip()
        loc = re.sub(r'\s+[^\u4e00-\u9fa5a-zA-Z0-9]{1,2}\s*$', '', loc).strip()
        result['location'] = loc

    # --- 主讲人（兼容「主讲人/主讲师/报告人/主讲嘉宾/演讲人」）---
    speaker_pat = rf'(?:主讲[人师]|报告人|主讲嘉宾|演讲人)[：:]\s*(.+?){STOP}'
    m = re.search(speaker_pat, text)
    if m:
        sp = m.group(1).strip()
        # 去掉尾部职称后缀
        sp_clean = re.sub(r'\s*(?:教授|研究员|副教授|讲师|博士|老师|先生|女士).*$', '', sp).strip()
        # 尝试拆分姓名+单位（括号形式）
        mm = re.match(r'(.+?)\s*[（(]([^）)]{2,40})[）)]', sp)
        if mm:
            result['speaker'] = sp_clean.split('（')[0].strip()
            result['speakerAffiliation'] = re.sub(r'\s+', '', mm.group(2))
        else:
            result['speaker'] = sp_clean

    # 兜底：从标题括号中提取主讲人，如「（朱英教授）」
    if not result['speaker']:
        tm = re.search(r'（([^（）]*?(教授|研究员|副教授|讲师|博士)[^（）]*?)）', title)
        if tm:
            result['speaker'] = re.sub(r'\s*(教授|研究员|副教授|讲师|博士|老师).*$', '', tm.group(1)).strip()

    # --- 简历/简介（优先在文章正文区域内搜索）---
    # body_text 已在函数开头构建（含可能的 OCR 文本）

    # 内容摘要类标签：出现这些说明主讲人简介已结束、讲座内容介绍开始
    SUMMARY_LABELS = (
        '讲座内容提要|内容提要|讲座内容摘要|内容摘要|内容简介|'
        '讲座内容|讲座简介|报告内容|讲座概要|内容概要|摘要'
    )

    # 页面噪声/侧边栏标记：遇到这些说明正文已结束，应截断
    NOISE_MARKERS = (
        '资讯及通知|相关新闻|最新动态|推荐阅读|相关文章|相关讲座|'
        '上一篇|下一篇|附件下载|相关链接|网友评论|分享|标签|相关推荐|'
        '通知公告|最新公告|站内搜索|快速导航'
    )

    bio_pat = rf'(?:主讲人简介|主讲人简历|简历|(?<!内容)简介)[\s:：]*'
    m = re.search(rf'{bio_pat}(.+?)(?=\s*(?:{SUMMARY_LABELS}|{NOISE_MARKERS}|$))', body_text)
    if m:
        bio = m.group(1).strip()
        # 清理版权声明等尾部噪声
        bio = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', bio).strip()
        # 清理图片路径等残留
        bio = re.sub(r'\s*(//[\w./-]+\.(jpg|jpeg|png|gif))\s*', '', bio).strip()
        # 截断页面噪声
        bio = re.split(rf'(?:{NOISE_MARKERS})', bio)[0].strip()
        if len(bio) > 10:
            result['speakerBio'] = bio

    # 无标签简介兜底：正文某段落以主讲人姓名开头，通常即个人简介
    if not result['speakerBio'] and result['speaker'] and content_div:
        speaker = result['speaker'].strip()
        for p in content_div.find_all('p'):
            p_text = re.sub(r'\s+', ' ', p.get_text(' ', strip=True)).strip()
            if len(p_text) < 30:
                continue
            # 去掉段落开头可能的职称/称谓，再判断是否以主讲人姓名开头
            start = re.sub(r'^\s*(Professor|Dr\.|Mr\.|Ms\.|教授|副教授|讲师|研究员|博士)\s*', '', p_text)
            if start.startswith(speaker):
                p_text = re.sub(r'\s*(//[\w./-]+\.(jpg|jpeg|png|gif))\s*', '', p_text).strip()
                p_text = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', p_text).strip()
                p_text = re.split(rf'(?:{NOISE_MARKERS})', p_text)[0].strip()
                if len(p_text) > 10:
                    result['speakerBio'] = p_text
                    break

    # --- 摘要/内容（优先从正文区域提取完整版）---
    # 把「讲座内容提要/讲座内容/报告摘要」等内容摘要类字段统一作为 abstract
    abs_pat = rf'(?:{SUMMARY_LABELS})[：:]\s*'
    m = re.search(rf'{abs_pat}(.+)', body_text)
    if m:
        abstract = m.group(1).strip()
        # 清理版权噪声和图片
        abstract = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', abstract).strip()
        abstract = re.sub(r'\s*(//[\w./-]+\.(jpg|jpeg|png|gif))\s*', '', abstract).strip()
        # 截断页面噪声/侧边栏
        abstract = re.split(rf'(?:{NOISE_MARKERS})', abstract)[0].strip()
        if len(abstract) > 5:
            result['abstract'] = abstract

    # 兜底：若正文来自图片 OCR 且没有明确「摘要」标签，把 OCR 文本清理后作为摘要
    if ocr_text and not result.get('abstract'):
        clean = _clean_ocr_text(ocr_text)
        clean = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', clean).strip()
        clean = re.sub(r'\s*(//[\w./-]+\.(jpg|jpeg|png|gif))\s*', '', clean).strip()
        clean = re.split(rf'(?:{NOISE_MARKERS})', clean)[0].strip()
        # 去掉海报顶部常见噪声（学院/学生会/系列讲座等重复字样）
        clean = re.sub(r'.*?(系列讲座|学术讲座|讲座预告)', '', clean, count=1).strip()
        # 去掉尾部「时间：... 地点：...」等结构化信息，避免与独立字段重复；
        # OCR 可能把「时间」误识为「时闻」，一并处理；同时截断尾部的日期/地点短语。
        clean = re.sub(r'\s*(时间|时闻)\s*[:：].*$', '', clean).strip()
        clean = re.sub(r'\s*地点\s*[:：].*$', '', clean).strip()
        clean = re.sub(r'\s*20\d{2}年\d{1,2}月\d{1,2}日.*$', '', clean).strip()
        # 再次清理尾部乱码
        clean = re.sub(r'[\s]*[曷号]+$\s*', '', clean).strip()
        clean = re.sub(r'\s+[^\u4e00-\u9fa5a-zA-Z0-9]{1,2}\s*$', '', clean).strip()
        if len(clean) > 10:
            result['abstract'] = clean

    # 图片 OCR 场景：标题通常就是海报主标题，若未提取到 topic，用标题去掉日期前缀作为主题
    if ocr_text and not result.get('topic') and title:
        topic_candidate = re.sub(r'^(20\d{6}\s+|20\d{2}[-/]\d{2}[-/]\d{2}\s+|\d{1,2}月\d{1,2}日\s*)', '', title).strip()
        # 去掉末尾的"学术讲座"/"讲座"等通用词，保留具体主题
        topic_candidate = re.sub(r'(?:教授|老师|先生|女士)\s*(学术讲座|讲座|报告|讲坛)$', '', topic_candidate).strip()
        if topic_candidate and topic_candidate != title and len(topic_candidate) > 3:
            result['topic'] = topic_candidate

    # 图片 OCR 场景下，「简介」二字常被标题误触发，导致 speakerBio 变成整段海报文字。
    # 若 speakerBio 来自 OCR 且包含时间/地点等结构化信息，说明不是真正的主讲人简介，清空。
    if ocr_text and result.get('speakerBio'):
        if result['speakerBio'] in ocr_text or ocr_text in result['speakerBio']:
            if any(k in result['speakerBio'] for k in ['时间', '地点', '时闻', '日期']):
                result['speakerBio'] = ''

    # 兜底：无结构化标签的叙事体文章（如人工智能学院）
    if not result['topic'] or not result['location'] or not result['speaker'] or not result.get('abstract'):
        narrative = _extract_narrative(body_text, title)
        if not result['topic'] and narrative.get('topic'):
            result['topic'] = narrative['topic']
        if not result['location'] and narrative.get('location'):
            result['location'] = narrative['location']
        if not result['speaker'] and narrative.get('speaker'):
            result['speaker'] = narrative['speaker']
        if not result.get('abstract') and narrative.get('abstract'):
            result['abstract'] = narrative['abstract']

    return result
