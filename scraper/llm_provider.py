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
1. 只做信息抽取，禁止编造、推理、补全、翻译或生成原文不存在的内容。
2. 仅从给定正文中提取字段；原文没有的字段一律返回 null（不得省略键）。
3. 每个字段附 snippet：直接抄录该字段在原文中的原句片段（含前后少量上下文），用于溯源核验。
4. 输出严格 JSON（不要 markdown 代码块、不要解释），结构如下：
{
  "title": {"value": "讲座系列名或整篇标题", "snippet": "..."},
  "topic": {"value": "单场讲座题目，无则 null", "snippet": "..."},
  "speaker": {"value": "主讲人姓名（仅姓名，不含职称/单位）", "snippet": "..."},
  "speakerTitle": {"value": "职称如 教授/研究员/博士，无则 null", "snippet": "..."},
  "speakerAffiliation": {"value": "单位/院系，无则 null", "snippet": "..."},
  "lectureStart": {"value": "ISO8601 如 2026-08-21 15:00，未知 null", "snippet": "..."},
  "lectureEnd": {"value": "ISO8601，未知 null", "snippet": "..."},
  "location": {"value": "地点（精确到楼栋房号，不含校名）", "snippet": "..."},
  "abstract": {"value": "摘要内容，无则 null", "snippet": "..."},
  "speakerBio": {"value": "主讲人简介，无则 null", "snippet": "..."}
}
5. 时间必须基于正文明确写出的日期与时间，不要猜测年份；年份无法确定时 lectureStart 用 null。
6. speaker 必须基于正文明确写出的主讲人姓名；正文无明确人名返回 null，严禁根据标题猜测。
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
        raw = self._post(messages, temperature)
        fields = _parse_model_json(raw) if raw else None
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
    """分歧裁决模型 B（当前复用 Agnes 同通道，可经 AGNES_JUDGE_MODEL 覆盖模型名）。"""
    judge_model = _get_env('AGNES_JUDGE_MODEL')
    p = AgnesProvider(model=judge_model) if judge_model else AgnesProvider()
    return p if p.api_key else None
