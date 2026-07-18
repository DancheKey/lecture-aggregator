"""详情页字段解析：从华师各学院 CMS 详情页提取讲座标准字段。"""
import re
import io
import datetime
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from timeparse import parse_cn_time, _year_from_text, resolve_lecture_time

def _clean_ocr_text(ocr_text):
    """清理图片 OCR 后常见的海报抬头、Logo、边框乱码等噪声。"""
    t = ocr_text
    # 合并连续空格
    t = re.sub(r'\s+', ' ', t).strip()
    # 常见顶部院校/机构抬头（行知书院、心理学院等海报常见）
    header_words = [
        '华南师范大学', '华南师大', '华师', '行知书院', '心理学院',
        '研究生会', '学生会', '学术讲座', '系列讲座', '讲座预告', 'LECTURE',
        '生命科学大讲堂', '木棉生命科学前沿论坛', '生命科学前沿论坛',
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
    # 去除开头孤立的数字年份（如海报左上角装饰「1933」「2026」）
    t = re.sub(r'^\d{3,4}\s+', '', t).strip()
    # 去除开头连续的中英文院校/论坛 Logo 噪声（允许 OCR 错字）
    # 模式：若干非汉字字符 + 大学/学院/大讲堂/论坛等词，重复出现直到真正内容
    for _ in range(5):
        new_t = re.sub(r'^[^\u4e00-\u9fa5]{0,50}(?:大学|学院|大讲堂|前沿论坛|论坛|UNIVERSITY|COLLEGE|NORMAL|CHINA|SOUTH|NORTH|EAST|WEST)\s*', '', t)
        if new_t == t:
            break
        t = new_t
    # 去除尾部常见边框乱码或装饰字符（如「曷」「号」孤立出现）
    t = re.sub(r'[\s]*[曷号]+$\s*', '', t).strip()
    # 去除孤立单个非中文字符（常见 OCR 噪声）
    t = re.sub(r'\s+[^\u4e00-\u9fa5a-zA-Z0-9]{1,2}\s*$', '', t).strip()
    return t


# 懒加载 RapidOCR 引擎：仅在遇到图片讲座时才初始化（ONNXRuntime 后端，
# 替代原 easyocr——中文海报准确率更高、无 torch/paddle 重型依赖、启动更快）
_OCR_ENGINE = None


def _ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR(use_cls=False, print_verbose=False)
    return _OCR_ENGINE


def _img_to_text(img_url_or_bytes):
    """对讲座海报图片做 OCR，返回识别到的文本（行以空格拼接，与原 easyocr 输出一致）。"""
    import tempfile, os
    target = None
    try:
        if isinstance(img_url_or_bytes, bytes):
            fd, target = tempfile.mkstemp(suffix='.jpg')
            with os.fdopen(fd, 'wb') as f:
                f.write(img_url_or_bytes)
        else:
            url = img_url_or_bytes
            if url.startswith('//'):
                url = 'https:' + url
            elif url.startswith('/statics.'):
                # 站点把 statics.scnu.edu.cn 以根路径形式引用，实际缺协议
                url = 'https:' + url
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
            fd, target = tempfile.mkstemp(suffix='.jpg')
            with os.fdopen(fd, 'wb') as f:
                f.write(r.content)
        res, _ = _ocr_engine()(target)
        if not res:
            return ''
        return ' '.join([l[1] for l in res])
    except Exception:
        return ''
    finally:
        if target and os.path.exists(target):
            try:
                os.remove(target)
            except Exception:
                pass

LECTURE_KW = ['学术讲座', '讲座', '学术报告', '学术沙龙', '讲坛', '报告会', '前沿讲座']
EXCLUDE_KW = ['回顾', '总结', '新闻', '喜报', '招聘', '招生', '答辩', '公示', '报名', '获奖', '工作坊', '申请表', '改期']


def is_lecture(title):
    if not any(k in title for k in LECTURE_KW):
        return False
    if any(k in title for k in EXCLUDE_KW):
        return False
    return True


def _date_head(s):
    """从日期字符串中提取 YYYY-MM-DD 并转为 datetime.date，失败返回 None。"""
    if not s:
        return None
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def is_news_record(rec):
    """判断是否为新闻/回顾而非讲座预告。

    主规则：发布时间晚于讲座时间（即讲座结束后才发布），视为对已结束讲座的
    报道或回顾，不纳入聚合。
    辅助规则：标题含明显新闻/回顾类关键词（已在 EXCLUDE_KW 中，由 is_lecture 拦截）。
    """
    if not rec:
        return False
    ls = _date_head(rec.get('lectureStart') or '')
    pub = _date_head(rec.get('publishTime') or '')
    if ls and pub and pub > ls:
        return True
    return False


# ---- 新闻/活动回顾稿识别（与 is_news_record 互补）----
# is_news_record 依赖「发布时间 > 讲座时间」，但 IBC 等站点的回顾稿往往没有
# 显式「发布」时间戳（publishTime 为空），无法触发。这里用语义特征识别：
# 机构作主语的「参与/举办」回顾、新闻署名审签块、回顾式总结短语等。
#
# 关键：必须区分「回顾式（活动已结束）」与「预告式（将举办）」。华师讲座预告页
# 也常用「在本次报告中，我们将介绍…」「本次讲座将围绕…展开」这类前向句式，绝不能
# 仅因出现「本次报告/本次讲座」就判为新闻。故回顾式规则只认总结性动词（不仅/取得/
# 为师生/圆满/特邀/史料…），并显式排除「将/拟/介绍/围绕…展开」等前向词。
_NEWS_RETRO_STRONG = r'(本次活动的成功举办|讲座圆满结束|活动圆满结束|圆满落幕|活动取得圆满成功|圆满成功举办|讲座在我院成功举办|报告会圆满|论坛圆满|讲座取得圆满)'
# 回顾式短语：本次/此次讲座|报告 + 总结性动词；显式排除「将/拟/计划/旨在」等前向词
# （华师讲座预告常用「本次报告将介绍…取得」「本次报告中，我们将提供新见解」，不是新闻）
_NEWS_RETRO = r'(本次讲座|此次讲座|本次报告|此次报告)(?!.{0,30}?(将|拟|计划|旨在|期待|希望))(?=.{0,30}?(不仅|为师生|让师生|受到|得到|圆满|顺利|特邀|史料|内容翔实|气氛热烈|拓宽|开拓|反响|一致好评|纷纷表示|收获|深入交流|提供(了)?新))'
# 标题即回顾式：机构「举办/开展/举行…讲座/报告」，且整体不含「通知/预告/公示」等预告词
_NEWS_TITLE_CONDUCT = r'^(?!.*(通知|预告|公示|启事))(?=.*(举办|开展|举行))(?=.*(学术讲座|专题讲座|讲座|报告会|学术报告)).+'
# 新闻署名审签链：供稿+初审+终审 / 初审+复审+终审（华师新闻稿专属页脚，区别于演讲者简介里的「总撰稿」）
_NEWS_SIGNATURE_CHAIN = r'((供稿|撰稿)[:：].{0,40})?(初审[:：].{0,30})?(复审[:：].{0,30})?终审[:：]'
# 叙事导语（YYYY年M月D日）+ 完成态动词
_NEWS_NARRATIVE = r'20\d{2}年\d{1,2}月\d{1,2}日'
_NEWS_DONE = r'(顺利举办|成功举办|顺利开展|圆满完成|圆满结束|圆满落幕|顺利召开|成功召开)'
_NEWS_TITLE_PARTICIPATE = r'^(国际商学院|华南师范大学|我院|学院|学校|研究生院|党支部|党委|师生|团队).{0,14}?(参加|赴.*参加|组织.*参加|师生参加|团队参加)'


def is_news_article(title, body):
    """判断详情页是否为新闻/活动回顾稿而非讲座预告。

    返回命中的规则名（'retro-summary'/'title-conduct'/'signature-block'/
    'narrative-completion'/'title-participate'）或 None。命中即视为非讲座，
    应在解析阶段剔除。

    仅采用高精规则，且严格区分「回顾式」与「预告式」：华师讲座预告页也常用
    「在本次报告中，我们将介绍…」这类前向句式，不能仅因出现「本次报告/本次讲座」
    就判为新闻。
    """
    t = title or ''
    b = body or ''
    # 1) 回顾式强总结语（活动已结束的报道）
    if re.search(_NEWS_RETRO_STRONG, b):
        return 'retro-summary'
    # 2) 回顾式短语（本次/此次讲座|报告 + 总结性动词，已排除「将/拟/介绍」等前向词）
    if re.search(_NEWS_RETRO, b):
        return 'retro-summary'
    # 3) 标题即回顾式：机构举办/开展…讲座（无「将/拟/计划/通知」预告词）
    if re.search(_NEWS_TITLE_CONDUCT, t):
        return 'title-conduct'
    # 4) 新闻署名审签链（供稿+初审+终审 / 初审+复审+终审），华师新闻稿专属页脚
    if re.search(_NEWS_SIGNATURE_CHAIN, b):
        return 'signature-block'
    # 5) 叙事导语（YYYY年M月D日）+ 完成态动词
    if re.search(_NEWS_NARRATIVE, b) and re.search(_NEWS_DONE, b):
        return 'narrative-completion'
    # 6) 标题机构作主语 + 参加类动词（本院是参与者而非主办方）
    if re.search(_NEWS_TITLE_PARTICIPATE, t):
        return 'title-participate'
    return None


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
    # 列表页锚文本常把发布日期前缀粘进标题（如「2024-05-21艺术乡建…」「2023年12月24日红树林…」）。
    # 去掉标题开头的日期前缀，仅保留真实讲座标题。日期本身已由时间解析单独处理。
    t = re.sub(r'^\s*(?:19|20)\d{2}\s*[-/年\.]\s*\d{1,2}\s*[-/月\.]\s*\d{1,2}\s*[日号]?\s*', '', t).strip()
    return t


# 全校级页脚/导航标记：这些文本只可能出现在站点全局页脚，绝不会出现在讲座正文里。
# 一旦在正文文本中检测到，其后的内容即页脚噪声，应整体截断。
_FOOTER_MARKERS = (
    '关于华南师范大学',   # 学校 about 页链接，历史文化学院等页脚
    '版权所有',           # 页脚版权行（含 All Rights Reserved）
    'All Rights Reserved',
    '粤ICP',              # 备案号
    '常用链接',           # 页脚友情/常用链接区起始
    '统一认证',           # 页脚统一认证入口
    '移动平台',           # 页脚移动平台入口
    '旧版网站', '网站地图', '无障碍', '联系我们',
)


def _strip_footer(text):
    """截断全校级页脚/导航噪声，避免其被误并入 location/topic 等字段。

    仅当标记出现在文本后 30% 时才截断——页脚必然在正文之后，此守卫可排除
    正文中偶发的同名词（如「联系我们」）造成的误删。
    """
    if not text:
        return text
    cut = -1
    for mk in _FOOTER_MARKERS:
        idx = text.find(mk)
        if idx > 0 and idx >= len(text) * 0.3:
            cut = idx if cut == -1 else min(cut, idx)
    if cut != -1:
        text = text[:cut]
    return text.strip()


def _normalize_label_text(text):
    """去除常见字段标签中因 CMS 拆分 span 而混入的空格，如「题 目」「地 点」「主 讲 人」。"""
    labels = [
        # 主题/题目
        '题目', '主题', '讲座主题', '报告题目', '演讲题目', '报告主题', '讲座题目',
        # 时间地点人物
        '地点', '时间', '主讲人', '主讲师', '报告人', '主讲嘉宾', '演讲人', '主讲',
        '学术主持', '主办单位',
        # 简介/摘要/内容
        '主讲人简介', '主讲人简历', '简历', '简介',
        '摘要', '讲座内容', '讲座内容提要', '内容提要', '讲座摘要',
        '报告摘要', '内容摘要', '内容简介', '讲座简介', '报告内容', '讲座概要', '内容概要',
        # 发布信息
        '发布时间', '发布日期', '来源',
    ]
    # 1) 先把方括号/花括号形式的标签统一转成「标签：」
    #    如美术学院页面：【主题】xxx、【主讲人】xxx、【时间】xxx、【地点】xxx
    for label in sorted(labels, key=len, reverse=True):
        text = re.sub(rf'[【\[]\s*{re.escape(label)}\s*[】\]]', label + '：', text)
    # 2) 再处理 CMS 把标签拆成单字加空格的情况，如「题 目：」「主 讲 人：」
    # 先匹配长的复合标签，避免「讲座内容」把「讲座内容提要」先吃掉
    for label in sorted(labels, key=len, reverse=True):
        spaced = ''.join(c + r'\s*' for c in label)
        text = re.sub(spaced, label, text)
    return text


def _date_from_url(url):
    """从内容页 URL 路径提取完整日期 (year, month, day)，失败返回 None。

    兼容：
      - /a/20251201/xxx.html  -> (2025, 12, 1)
      - /a/2025/0507/xxx.html -> (2025, 5, 7)
      - /xxx/2025/1028/xxx.html -> (2025, 10, 28)  (汕尾校区等栏目)
      - /xxx/2025/10/28/xxx.html -> (2025, 10, 28)  (部分老站/国际站)
    """
    if not url:
        return None
    # 匹配 /a/YYYYMMDD/ 紧凑日期路径
    m = re.search(r'/a/(20\d{2})(\d{2})(\d{2})/', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    # 匹配 /a/YYYY/MMDD/ 路径
    m = re.search(r'/a/(20\d{2})/(\d{2})(\d{2})/', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    # 匹配 /xxx/YYYY/MMDD/xxx.html（如汕尾校区 /collaborative/2022/1028/36.html）
    m = re.search(r'/(20\d{2})/(\d{2})(\d{2})/[^/]+\.html?$', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    # 匹配 /xxx/YYYY/MM/DD/xxx.html（部分老站/国际站）
    m = re.search(r'/(20\d{2})/(\d{1,2})/(\d{1,2})/[^/]+\.html?$', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    return None


def _year_from_url(url):
    """从内容页 URL 路径提取年份。兼容：/a/20251201/xxx.html -> 2025。"""
    d = _date_from_url(url)
    return d[0] if d else None


def _split_location_time(loc):
    """从地点字段中抽取被 OCR 混入的时间区间，返回 (clean_loc, (h0, m0, h1, m1) or None)。

    汕尾校区教学工作坊等海报详情页：正文只有一张海报图，OCR 出的正文既含地点也含时间，
    如「南教-209教室14: 30-17: 00」「蔚教-101教室14: 30-16: O0士办单位 …」。
    把时间分离出来，地点只保留「南教-209教室」这样的纯地点，时间稍后补到 lectureStart/End。
    OCR 常见噪声：冒号被识为分号「;」、把 0 写成 O/o。需一并容忍。
    """
    if not loc:
        return loc, None
    # 容忍 OCR 把时间区间的冒号写成「;」、把 0 写成 O/o
    pat = re.compile(
        r'(\d{1,2})\s*[:;]\s*([O0-9]{1,2})\s*[-–~—]\s*(\d{1,2})\s*[:;]\s*([O0-9]{1,2})?'
    )
    m = pat.search(loc)
    if not m:
        return loc, None

    def fix(x):
        return int(str(x).replace('O', '0').replace('o', '0'))

    try:
        h0, m0 = fix(m.group(1)), fix(m.group(2))
        h1, m1 = fix(m.group(3)), fix(m.group(4)) if m.group(4) else 0
    except (ValueError, TypeError):
        return loc, None
    # 合理性校验（含 OCR 把 17:30 误识成 17:30 等正常情况）；越界则放弃分离
    if not (0 <= h0 < 24 and 0 <= m0 < 60 and 0 <= h1 < 24 and 0 <= m1 < 60):
        return loc, None
    # 截掉时间及其后的「主办单位…」等 OCR 噪声，仅保留地点本体
    clean = loc[:m.start()].strip()
    clean = re.sub(r'[\s]*[曷号]+$\s*', '', clean).strip()
    clean = re.sub(r'\s+[^\u4e00-\u9fa5a-zA-Z0-9]{1,2}\s*$', '', clean).strip()
    return clean or loc, (h0, m0, h1, m1)


def _locate_publish_time(soup, content_div, body_text, full_text):
    """R3 发布时间精确定位：标签 > 伴生词/class > 位置兜底。返回 (publish_time, level)。

    level: 1=显式标签, 2=伴生词/class, 3=位置兜底。用于 R3 本质条款——
    第 2/3 级兜底抓到的候选若等于权威讲座日，视为误抓讲座时间，作废该候选；
    第 1 级标签值即使等于讲座日也保留（R5：同天发布同天讲属正常）。

    本质条款（修 Bug A）：本函数只在"定位发布时间"这一动作里工作，绝不对讲座正文
    做任何字符串删除——发布日排除只作用于此处，不影响正文日期解析。
    """
    PUB = r'(?:发布(?:时间|日期)?|发表时间|发布于|posted|date)\s*[：:]?\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)'
    # Level 1：显式标签（正文优先，整页兜底）
    m = re.search(PUB, body_text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), 1
    m = re.search(PUB, full_text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), 1
    # Level 2：class 命中 .info/.meta/.article-info/.pub
    if content_div:
        for tag in content_div.find_all(class_=re.compile(r'info|meta|pub|article-info', re.I)):
            mm = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)', tag.get_text())
            if mm:
                return mm.group(1).strip(), 2
    # Level 2：伴生词行（来源/点击/评论/浏览/作者）
    m = re.search(r'(?:来源|点击|评论|浏览|作者)[：: ]*.*?(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)', body_text)
    if m:
        return m.group(1).strip(), 2
    # Level 3：位置兜底（正文第一个日期）
    m = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)', body_text)
    if m:
        return m.group(1).strip(), 3
    m = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)', full_text)
    if m:
        return m.group(1).strip(), 3
    return None, 0


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
    # 美术学院等站点：正文可能是图片，但 meta description / og:description 里保存了结构化文字
    meta_parts = []
    for meta in (
        soup.find('meta', attrs={'name': 'description'}),
        soup.find('meta', property='og:description'),
        soup.find('meta', attrs={'name': 'og:description'}),
    ):
        if meta and meta.get('content') and len(meta.get('content').strip()) > 3:
            meta_parts.append(meta.get('content').strip())
    if meta_parts:
        text = text + ' ' + ' '.join(meta_parts)
    text = re.sub(r'\s+', ' ', text).strip()
    text = _normalize_label_text(text)
    # 截断全校级页脚/导航噪声（如「关于华南师范大学 | 统一认证 | 移动平台」），
    # 否则 location/topic 等字段会一直吞到文末把页脚吃进来。
    text = _strip_footer(text)

    # 提前定位正文容器；若正文几乎为空但含图片（如行知书院讲座海报），对图片 OCR 提取文字
    content_div = (soup.find('div', class_='wp_articlecontent')   # WebPlus CMS（生命科学学院等）
                   or soup.find('div', class_='wp_entry')
                   or soup.find('div', class_='article-content')
                   or soup.find('div', class_='content')
                   or soup.find('div', class_='news-details-all')
                   or soup.find('div', class_='news-details-middle')
                   or soup.find('article')
                   or soup.find('div', class_='entry-content'))
    body_text = content_div.get_text(' ') if content_div else text
    body_text = re.sub(r'\s+', ' ', body_text).strip()
    body_text = _normalize_label_text(body_text)
    body_text = _strip_footer(body_text)
    ocr_text = ''
    # 预收集正文图片（用于「解析不到日期 / 字段缺失时按需 OCR 海报」）。
    # content_div 找不到时（非 WebPlus 站点，如图书馆 lib.scnu.edu.cn）退化为整页收集，
    # 并用 _is_chrome_img 过滤导航/页脚图标，避免对无关图做无意义 OCR。
    def _is_chrome_img(src):
        s = (src or '').lower()
        bad = ('icon', 'logo', 'banner', 'arrow', 'foot', 'weixin', 'wx', 'qr',
               'qrcode', 'bg', 'btn', 'nav', 'share', 'close', 'more', 'header',
               'top', 'bottom', 'slide', 'ad', 'avatar')
        return any(k in s for k in bad)

    imgs = []
    img_src_root = content_div if content_div else soup
    for img in img_src_root.find_all('img'):
        src = img.get('src') or img.get('data-src')
        if not src:
            continue
        if src.lower().endswith(('.svg', '.gif')):
            continue
        abs_src = urljoin(url, src)
        if _is_chrome_img(abs_src):
            continue
        imgs.append(abs_src)
    # 优先取带日期路径的图片（海报多上传到 /YYYY/MM/ 目录），其余兜底
    dated = [c for c in imgs if re.search(r'/\d{4}[/-]\d{1,2}[/-]', c)]
    imgs = dated or imgs
    imgs = imgs[:3]

    def _do_ocr():
        """对正文海报图片做 OCR，把识别文字并入 text / body_text（仅做一次）。"""
        nonlocal ocr_text, body_text, text
        if ocr_text or not imgs:
            return
        raw = ' '.join(_img_to_text(img) for img in imgs[:3])
        if raw:
            # 清理 OCR 中常见的顶部/底部噪声
            ocr_text = _clean_ocr_text(raw)
            body_text = (body_text + ' ' + ocr_text).strip()
            text = (text + ' ' + ocr_text).strip()

    # 纯海报页（正文几乎为空）时直接 OCR；其余含图页面在字段提取后再按需 OCR 摘要/简介
    if len(body_text) < 50:
        _do_ocr()

    # R3 发布时间定位（标签 > 伴生词/class > 位置兜底）
    publish_time, publish_level = _locate_publish_time(soup, content_div, body_text, text)

    # 从标题/URL 提取显式年份（标题兼容紧凑格式 20251204）
    title_year = _year_from_text(title) if title else None
    url_year = _year_from_url(url)

    # 海报 OCR 场景中，地点字段常混入时间区间（如「南教-209教室14: 30-17: 00」），
    # 用 loc_times 累积「(start_h, start_m, end_h, end_m)」元组，解析完日期后回填到讲座时间。
    loc_times = []

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

    # ---- 讲座时间抽取 R1–R6（编排见 timeparse.resolve_lecture_time）----
    # R3 本质条款：发布日排除只作用于定位 publish_time（已在上方完成），绝不删除正文日期（修 Bug A）。
    # R2：通用解析只扫正文 body_text（content_div 去噪），不扫整页侧边栏/页脚。
    # R5：讲座日 = 发布日属正常，不再因此置空或降级（原同天降级逻辑已删除）。
    t = None
    t_untrusted = False
    rt = resolve_lecture_time(
        body_text=body_text,
        title=title,
        url_year=url_year,
        title_year=title_year,
        publish_time=publish_time,
        publish_level=publish_level,
        default_year=default_year,
        list_title=list_title,
    )
    if rt and rt.get('start'):
        t = {'start': datetime.datetime.fromisoformat(rt['start']),
             'end': datetime.datetime.fromisoformat(rt['end']) if rt.get('end') else None,
             'has_time': True}
        result['timeConfidence'] = rt.get('confidence')
        result['timeNote'] = rt.get('note')
    # 正文未解析出日期且含海报图片：OCR 后重试（仅补缺失，不覆盖已有）
    if not t and imgs:
        _do_ocr()
        if ocr_text:
            t_ocr = parse_cn_time(ocr_text, default_year, publish_time=publish_time,
                                  title_year=title_year, url_year=url_year)
            tm = re.search(r'(?:讲座)?时间[：:\s]*(.{0,40})', ocr_text)
            if tm:
                t_label = parse_cn_time(tm.group(1).strip(), default_year,
                                        publish_time=publish_time, title_year=title_year, url_year=url_year)
                if t_label:
                    t_ocr = t_label
            if t_ocr:
                t = t_ocr
    if not t and list_title:
        # 兜底：部分站点讲座日期只在列表标题里（如心理学院）
        t = parse_cn_time(list_title, default_year, publish_time=publish_time,
                          title_year=title_year, url_year=url_year)
    if not t:
        # 最终兜底：URL 路径完整日期（旧站点/极简页）。不可信（常为发布日/通知日）。
        url_date = _date_from_url(url)
        if url_date:
            y, mo, d = url_date
            try:
                t = {'start': datetime.datetime(y, mo, d, 0, 0), 'end': None}
                t_untrusted = True
            except ValueError:
                t = None
    # R3 本质条款：第 2/3 级兜底的发布时间若等于权威讲座日，视为误抓讲座时间，作废该候选
    if (publish_time and publish_level in (2, 3) and t
            and t['start'].strftime('%Y-%m-%d') == publish_time[:10]):
        publish_time = None
    if t:
        result['lectureStart'] = t['start'].isoformat(sep=' ')
        result['lectureEnd'] = t['end'].isoformat(sep=' ') if t.get('end') else None

    # 把地点字段里分离出的时间区间回填到讲座时间：
    #  - 若已有日期但时间完全缺失（00:00），用分离出的区间补全 start/end；
    #  - 若 start 已有时间但 end 缺失，用分离出的结束时间补全 end；
    # 这样海报中「地点: 南教-209教室14:30-17:00」式 OCR 能补全完整的起止时间。
    if loc_times and result['lectureStart']:
        h0, m0, h1, m1 = loc_times[0]
        try:
            st = datetime.datetime.fromisoformat(result['lectureStart'])
            if st.hour == 0 and st.minute == 0:
                st = st.replace(hour=h0, minute=m0)
                result['lectureStart'] = st.isoformat(sep=' ')
                if not result['lectureEnd']:
                    result['lectureEnd'] = st.replace(hour=h1, minute=m1).isoformat(sep=' ')
            elif not result['lectureEnd'] and (h1, m1) != (st.hour, st.minute):
                result['lectureEnd'] = st.replace(hour=h1, minute=m1).isoformat(sep=' ')
        except (ValueError, TypeError):
            pass

    # 字段标签前瞻——每个字段只取到下一个标签为止
    # 美术学院常见标签：讲座题目、主讲嘉宾、学术主持、主办单位、上一篇/下一篇
    LABELS = (
        '教学工作坊时间|教学工作坊地点|'
        '报告时间|报告地点|报告内容|报告题目|报告专家|报告嘉宾|'
        '讲座题目|讲座时间|讲座地点|主办单位|学术主持|上一篇|下一篇|标签|Tags|'
        '地点|题目|主题|讲座主题|演讲题目|报告主题|'
        '时间|主讲[人师]|讲座人|主持人|主讲|报告人|主讲嘉宾|演讲人|邀请人|'
        '摘要|讲座内容提要|内容提要|讲座内容摘要|内容摘要|内容简介|'
        '讲座内容|讲座简介|报告内容|讲座概要|内容概要|'
        '简历|主讲人简介|主讲人简历|简介|专家介绍|专家简介|发布|来源'
    )
    STOP = rf'(?=\s*(?:{LABELS}|$))'

    # --- 题目/主题（兼容「题目/主题/讲座主题/报告题目/演讲题目/报告主题」）---
    topic_pat = rf'(?:讲座题目|题目|主题|讲座主题|报告题目|演讲题目|报告主题)[：:]\s*(.+?){STOP}'
    m = re.search(topic_pat, text)
    if m:
        t = m.group(1).strip()
        # 清除尾部粘连的「摘要」「主讲人」「预告」等非正文词
        t = re.sub(r'\s*(?:摘要|主讲人|报告人|预告)\s*[:：]?.*$', '', t).strip()
        result['topic'] = t

    # 标题格式兜底：「2026年7月2日学术讲座：主题」或「学术讲座：主题」
    if not result['topic'] and title:
        m = re.search(r'(?:学术讲座|讲座|报告会|学术报告)[：:]\s*(.+)$', title)
        if m:
            topic_candidate = m.group(1).strip()
            # 去掉末尾常见通用词，保留具体主题
            topic_candidate = re.sub(r'(?:教授|老师|先生|女士)\s*(学术讲座|讲座|报告|讲坛)$', '', topic_candidate).strip()
            if len(topic_candidate) > 3:
                result['topic'] = topic_candidate

    # --- 地点 ---
    m = re.search(rf'地点[：:]\s*(.+?){STOP}', text)
    if m:
        loc = m.group(1).strip()
        # 美术学院等页面：地点后常粘连「主办单位/上一篇/下一篇/Tags/版权」等噪声，优先截断
        loc = re.split(r'(?:主办单位|协办单位|承办单位|邀请人|讲座人|主持人|上一篇|下一篇|标签|Tags|Copyright|版权所有|All Rights Reserved|SCNU)', loc)[0].strip()
        # 汕尾校区教学工作坊海报：地点标签常为「教学工作坊地点:」，且「教学工作坊时间:」中的
        # 「时间」二字会 premature 触发 STOP，把「教学工作坊」后缀带进地点；这里显式剔除。
        loc = re.sub(r'教学工作坊.*$', '', loc).strip()
        # 地点值通常很短；如果超过 60 字符说明仍吃到了后续内容，截断到第一个句号/逗号
        if len(loc) > 60:
            loc = re.split(r'[。，;；\n]', loc)[0].strip()
        # 去除 OCR 尾部常见乱码或装饰字符（如「曷」「号」）
        loc = re.sub(r'[\s]*[曷号]+$\s*', '', loc).strip()
        loc = re.sub(r'\s+[^\u4e00-\u9fa5a-zA-Z0-9]{1,2}\s*$', '', loc).strip()
        # 折叠 CMS 把地点拆成单字/单数字造成的空格（如「理 6 栋 302」→「理6栋302」），
        # 仅合并「中文-中文/中文-数字/数字-中文」间的空格，保留英文单词与纯数字间的空格。
        loc = re.sub(r'(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5\d])|(?<=\d)\s+(?=[\u4e00-\u9fa5])', '', loc).strip()
        # 海报 OCR 场景中，地点字段常被混入时间区间（如「南教-209教室14: 30-17: 00」）
        # 或「南教-209教室14: 30-17; 主办甲位: …」式 OCR 噪声。把时间分离出来补到讲座时间，
        # 地点只保留纯地点。
        loc, loc_time = _split_location_time(loc)
        if loc_time:
            loc_times.append(loc_time)
        result['location'] = loc

    # --- 主讲人（兼容「主讲人/主讲师/报告人/主讲嘉宾/演讲人/主讲」）---
    # 注意：排除「主讲《…》」（正文里「主讲《课程名》」是动宾短语，不是主讲人标签），
    # 否则会把书名号后的课程名误当主讲人（如汕尾校区海报 bio 中的「主讲《动物组织学与胚胎学》」）。
    # 汕尾校区教学工作坊海报用「主讲专家:」「专家姓名:」标注主讲人，一并纳入。
    speaker_label_found = False
    speaker_pat = rf'(?:主讲[人师]|主讲(?!《)(?:专家)?|报告人|主讲嘉宾|演讲人|报告专家|报告嘉宾|专家姓名)\s*[：:]\s*(.+?){STOP}'
    m = re.search(speaker_pat, text)
    if m:
        speaker_label_found = True
        sp = m.group(1).strip()
        # 如果值太长，先在第一个非 speaker/affiliation 的标点处截断，避免把整段简历都吞进来
        if len(sp) > 25:
            cut = re.search(r'[，、；。]', sp[4:])
            if cut:
                sp = sp[:4 + cut.start()].strip()
        # 去掉尾部职称后缀
        sp_clean = re.sub(r'\s*(?:特聘教授|特任教授|副教授|助理教授|副研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士).*$', '', sp).strip()
        # 尝试拆分姓名+单位（括号形式）
        mm = re.match(r'(.+?)\s*[（(]([^）)]{2,40})[）)]', sp)
        if mm:
            result['speaker'] = sp_clean.split('（')[0].strip()
            aff = re.sub(r'\s*(?:特聘教授|特任教授|副教授|助理教授|副研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士).*$', '', mm.group(2)).strip()
            result['speakerAffiliation'] = re.sub(r'\s+', '', aff)
        else:
            # 空格分隔的「姓名 职称 单位」或「姓名 单位」（如物理学院「郑炜 教授 中国科学技术大学」）
            _TITLES = r'(?:特聘教授|特任教授|副教授|助理教授|副研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师)'
            # 先处理「姓名 职称，单位」逗号分隔（生命科学学院常见：报告人：肖媛 博士，清华大学）
            sp_normalized = re.sub(r'[，,]', ' ', sp)
            mm2 = re.match(rf'^([\u4e00-\u9fa5·]{{2,5}})\s+[\u4e00-\u9fa5]{{0,4}}{_TITLES}\s+([\u4e00-\u9fa5A-Za-z].{{2,40}})$', sp_normalized)
            if not mm2:
                mm2 = re.match(r'^([\u4e00-\u9fa5·]{2,5})\s+([\u4e00-\u9fa5]{4,40})$', sp_normalized)
            if mm2:
                result['speaker'] = mm2.group(1).strip()
                aff = re.sub(r'\s*(?:特聘教授|特任教授|副教授|助理教授|副研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士).*$', '', mm2.group(2)).strip()
                result['speakerAffiliation'] = re.sub(r'\s+', '', aff).strip()
            else:
                result['speaker'] = sp_clean

    # 兜底：从标题括号中提取主讲人，如「（朱英教授）」
    if not result['speaker']:
        tm = re.search(r'（([^（）]*?(教授|研究员|副教授|讲师|博士)[^（）]*?)）', title)
        if tm:
            result['speaker'] = re.sub(r'\s*(教授|研究员|副教授|讲师|博士|老师).*$', '', tm.group(1)).strip()

    # --- OCR 决策（T2 关键字段缺失 + T3 讲座页内容不完整）---
    # 通用、不按院：仅依据「页面内容特征 + 标题关键词 + 是否含图」判断，避免为特定学院写白名单例外。
    LECTURE_KW = ('讲座', '报告', '工作坊', '沙龙', '论坛', '研讨会', '讲坛', '座谈会')
    title_is_lecture = bool(title) and any(kw in title for kw in LECTURE_KW)
    # T2：时间/地点/主讲/题目 任一关键字段缺失且含图 → OCR 补充（仅补缺失、不覆盖已有）
    missing_key = (not result.get('lectureStart') or not result.get('location')
                   or not result.get('speaker') or not result.get('topic'))
    # T3：讲座类标题 + 含图 + (时间不可信/缺失 或 地点缺失) → OCR，海报日期更具体则覆盖 lectureStart
    need_ocr = bool(imgs) and (missing_key or (title_is_lecture and (t_untrusted or not result.get('location'))))
    if need_ocr and not ocr_text:
        _do_ocr()
    # T3 覆盖：讲座类标题 + 含图 + OCR 抽到日期 → 以海报日期为准覆盖 lectureStart/End。
    # 海报是讲座时间的权威源，故不再强依赖 t_untrusted（发布日未被识别时会漏判）；
    # 覆盖条件收敛为「OCR 日期与现有不同 / OCR 补出了时间 / OCR 补出了结束时间」，
    # 避免把正文已正确的时间误覆盖。必须解析 ocr_text 本身，而非整页 text——
    # 整页 text 里排在前的发布日/通知日会先被命中，导致「日期相同」误判、海报日期无法覆盖。
    if ocr_text and title_is_lecture:
        t_ocr = parse_cn_time(ocr_text, default_year, publish_time=publish_time, title_year=title_year, url_year=url_year)
        # 优先取 OCR 中「时间」标签后的片段（更精准，避免海报其他处日期干扰）
        tm = re.search(r'(?:讲座)?时间[：:\s]*(.{0,40})', ocr_text)
        if tm:
            t_label = parse_cn_time(tm.group(1).strip(), default_year, publish_time=publish_time, title_year=title_year, url_year=url_year)
            if t_label:
                t_ocr = t_label
        if t_ocr and (t is None
                     or t_ocr['start'].date() != t['start'].date()
                     or (not t.get('has_time') and t_ocr.get('has_time'))
                     or (t.get('end') is None and t_ocr.get('end'))):
            t = t_ocr
            result['lectureStart'] = t_ocr['start'].isoformat(sep=' ')
            result['lectureEnd'] = t_ocr['end'].isoformat(sep=' ') if t_ocr.get('end') else None

    # --- 简历/简介（优先在文章正文区域内搜索）---
    # body_text 已在函数开头构建（含可能的 OCR 文本）

    # 内容摘要类标签：出现这些说明主讲人简介已结束、讲座内容介绍开始
    SUMMARY_LABELS = (
        '讲座内容提要|内容提要|讲座内容摘要|内容摘要|内容简介|报告简介|讲座简介|'
        '讲座内容|讲座简介|报告内容|讲座概要|内容概要|摘要'
    )

    # 页面噪声/侧边栏标记：遇到这些说明正文已结束，应截断
    NOISE_MARKERS = (
        '资讯及通知|相关新闻|最新动态|推荐阅读|相关文章|相关讲座|'
        '上一篇|下一篇|附件下载|相关链接|网友评论|分享|标签|相关推荐|'
        '通知公告|最新公告|站内搜索|快速导航'
    )

    bio_pat = rf'(?:报告人简介|主讲人简介|主讲人简历|简历|(?<!内容)简介)[\s:：]*'
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
        # 以 title / topic 为锚点截断顶部院校 Logo 等噪声，保留真正内容起始
        if title and title in clean:
            idx = clean.find(title)
            clean = clean[idx + len(title):].strip()
        if result['topic'] and result['topic'] in clean:
            idx = clean.find(result['topic'])
            clean = clean[idx + len(result['topic']):].strip()
        # 如果清理后只剩元信息（开头即报告人/主讲人/主持人/时间/地点/报告人简介/院校抬头），说明海报没有讲座摘要
        if re.match(r'^(报告人|主讲人|主持人|时间|地点|时闻|报告人简介|主讲人简介|20\d{2}年|華南|华南|大学|学院|UNIVERSITY|COLLEGE|SOUTH|CHINA|大讲堂|论坛|生命科学|木棉|1933|20\d{2})', clean.strip()):
            clean = ''
        # 若 OCR 明确区分「报告简介/讲座简介」等，直接取该部分
        m_summary = re.search(r'(?:报告简介|讲座简介|讲座摘要|报告摘要|内容摘要|内容简介|讲座内容)[：:\s]*(.+)', clean)
        if m_summary:
            clean = m_summary.group(1).strip()
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
        # 若已识别到主讲人标签（即便其值为空，如汕尾海报「专家姓名:」与「活动主题:」错位），
        # 不再用叙事兜底覆盖，避免把研究方向片段（如「毒理及细胞对话机制」）误当主讲人。
        if not result['speaker'] and not speaker_label_found and narrative.get('speaker'):
            result['speaker'] = narrative['speaker']
        if not result.get('abstract') and narrative.get('abstract'):
            # OCR 场景下，叙事兜底容易把主讲人简介当成讲座摘要；
            # 若已有 OCR 文本且未提取到明确摘要标签，宁可让 abstract 留空。
            if not (ocr_text and len(ocr_text) > 50):
                result['abstract'] = narrative['abstract']

    # 新闻/回顾处理（R5 政策变更，2026-07-18）：
    # 之前把「发布晚于讲座」的回顾稿整条丢弃；现改为保留——它们也是真实开展过的讲座，
    # 只是未提前预告。仅打标记供后续筛选/核验，不丢弃数据。
    if is_news_record(result):
        result['retrospective'] = True
        result['timeNote'] = (result.get('timeNote') or '') + ';news-publish-after-lecture'
    # 新闻/活动回顾稿识别（与 is_news_record 互补，覆盖无显式发布时间戳的回顾稿）
    _news = is_news_article(title, body_text)
    if _news:
        result['retrospective'] = True
        result['newsRule'] = _news
        result['timeNote'] = (result.get('timeNote') or '') + ';news-article:' + _news

    return result
