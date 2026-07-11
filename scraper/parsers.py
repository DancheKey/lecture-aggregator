"""详情页字段解析：从华师各学院 CMS 详情页提取讲座标准字段。"""
import re
import io
import requests
from bs4 import BeautifulSoup
from timeparse import parse_cn_time, _year_from_text

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


def _clean_title(t):
    t = t.strip()
    if ' - ' in t:
        t = t.split(' - ')[0].strip()
    if '｜' in t:
        t = t.split('｜')[0].strip()
    return t


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

    # 提前定位正文容器；若正文几乎为空但含图片（如行知书院讲座海报），对图片 OCR 提取文字
    content_div = (soup.find('div', class_='article-content')
                   or soup.find('div', class_='content')
                   or soup.find('article')
                   or soup.find('div', class_='entry-content'))
    body_text = content_div.get_text(' ') if content_div else text
    body_text = re.sub(r'\s+', ' ', body_text).strip()
    ocr_text = ''
    if content_div and len(body_text) < 50:
        imgs = [img.get('src') for img in content_div.find_all('img') if img.get('src')]
        if imgs:
            ocr_text = ' '.join(_img_to_text(img) for img in imgs[:3])
            if ocr_text:
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
        # 兜底：整页前部
        m = re.search(PUB, text)
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
    # 兜底：部分站点（如心理学院）讲座日期只在列表标题里，正文仅有发布日期
    if not t and list_title:
        t = parse_cn_time(list_title, default_year, publish_time=publish_time, title_year=title_year, url_year=url_year)
    if t:
        result['lectureStart'] = t['start'].isoformat(sep=' ')
        result['lectureEnd'] = t['end'].isoformat(sep=' ') if t['end'] else None

    # 字段标签前瞻——每个字段只取到下一个标签为止
    LABELS = '地点|题目|时间|主讲[人师]|报告人|主讲嘉宾|演讲人|摘要|简历|简介|发布|来源'
    STOP = rf'(?=\s*(?:{LABELS}|$))'

    # --- 题目 ---
    m = re.search(rf'题目[：:]\s*(.+?){STOP}', text)
    if m:
        t = m.group(1).strip()
        # 清除尾部粘连的「摘要」（如「题目：xxx摘要：」无空格的情况）
        t = re.sub(r'\s*摘要\s*[:：]?.*$', '', t).strip()
        # 清除尾部粘连的「报告会」「预告」等非正文词
        t = re.sub(r'(?:摘要|预告)\s*[:：].*$', '', t).strip()
        result['topic'] = t

    # --- 地点 ---
    m = re.search(rf'地点[：:]\s*(.+?){STOP}', text)
    if m:
        loc = m.group(1).strip()
        # 地点值通常很短；如果超过 50 字符说明吃到了后续内容，截断到第一个句号/逗号
        if len(loc) > 60:
            loc = re.split(r'[。，;；\n]', loc)[0].strip()
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

    bio_pat = rf'(?:简历|主讲人简介|简介)[\s:：]*'
    m = re.search(rf'{bio_pat}(.+)', body_text)
    if m:
        bio = m.group(1).strip()
        # 清理版权声明等尾部噪声
        bio = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', bio).strip()
        # 清理图片路径等残留
        bio = re.sub(r'\s*(//[\w./-]+\.(jpg|jpeg|png|gif))\s*', '', bio).strip()
        if len(bio) > 10:
            result['speakerBio'] = bio

    # --- 摘要/内容（优先从正文区域提取完整版）---
    abs_pat = rf'摘要[：:]\s*'
    m = re.search(rf'{abs_pat}(.+)', body_text)
    if m:
        abstract = m.group(1).strip()
        # 清理版权噪声和图片
        abstract = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', abstract).strip()
        abstract = re.sub(r'\s*(//[\w./-]+\.(jpg|jpeg|png|gif))\s*', '', abstract).strip()
        if len(abstract) > 5:
            result['abstract'] = abstract

    # 兜底：若正文来自图片 OCR 且没有明确「摘要」标签，把 OCR 文本清理后作为摘要
    if ocr_text and not result.get('abstract'):
        clean = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', ocr_text).strip()
        clean = re.sub(r'\s*(//[\w./-]+\.(jpg|jpeg|png|gif))\s*', '', clean).strip()
        # 去掉海报顶部常见噪声（学院/学生会/系列讲座等重复字样）
        clean = re.sub(r'.*?(系列讲座|学术讲座|讲座预告)', '', clean, count=1).strip()
        # 去掉尾部「时间：... 地点：...」等结构化信息，避免与独立字段重复；
        # OCR 可能把「时间」误识为「时闻」，一并处理；同时截断尾部的日期/地点短语。
        clean = re.sub(r'\s*(时间|时闻)\s*[:：].*$', '', clean).strip()
        clean = re.sub(r'\s*地点\s*[:：].*$', '', clean).strip()
        clean = re.sub(r'\s*20\d{2}年\d{1,2}月\d{1,2}日.*$', '', clean).strip()
        if len(clean) > 10:
            result['abstract'] = clean

    # 图片 OCR 场景下，「简介」二字常被标题误触发，导致 speakerBio 变成整段海报文字。
    # 若 speakerBio 来自 OCR 且包含时间/地点等结构化信息，说明不是真正的主讲人简介，清空。
    if ocr_text and result.get('speakerBio'):
        if result['speakerBio'] in ocr_text or ocr_text in result['speakerBio']:
            if any(k in result['speakerBio'] for k in ['时间', '地点', '时闻', '日期']):
                result['speakerBio'] = ''

    return result
