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
    r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]',
    r'(\d{4})-(\d{2})-(\d{2})',
    r'(\d{4})\.(\d{2})\.(\d{2})',
]
MONTHDAY = r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?'
SLASH_MONTHDAY = r'(\d{1,2})/(\d{1,2})'
COLON = r'[:：]'


def _apply_period(hh, period):
    if period:
        if hh < 12:
            hh += period
        elif period == 12 and hh == 12:
            hh = 12
    return hh % 24


def _build(m, seg, y, mo, d):
    # 防御：月份/日期/年份越界（如 URL 路径 2025/0507 被 SLASH_MONTHDAY 误匹配成
    # "月=25/日=05"）直接返回 None，避免构造 datetime 抛异常导致整条解析失败；
    # 调用方会回退到其他日期模式或留空，而非丢弃整条讲座。
    try:
        y_i, mo_i, d_i = int(y), int(mo), int(d)
    except (ValueError, TypeError):
        return None
    if not (1 <= mo_i <= 12) or not (1 <= d_i <= 31) or y_i <= 0:
        return None
    seg = seg[m.start():]
    period = 0
    pm = re.search(r'(上午|早上|中午|下午|晚上|傍晚)', seg)
    if pm:
        period = PERIOD_OFFSET[pm.group(1)]
    times = re.findall(r'(\d{1,2})\s*' + COLON + r'\s*(\d{2})', seg)
    if not times:
        # 中文「X点 / X点X分 / X点半」式时间（如「下午3点」「上午10点30分」），
        # 冒号时间缺失时兜底（常见于海报 OCR 文本）。
        dot = re.findall(r'(\d{1,2})\s*点\s*(?:(\d{1,2})\s*分?|半)?', seg)
        if dot:
            hh = int(dot[0][0])
            if dot[0][1]:
                mm = int(dot[0][1])
            elif re.search(r'半', seg):
                mm = 30
            else:
                mm = 0
            times = [(str(hh), str(mm).zfill(2))]
    if not times:
        return {'start': datetime(y_i, mo_i, d_i, 0, 0),
                'end': None, 'has_time': False}
    h0 = _apply_period(int(times[0][0]), period)
    start = datetime(y_i, mo_i, d_i, h0, int(times[0][1]))
    end = None
    if len(times) > 1:
        h1 = _apply_period(int(times[1][0]), period)
        end = datetime(y_i, mo_i, d_i, h1, int(times[1][1]))
    return {'start': start, 'end': end, 'has_time': True}


def _parse_compact_run(m, seg, yy, run):
    """抗 OCR 噪声的紧凑数字日期：年份后接 3-6 位乱序数字（如 2024111128 / 20241715 / 2024715）。

    汕尾校区教学工作坊海报经 easyocr 后，日期常被粘连成无分隔符的数字串，
    且可能多/少一位（如「2024年11月28日」→「2024111128」、月份被重复一次；
    「2024年7月5日」→「20241715」，斜杠被误识成「1」）。
    这里对 run 做多种切分试探，按优先级取首个「月∈[1,12] 且 日∈[1,31]」的合法组合。

    额外处理：若标准切分失败，尝试把 run 中的「1」当作斜杠/分隔符的 OCR 误识别
    （如「1715」→「7/5」），从而恢复单数月日格式。
    """
    n = len(run)
    # 避免把年份区间（如「2016-2023」）误当日期：4 位数字且前两位是 19/20 时，
    # 它通常是某个「年份」而非「月日」（月最大为 12），直接跳过。
    if n == 4 and run[:2] in ('19', '20'):
        return None
    cands = []
    if n == 3:
        cands.append((int(run[0]), int(run[1:])))
    elif n == 4:
        cands.append((int(run[:2]), int(run[2:])))      # 主切分：MM=前两位
        cands.append((int(run[0]), int(run[1:])))        # 单数月：715 -> (7,15)
    elif n == 5:
        cands.append((int(run[1:3]), int(run[3:])))      # 首部多一位
        cands.append((int(run[:2]), int(run[2:4])))      # 尾部多一位
    elif n == 6:
        if run[0:2] == run[2:4]:
            # 月份被重复一次：111128 -> 11(月) 28(日)
            cands.append((int(run[2:4]), int(run[4:6])))
        else:
            cands.append((int(run[:2]), int(run[2:4])))
            cands.append((int(run[2:4]), int(run[4:6])))
    for mo, d in cands:
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return _build(m, seg, str(yy), str(mo), str(d))

    # 兜底：把「1」当作斜杠/分隔符的 OCR 误识别（如 2024/7/5 → 20241715）
    if '1' in run:
        parts = [p for p in re.split(r'1', run) if p and p.isdigit()]
        if len(parts) == 2:
            mo, d = int(parts[0]), int(parts[1])
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return _build(m, seg, str(yy), str(mo), str(d))
    return None


def _parse_segment(seg, default_year, publish_time):
    seg = re.sub(r'\s+', '', seg)
    pub = publish_time[:10] if publish_time else None
    y = default_year

    # 1) 完整中文日期（含中文年）：最可靠，优先使用显式年份。
    #    YYYY年M月D日 不可能是发布时间，可直接命中。
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', seg)
    if m:
        return _build(m, seg, m.group(1), m.group(2), m.group(3))

    # 2) 完整日期 YYYY-MM-DD / YYYY.MM.DD / YYYY/M/D / YYYY/MMDD：
    #    跳过等于发布日期的，避免把发布日期当讲座日期。
    for p in [r'(\d{4})-(\d{2})-(\d{2})',
              r'(\d{4})\.(\d{2})\.(\d{2})',
              r'(\d{4})/(\d{1,2})/(\d{1,2})',   # 2023/11/30、2024/3/14、2025/5/8
              r'(\d{4})/(\d{2})(\d{2})']:        # 2023/1119（年 + MMDD 无内分隔）
        for m in re.finditer(p, seg):
            if pub and f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}" == pub:
                continue
            return _build(m, seg, m.group(1), m.group(2), m.group(3))

    # 3) 抗 OCR 噪声的紧凑数字日期：年份后接 3-6 位紧邻数字（如 2024111128 / 2024715）。
    # 分隔符只允许空格/制表符这类 OCR 间隙，绝不能是「-」「~」「—」等区间符号，
    # 否则会把「2004-2016」「2016-202 3」这类年份区间误当成紧凑日期。
    m = re.search(r'20(\d{2})([ \t]{0,2})(\d{3,6})', seg)
    if m:
        cand = _parse_compact_run(m, seg, 2000 + int(m.group(1)), m.group(2))
        if cand:
            return cand

    # 4) 仅有 M月D日：使用外部传入的默认年份（title_year / url_year / publish_time / current）
    md = re.search(MONTHDAY, seg)
    if md:
        return _build(md, seg, y, md.group(1), md.group(2))

    # 5) 图片 OCR 常见美式月日：06/10，默认取 default_year
    sm = re.search(SLASH_MONTHDAY, seg)
    if sm:
        # 避免把日期时间 2026/06/10 中的 MM/DD 重复解析；slash 月日要求前无四位年份
        if not re.search(r'20\d{2}/' + sm.group(0), seg):
            return _build(sm, seg, y, sm.group(1), sm.group(2))
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
    """发布时间交叉校验：仅当解析年份比发布年早 2 年及以上时才修正。

    典型场景是 OCR/正文漏年份导致的明显错位（如把 2024 解成 2022）。
    年差 0 或 1 年的跨年讲座（如 2025-12 讲座、2026-01 发布）属正常预告，
    不应被抬年——否则会破坏「发布时间晚于讲座时间即新闻」的过滤判断，
    导致跨年新闻稿被误判为未来讲座而漏过滤。
    """
    if not res or not publish_time:
        return res
    try:
        pub_y = int(publish_time[:4])
    except (ValueError, IndexError):
        return res
    if pub_y - res['start'].year >= 2:
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
        # 移除「发布时间：」标签本身，避免正文里的「时间：」标签被它抢先匹配
        clean = re.sub(r'发布\s*时间\s*[：:]\s*', '', clean)

    lm = re.search(r'(时间|时闻|讲座时间|讲座时闻)\s*[：:]?\s*(.{0,80})', clean)
    if lm:
        res = _parse_segment(lm.group(2), effective_default, publish_time)
        if res:
            return _apply_publish_correction(res, publish_time)
    res = _parse_segment(clean, effective_default, publish_time)
    if res:
        return _apply_publish_correction(res, publish_time)
    return None
