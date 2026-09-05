# -*- coding: utf-8 -*-
"""讲座文本解析的大模型抽象层（ModelProvider）。

对应 2026-09-02 讨论的「规则 + 大模型 A 双轨 + 分歧裁决 B」方案，本模块只负责
"如何与一个大模型对话、拿到结构化字段"，不关心规则解析与裁决逻辑（见 hybrid.py）。

设计要点：
- 模型可插拔：Agnes 是当前实现；换模型只需新增一个 ModelProvider 子类并改工厂，
  不动解析主干与裁决逻辑。
- extraction-only：系统提示词硬约束"只解析、不生成新数据"，降低幻觉（但规则仍作
  对照锚点，见 hybrid.py）。每个字段要求附 snippet 原文片段，便于溯源与抽检。
- 离线可测：MockProvider 不发起任何网络请求，用于验证双轨调度与裁决逻辑。
- 海报页（VLM）沿用 parsers._vlm_extract_fields 路线，不接入本模块（海报文字在
  图里，规则无法提供 ground truth，不适合接入 A/B 裁决）。

缓存：自带与 parsers 同目录的 data/.vlm_cache.json，但 key 加 "text:" 前缀与旧缓存
隔离，避免新旧字段格式相互覆盖。
"""

import abc
import hashlib
import json
import os
import re
import threading
import time

import requests


# ---------------------------------------------------------------------------
# extraction-only 系统提示词（硬约束：只解析、不生成）
# ---------------------------------------------------------------------------
EXTRACTION_ONLY_SYSTEM = """你是一个学术讲座信息抽取助手。严格遵守以下规则：
1. 你只做「识别并照抄」，绝不「生成/创作/补全」。网页里没有的东西一律返回 null，
   宁可字段留空也绝不能为了让字段有值而自行撰写任何内容。abstract 与 speakerBio
   必须原样照抄网页中对应的「摘要」「主讲人简介」段落，跟网页里面的内容一致，不得自行概括、
   总结、改写或续写，更不得无中生有；若网页无独立摘要/简介段落，对应字段返回 null，不要从其他段落拼凑。
   严禁生成「放之四海皆准」的模板化概括句——以下均为典型的虚构摘要，绝对不得输出：
   「随着大数据/互联网...」「本讲座将探讨量子计算/人工智能...」「This lecture will delve into...」
   「沙龙立足教育教学...」「群名称:...」「在其早期历程中...」等通用开场白或站点介绍性文字，
   即使你觉得"像摘要"也一律禁止生成；页面无本场专属摘要时 abstract 直接返回 null。
   「摘要/简介」仅指网页中针对本场讲座/这位主讲人的专属段落。站点通用文本——学院/书院/研究院/
   主办方/中心的介绍、学科或学院沿革、沙龙或系列的固定简介模板、QQ群信息、议程安排、页脚导航、
   版权声明——即使原样出现在网页里，也严禁填入 abstract/speakerBio（选错段落等同无中生有）；
   页面只有这类通用文本而没有本场专属摘要/简介时，对应字段一律返回 null。
2. 仅从给定正文中提取字段；原文没有的字段一律返回 null（不得省略键）。
3. 每个字段附 snippet：直接抄录该字段在原文中的原句片段（含前后少量上下文），用于溯源核验。
4. 输出严格 JSON（不要 markdown 代码块、不要解释），结构如下：
{
  "title": {"value": "讲座系列名或整篇标题", "snippet": "..."},
  "topic": {"value": "单场讲座题目，无则 null", "snippet": "..."},
  "speaker": {"value": "主讲人姓名（仅姓名，不含职称/单位）", "snippet": "..."},
  "speakerTitle": {"value": "主讲人当前职称，如 教授/副教授/研究员/讲师，无则 null；可从 speakerBio/abstract 中明确提到的职称提取", "snippet": "..."},
  "speakerAffiliation": {"value": "主讲人当前单位/院系，无则 null；若 speakerBio/abstract 中明确出现『XX大学XX学院』、『现任/现为 XX大学』等当前所属机构，必须提取出来", "snippet": "..."},
  "lectureStart": {"value": "ISO8601 如 2026-08-21 15:00，未知 null", "snippet": "..."},
  "lectureEnd": {"value": "ISO8601，未知 null", "snippet": "..."},
  "location": {"value": "地点（精确到楼栋房号，不含校名）", "snippet": "..."},
  "abstract": {"value": "摘要内容，无则 null", "snippet": "..."},
  "speakerBio": {"value": "主讲人简介，无则 null", "snippet": "..."}
}
5. 时间必须基于正文明确写出的日期与时间，不要猜测年份；年份无法确定时 lectureStart 用 null。
6. speaker 必须基于正文明确写出的主讲人姓名；正文无明确人名返回 null，严禁根据标题猜测。
6b. **英文 speaker 禁区**：英文语境下，speaker 只提取真实人名（如 "John Smith"、"刘潇屿"），**严禁把职位/头衔/机构名当作 speaker**，包括但不限于：Professor / Associate Professor / Postdoctoral Associate / Research Fellow / Director / Dean / Chair 等；若原文只出现 "Name + Title"，speaker 取 Name，Title 归 speakerTitle 字段。
7. abstract 与 speakerBio 只提取各自段落的正文，必须到此为止：遇到「主讲人简介/报告人简介/专家简介/
   个人简介/报告题目/报告时间/报告地点/报名方式/联系方式/面向对象」等后续字段标题，或「一、二、
   三、」等章节序号时立即截断，严禁把后续段落（简介、题目、时间、地点、报名方式等）并入本字段。此外，正文末尾的「欢迎老师/同学参加」「诚邀」「敬请期待」等邀请语，以及页脚「编辑：/审核：/摄影：」署名，均不属于讲座内容，遇到即从该处截断，不并入 abstract。此外，正文末尾的「欢迎老师/同学参加」「诚邀」「敬请期待」等邀请语，以及页脚「编辑：/审核：/摄影：」署名，均不属于讲座内容，遇到即从该处截断，不并入 abstract。
8. 输出必须是纯 JSON（以 { 开头、} 结尾）。严禁输出 markdown 列表（如 "- **题目**：..."）、
   加粗、代码块围栏或任何解释文字；若某字段无法从原文解析，返回该字段为 null 的 JSON，
   而不是用文字说明「无法提取」。
"""


_ABSTRACT_BOUNDS = (
    "附件", "附录", "课程名称", "授课对象", "报名方式", "报名链接", "参会方式",
    "联系方式", "欢迎各位", "欢迎老师", "欢迎同学", "欢迎广大", "欢迎感兴趣",
    "欢迎广大师生", "诚邀", "敬请期待", "编辑：", "审核：", "摄影：",
    "扫描二维码", "长按识别", "点击查看", "阅读原文", "更多资讯",
    "主办单位", "承办单位", "腾讯会议", "会议号", "Meeting ID", "Zoom",
    "直播链接", "观看方式",
)

def _truncate_abstract(text):
    """清理摘要正文后的邀请语/报名/附件/署名等模板尾巴（保留讲座内容本体）。"""
    if not text or not isinstance(text, str):
        return text
    pos = None
    for b in _ABSTRACT_BOUNDS:
        i = text.find(b)
        if i != -1 and (pos is None or i < pos):
            pos = i
    if pos is None:
        return text
    cut = text[:pos].rstrip()
    return cut if len(cut) >= 40 else text  # 护栏：边界词在头部则放弃截断


# ---------------------------------------------------------------------------
# JSON 解析（处理模型可能夹带的代码块围栏）
# ---------------------------------------------------------------------------
# 裁决员字段语义说明（拼接到 extract_verdict 的 system prompt）。
# 实测教训（bench_judge_model.py v1）：不说明字段语义时，裁决模型会认为
# "规则值信息更完整就更好"，把 LLM 提取的纯人名 speaker 错判为 rule 方。
# 补上语义后（bench v2）：glm-4-flash / glm-4.5-air / agnes-2.5-flash 全部判对。
_VERDICT_FIELD_SEMANTICS = (
    "\n\n【字段语义（重要，判定前必读）】\n"
    "- speaker = 主讲人**姓名本身**，是纯粹的人名。\n"
    "- speaker **不得包含**职称/职务/头衔（如 教授、副教授、研究员、院长、"
    "Vice President、Director、Professor、Dr. 等），也不得包含单位/机构名。\n"
    "- 职称与单位应归入 speakerTitle / affiliation 字段，不属于 speaker。\n"
    "- 判据：若一方的值只是另一方的『人名部分』（即另一方在其后拼接了职称/职务/单位），"
    "则**只含人名的那一方更准确**，即使它看起来「信息更少」。\n"
    "- lectureStart/lectureEnd = 讲座实际开始/结束时间。注意：时刻为 08:00 或 00:00 "
    "属**占位符，表示时刻未知**，只用于按日期比较——一方给出占位时刻不代表它认为讲座"
    "在那个时刻开始。对比时以**日期部分**为准，时刻格式不必强求一致。\n"
    "- topic = 讲座真正的题目；title = 通知页面的外包装标题（如「学术报告（第N期）」）。"
    "两者可以不同，都合法，都不算错。\n"
    "- location = 讲座地点（楼栋+房间号），不含学校名。\n"
    "- self_extract 是你自己的独立提取结果，仅作对比依据与留痕，不直接被采纳；"
    "最终采纳以 verdict / fields 为准。"
)


def _parse_model_json(text):
    if not text:
        return None
    t = text.strip()
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', t, re.DOTALL)
    if m:
        t = m.group(1)
    else:
        s = t.find('{')
        e = t.rfind('}')
        if s >= 0 and e > s:
            t = t[s:e + 1]
    try:
        return json.loads(t)
    except Exception:
        return None


def _parse_verdict_json(text):
    """裁决输出专用解析（方案A三步法配套，2026-09-05）。

    glm-4-flash 等模型按三步作答时会输出**多个** JSON 代码块：
    第一步 self_extract 一个、第三步裁决（含 verdict/fields）又一个。
    旧 _parse_model_json 非贪婪只取第一个块 -> 拿到无 verdict 的中间产物 ->
    解析"失败"保守回落 unknown，B 的正确裁决被丢弃（实测 12246 页踩中）。
    故此处从后往前找**含 verdict 键**的 JSON 块；无围栏时用 raw_decode
    从 '{"self_extract"/"verdict"' 起点做括号配对解析（可处理嵌套 fields）。
    """
    if not text:
        return None
    # 1) 有 ```json 围栏：从后往前找含 verdict 的块
    for b in reversed(re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)):
        try:
            obj = json.loads(b)
            if isinstance(obj, dict) and 'verdict' in obj:
                return obj
        except Exception:
            continue
    # 2) 无围栏：从 {"self_extract" / {"verdict" 起点做括号配对解析（支持嵌套）
    dec = json.JSONDecoder()
    for m in reversed(list(re.finditer(r'\{"(?:self_extract|verdict)"', text))):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
            if isinstance(obj, dict) and 'verdict' in obj:
                return obj
        except Exception:
            continue
    # 3) 兜底：旧行为（首个可解析 JSON）
    obj = _parse_model_json(text)
    return obj if isinstance(obj, dict) and 'verdict' in obj else None


def _unwrap(obj):
    """把 {"value":..,"snippet":..} 或裸值统一成 (value, snippet)。"""
    if isinstance(obj, dict) and 'value' in obj:
        return obj.get('value'), obj.get('snippet')
    return obj, None


# ---------------------------------------------------------------------------
# 自包含缓存（与 parsers 共享 data/.vlm_cache.json，key 加 "text:" 前缀隔离）
# ---------------------------------------------------------------------------
_CACHE_LOCK = threading.Lock()


def _cache_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), 'data', '.vlm_cache.json')


def _cache_get(key):
    try:
        with _CACHE_LOCK:
            p = _cache_path()
            if not os.path.exists(p):
                return None
            with open(p, encoding='utf-8') as f:
                c = json.load(f)
            return c.get(key)
    except Exception:
        return None


def _cache_set(key, val):
    try:
        with _CACHE_LOCK:
            p = _cache_path()
            c = {}
            if os.path.exists(p):
                try:
                    with open(p, encoding='utf-8') as f:
                        c = json.load(f)
                except Exception:
                    c = {}
            c[key] = val
            tmp = p + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(c, f, ensure_ascii=False)
            os.replace(tmp, p)
    except Exception:
        pass


def _fields_useful(f):
    if not isinstance(f, dict):
        return False
    for v in f.values():
        if isinstance(v, dict):
            val = v.get('value')
        else:
            val = v
        if val not in (None, '', 'null', 'None'):
            if str(val).strip():
                return True
    return False


# ---------------------------------------------------------------------------
# 环境读取（不依赖 python-dotenv，避免新增依赖；与 parsers 同约定）
# ---------------------------------------------------------------------------
_ENV_LOADED = False
_ENV = {}


def _load_dotenv():
    global _ENV_LOADED, _ENV
    if _ENV_LOADED:
        return _ENV
    _ENV_LOADED = True
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    env = {}
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    _ENV = env
    return env


def _get_env(name):
    v = os.environ.get(name)
    if v:
        return v
    return _load_dotenv().get(name)


# ---------------------------------------------------------------------------
# 主动限速
# ---------------------------------------------------------------------------
_RATE_LOCK = threading.Lock()
_NEXT_SLOT = {}  # channel -> 下一次可用 epoch（按通道分桶，文本与视觉互不拖累）


def _throttle(channel='agnes', rpm=None):
    """按 RPM 占用一个时间槽，在真实 HTTP 调用前生效（全局串行、按通道分桶）。

    仅靠 429 被动退避不足以保护批量任务：全库并发时会在短时间打出大量请求，
    先撞限流再重试反而更慢。这里按通道（agnes / zhipu 等不同 API 服务）分配时间槽，
    把速率压在阈值以内；文本与视觉调用计入各自服务的总 RPM，互不拖累。

    rpm 缺省读 LLM_RPM（文本，默认 20/分钟）；VLM 视觉通道调用方应显式传 VLM_RPM
    （默认 10/分钟，更保守，应对 glm-4v-flash 等视觉模型限流）。
    """
    try:
        if rpm is None:
            rpm = int(_get_env('LLM_RPM') or 20)
        else:
            rpm = int(rpm)
    except (TypeError, ValueError):
        rpm = 20
    if rpm <= 0:
        return
    slot = 60.0 / rpm
    with _RATE_LOCK:
        now = time.time()
        nxt = _NEXT_SLOT.get(channel, 0.0)
        start = max(now, nxt)
        _NEXT_SLOT[channel] = start + slot
        wait = start - now
        if wait > 0:
            time.sleep(wait)


# ---------------------------------------------------------------------------
# 负缓存（negative cache）：把「确认无价值」的解析结果也缓存，避免每轮批量对
# 解析失败/无结果的正文重复烧 token。
#   硬负：模型明确「非讲座/无信息」（HTTP 200 但字段全空）→ 常驻，不重试。
#   软负：网络/限流/超时等偶发失败 → 短 TTL（默认 1 天），到期自动重试，
#         防止「模型临时故障误标负」导致永久漏抓。
# 标记结构：{"__neg__": True, "ts": epoch, "hard": bool, "reason": str}
# ---------------------------------------------------------------------------
NEG_SOFT_TTL = int(_get_env('NEG_SOFT_TTL') or 86400)    # 软负默认 1 天
NEG_HARD_TTL = int(_get_env('NEG_HARD_TTL') or 2592000)  # 硬负默认 30 天（误标自愈，避免永久漏抓）


def _neg_marker(hard, reason):
    return {"__neg__": True, "ts": time.time(), "hard": bool(hard), "reason": reason}


def _is_neg(v):
    return isinstance(v, dict) and v.get('__neg__') is True


def _neg_expired(v):
    """负标记是否已过期需重试（软负过期返回 True；硬负/永久返回 False）。"""
    if not _is_neg(v):
        return False
    ttl = NEG_HARD_TTL if v.get('hard') else NEG_SOFT_TTL
    if ttl <= 0:
        return False
    return (time.time() - v.get('ts', 0)) > ttl


# ---------------------------------------------------------------------------
# Provider 抽象
# ---------------------------------------------------------------------------
class ModelProvider(abc.ABC):
    name = 'base'

    @abc.abstractmethod
    def extract_text(self, body_text, *, temperature=0.0):
        """从讲座正文抽取结构化字段。返回 dict（value/snippet 形态）或 None。"""
        raise NotImplementedError

    def extract_verdict(self, body_text, rule_fields, llm_fields, *, temperature=0.0):
        """分歧裁决：读原文 + 规则结果 + A 结果，返回
        {'verdict': 'rule'|'llm'|'unknown', 'fields': {...}}。
        默认实现返回 unknown（保守保留规则）。真实模型子类应重写。"""
        return {'verdict': 'unknown', 'fields': {}}


class AgnesProvider(ModelProvider):
    """Agnes-ai 文本通道（OpenAI 兼容 /v1/chat/completions）。当前默认文本识别模型。"""

    name = 'agnes'

    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key or _get_env('AGNES_API_KEY')
        self.base_url = (base_url or _get_env('AGNES_BASE_URL')
                         or 'https://api.agnes-ai.cn/v1/chat/completions')
        self.model = (model or _get_env('AGNES_MODEL') or 'agnes-2.5-flash')

    # --- 内部：带重试的 chat 调用 ---
    def _post(self, messages, temperature):
        if not self.api_key:
            return None
        _throttle(channel=self.name)
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        # 不显式构造 proxies 字典：交给 requests 按标准环境变量解析，
        # HTTP_PROXY/HTTPS_PROXY/NO_PROXY 均生效。本地若代理访问国内 LLM 异常，
        # 可设 NO_PROXY=open.bigmodel.cn,api.agnes-ai.cn 让其直连绕过坏节点。
        # （GitHub CI 不设任何代理变量，天然直连，不受影响。）
        proxies = None
        for attempt in range(3):
            try:
                r = requests.post(self.base_url, headers=headers, json=payload,
                                  timeout=60, proxies=proxies)
                if r.status_code in (401, 403):
                    return None  # 鉴权失败，重试无意义
                if r.status_code == 429 and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                if r.status_code >= 500 and attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()['choices'][0]['message']['content']
            except Exception:
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                return None
        return None

    def extract_text(self, body_text, *, temperature=0.0):
        if not self.api_key or not body_text or len(body_text) < 30:
            return None
        key = 'text:' + hashlib.md5(body_text.encode('utf-8')).hexdigest()
        cached = _cache_get(key)
        if _is_neg(cached):
            if not _neg_expired(cached):
                return None  # 负缓存命中（未过期），跳过模型调用
            # 已过期：当作未命中，继续重试
        elif cached is not None and _fields_useful(cached):
            return cached
        txt = body_text[:3500]
        messages = [
            {"role": "system", "content": EXTRACTION_ONLY_SYSTEM},
            {"role": "user", "content": "讲座正文：\n" + txt},
        ]
        # 模型 A 偶发不稳定：可能返回 markdown、或全字段 null 的 JSON。解析失败/全空时
        # 重试（最多 3 次），拿到含有效字段的 JSON 才采用（回落规则由调用方负责）。
        fields = None
        got_empty = False
        for _attempt in range(3):
            raw = self._post(messages, temperature)
            fields = _parse_model_json(raw) if raw else None
            if fields and _fields_useful(fields):
                break
            got_empty = raw is not None  # 有响应但无效/全空 → 硬负
            time.sleep(2)
        if fields and _fields_useful(fields):
            fields['abstract'] = _truncate_abstract(fields.get('abstract'))
            _cache_set(key, fields)
        else:
            _cache_set(key, _neg_marker(hard=got_empty,
                                        reason='empty' if got_empty else 'error'))
        return fields

    def extract_verdict(self, body_text, rule_fields, llm_fields, *, temperature=0.0):
        if not self.api_key:
            return {'verdict': 'unknown', 'fields': {}}
        txt = (body_text or '')[:3500]
        rule_s = json.dumps(rule_fields or {}, ensure_ascii=False)
        llm_s = json.dumps(llm_fields or {}, ensure_ascii=False)
        # 方案A三步裁决（2026-09-05）：①独立提取结构字段 -> ②与双方对比 -> ③裁决。
        # self_extract 仅作裁决依据与日志留痕，采纳逻辑不变（verdict=llm 才采用 A 的 fields）。
        system = ("你是严格的讲座信息裁决员。工作分三步，全部只依据给定原文，禁止编造。\n"
                  "第一步【独立提取】：从原文独立提取讲座结构化信息，字段："
                  "speaker(主讲人姓名)、speakerTitle(职称)、affiliation(单位)、"
                  "lectureStart(讲座开始日期时间)、lectureEnd(讲座结束时间)、"
                  "location(地点)、topic(讲座题目)、title(通知标题)。"
                  "原文没有的字段填 null。\n"
                  "第二步【对比】：把你的独立提取结果与『规则解析结果』『大模型解析结果』"
                  "逐字段对比，判断哪一方更准确，或是否都无法确定。\n"
                  "第三步【裁决】：输出严格 JSON：\n"
                  '{"self_extract":{第一步提取的字段},"verdict":"rule"|"llm"|"unknown",'
                  '"reason":"简述依据","fields":{采纳方的字段}}'
                  + _VERDICT_FIELD_SEMANTICS)
        user = ("原文：\n" + txt +
                "\n\n规则解析结果：\n" + rule_s +
                "\n\n大模型解析结果：\n" + llm_s +
                "\n\n请按三步裁决：先独立提取（self_extract），再与双方逐字段对比，最后裁决。"
                "若大模型明显更准确且原文支持，verdict=llm 并把采纳字段放入 fields；"
                "若规则更准确或无法判断，verdict=rule 或 unknown（fields 留空）。")
        raw = self._post([{"role": "system", "content": system},
                          {"role": "user", "content": user}], temperature)
        parsed = _parse_verdict_json(raw) if raw else None
        if isinstance(parsed, dict) and parsed.get('verdict') in ('rule', 'llm', 'unknown'):
            # self_extract 容错：非 dict 直接剥离，不影响 verdict/fields 主流程
            if not isinstance(parsed.get('self_extract'), dict):
                parsed.pop('self_extract', None)
            return parsed
        return {'verdict': 'unknown', 'fields': {}}


class ZhipuProvider(ModelProvider):
    """智谱 GLM 文本通道（OpenAI 兼容 /v1/chat/completions）。当前默认分歧裁决模型 B。"""

    name = 'zhipu'

    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key or _get_env('ZHIPU_API_KEY')
        self.base_url = (base_url or _get_env('ZHIPU_BASE_URL')
                         or 'https://open.bigmodel.cn/api/paas/v4/chat/completions')
        # glm-4-flash（免费普惠、非推理）：实测裁决 2.6~4.1s 且判对；
        # 旧默认 glm-4.7-flash 为推理模型，实测单次 32~90s 甚至超时，是批量解析慢的主因。
        self.model = (model or _get_env('JUDGE_MODEL') or 'glm-4-flash')

    def _post(self, messages, temperature):
        if not self.api_key:
            return None
        _throttle(channel=self.name)
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        # 不显式构造 proxies 字典：交给 requests 按标准环境变量解析，
        # HTTP_PROXY/HTTPS_PROXY/NO_PROXY 均生效。本地若代理访问国内 LLM 异常，
        # 可设 NO_PROXY=open.bigmodel.cn,api.agnes-ai.cn 让其直连绕过坏节点。
        # （GitHub CI 不设任何代理变量，天然直连，不受影响。）
        proxies = None
        for attempt in range(3):
            try:
                r = requests.post(self.base_url, headers=headers, json=payload,
                                  timeout=60, proxies=proxies)
                if r.status_code in (401, 403):
                    return None
                if r.status_code == 429 and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                if r.status_code >= 500 and attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()['choices'][0]['message']['content']
            except Exception:
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                return None
        return None

    def extract_text(self, body_text, *, temperature=0.0):
        """文本提取（备用）；默认不走此路径，但保留接口一致性。"""
        if not self.api_key or not body_text or len(body_text) < 30:
            return None
        key = 'text:zhipu:' + hashlib.md5(body_text.encode('utf-8')).hexdigest()
        cached = _cache_get(key)
        if _is_neg(cached):
            if not _neg_expired(cached):
                return None  # 负缓存命中（未过期），跳过模型调用
            # 已过期：当作未命中，继续重试
        elif cached is not None and _fields_useful(cached):
            return cached
        txt = body_text[:3500]
        messages = [
            {"role": "system", "content": EXTRACTION_ONLY_SYSTEM},
            {"role": "user", "content": "讲座正文：\n" + txt},
        ]
        raw = self._post(messages, temperature)
        fields = _parse_model_json(raw) if raw else None
        got_empty = raw is not None  # 有响应但解析无效/全空 → 硬负
        if fields and _fields_useful(fields):
            fields['abstract'] = _truncate_abstract(fields.get('abstract'))
            _cache_set(key, fields)
        else:
            _cache_set(key, _neg_marker(hard=got_empty,
                                        reason='empty' if got_empty else 'error'))
        return fields

    def extract_verdict(self, body_text, rule_fields, llm_fields, *, temperature=0.0):
        """分歧裁决：读原文 + 规则结果 + A 结果，返回裁决 dict。"""
        if not self.api_key:
            return {'verdict': 'unknown', 'fields': {}}
        txt = (body_text or '')[:3500]
        rule_s = json.dumps(rule_fields or {}, ensure_ascii=False)
        llm_s = json.dumps(llm_fields or {}, ensure_ascii=False)
        # 方案A三步裁决（2026-09-05）：①独立提取结构字段 -> ②与双方对比 -> ③裁决。
        # self_extract 仅作裁决依据与日志留痕，采纳逻辑不变（verdict=llm 才采用 A 的 fields）。
        system = ("你是严格的讲座信息裁决员。工作分三步，全部只依据给定原文，禁止编造。\n"
                  "第一步【独立提取】：从原文独立提取讲座结构化信息，字段："
                  "speaker(主讲人姓名)、speakerTitle(职称)、affiliation(单位)、"
                  "lectureStart(讲座开始日期时间)、lectureEnd(讲座结束时间)、"
                  "location(地点)、topic(讲座题目)、title(通知标题)。"
                  "原文没有的字段填 null。\n"
                  "第二步【对比】：把你的独立提取结果与『规则解析结果』『大模型解析结果』"
                  "逐字段对比，判断哪一方更准确，或是否都无法确定。\n"
                  "第三步【裁决】：输出严格 JSON：\n"
                  '{"self_extract":{第一步提取的字段},"verdict":"rule"|"llm"|"unknown",'
                  '"reason":"简述依据","fields":{采纳方的字段}}'
                  + _VERDICT_FIELD_SEMANTICS)
        user = ("原文：\n" + txt +
                "\n\n规则解析结果：\n" + rule_s +
                "\n\n大模型解析结果：\n" + llm_s +
                "\n\n请按三步裁决：先独立提取（self_extract），再与双方逐字段对比，最后裁决。"
                "若大模型明显更准确且原文支持，verdict=llm 并把采纳字段放入 fields；"
                "若规则更准确或无法判断，verdict=rule 或 unknown（fields 留空）。")
        raw = self._post([{"role": "system", "content": system},
                          {"role": "user", "content": user}], temperature)
        parsed = _parse_verdict_json(raw) if raw else None
        if isinstance(parsed, dict) and parsed.get('verdict') in ('rule', 'llm', 'unknown'):
            # self_extract 容错：非 dict 直接剥离，不影响 verdict/fields 主流程
            if not isinstance(parsed.get('self_extract'), dict):
                parsed.pop('self_extract', None)
            return parsed
        return {'verdict': 'unknown', 'fields': {}}


class MockProvider(ModelProvider):
    """离线测试用：返回预设值，不发起任何网络请求。"""

    name = 'mock'

    def __init__(self, text_result=None, verdict=None):
        self._text_result = text_result
        self._verdict = verdict or {'verdict': 'unknown', 'fields': {}}

    def extract_text(self, body_text, *, temperature=0.0):
        return self._text_result

    def extract_verdict(self, body_text, rule_fields, llm_fields, *, temperature=0.0):
        return self._verdict


# ---------------------------------------------------------------------------
# 工厂：返回可用 provider；无 key 返回 None -> 调用方回落规则
# ---------------------------------------------------------------------------
def get_text_provider():
    """文本识别主模型 A（当前 Agnes）。无 key 返回 None。"""
    p = AgnesProvider()
    return p if p.api_key else None


def get_judge_provider():
    """分歧裁决模型 B（当前智谱 GLM，可经 JUDGE_PROVIDER 覆盖为 agnes/zhipu/mock）。"""
    provider_type = (_get_env('JUDGE_PROVIDER') or 'zhipu').lower()
    if provider_type == 'agnes':
        judge_model = _get_env('AGNES_JUDGE_MODEL')
        p = AgnesProvider(model=judge_model) if judge_model else AgnesProvider()
        return p if p.api_key else None
    if provider_type == 'mock':
        from llm_provider import MockProvider
        return MockProvider(verdict={'verdict': 'unknown', 'fields': {}})
    # 默认：智谱 GLM（ZHIPU_API_KEY 已在 .env 中配置）
    p = ZhipuProvider()
    return p if p.api_key else None
