"""中文讲座时间结构化：将「2025年7月2日（星期一）下午3:00」等解析为 datetime。

优先级：
1) 优先且独占使用「时间/讲座时间：」标签后的内容；
2) 完整日期（YYYY年M月D日 / YYYY-MM-DD / YYYY.MM.DD）；
3) 仅有「M月D日」时，年份取发布时间年份，否则取当前年。
解析前会剔除发布时间文本，避免把发布日期误当作讲座日期。
注：详情页常因加粗标签产生「4 月 2 5 日」「1 0 ： 0 0」式空白与全角冒号，
解析前先去除空白并兼容全角标点。
"""
import re
from datetime import datetime, date

PERIOD_OFFSET = {'上午': 0, '早上': 0, '中午': 12, '下午': 12, '晚上': 12, '傍晚': 12}

FULL_PATTERNS = [
    r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
    r'(\d{4})-(\d{2})-(\d{2})',
    r'(\d{4})\.(\d{2})\.(\d{2})',
]
MONTHDAY = r'(\d{1,2})\s*月\s*(\d{1,2})\s*日'
COLON = r'[:：]'


def _apply_period(hh, period):
    if period:
        if hh < 12:
            hh += period
        elif period == 12 and hh == 12:
            hh = 12
    return hh % 24


def _build(m, seg, y, mo, d):
    seg = seg[m.start():]
    period = 0
    pm = re.search(r'(上午|早上|中午|下午|晚上|傍晚)', seg)
    if pm:
        period = PERIOD_OFFSET[pm.group(1)]
    times = re.findall(r'(\d{1,2})\s*' + COLON + r'\s*(\d{2})', seg)
    if not times:
        return {'start': datetime(int(y), int(mo), int(d), 0, 0),
                'end': None, 'has_time': False}
    h0 = _apply_period(int(times[0][0]), period)
    start = datetime(int(y), int(mo), int(d), h0, int(times[0][1]))
    end = None
    if len(times) > 1:
        h1 = _apply_period(int(times[1][0]), period)
        end = datetime(int(y), int(mo), int(d), h1, int(times[1][1]))
    return {'start': start, 'end': end, 'has_time': True}


def _parse_segment(seg, default_year, publish_time):
    seg = re.sub(r'\s+', '', seg)
    pub = publish_time[:10] if publish_time else None
    y = default_year

    # 1) 完整日期（含中文年）：最可靠，优先使用显式年份。
    #    YYYY年M月D日 不可能是发布时间，可直接命中。
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', seg)
    if m:
        return _build(m, seg, m.group(1), m.group(2), m.group(3))

    # 2) 完整日期 YYYY-MM-DD / YYYY.MM.DD：跳过等于发布日期的，避免把发布日期当讲座日期。
    for p in [r'(\d{4})-(\d{2})-(\d{2})', r'(\d{4})\.(\d{2})\.(\d{2})']:
        for m in re.finditer(p, seg):
            if pub and f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}" == pub:
                continue
            return _build(m, seg, m.group(1), m.group(2), m.group(3))

    # 3) 仅有 M月D日：使用外部传入的默认年份（title_year / url_year / publish_time / current）
    md = re.search(MONTHDAY, seg)
    if md:
        return _build(md, seg, y, md.group(1), md.group(2))
    return None


def _year_from_text(text):
    """从文本中提取显式年份，兼容 2024-12-02 / 20251204 等常见格式。"""
    if not text:
        return None
    # 分隔符格式：2024-12-02 / 2024.12.02 / 2024年12月02日
    m = re.search(r'(20\d{2})[-/.年]\s*\d{1,2}[-/.月]\s*\d{1,2}', text)
    if m:
        return int(m.group(1))
    # 紧凑格式：20251204（讲座标题常见）
    m = re.search(r'(20\d{2})(\d{2})(\d{2})', text)
    if m:
        return int(m.group(1))
    return None


def _apply_publish_correction(res, publish_time):
    """发布时间交叉校验：讲座不可能早于发布时间。

    若解析出的年份早于发布年（典型 OCR/正文漏年份被默认成更早或解析错位），
    将年份抬到发布年。这是单向安全修正，不会把正常「发布后举办」的讲座改错。
    """
    if not res or not publish_time:
        return res
    try:
        pub_y = int(publish_time[:4])
    except (ValueError, IndexError):
        return res
    if res['start'].year < pub_y:
        res['start'] = res['start'].replace(year=pub_y)
        if res['end']:
            res['end'] = res['end'].replace(year=pub_y)
    return res


def parse_cn_time(text, default_year=None, publish_time=None, title_year=None, url_year=None):
    """解析中文讲座时间。

    Args:
        text: 要解析的文本（正文或标题）
        default_year: 默认年份（当前年）
        publish_time: 发布时间字符串，用于排除和提供年份回退
        title_year: 从标题中提取的显式年份，优先级最高（可识别紧凑格式 20251204）
        url_year: 从内容页 URL 路径提取的年份，次之
    """
    if default_year is None:
        default_year = date.today().year

    # 默认年份优先级：title_year > url_year > publish_time年份 > current_year
    effective_default = default_year
    if title_year is not None:
        effective_default = title_year
    elif url_year is not None:
        effective_default = url_year
    elif publish_time:
        try:
            effective_default = int(publish_time[:4])
        except (ValueError, IndexError):
            pass

    clean = text
    if publish_time:
        clean = clean.replace(publish_time, '')
        clean = clean.replace(publish_time[:10], '')

    lm = re.search(r'(时间|讲座时间)[：:]\s*(.{0,40})', clean)
    if lm:
        res = _parse_segment(lm.group(2), effective_default, publish_time)
        if res:
            return _apply_publish_correction(res, publish_time)
    res = _parse_segment(clean, effective_default, publish_time)
    if res:
        return _apply_publish_correction(res, publish_time)
    return None
