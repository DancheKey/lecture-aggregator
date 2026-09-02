# -*- coding: utf-8 -*-
"""规则 + 大模型 A 双轨解析与分歧裁决（仅文本页）。

对应 2026-09-02 方案：规则与 Agnes（A）并行识别，比较主要结构（地点/主讲人/时间/题目），
一致则采用 A 的丰富结果；不一致则调用第二个大模型（B）读原文裁决，且保守偏向规则。

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


def _merge_a_into_result(result, a, default_year=None, publish_time=None,
                         title_year=None, url_year=None):
    def _prefer(field):
        cur = (result.get(field) or '').strip()
        lv = (a.get(field) or '').strip()
        if lv and lv not in _NOISE:
            result[field] = lv

    for fld in ('topic', 'speaker', 'speakerTitle', 'speakerAffiliation',
                'location', 'abstract', 'speakerBio'):
        _prefer(fld)

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
                          title_year=None, url_year=None):
    """双轨解析 + 分歧裁决。原地修改 result 并打溯源标记，返回 result。

    provider: 模型 A（ModelProvider）；judge: 裁决模型 B（ModelProvider 或 None）。
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
        return result  # A 失效 -> 规则保底

    a = flatten_fields(a_raw)
    diffs = compare_struct(result, a)
    if not diffs:
        # 一致：采用 A 丰富结果（abstract/bio 等规则没有的字段直接采用）
        _merge_a_into_result(result, a, default_year, publish_time, title_year, url_year)
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
        _merge_a_into_result(result, a, default_year, publish_time, title_year, url_year)
        result['llmTextEnhanced'] = True
        if a.get('speaker'):
            result['speakerSource'] = 'llm'
    else:
        # 保留规则；标记需人工抽检（仅当确有差异）
        result['needsHumanReview'] = '|'.join(diffs)
    return result
