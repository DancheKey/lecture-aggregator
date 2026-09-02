# -*- coding: utf-8 -*-
"""规则 + 大模型 A 双轨解析与分歧裁决（仅文本页）。

对应 2026-09-02 方案：规则与 Agnes（A）并行识别，比较主要结构（地点/主讲人/时间/题目），
一致则放行；不一致则调用第二个大模型（B）读原文裁决，且保守偏向规则。

2026-09-02 下午修订（字段分层，实测 maths 8781 后确立）：
- 融合一律「仅填空」：规则已有值的字段 A 绝不覆盖（含职称/单位/摘要/简介）。
  职称与单位属填空型字段——规则没抓到时由 A 补上，规则已有则保留规则值。
- 每个从 A 采纳的值都必须通过 snippet 溯源闸门（值能在原文中找到出处），
  否则判为幻觉并记入 llmRejected。这是可规模化的信任机制，替代人工全量抽查。
- 发布时间不接入本模块：实测其位于 div.meta 图标区、不在 body_text 内，A 看不到；
  且发布时间是删除依据，须保持确定性规则。

铁律保障（用户硬性要求"大模型可能失效，规则必须独立可用"）：
- 规则结果 result 在 parse_detail 内已先行算出，本模块永不阻塞、永不空库；
- 若 A 为 None（无 key / 超时 / 异常）→ 直接保留规则结果，llmTextEnhanced=False；
- 若 A 与规则分歧，B 未明确高置信支持 A → 保留规则，仅打 needsHumanReview 标记。

海报页不接入本模块：海报文字在图片里，规则无法提供 ground truth，仍走 parsers 的
VLM 路线（VLM 主 + 第二 VLM 备份 + RapidOCR 兜底）。
"""

import datetime
import re

from llm_provider import _unwrap


# ---------------------------------------------------------------------------
# 字段展平：把模型返回的 {"value":..,"snippet":..} 形态转成纯值 dict
# ---------------------------------------------------------------------------
def flatten_fields(fields):
    """{'speaker':{'value':'温永立','snippet':'...'}} -> {'speaker':'温永立','speakerSnippet':'...'}。"""
    out = {}
    if not isinstance(fields, dict):
        return out
    for k, v in fields.items():
        val, snip = _unwrap(v)
        if val is None:
            continue
        if isinstance(val, (dict, list)):
            continue
        out[k] = val
        if snip:
            out[k + 'Snippet'] = snip
    return out


# ---------------------------------------------------------------------------
# 语义归一（比较用，不对原结果做改动）
# ---------------------------------------------------------------------------
_SPEAKER_TITLE_NOISE = ('教授', '研究员', '副教授', '讲师', '博士', '院士', '老师',
                        '主任', '院长', '所长', '博导', '硕导', '助理', '工程师',
                        '专家', '先生', '女士', '博士后')


def _norm_speaker(s):
    if not s:
        return ''
    s = re.sub(r'\s+', '', str(s))
    s = re.sub(r'[（(].*?[)）]', '', s)  # 去单位括号
    for n in _SPEAKER_TITLE_NOISE:
        s = s.replace(n, '')
    return s.strip()


def _norm_location(s):
    if not s:
        return ''
    s = re.sub(r'\s+', '', str(s))
    s = s.replace('室', '').replace('房', '')  # 细微房号差异忽略
    for kw in ('华南师范大学', '大学城', '石牌校区', '石牌', '汕尾', '佛山', '校区'):
        s = s.replace(kw, '')
    return s.strip()


def _norm_date(s):
    if not s:
        return None
    m = re.search(r'(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})', str(s))
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _edit_distance(a, b):
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            cur.append(min(cur[-1] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = cur
    return prev[-1]


def _similar_topic(a, b):
    if not a or not b:
        return False
    a, b = str(a), str(b)
    if a in b or b in a:
        return True
    return _edit_distance(a, b) <= max(2, min(len(a), len(b)) // 3)


def _split_speakers(s):
    if not s:
        return set()
    return {_norm_speaker(x) for x in re.split(r'[、,，/]', str(s)) if _norm_speaker(x)}


# ---------------------------------------------------------------------------
# 主要结构比较：返回差异字段列表（空 = 一致）
# 只在"两者都能产出"的共同字段上比较，规则缺失的字段不报差异（否则每页都误触发）。
# ---------------------------------------------------------------------------
def compare_struct(rule, llm):
    diffs = []
    rs, ls = _split_speakers(rule.get('speaker')), _split_speakers(llm.get('speaker'))
    if rs and ls and not (rs & ls):
        diffs.append('speaker')
    rd, ld = _norm_date(rule.get('lectureStart')), _norm_date(llm.get('lectureStart'))
    if rd and ld and rd != ld:
        diffs.append('lectureStart')
    rl, ll = _norm_location(rule.get('location')), _norm_location(llm.get('location'))
    if rl and ll and rl != ll:
        diffs.append('location')
    if rule.get('topic') and llm.get('topic') and not _similar_topic(rule.get('topic'), llm.get('topic')):
        diffs.append('topic')
    return diffs


# ---------------------------------------------------------------------------
# 融合：采用 A 的丰富结果（守卫时间，防幻觉覆盖规则已确认的时间）
# ---------------------------------------------------------------------------
_NOISE = ('null', 'None', '无', '暂无', 'N/A', 'na', '-', '—')


def _norm_for_match(s):
    """归一化用于溯源匹配：去空白 + 全角转半角（body_text 已过 N1 归一，两侧须同口径）"""
    if not s:
        return ''
    s = re.sub(r'\s+', '', str(s))
    return ''.join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in s)


def _snippet_ok(snippet, body_text):
    """溯源闸门：A 给的值必须能在原文中找到出处，否则判为幻觉、拒绝采用。

    没附 snippet、或原文里匹配不上 -> 一律拒绝（而不是照单全收）。
    """
    sn = _norm_for_match(snippet)
    if len(sn) < 4:
        return False
    bt = _norm_for_match(body_text)
    if sn in bt:
        return True
    # 容忍 A 对 snippet 的轻微省略：首尾各取一片都命中，才算溯源成功
    return len(sn) >= 12 and sn[:6] in bt and sn[-6:] in bt


# 单位字段常被后续元数据标记污染（源页常见「XX大学)日期:3月3日」粘连），填入前截断
_AFFIL_CUT = re.compile(r'(日期|时间|地点|主持人|主讲人|报告人|讲座|摘要|腾讯|会议|线上)\s*[:：]')


def _clean_affiliation(v):
    if not v:
        return ''
    m = _AFFIL_CUT.search(str(v))
    return (str(v)[:m.start()] if m else str(v)).strip(' )）:：-—、,，')


# 单位字段质量校验：A 偶发把职称片段（如"助理"）误填进 affiliation，
# 必须含机构后缀且不能是纯职称词，否则拒绝并回到规则兜底。
_AFFIL_INVALID = re.compile(
    r'^(教授|副教授|助理教授|助理|讲师|研究员|副研究员|助理研究员|博士|博士后|院士|主任|院长|所长|老师|先生|女士)$'
)
# 合法机构标识词（白名单）。覆盖：高校/科院/实验室/研究所/中小学/企业/协会/博物馆/政府/
# 医院等中文机构类型，中文高校常用缩写（交大/科大/理工/中科院…），以及英文与多语种
# （葡/德/法/西/荷/意等）高校与研究机构的常见写法。纯职称词由上方 _AFFIL_INVALID 黑名单拦截。
# 额外规则：以「院」结尾即视为合法（学院/研究院/医院/书院/量子院/广医三院 等；职称片段多以
# 「员/士」结尾，不会误放）。
_AFFIL_VALID_SUFFIX = (
    # 中文机构类型（完整词）
    '大学', '学院', '研究院', '研究所', '研究室', '学部', '系', '中心',
    '实验室', '所', '医院', '中学', '小学', '学校', '教育',
    '公司', '集团', '企业', '协会', '学会', '研究会',
    '博物馆', '银行', '政府', '机关', '报社', '出版社',
    '画院', '幼儿园', '文联', '科技', '网络',
    # 中文机构缩写（高校/科研院所常用简称）
    '交大', '科大', '工大', '师大', '理工', '医科', '农大', '财大',
    '财经', '政法', '中科院', '社科院', '科院',
    # 外文机构类型（含非英文语种）
    'University', 'College', 'Institute', 'School', 'Department',
    'Centre', 'Center', 'Laboratory', 'Lab',
    'Universidade', 'Universität', 'Université', 'Universidad',
    'Universiteit', 'Università', 'Hochschule', 'Polytechnic',
    'Politecnico', 'Academy', 'Akademie', 'Instituto', 'Institut',
    'École', 'Ecole', 'Conservatory', 'Hospital',
)


def _is_valid_affiliation(v):
    if not v or len(v.strip()) < 3:
        return False
    v = v.strip()
    if _AFFIL_INVALID.match(v):
        return False
    # 以「院」结尾（学院/研究院/医院/书院/量子院/广医三院 等；职称片段多「员/士」结尾，安全）
    if v.endswith('院'):
        return True
    # 全大写外文缩写（如 NIDA），多为机构 acronym；混合大小写的人名（ZhongshanLi）不匹配
    if re.fullmatch(r'[A-Z]{2,10}', v):
        return True
    lowered = v.lower()
    return any(s.lower() in lowered for s in _AFFIL_VALID_SUFFIX)


# 主办校名（页面框架常出现，易被模型 A 误当主讲人单位）。蔡瑞初 cs/5826 实战：页面仅
# 「报告人：蔡瑞初 教授」无单位，模型 A 把页眉/页脚的「华南师范大学」当单位填入；snippet
# 溯源闸门无法识别（校名每页必现）。故增加 host 守卫：裸主办校名（无更具体院系后缀）视为
# 页面框架，拒填。真实本校主讲人单位多为「华南师范大学XX学院」（含院系后缀），不受影响。
_AFFIL_HOST_NAMES = ('华南师范大学', 'south china normal university', 'scnu')


def _is_host_affiliation(v):
    if not v:
        return False
    return v.strip().lower() in _AFFIL_HOST_NAMES


# ---------------------------------------------------------------------------
# 纯规则兜底：模型 A 偶发抽不到 affiliation/title 时，从已有 speakerBio 开头片段
# 正则提取。华师讲座简介通用格式为「姓名,单位职称,...」，此兜底确定性、不依赖模型可用性，
# 仅在 A 仍未提供时触发，绝不覆盖已有值。搜索范围限定 bio 前 40 字（姓名+单位职称通常在此），
# 避免误抓后文的「博士毕业于 XX 大学」等历史单位。
#
# 2026-09-02 扩展：同时支持英文/中英混合单位（如 New York University Abu Dhabi）。
# ---------------------------------------------------------------------------
_AFFIL_RE = re.compile(
    r'([\u4e00-\u9fa5]{2,6}大学[\u4e00-\u9fa5]{1,8}?(?:学院|研究院|学部|系|中心))'
    r'|([\u4e00-\u9fa5]{2,10}?(?:大学|研究院|研究所)(?:[\u4e00-\u9fa5]{0,6}?(?:分校|校区|学部|学院|系|中心))?)'
    r'|([A-Za-z][A-Za-z\s]*(?:University|College|Institute|School|Department|Centre|Center|Laboratory|Lab)(?:\s+(?:of|and|&|at|in|[A-Za-z]+)){0,8})'
)
_TITLE_RE = re.compile(
    r'(教授|副教授|研究员|副研究员|助理研究员|讲师|助理教授|博士后|博士|主任医师|副主任医师)'
)


# 规则兜底提取单位时，可能连带命中「毕业于/就读于/现为」等动词前缀或学历词，
# 需要剥掉这些非单位前缀，保留从机构名开始的部分。
_AFFIL_PREFIX_NOISE = re.compile(
    r'^(?:'
    r'博士|硕士|本科|研究生|'
    r'毕业|毕业于|就读于|获|获得|年获|年分别获得|年于|年在|年本科|'
    r'现为|现任|现任于|现任职|任职|就职于|任职于|工作于|就职|'
    r'是|曾|曾任职|曾在|曾任|曾为|于|在|分别|先后'
    r')\s*[于在,，]?\s*'
)
_AFFIL_VALID_KEYWORDS = (
    '大学', '学院', '研究院', '研究所', '学部', '系', '中心',
    'University', 'College', 'Institute', 'School', 'Department',
    'Centre', 'Center', 'Laboratory', 'Lab',
)


_AFFIL_EN_STOP_WORDS = {
    'of', 'at', 'in', 'and', '&', 'the', 'from', 'joined', 'worked', 'graduated',
    'as', 'a', 'an', 'to', 'for', 'with', 'by', 'is', 'was', 'has', 'have', 'had',
    'did', 'after', 'that', 'he', 'she', 'they', 'it', 'this', 'these', 'there',
    'born', 'received', 'earned', 'obtained', 'got', 'gotten', 'before', 'when',
    'where', 'who', 'which', 'while', 'during', 'until', 'since', 'between',
}


def _strip_affil_prefix(aff):
    """保留从第一个机构后缀词开头的部分，剥去前带动词/学历前缀。"""
    if not aff:
        return aff
    lowered = aff.lower()
    best = -1
    best_kw = ''
    for kw in _AFFIL_VALID_KEYWORDS:
        idx = lowered.find(kw.lower())
        if idx >= 0 and (best < 0 or idx < best):
            best = idx
            best_kw = kw
    if best < 0:
        return aff
    # 英文机构后缀：直接用 _AFFIL_RE 的英文分支重新提取，剥去连带动词/人名
    if re.match(r'^[A-Za-z]', best_kw):
        m = _AFFIL_RE.search(aff)
        if m and m.group(3):
            g3 = m.group(3).strip()
            # 从末尾跳过 stop words（介词/年份后的 in/as 等），再向前保留连续大写修饰词
            parts = g3.split()
            end_idx = len(parts) - 1
            while end_idx >= 0 and parts[end_idx].lower() in _AFFIL_EN_STOP_WORDS:
                end_idx -= 1
            if end_idx >= 0:
                start_idx = end_idx
                while start_idx > 0:
                    w = parts[start_idx - 1]
                    if w.lower() in _AFFIL_EN_STOP_WORDS:
                        break
                    if w and w[0].isupper():
                        start_idx -= 1
                        continue
                    break
                return ' '.join(parts[start_idx:end_idx + 1]).strip()
        return aff[best:best + len(best_kw)].strip()
    # 中文机构后缀：从机构名开始
    prefix = aff[:best]
    for sep in ('，', '、', ',', ' '):
        pos = prefix.rfind(sep)
        if pos >= 0:
            return aff[pos + 1:].strip()
    # 若机构词紧贴开头但前面只剩 1-2 个字的动词残片（如"获""在"），也剥掉
    if len(prefix) <= 2 and prefix and not re.match(r'^[\u4e00-\u9fa5]{2,}$', prefix):
        return aff[best:].strip()
    return aff


def _infer_affiliation(bio):
    if not bio:
        return ''
    # 优先匹配「现任/现为/任职于」等当前单位表达，避免把学历单位错当现单位。
    current_re = re.compile(
        r'(?:现为|现任|现任于|现任职|任职|就职于|任职于|工作于|就职)'
        r'\s*[于在,，]?\s*'
        r'(' + _AFFIL_RE.pattern + r')'
    )
    m = current_re.search(bio)
    if m:
        aff = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or '').strip()
        aff = _strip_affil_prefix(aff)
        aff = re.sub(r'(助理教授|教授|副教授|讲师|研究员|副研究员|助理研究员|博士后|博士|院士|主任|院长|所长|老师|先生|女士)$', '', aff).strip(' ,，')
        if _is_valid_affiliation(aff):
            return aff
    # 全英文 bio 且无现单位表达时：通常现单位在最后，取最后一个有效机构
    if not re.search(r'[\u4e00-\u9fa5]', bio):
        last = ''
        for m in _AFFIL_RE.finditer(bio):
            cand = (m.group(1) or m.group(2) or m.group(3) or '').strip()
            cand = _strip_affil_prefix(cand)
            cand = re.sub(r'(助理教授|教授|副教授|讲师|研究员|副研究员|助理研究员|博士后|博士|院士|主任|院长|所长|老师|先生|女士)$', '', cand).strip(' ,，')
            if _is_valid_affiliation(cand):
                last = cand
        if last:
            return last
    # 回退：从 bio 开头提取单位
    head = bio[:80]
    m = _AFFIL_RE.search(head)
    if not m:
        return ''
    aff = (m.group(1) or m.group(2) or m.group(3) or '').strip()
    # 先剥开头常见动词/学历前缀（可循环 3 次）
    for _ in range(3):
        new_aff = _AFFIL_PREFIX_NOISE.sub('', aff)
        if new_aff == aff:
            break
        aff = new_aff
    aff = _strip_affil_prefix(aff)
    # 清理尾部职称词
    aff = re.sub(r'(助理教授|教授|副教授|讲师|研究员|副研究员|助理研究员|博士后|博士|院士|主任|院长|所长|老师|先生|女士)$', '', aff).strip(' ,，')
    return aff


def _infer_title(bio):
    if not bio:
        return ''
    head = bio[:40]
    for m in _TITLE_RE.finditer(head):
        t = m.group(1)
        # 排除「博士毕业于 / 博士学位 / 博士后」这类非当前职称的误匹配
        if t == '博士' and any(k in head[m.end():m.end() + 3]
                               for k in ('毕业', '学位', '后')):
            continue
        return t
    return ''


def _apply_bio_fallback(result):
    """规则兜底：模型 A 未提供 affiliation/title 时，从已有 speakerBio 开头片段正则提取。

    华师讲座简介通用格式为「姓名,单位职称,...」，此兜底确定性、不依赖模型可用性，
    仅在字段仍为空时触发，绝不覆盖已有值。需在 A 成功融合后、以及 A 完全失败提前
    return 前各调用一次（A 失败时 apply 不会进入 _merge，兜底必须独立存在）。
    """
    if not (result.get('speakerAffiliation') or '').strip() and (result.get('speakerBio') or '').strip():
        _a = _infer_affiliation(result['speakerBio'])
        if _a:
            result['speakerAffiliation'] = _a
    if not (result.get('speakerTitle') or '').strip() and (result.get('speakerBio') or '').strip():
        _t = _infer_title(result['speakerBio'])
        if _t:
            result['speakerTitle'] = _t


# rich_only 模式只处理丰富/补全型字段，绝不碰结构字段（speaker/topic/location），
# 确保结构字段完全由规则主导。
_RICH_FIELDS = ('abstract', 'speakerBio', 'speakerTitle', 'speakerAffiliation')
_ALL_FIELDS = ('topic', 'speaker', 'speakerTitle', 'speakerAffiliation',
              'location', 'abstract', 'speakerBio')

# rich 字段边界截断：A 模型对 abstract/speakerBio 缺乏边界约束，会把后续「专家简介/
# 报告题目/报告时间」等段落一并吞入。复用 parsers 既有锚点词表做后处理兜底——在 A 返回值
# 填入 result 之前先截断到自然段落边界（遇到下个字段标题或章节序号即停）。正常干净提取
# 不含这些标记，截断函数不会破坏它；仅当 A 溢出时才生效。
_RICH_CUT = re.compile(
    r'\s*(?:'
    r'主讲人简介|报告人简介|主讲人简历|专家介绍|专家简介|个人简介|作者简介|'
    r'主讲人介绍|报告人介绍|主讲介绍|简历|Bio|'
    r'学术报告|报告题目|讲座题目|报告时间|讲座时间|报告地点|讲座地点|'
    r'报告专家|报告人[：:]|讲座专家|'
    r'报名时间|报名方式|联系方式|面向对象|参与方式|注意事项|交通指引|温馨提示|'
    r'会议时间|会议地点|会议简介|论坛简介|沙龙简介|研讨会议程|'
    r'主办单位|承办单位|协办单位|'
    r'资讯及通知|相关新闻|最新动态|推荐阅读|相关文章|附件下载|相关链接|通知公告|'
    r'[一二三四五六七八九十百零0-9]+[、.．](?!\d)|第.{1,6}期'
    r')'
)


def _truncate_rich_text(v):
    """把 abstract/speakerBio 截断到自然段落边界，去除 A 溢出吞入的后续段落。"""
    if not v:
        return v
    m = _RICH_CUT.search(str(v))
    if m:
        return str(v)[:m.start()].strip()
    return str(v).strip()


def _merge_a_into_result(result, a, body_text, default_year=None, publish_time=None,
                         title_year=None, url_year=None, rich_only=False):
    """仅填空融合：规则已有值的字段一律不动（铁律：不破坏已提取值）。

    规则为空才考虑 A 的值，且必须通过 snippet 溯源闸门；被闸门拦下的字段名
    记入 llmRejected，便于事后统计 A 的幻觉率。
    rich_only=True 时只处理丰富/补全型字段（abstract/speakerBio/职称/单位），
    不碰结构字段（speaker/topic/location），确保结构字段完全由规则主导。
    """
    rejected = []
    _fields = _RICH_FIELDS if rich_only else _ALL_FIELDS
    for fld in _fields:
        cur = (result.get(fld) or '').strip()
        lv = (a.get(fld) or '').strip()
        if fld in ('abstract', 'speakerBio'):
            # A 主导（用户路线「摘要/简介 A 主导」）：abstract/speakerBio 以 A 的干净值为准，
            # 规则仅作兜底。A 值须先截断到段落边界（去溢出），再经 snippet 溯源闸门验证
            # （证明确系网页原文、非幻觉）才采用；A 无值或溯源失败则保留规则值，不污染。
            if not lv or lv in _NOISE:
                continue  # A 无值 -> 保留规则
            lv = _truncate_rich_text(lv)
            if not lv or lv in _NOISE:
                continue
            if not _snippet_ok(a.get(fld + 'Snippet'), body_text):
                rejected.append(fld)  # A 溯源失败 -> 保留规则
                continue
            result[fld] = lv
            continue
        # 其余字段（职称/单位/结构字段）：规则已有值不覆盖（仅填空补全）
        if cur and cur not in _NOISE:
            continue
        if not lv or lv in _NOISE:
            continue
        if fld == 'speakerAffiliation':
            lv = _clean_affiliation(lv)
            if not lv or not _is_valid_affiliation(lv) or _is_host_affiliation(lv):
                if lv:
                    rejected.append(fld)
                continue
        if not _snippet_ok(a.get(fld + 'Snippet'), body_text):
            rejected.append(fld)
            continue
        result[fld] = lv
    if rejected:
        result['llmRejected'] = '|'.join(rejected)

    # 纯规则兜底：模型 A 偶发抽不到 affiliation/title 时，从已有 speakerBio 开头片段
    # 正则提取（确定性、不依赖模型可用性）。仅在 A 仍未提供时触发，绝不覆盖已有值。
    _apply_bio_fallback(result)

    # 时间守卫：仅当规则时间是占位/缺失且年份与 A 一致时，才采用 A 的精确时刻。
    # 规则已有具体时间或年份不一致 -> 完全保留规则时间（防 LLM 年份/时刻幻觉）。
    _rule_start = result.get('lectureStart')
    _rule_year = None
    _rule_has_time = False
    if _rule_start:
        try:
            _rs = datetime.datetime.fromisoformat(str(_rule_start))
            _rule_year = _rs.year
            _rule_has_time = not (_rs.hour == 0 and _rs.minute == 0 and _rs.second == 0)
        except Exception:
            pass
    _ls_raw = a.get('lectureStart') or a.get('start')
    if _ls_raw and str(_ls_raw).strip() not in ('', 'null', 'None'):
        try:
            _ls = datetime.datetime.fromisoformat(str(_ls_raw).replace('T', ' ').replace('Z', ''))
            _llm_year = _ls.year
            _now = datetime.datetime.now()
            _year_lo, _year_hi = 2018, _now.year + 2
            if _year_lo <= _llm_year <= _year_hi:
                _year_match = (_rule_year is None) or (_rule_year == _llm_year)
                _rule_is_placeholder = (_rule_start is None) or (not _rule_has_time)
                if _year_match and _rule_is_placeholder:
                    result['lectureStart'] = _ls.isoformat(sep=' ')
                    _le_raw = a.get('lectureEnd') or a.get('end')
                    if _le_raw and str(_le_raw).strip() not in ('', 'null', 'None'):
                        try:
                            _le = datetime.datetime.fromisoformat(
                                str(_le_raw).replace('T', ' ').replace('Z', ''))
                            if _year_lo <= _le.year <= _year_hi:
                                result['lectureEnd'] = _le.isoformat(sep=' ')
                        except Exception:
                            pass
        except Exception:
            pass  # A 时间解析失败 -> 保留规则值


# ---------------------------------------------------------------------------
# 主调度：规则常算保底 + A 优先 + 分歧调 B 裁决（保守偏向规则）
# ---------------------------------------------------------------------------
def apply_llm_text_hybrid(result, body_text, url, provider, judge,
                          default_year=None, publish_time=None,
                          title_year=None, url_year=None, rich_only=False):
    """双轨解析 + 分歧裁决。原地修改 result 并打溯源标记，返回 result。

    provider: 模型 A（ModelProvider）；judge: 裁决模型 B（ModelProvider 或 None）。
    rich_only=True：仅把 A 的摘要/简介（及规则空的职称/单位）填空融合，
        结构字段完全由规则主导，不比较、不调 B 裁决。
    """
    result['llmTextEnhanced'] = False
    result['llmVerdict'] = None
    result['llmSpeakerSource'] = result.get('speakerSource')

    if provider is None:
        return result  # 无模型可用 -> 纯规则保底

    a_raw = None
    try:
        a_raw = provider.extract_text(body_text)
    except Exception:
        a_raw = None
    if not a_raw:
        # A 失效（含全 null / 返回非 JSON 被拒）-> 规则保底，但先用 bio 兜底补单位/职称
        _apply_bio_fallback(result)
        return result

    a = flatten_fields(a_raw)

    if rich_only:
        # 丰富字段模式：直接 only-fill abstract/speakerBio/职称/单位，
        # 不比较结构字段、不调 B，结构字段完全由规则主导。
        _merge_a_into_result(result, a, body_text, default_year, publish_time,
                             title_year, url_year, rich_only=True)
        if result.get('abstract') or result.get('speakerBio'):
            result['llmTextEnhanced'] = True
            result['llmVerdict'] = 'rich-only'
        return result

    diffs = compare_struct(result, a)
    if not diffs:
        # 一致：采用 A 丰富结果（abstract/bio 等规则没有的字段直接采用）
        _merge_a_into_result(result, a, body_text, default_year, publish_time,
                             title_year, url_year)
        result['llmTextEnhanced'] = True
        result['llmVerdict'] = 'consistent'
        if a.get('speaker'):
            result['speakerSource'] = 'llm'
        return result

    # 分歧：调用 B 裁决（读原文 + 双方结果）
    verdict = {'verdict': 'unknown', 'fields': {}}
    if judge is not None:
        try:
            verdict = judge.extract_verdict(body_text, result, a) or verdict
        except Exception:
            verdict = {'verdict': 'unknown', 'fields': {}}
    result['llmVerdict'] = verdict.get('verdict', 'unknown')

    # 保守偏向规则：仅当 B 明确支持 llm 且给出采纳字段时才采用 A
    if verdict.get('verdict') == 'llm' and verdict.get('fields'):
        _merge_a_into_result(result, a, body_text, default_year, publish_time,
                             title_year, url_year)
        result['llmTextEnhanced'] = True
        if a.get('speaker'):
            result['speakerSource'] = 'llm'
    else:
        # 保留规则；标记需人工抽检（仅当确有差异）
        result['needsHumanReview'] = '|'.join(diffs)
    return result
