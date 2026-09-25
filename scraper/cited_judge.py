# -*- coding: utf-8 -*-
"""引证裁决（Cited Verdict）生产模块 —— v2 值级终审裁判。

来源与定位
----------
由离线试点 `tmp/cite_pilot.py` 的 v2.3（ValueFirstJudge + _v22_decide）于
2026-09-25 合入生产。核心思路与试点完全一致：

  B（模型 GLM）不再做「选 rule 还是选 llm」的二选一站队，而是**基于原文语义
  独立给出每个争议字段的唯一正确值 value**，并附原文逐字 citation。值级闸门
  （逐字溯源 + 词边界 + 合理性 + 防信息丢失）通过即采纳，否则保留规则值。

  采纳判定集中在 `_v22_decide` 唯一实现里（运行时与离线回放共用同一段代码，
  防两处漂移——v2.2 复盘的教训）。

为什么不改 hybrid.py
--------------------
本模块对外只暴露 `extract_verdict(body_text, rule_fields, llm_fields)`，
返回值形态与旧 judge 完全兼容（`{'verdict','fields'}`），并通过**原地替换
llm_fields**把 B 的值注入 A 的候选，使主链路的溯源闸门/专项守卫照常运行。
因此 `hybrid.apply_llm_text_hybrid` 无需任何改动即可切换到新机制。

开关
----
`SCNU_JUDGE_MODE=cited|legacy`  默认 cited（启用本机制）；legacy 回退旧选边裁决。
`SCNU_JUDGE_SE=0|1`             默认 1：让 B 同时输出 self_extract，保留
                                「规则与 A 都空时由 B 填空」的既有能力。
`SCNU_JUDGE_TRACE=<路径>`       非空时把每次裁决明细追加写入 jsonl，供审计。

失败即保底：B 调用异常 / JSON 解析失败 / 无字段过闸，一律保留规则值，
绝不让裁决失败改变数据。
"""
import json
import os
import re
import time
import unicodedata

import hybrid
import llm_provider
import timeparse

# 康煕部首还原：延迟从 parsers 取（单一事实源），避免顶层引入 parsers
# 重依赖（459KB）与潜在循环导入（parsers -> llm_provider -> 本模块）。
_NFKC_CACHE = {}


def _nfkc(s):
    if not s:
        return s
    fn = _NFKC_CACHE.get('fn')
    if fn is None:
        try:
            from parsers import _nfkc_radicals as _fn
        except Exception:
            def _fn(x):
                return x
        _NFKC_CACHE['fn'] = _fn
        fn = _fn
    return fn(s)


# 参与裁决的字段：与 hybrid.compare_struct 能产出的分歧字段完全对应
CITED_FIELDS = ('speaker', 'lectureStart', 'location', 'topic', 'speakerAffiliation')


# ---------------------------------------------------------------------------
# 归一化与闸门
# ---------------------------------------------------------------------------
def _cite_norm(s):
    """引证/值校验归一化：部首还原 + NFKC + hybrid 溯源归一 + 会议号形态等价。

    会议号 9 位连写与 3-3-3（含空格/连字符变体）在校验层等价，避免
    「腾讯会议548677284」vs「腾讯会议 548-677-284」型假性不匹配。
    """
    if not s:
        return ''
    s = _nfkc(str(s))
    s = unicodedata.normalize('NFKC', s)
    s = hybrid._norm_for_match(s)
    return re.sub(r'(\d{3})[- ]?(\d{3})[- ]?(\d{3})', r'\1\2\3', s)


def cite_ok(citation, body_text):
    """引证硬闸门：citation 归一化后须为原文归一化文本的子串。"""
    sn = _cite_norm(citation)
    if len(sn) < 4:
        return False
    return sn in _cite_norm(body_text)


def _val_in_body(val, body_text):
    """value 逐字校验：与 cite_ok 同归一化，但最短长度放宽到 2（2 字人名合法）。

    额外剥离引号类标点：B 常把「"平台名"科研应用」摘成去引号形态，语义等价，
    不应因引号差异判不在原文。
    """
    _Q = re.compile('["“”„‟\'‘’「」『』]')
    sn = _Q.sub('', _cite_norm(val))
    if not sn or len(sn) < 2:
        return False
    return sn in _Q.sub('', _cite_norm(body_text))


def _word_char(ch):
    return bool(re.match(r'[\u4e00-\u9fff\u3400-\u4dbfA-Za-z0-9]', ch or ''))


# ---- 值级 suspect（对 B 给的 value 用）----
_LATIN_RE = re.compile(r'[A-Za-z]')
_JOB_TITLE_TAIL = re.compile(
    '(所长|副所长|馆长|副馆长|院长|副院长|主任|副主任|总经理|副总经理|'
    '董事长|总裁|副总裁|总监|经理|教授|副教授|研究员|副研究员|博士|硕士)$')

# 职务尾剥离路径的 right 边界放行字符（职务词首字）：源页「单位+职务+姓名」
# 连写形态（如"东莞市图书馆馆长李东来"）中，单位值后紧跟职务词首字是正常的。
_JOB_HEAD_CHARS = set('所馆院室总裁教研博副主董')


def _strip_job_title(val):
    """剥离单位值尾部的职务词（用户口径：单位字段不带职位）。

    可叠加多层；剥离后非空且与原文不同才返回（否则返回 ''）。
    """
    out = str(val or '').strip()
    prev = None
    while prev != out:
        prev = out
        m = _JOB_TITLE_TAIL.search(out)
        if m:
            out = out[:m.start()].rstrip(' ，,、')
    return out if out and out != val else ''


def _val_boundary_ok_stripped(val, body_text):
    """职务尾剥离路径专用边界：left 须良好；right 允许紧跟职务词首字或非词字符。"""

    def _ws(s):
        return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', _nfkc(str(s or ''))))

    nb, nv = _ws(body_text), _ws(val)
    if not nv:
        return False
    for m in re.finditer(re.escape(nv), nb):
        p, q = m.start(), m.end()
        left_ok = p == 0 or not _word_char(nb[p - 1])
        right_ok = q >= len(nb) or not _word_char(nb[q]) or nb[q] in _JOB_HEAD_CHARS
        if left_ok and right_ok:
            return True
    return False


def _val_boundary_ok(val, body_text):
    """词边界检查：在保留空白形态的归一化文本上做（NFKC + 部首还原），
    避免吃空格造成「李芳芳 产业」粘连误判。存在任一边界良好出现位置 → True。

    匹配前把双方空白统一折叠为单空格——B 值常用空格、正文提取文本常在标签
    边界产生换行，空白形态不一致会假性匹配失败。
    """

    def _ws(s):
        return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', _nfkc(str(s or ''))))

    nb, nv = _ws(body_text), _ws(val)
    if not nv:
        return False
    for m in re.finditer(re.escape(nv), nb):
        p, q = m.start(), m.end()
        if (p == 0 or not _word_char(nb[p - 1])) and \
                (q >= len(nb) or not _word_char(nb[q])):
            return True
    return False


def _vf_suspect(field, val, body_text):
    """v2.1 值级 suspect：True=拒绝采纳；None=该字段不适用本校验。"""
    if not val:
        return False
    if field == 'speaker':
        if _LATIN_RE.search(val):
            # 英文姓名：字母词 + 常见分隔符，不再走中文 plausible 校验
            if not re.fullmatch(r"[A-Za-z][A-Za-z .'\-()]{1,60}", val):
                return True
            return not _val_boundary_ok(val, body_text)
        if not hybrid._is_plausible_speaker(val, body_text or ''):
            return True
        return not _val_boundary_ok(val, body_text)
    if field == 'speakerAffiliation':
        if _LATIN_RE.search(val):
            # 英文机构：长度不设限（常超 40 字符），边界过即放行
            return not _val_boundary_ok(val, body_text)
        if len(val) > 40:
            return True
        # 职务尾收窄：含《》或顿号的复合形态（"…所长、《…》编辑部主任"）
        # 是合法机构+职务串，不再判疑
        if _JOB_TITLE_TAIL.search(val or '') and '《' not in val and '、' not in val:
            return True
        if not hybrid._is_valid_affiliation(hybrid._clean_affiliation(val)):
            return True
        return False
    return None  # location/topic/lectureStart 只做 info-loss 保底


# ---- 输出格式归一 + 防信息丢失 ----
_MEETING_INFO = re.compile(
    '会议号|会议\\s*ID|会议密码|密码|\\d{3}[ \\-]?\\d{3}[ \\-]?\\d{3}|\\d{9}')
_MEETING_PREFIX = re.compile('腾讯会议\\s*[:：]?\\s*(?=\\d)')
_MEETING_NUM = re.compile(r'(?<!\d)(\d{3})[ \-]?(\d{3})[ \-]?(\d{3})(?!\d)')


def _norm_meeting_format(val):
    """腾讯会议号统一为「腾讯会议 xxx-xxx-xxx」（用户口径 3-3-3）。"""
    if not val or '腾讯会议' not in val:
        return val
    out = _MEETING_PREFIX.sub('腾讯会议 ', val)
    return _MEETING_NUM.sub(r'\1-\2-\3', out)


def _is_info_loss(newv, orig):
    """value 是否丢失 orig 的实义信息（收窄版）：仅当 newv 是 orig 的归一化
    子串、明显更短，且 orig 含会议号/密码等实义标记而 newv 不含时判定丢失。

    普通「去脏尾」缩短（"学院301会议室作者"→"学院301会议室"）不拦截。
    """
    on = _cite_norm(orig or '')
    vn = _cite_norm(newv or '')
    if not on or not vn:
        return False
    if vn in on and len(vn) < len(on) * 0.9:
        if _MEETING_INFO.search(on) and not _MEETING_INFO.search(vn):
            return True
    return False


def _resolve_time_val(val, rule_start):
    """把 B 摘的时间片段解析为规范 lectureStart。失败返回 None。

    优先 timeparse.parse_cn_time；纯时段（无日期）借规则值日期合成。
    """
    import datetime
    try:
        t = timeparse.parse_cn_time(val)
    except Exception:
        t = None
    if t and t.get('start'):
        if not t.get('has_time'):
            return None  # 只有日期没有时间，信息不足以覆盖
        return t['start'].strftime('%Y-%m-%d %H:%M:%S')
    m = re.search('(\\d{1,2})[:：](\\d{2})', val or '')
    if m and rule_start:
        try:
            base = datetime.datetime.strptime(str(rule_start)[:10], '%Y-%m-%d')
        except Exception:
            return None
        return base.replace(hour=int(m.group(1)),
                            minute=int(m.group(2))).strftime('%Y-%m-%d %H:%M:%S')
    return None


# ---------------------------------------------------------------------------
# 采纳判定（唯一实现，运行时与离线回放共用）
# ---------------------------------------------------------------------------
def _v22_decide(field, val, cite, body_text, rule_val, a_val):
    """v2.2 采纳判定。返回 (ok, applied_value, extras)。

    v2.3：入口先做腾讯会议号输出格式归一；suspect 拦截后对
    speakerAffiliation 走「职务尾剥离再验证」（用户口径：单位不带职位）。
    """
    extras = {}
    if not val:
        return False, '', {'blocked': 'empty'}
    val = _norm_meeting_format(val)
    vib = _val_in_body(val, body_text)
    ck = cite_ok(cite, body_text) if cite else False
    suspect = _vf_suspect(field, val, body_text)
    extras.update({'val_in_body': vib, 'cite_ok': ck, 'suspect': suspect})
    b_is_a = bool(a_val) and val == a_val
    if b_is_a:
        # 双通道一致：B 复述 A 值。保留 vib 硬闸，跳过 suspect/cite_ok（仅记录）。
        # speaker 额外保留边界检查（防"向大家分"型截断值被 A/B 双双复述）。
        ok = vib and (field != 'speaker' or _val_boundary_ok(val, body_text))
        if suspect is True:
            extras['suspect_note'] = 'b_equals_a_fastpath'
    else:
        # B 原创值：vib + suspect 硬闸；cite_ok 仅记录不拦截
        ok = vib and suspect is not True
        if not ok and vib and field == 'speakerAffiliation' and suspect is True:
            stripped = _strip_job_title(val)
            if stripped and _val_boundary_ok_stripped(stripped, body_text):
                extras['title_stripped_from'] = val
                val = stripped
                ok = True
                extras['suspect_note'] = 'job_title_stripped'
    newv = val
    if ok and field == 'lectureStart':
        resolved = _resolve_time_val(val, rule_val)
        if resolved is None:
            extras['blocked'] = 'timeparse_fail'
            return False, '', extras
        newv = resolved
    if ok:
        if _is_info_loss(newv, rule_val):
            extras['blocked'] = 'info_loss_vs_rule'
            return False, '', extras
        if a_val is not None and newv != a_val and _is_info_loss(newv, a_val):
            extras['blocked'] = 'info_loss_vs_a'
            return False, '', extras
    return ok, newv, extras


# ---------------------------------------------------------------------------
# 提示词（与 tmp/cite_pilot.py v2.3 一致；仅按需追加 self_extract 段）
# ---------------------------------------------------------------------------
V2_SYSTEM = (
    '你是讲座信息抽取的终审裁判。给定网页原文与若干争议字段的双候选'
    '（rule=规则解析候选，llm=大模型解析候选），你必须基于原文独立判断'
    '每个字段的正确值，不偏袒任何一方候选。'
)

V2_USER_TMPL = (
    '网页原文：\n{body}\n\n'
    '待裁决的争议字段（每行：字段名 | 规则候选 rule | 模型候选 llm）：\n{fields}\n\n'
    '要求：\n'
    '1. 逐字段裁决。对每个字段，基于原文确定唯一正确值 value：'
    '若 rule 候选正确则 value 取 rule 值；若 llm 候选正确则 value 取 llm 值；'
    '若两者都不完整或不准确，value 直接从原文提取正确值；\n'
    '2. value 必须是网页原文的逐字片段（不得改写、规范化、翻译、增删任何字）；'
    '时间字段摘原文时间片段即可（如"2024年7月6日14：30"或"13:30-14:10"）；\n'
    '3. citation 必须是包含 value 的原文逐字片段（可带前后文提供上下文）；\n'
    '4. 若原文确实没有该字段的信息，value 给空字符串 ""；\n'
    '5. reason 用一句话说明理由。\n'
    '{se_req}'
    '输出严格 JSON（以 {{ 开头、}} 结尾，不要 markdown 代码块）：\n'
    '{{"judgements":[{{"field":"...","value":"...","citation":"...",'
    '"reason":"..."}}]{se_tail}}}'
)

# self_extract 追加段（SCNU_JUDGE_SE=1 时启用）：保留「规则与 A 都空时由 B 填空」
# 的既有能力（hybrid._fill_from_self_extract），避免合入后能力回退。
_SE_REQ = (
    '6. 另外，请在 self_extract 中给出你从原文独立提取的字段'
    '（speaker/speakerTitle/affiliation/lectureStart/lectureEnd/location/topic/title），'
    '原文没有的字段填 null；\n'
)
_SE_TAIL = (
    ',"self_extract":{"speaker":null,"speakerTitle":null,"affiliation":null,'
    '"lectureStart":null,"lectureEnd":null,"location":null,"topic":null,"title":null}'
)


def _se_enabled():
    return (os.environ.get('SCNU_JUDGE_SE') or '1').strip() not in ('0', 'false', 'no')


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
class CitedJudgeProvider:
    """引证裁决 provider：包装底层 B 模型 provider（需具备 `_post`）。

    接口兼容 `ModelProvider.extract_verdict`：
      返回 {'verdict': 'llm'|'rule'|'unknown', 'fields': {...}, ['self_extract': {...}]}
    - 有字段过闸 → 'llm' + fields（键为字段名，值为 'llm'）
    - 正常裁决但无一过闸 → 'rule'（语义：B 终审后维持规则值）
    - 调用/解析失败 → 'unknown'（机制未决，主链路按未获支持处理）
    """

    def __init__(self, base):
        self.base = base
        self.name = 'cited-' + str(getattr(base, 'name', 'unknown'))

    def _trace(self, url_hint, recs, status, verdict):
        """可选审计留痕：仅在 SCNU_JUDGE_TRACE 指定路径时写 jsonl。"""
        path = (os.environ.get('SCNU_JUDGE_TRACE') or '').strip()
        if not path:
            return
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'url': url_hint or '',
                    'status': status,
                    'judgements': recs,
                    'verdict_returned': verdict,
                }, ensure_ascii=False) + '\n')
        except Exception:
            pass

    def extract_verdict(self, body_text, rule_fields, llm_fields, *, temperature=0.0):
        diffs = hybrid.compare_struct(rule_fields, llm_fields)
        if not diffs:
            return {'verdict': 'rule', 'fields': {}}

        # 底层 provider 不支持原始 chat 调用（如 MockProvider）→ 原样委托，不包装
        if not hasattr(self.base, '_post'):
            return self.base.extract_verdict(body_text, rule_fields, llm_fields,
                                             temperature=temperature)

        lines = []
        for f in diffs:
            lines.append('- %s: rule=%s | llm=%s' % (
                f, json.dumps(rule_fields.get(f) or '', ensure_ascii=False),
                json.dumps(llm_fields.get(f) or '', ensure_ascii=False)))
        se_on = _se_enabled()
        user = V2_USER_TMPL.format(
            body=(body_text or '')[:6000], fields='\n'.join(lines),
            se_req=(_SE_REQ if se_on else ''),
            se_tail=(_SE_TAIL if se_on else ''))

        raw = None
        try:
            raw = self.base._post([{"role": "system", "content": V2_SYSTEM},
                                   {"role": "user", "content": user}], temperature)
        except Exception:
            raw = None
        parsed = llm_provider._parse_model_json(raw) if raw else None
        if not isinstance(parsed, dict):
            self._trace(None, [], 'parse_fail', {'verdict': 'unknown', 'fields': {}})
            return {'verdict': 'unknown', 'fields': {}}

        judgements = parsed.get('judgements')
        if not isinstance(judgements, list):
            self._trace(None, [], 'parse_fail_no_list',
                        {'verdict': 'unknown', 'fields': {}})
            return {'verdict': 'unknown', 'fields': {}}

        fields_force = {}
        recs = []
        for j in judgements:
            if not isinstance(j, dict):
                continue
            f = j.get('field')
            if f not in CITED_FIELDS or f not in diffs:
                continue
            val = str(j.get('value') or '').strip()
            cite = str(j.get('citation') or '').strip()
            reason = str(j.get('reason') or '')[:200]
            ok, newv, extras = _v22_decide(
                f, val, cite, body_text,
                rule_fields.get(f), llm_fields.get(f))
            rec = {'field': f, 'value': val, 'citation': cite,
                   'accepted': bool(ok), 'reason': reason}
            rec.update(extras)
            if ok:
                rec['applied_value'] = newv
                if newv != rule_fields.get(f):
                    llm_fields[f] = newv  # 原地替换：主链路闸门/守卫照常处理
                    fields_force[f] = 'llm'
                # newv == 规则值：B 认可规则值，无需改动
            recs.append(rec)

        verdict = {'verdict': 'llm' if fields_force else 'rule',
                   'fields': fields_force}
        se = parsed.get('self_extract')
        if isinstance(se, dict) and se:
            verdict['self_extract'] = se
        self._trace(None, recs, 'ok' if recs else 'empty', verdict)
        return verdict
