#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 双轨（模型A 文本识别 + 模型B 引证裁决）离线回放门禁（2026-11-06，#14）。

背景：daily.yml 注入 SCNU_LLM_TEXT=1 让生产走 AI 双轨，但 test_parser_golden 在
import 前把 SCNU_LLM_*=0（与 CI 无密钥一致），且每日主路径根本不跑 golden——
LLM 分支一旦回归（参数错位 / 异常被吞 / 融合逻辑坏），CI 依然全绿。
库里 181 条「已富化但无裁决」是侧面佐证。

本测试用**桩 provider 回放**录制好的模型响应（绝不真调 API、零 key、零网络），
以 SCNU_LLM_TEXT=1（及 rich_only）跑 parse_detail，断言：
  · LLM 分支确实被执行（llmTextEnhanced=True）
  · 模型A 回填的 abstract 真实落进结果（静默错：分支不跑则 abstract 留空）
  · 模型B 裁决被调用（llmVerdict 被记录，非 None）

录制回放机制（照搬 tests/fixtures/vlm_cache.json 的「录制回放」哲学）：
  · 夹具 tests/fixtures/llm_text_replay.json 存模型A 的 text 响应与模型B 的 verdict；
  · 测试把 llm_provider.get_text_provider / get_judge_provider 打桩为返回
    MockProvider（吃夹具内容）；命中即返回，未命中会直接红（不静默回退真实 API）。
  · 新增录制：把模型真实响应按本文件顶部 a_text / b_verdict 形状写入夹具即可。

⚠ 必须在 import parsers **之前** setdefault SCNU_LLM_TEXT/RICH=1：本测试就是要验
这条双轨路径，纯规则守卫与本次要验的分支必须显式开启。
运行：python tests/test_llm_text_replay.py
"""
import os
import sys
import json
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
FIX = os.path.join(ROOT, 'tests', 'fixtures', 'llm_text_replay.json')
HTML_DIR = os.path.join(ROOT, 'tests', 'fixtures', 'html')

# 必须在 import parsers 之前开启双轨：本测试就是要验这条路径。
# .env 加载晚于此处、无法翻转；setdefault 不覆盖 shell 已显式设置的环境。
os.environ.setdefault('SCNU_LLM_TEXT', '1')
os.environ.setdefault('SCNU_LLM_RICH', '1')

import parsers as P
import llm_provider as LP
from llm_provider import MockProvider


class _CountingProvider(MockProvider):
    """在 MockProvider 基础上计数调用次数，直接证明 LLM 分支是否真的被执行。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.text_calls = 0
        self.verdict_calls = 0

    def extract_text(self, body_text, *, temperature=0.0):
        self.text_calls += 1
        return super().extract_text(body_text, temperature=temperature)

    def extract_verdict(self, body_text, rule_fields, llm_fields, *, temperature=0.0):
        self.verdict_calls += 1
        return super().extract_verdict(body_text, rule_fields, llm_fields,
                                       temperature=temperature)


def _load_fixture():
    with open(FIX, encoding='utf-8') as f:
        return json.load(f)


def _read_html(name):
    with open(os.path.join(HTML_DIR, name), encoding='utf-8', errors='replace') as f:
        return f.read()


class LLMReplayTest(unittest.TestCase):
    def setUp(self):
        self.fix = _load_fixture()
        self.html = _read_html(self.fix['html'])
        self.url = self.fix['url']

    def _parse_with(self, text_result, verdict):
        """临时把 provider 工厂打桩为吃夹具的计数型 MockProvider，跑一次解析。

        ⚠ 必须在调用前强制开启双轨：其它测试可能已把 SCNU_LLM_TEXT/RICH 硬设为 '0'，
        顶层的 setdefault 不覆盖已存在的值，会导致本测试在整跑时被污染（分支不执行）。
        这里在调用前后保存/复原，既不污染其它测试，也保证双轨路径一定被执行。
        """
        orig_text = LP.get_text_provider
        orig_judge = LP.get_judge_provider
        saved = {k: os.environ.get(k) for k in ('SCNU_LLM_TEXT', 'SCNU_LLM_RICH')}
        os.environ['SCNU_LLM_TEXT'] = '1'
        os.environ['SCNU_LLM_RICH'] = '1'
        text_p = _CountingProvider(text_result=text_result)
        judge_p = _CountingProvider(verdict=verdict)
        LP.get_text_provider = lambda: text_p
        LP.get_judge_provider = lambda: judge_p
        try:
            recs = P.parse_detail(self.html, self.url,
                                  college='', campus='', default_year=None)
            return (recs if isinstance(recs, list) else [recs]), text_p, judge_p
        finally:
            LP.get_text_provider = orig_text
            LP.get_judge_provider = orig_judge
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_01_llm_branch_executed_and_abstract_filled(self):
        """LLM 双轨分支必须被执行；模型A 回填的 abstract 落进结果（静默错：分支不跑则 abstract 留空）。"""
        recs, text_p, judge_p = self._parse_with(self.fix['a_text'], self.fix['b_verdict'])
        self.assertTrue(recs, '解析未产出任何记录')
        rec = recs[0]
        # 直接证明 LLM 链路被调用——这是 #14 要防的回归（分支不跑 CI 仍全绿）
        self.assertGreaterEqual(text_p.text_calls, 1,
                               '模型A 未被调用（双轨分支根本没执行）')
        self.assertGreaterEqual(judge_p.verdict_calls, 1,
                               '模型B 裁决未被调用（双轨分支根本没执行）')
        # 该 fixture 规则 abstract 为空，模型A 的 abstract 应真正落库
        self.assertEqual(rec.get('abstract'),
                         self.fix['a_text']['abstract']['value'],
                         '模型A 回填的 abstract 未落到结果（LLM 融合被绕过）')

    def test_02_judge_verdict_recorded(self):
        """模型B 裁决的返回值必须被记录到 llmVerdict，便于人工抽检。"""
        recs, _, judge_p = self._parse_with(self.fix['a_text'], self.fix['b_verdict'])
        rec = recs[0]
        self.assertIsNotNone(rec.get('llmVerdict'),
                             '模型B 裁决未被调用（llmVerdict 应非 None）')


if __name__ == '__main__':
    unittest.main(verbosity=2)
