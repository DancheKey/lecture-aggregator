"""中文讲座时间结构化：将「2025年7月2日（星期一）下午3:00」等解析为 datetime。

时间抽取规则（R1–R6，2026-07-18 定稿）：
  R1 权威标签优先：按 Tier 顺序扫描带日期的"时间"标签，完整日期直接采用、仅月日转 R4 补年。
  R2 通用解析只扫正文：仅作用于 content_div（剔除 nav/aside/footer/meta），缺失回退整页并降置信。
  R3 发布时间精确定位（在 parsers.py 的 _locate_publish_time 实现）：发布日排除只用于定位发布时间，
      绝不用字符串替换删除正文里所有同天日期（修 Bug A）。
  R4 年份优先级固定：URL年 > 标题年 > 正文年 > 发布年 > 当前年；仅月日沿链补年，不二次抬年。
  R5 讲座日 = 发布日属正常：不因此置空或降级。
  R6 跨年修正（双向）：仅月日补年结果、补年源∈{URL年,发布年}、publish 已定位时触发；
      lecture<publish 或 lecture>publish 双向判定 +1/-1/不动，附置信度与 note。

本模块只做"纯解析"：parse_cn_time 是底层原语（在给定文本里找第一个日期，按优先级补年，
但**不删除**任何文本）；resolve_lecture_time 是编排层，实现 R1/R2/R4/R6。
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
    # 时间合法性校验：OCR/编码噪声可能抓出 "11:60" / "25:00" 等非法时间。
    # 一旦非法则退回「仅日期」（has_time=False），绝不直接构造 datetime 抛异常——
    # 否则整条解析失败、讲座被静默丢弃。
    def _valid(hh, mm):
        return 0 <= hh <= 23 and 0 <= mm <= 59
    h0_raw, m0_raw = int(times[0][0]), int(times[0][1])
    if not _valid(h0_raw, m0_raw):
        return {'start': datetime(y_i, mo_i, d_i, 0, 0),
                'end': None, 'has_time': False}
    h0 = _apply_period(h0_raw, period)
    start = datetime(y_i, mo_i, d_i, h0, m0_raw)
    end = None
    if len(times) > 1:
        h1_raw, m1_raw = int(times[1][0]), int(times[1][1])
        if _valid(h1_raw, m1_raw):
            h1 = _apply_period(h1_raw, period)
            end = datetime(y_i, mo_i, d_i, h1, m1_raw)
    return {'start': start, 'end': end, 'has_time': True}


def _parse_compact_run(m, seg, yy, run):
    """抗 OCR 噪声的紧凑数字日期：年份后接 3-6 位乱序数字（如 2024111128 / 20241715 / 2024715）。

    汕尾校区教学工作坊海报经 OCR 后，日期常被粘连成无分隔符的数字串，且可能多/少一位。
    对 run 做多种切分试探，按优先级取首个「月∈[1,12] 且 日∈[1,31]」的合法组合。
    """
    n = len(run)
    if n == 4 and run[:2] in ('19', '20'):
        return None
    cands = []
    if n == 3:
        cands.append((int(run[0]), int(run[1:])))
    elif n == 4:
        cands.append((int(run[:2]), int(run[2:])))
        cands.append((int(run[0]), int(run[1:])))
    elif n == 5:
        cands.append((int(run[1:3]), int(run[3:])))
        cands.append((int(run[:2]), int(run[2:4])))
    elif n == 6:
        if run[0:2] == run[2:4]:
            cands.append((int(run[2:4]), int(run[4:6])))
        else:
            cands.append((int(run[:2]), int(run[2:4])))
            cands.append((int(run[2:4]), int(run[4:6])))
    for mo, d in cands:
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return _build(m, seg, str(yy), str(mo), str(d))
    if '1' in run:
        parts = [p for p in re.split(r'1', run) if p and p.isdigit()]
        if len(parts) == 2:
            mo, d = int(parts[0]), int(parts[1])
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return _build(m, seg, str(yy), str(mo), str(d))
    return None


def _parse_segment(seg, default_year, publish_time):
    """在单段文本里找第一个日期。完整日期用其显式年；仅月日用 default_year 补年。

    返回 {'start','end','has_time','from_full'} 或 None。from_full 表示该日期含显式 4 位年。
    注意：绝不对 seg 做字符串删除（修 Bug A）；仅对"完整日期"循环做发布日精确跳过，
    避免把发布时间戳当讲座日。
    """
    seg = re.sub(r'\s+', '', seg)
    pub = publish_time[:10] if publish_time else None
    y = default_year

    # 1) 完整中文日期（含中文年）：最可靠，优先使用显式年份。
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]', seg)
    if m:
        r = _build(m, seg, m.group(1), m.group(2), m.group(3))
        if not r:
            return None
        r['from_full'] = True
        return r

    # 2) 完整日期 YYYY-MM-DD / YYYY.MM.DD / YYYY/M/D / YYYY/MMDD：
    #    跳过等于发布日期的（避免把发布日期当讲座日期）。
    for p in [r'(\d{4})-(\d{2})-(\d{2})',
              r'(\d{4})\.(\d{2})\.(\d{2})',
              r'(\d{4})/(\d{1,2})/(\d{1,2})',
              r'(\d{4})/(\d{2})(\d{2})']:
        for m in re.finditer(p, seg):
            if pub and f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}" == pub:
                continue
            r = _build(m, seg, m.group(1), m.group(2), m.group(3))
            if not r:
                continue
            r['from_full'] = True
            return r

    # 3) 抗 OCR 噪声的紧凑数字日期：年份后接 3-6 位紧邻数字。
    m = re.search(r'20(\d{2})([ \t]{0,2})(\d{3,6})', seg)
    if m:
        cand = _parse_compact_run(m, seg, 2000 + int(m.group(1)), m.group(2))
        if cand:
            cand['from_full'] = True
            return cand

    # 4) 仅有 M月D日：使用外部传入的默认年份（title_year / url_year / publish_time / current）
    md = re.search(MONTHDAY, seg)
    if md:
        r = _build(md, seg, y, md.group(1), md.group(2))
        if not r:
            return None
        r['from_full'] = False
        return r

    # 5) 图片 OCR 常见美式月日：06/10，默认取 default_year
    sm = re.search(SLASH_MONTHDAY, seg)
    if sm:
        if not re.search(r'20\d{2}/' + sm.group(0), seg):
            r = _build(sm, seg, y, sm.group(1), sm.group(2))
            if not r:
                return None
            r['from_full'] = False
            return r
    return None


def _year_from_text(text):
    """从文本中提取显式年份，兼容 2024-12-02 / 20251204 等常见格式。"""
    if not text:
        return None
    m = re.search(r'(20\d{2})[-/.年]\s*\d{1,2}[-/.月]\s*\d{1,2}', text)
    if m:
        return int(m.group(1))
    m = re.search(r'(20\d{2})(\d{2})(\d{2})', text)
    if m:
        return int(m.group(1))
    return None


# ============================================================================
# R1 权威标签分级扫描
# ============================================================================
# Tier-1 强权威（优先）
_TIER1_KW = ['讲座时间', '报告时间', '学术报告时间', 'seminar时间', 'Time:']
# Tier-2 弱权威
_TIER2_KW = ['开讲时间', '开课时间', '会议时间', '时间', '时闻']
# 排除（非讲座时间）
_EXCLUDE_PREFIX = ['报名', '报名截止', '截止', '直播', '提交', '签到', '用餐', '返程']
# R1 标签扫描正则（捕获标签关键词 + 其后值）
_LABEL_RE = re.compile(
    r'(讲座时间|报告时间|学术报告时间|开讲时间|开课时间|会议时间|seminar\s*时间|Time\s*:|时间|时闻)\s*[：:]?\s*(.{0,50})',
    re.IGNORECASE)
_RETRO_WORDS = ['成功', '已举办', '已举行', '圆满', '回顾', '报道', '纪实', '日前']
_PREVIEW_WORDS = ['将', '拟', '定于', '将于', '预告', '即将', '本周', '下周']


def _label_scan(body_text):
    """R1：在正文内扫描带日期的"时间"标签，返回命中列表。

    每条 hit: {tier, kw, full:(y,mo,d)|None, md:(mo,d)|None, seg:解析结果, pos}
    已应用：Tier 分级、排除前缀（报名/截止/直播…）、Time: 负向排除(deadline/submission/regist)、
    会议时间 后接含截止/deadline/提交 则排除。
    """
    hits = []
    if not body_text:
        return hits
    for m in _LABEL_RE.finditer(body_text):
        kw = m.group(1)
        val = m.group(2).strip()
        if not val:
            continue
        pre = body_text[max(0, m.start(1) - 6):m.start(1)]
        # 排除前缀
        if any(p in pre for p in _EXCLUDE_PREFIX):
            continue
        kw_norm = re.sub(r'\s*', '', kw).lower()
        # Time: 负向排除
        if kw_norm.startswith('time'):
            if re.search(r'(deadline|submission|regist)', pre, re.IGNORECASE):
                continue
            tier = 1
        elif kw_norm in ('讲座时间', '报告时间', '学术报告时间', 'seminartime'):
            tier = 1
        elif kw_norm in ('开讲时间', '开课时间', '会议时间'):
            tier = 2
            if re.search(r'截止|deadline|提交', val, re.IGNORECASE):
                continue
        else:  # 时间 / 时闻
            tier = 2
        # 解析值：完整日期 or 仅月日
        seg_res = _parse_segment(val, 2000, None)
        if not seg_res:
            continue
        if seg_res.get('from_full'):
            full = (seg_res['start'].year, seg_res['start'].month, seg_res['start'].day)
            md = None
        else:
            full = None
            md = (seg_res['start'].month, seg_res['start'].day)
        hits.append({'tier': tier, 'kw': kw, 'full': full, 'md': md,
                     'seg': seg_res, 'val': val, 'pos': m.start()})
    return hits


def _resolve_year(url_year, title_year, publish_year, default_year):
    """R4：年份优先级链，返回 (year, src)。src ∈ {url,title,publish,current}。"""
    if url_year:
        return url_year, 'url'
    if title_year:
        return title_year, 'title'
    if publish_year:
        return publish_year, 'publish'
    return default_year, 'current'


def _wrap_year(res, new_year):
    """把解析结果整体平移年份（R6 跨年修正用）。"""
    new = dict(res)
    new['start'] = res['start'].replace(year=new_year)
    if res.get('end'):
        new['end'] = res['end'].replace(year=new_year)
    return new


def _cross_year(res, publish_time, title, body_text, year, url_year, publish_year):
    """R6：双向跨年修正。仅当 year ∈ {url_year, publish_year}（补年源为 URL/发布年）时触发。

    返回 (res, confidence, note)。confidence ∈ {high, mid, low}。
    """
    ls = res['start']
    try:
        pub = datetime.strptime(publish_time[:10], '%Y-%m-%d')
    except (ValueError, TypeError):
        return res, 'low', 'publish-unparseable'
    can_cross = year in (url_year, publish_year)
    hay = (title or '') + ' ' + (body_text or '')
    retro = any(w in hay for w in _RETRO_WORDS)
    preview = any(w in hay for w in _PREVIEW_WORDS)

    if ls.date() == pub.date():
        return res, 'high', 'same-day-normal'

    # 同年（未跨年边界）：年份来自 URL/标题等可靠源，无需跨年修正，直接定 high。
    # 跨年修正只在"讲座与发布分属不同日历年"时才有意义。
    if ls.year == pub.year:
        return res, 'high', 'same-year'

    if ls.date() < pub.date():
        # 讲座落在发布前
        if retro:
            return res, 'high', 'retrospective-report'
        if preview:
            return _wrap_year(res, ls.year + 1), 'high', 'preview-nextyear'
        if pub.month in (11, 12) and ls.month in (1, 2):
            return _wrap_year(res, ls.year + 1), 'high', 'crossyear-window'
        return res, 'low', 'crossyear-uncertain'

    # 讲座落在发布后
    if preview:
        return res, 'high', 'preview-normal'
    if retro:
        return _wrap_year(res, ls.year - 1), 'high', 'retro-prevyear'
    if pub.month in (1, 2) and ls.month in (11, 12):
        return _wrap_year(res, ls.year - 1), 'low', 'crossyear-window-prev'
    return res, 'low', 'crossyear-uncertain'


def resolve_lecture_time(body_text, title, url_year, title_year, publish_time,
                         publish_level, default_year, list_title=None):
    """R1–R6 编排层。返回 {'start':iso,'end':iso|None,'confidence','note'} 或 None。

    Args:
        body_text: 正文区域文本（content_div 去噪后），R1/R2 仅扫此。
        title: 详情页标题（用于 R6 回顾/预告词检索）。
        url_year / title_year: 从 URL/标题提取的年份。
        publish_time: 已定位的发布时间字符串（R3 产物）。
        publish_level: 发布时间来源级别（1=标签,2=伴生/class,3=位置），用于 R3 本质条款。
        default_year: 当前年（补年最后兜底）。
        list_title: 列表标题（补充年份来源）。
    """
    if list_title:
        ly = _year_from_text(list_title)
        if ly:
            title_year = title_year or ly
    publish_year = int(publish_time[:4]) if publish_time else None

    # ---- R1：权威标签扫描 ----
    hits = _label_scan(body_text)

    def _year_consistent(y):
        return y in (url_year, title_year)

    primary = None
    for tier in (1, 2):
        cand = [h for h in hits if h['tier'] == tier]
        if not cand:
            continue
        cons = [h for h in cand if h['full'] and _year_consistent(h['full'][0])]
        primary = cons[0] if cons else cand[0]
        break

    if primary:
        if primary['full']:
            # 完整日期直接采用，结束年份处理（R1 + R4 前三级不二次抬年）
            dt = primary['seg']
            return {'start': dt['start'].isoformat(sep=' '),
                    'end': dt['end'].isoformat(sep=' ') if dt.get('end') else None,
                    'confidence': 'high', 'note': 'authoritative-label'}
        # 仅月日：取高 Tier 月日，年份补年（优先借低 Tier 完整年 url/title 一致者）
        mo, dd = primary['md']
        year, src = _resolve_year(url_year, title_year, publish_year, default_year)
        lower = [h for h in hits if h['tier'] > primary['tier']
                 and h['full'] and _year_consistent(h['full'][0])]
        if lower:
            year = lower[0]['full'][0]
            src = 'label'
        dt = _parse_segment(primary['val'], year, publish_time)
        if not dt:
            dt = primary['seg']
        # R6 跨年修正（仅月日补年结果）
        if publish_time:
            dt, conf, note = _cross_year(dt, publish_time, title, body_text, year,
                                         url_year, publish_year)
            return {'start': dt['start'].isoformat(sep=' '),
                    'end': dt['end'].isoformat(sep=' ') if dt.get('end') else None,
                    'confidence': conf, 'note': note}
        return {'start': dt['start'].isoformat(sep=' '),
                'end': dt['end'].isoformat(sep=' ') if dt.get('end') else None,
                'confidence': 'high', 'note': 'monthday-nopublish'}

    # ---- R2：通用解析只扫正文 ----
    eff = default_year
    if title_year:
        eff = title_year
    elif url_year:
        eff = url_year
    elif publish_year:
        eff = publish_year
    g = _parse_segment(body_text, eff, publish_time)
    if g:
        if g.get('from_full'):
            # 完整日期直接采用，不触发 R6
            return {'start': g['start'].isoformat(sep=' '),
                    'end': g['end'].isoformat(sep=' ') if g.get('end') else None,
                    'confidence': 'high', 'note': 'general-body-full'}
        # 仅月日：补年 + R6
        year = g['start'].year
        if publish_time:
            g, conf, note = _cross_year(g, publish_time, title, body_text, year,
                                         url_year, publish_year)
            return {'start': g['start'].isoformat(sep=' '),
                    'end': g['end'].isoformat(sep=' ') if g.get('end') else None,
                    'confidence': conf, 'note': note}
        return {'start': g['start'].isoformat(sep=' '),
                'end': g['end'].isoformat(sep=' ') if g.get('end') else None,
                'confidence': 'high', 'note': 'general-body-monthday-nopublish'}

    # R2 回退：正文为空（纯海报且尚未 OCR）时返回 None，交由调用方走 OCR/URL 兜底
    return None


def parse_cn_time(text, default_year=None, publish_time=None, title_year=None, url_year=None):
    """底层原语：在给定文本里找第一个日期。

    年份补年优先级：title_year > url_year > publish_time年份 > current_year。
    **不做任何字符串删除**（修 Bug A）；仅在完整日期循环中精确跳过发布日。
    用于 OCR 路径与列表标题兜底。
    """
    if default_year is None:
        default_year = date.today().year
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
    if not text:
        return None
    lm = re.search(
        r'(时间|时闻|讲座时间|讲座时闻|报告时间|学术报告时间|开讲时间|开课时间|会议时间)\s*[：:]?\s*(.{0,80})',
        text)
    if lm:
        res = _parse_segment(lm.group(2), effective_default, publish_time)
        if res:
            return res
    res = _parse_segment(text, effective_default, publish_time)
    if res:
        return res
    return None
