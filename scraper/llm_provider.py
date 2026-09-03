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
7. abstract 与 speakerBio 只提取各自段落的正文，必须到此为止：遇到「主讲人简介/报告人简介/专家简介/
   个人简介/报告题目/报告时间/报告地点/报名方式/联系方式/面向对象」等后续字段标题，或「一、二、
   三、」等章节序号时立即截断，严禁把后续段落（简介、题目、时间、地点、报名方式等）并入本字段。
8. 输出必须是纯 JSON（以 { 开头、} 结尾）。严禁输出 markdown 列表（如 "- **题目**：..."）、
   加粗、代码块围栏或任何解释文字；若某字段无法从原文解析，返回该字段为 null 的 JSON，
   而不是用文字说明「无法提取」。
"""


# ---------------------------------------------------------------------------
# JSON 解析（处理模型可能夹带的代码块围栏）
# ---------------------------------------------------------------------------
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
_NEXT_SLOT = [0.0]


def _throttle():
    """按 LLM_RPM（默认 20 次/分钟）占用一个时间槽，在真实 HTTP 调用前生效。

    仅靠 429 被动退避不足以保护批量任务：全库并发时会在短时间打出大量请求，
    先撞限流再重试反而更慢。这里全局串行分配时间槽，把速率压在阈值以内。
    """
    try:
        rpm = int(_get_env('LLM_RPM') or 20)
    except (TypeError, ValueError):
        rpm = 20
    if rpm <= 0:
        return
    slot = 60.0 / rpm
    with _RATE_LOCK:
        now = time.time()
        start = max(now, _NEXT_SLOT[0])
        _NEXT_SLOT[0] = start + slot
        wait = start - now
    if wait > 0:
        time.sleep(wait)


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
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        _hp = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
        proxies = {'https': _hp, 'http': _hp} if _hp else None
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
        if cached is not None and _fields_useful(cached):
            return cached
        txt = body_text[:3500]
        messages = [
            {"role": "system", "content": EXTRACTION_ONLY_SYSTEM},
            {"role": "user", "content": "讲座正文：\n" + txt},
        ]
        # 模型 A 偶发不稳定：可能返回 markdown、或全字段 null 的 JSON。解析失败/全空时
        # 重试（最多 3 次），拿到含有效字段的 JSON 才采用（回落规则由调用方负责）。
        fields = None
        for _attempt in range(3):
            raw = self._post(messages, temperature)
            fields = _parse_model_json(raw) if raw else None
            if fields and _fields_useful(fields):
                break
            time.sleep(2)
        if fields and _fields_useful(fields):
            _cache_set(key, fields)
        return fields

    def extract_verdict(self, body_text, rule_fields, llm_fields, *, temperature=0.0):
        if not self.api_key:
            return {'verdict': 'unknown', 'fields': {}}
        txt = (body_text or '')[:3500]
        rule_s = json.dumps(rule_fields or {}, ensure_ascii=False)
        llm_s = json.dumps(llm_fields or {}, ensure_ascii=False)
        system = ("你是严格的讲座信息裁决员。只依据给定原文判断『规则解析结果』与『大模型解析结果』"
                  "哪一方更准确，或是否都无法确定。禁止编造。输出严格 JSON："
                  '{"verdict":"rule"|"llm"|"unknown","reason":"简述依据","fields":{采纳方的字段}}')
        user = ("原文：\n" + txt +
                "\n\n规则解析结果：\n" + rule_s +
                "\n\n大模型解析结果：\n" + llm_s +
                "\n\n请裁决：哪一方更准确？若大模型明显更准确且原文支持，verdict=llm 并把采纳字段放入 fields；"
                "若规则更准确或无法判断，verdict=rule 或 unknown（fields 留空）。")
        raw = self._post([{"role": "system", "content": system},
                          {"role": "user", "content": user}], temperature)
        parsed = _parse_model_json(raw) if raw else None
        if isinstance(parsed, dict) and parsed.get('verdict') in ('rule', 'llm', 'unknown'):
            return parsed
        return {'verdict': 'unknown', 'fields': {}}


class ZhipuProvider(ModelProvider):
    """智谱 GLM 文本通道（OpenAI 兼容 /v1/chat/completions）。当前默认分歧裁决模型 B。"""

    name = 'zhipu'

    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key or _get_env('ZHIPU_API_KEY')
        self.base_url = (base_url or _get_env('ZHIPU_BASE_URL')
                         or 'https://open.bigmodel.cn/api/paas/v4/chat/completions')
        self.model = (model or _get_env('JUDGE_MODEL') or 'glm-4.7-flash')

    def _post(self, messages, temperature):
        if not self.api_key:
            return None
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        _hp = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
        proxies = {'https': _hp, 'http': _hp} if _hp else None
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
        if cached is not None and _fields_useful(cached):
            return cached
        txt = body_text[:3500]
        messages = [
            {"role": "system", "content": EXTRACTION_ONLY_SYSTEM},
            {"role": "user", "content": "讲座正文：\n" + txt},
        ]
        raw = self._post(messages, temperature)
        fields = _parse_model_json(raw) if raw else None
        if fields and _fields_useful(fields):
            _cache_set(key, fields)
        return fields

    def extract_verdict(self, body_text, rule_fields, llm_fields, *, temperature=0.0):
        """分歧裁决：读原文 + 规则结果 + A 结果，返回裁决 dict。"""
        if not self.api_key:
            return {'verdict': 'unknown', 'fields': {}}
        txt = (body_text or '')[:3500]
        rule_s = json.dumps(rule_fields or {}, ensure_ascii=False)
        llm_s = json.dumps(llm_fields or {}, ensure_ascii=False)
        system = ("你是严格的讲座信息裁决员。只依据给定原文判断『规则解析结果』与『大模型解析结果』"
                  "哪一方更准确，或是否都无法确定。禁止编造。输出严格 JSON："
                  '{"verdict":"rule"|"llm"|"unknown","reason":"简述依据","fields":{采纳方的字段}}')
        user = ("原文：\n" + txt +
                "\n\n规则解析结果：\n" + rule_s +
                "\n\n大模型解析结果：\n" + llm_s +
                "\n\n请裁决：哪一方更准确？若大模型明显更准确且原文支持，verdict=llm 并把采纳字段放入 fields；"
                "若规则更准确或无法判断，verdict=rule 或 unknown（fields 留空）。")
        raw = self._post([{"role": "system", "content": system},
                          {"role": "user", "content": user}], temperature)
        parsed = _parse_model_json(raw) if raw else None
        if isinstance(parsed, dict) and parsed.get('verdict') in ('rule', 'llm', 'unknown'):
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
