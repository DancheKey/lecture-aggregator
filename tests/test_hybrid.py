# -*- coding: utf-8 -*-
"""双轨解析 + 分歧裁决的离线单元测试。

用 MockProvider 验证三核心场景，不依赖真实 API，不触碰 parsers.golden 测试。
运行：python -m unittest tests.test_hybrid -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scraper'))
from llm_provider import MockProvider
from hybrid import apply_llm_text_hybrid, compare_struct


class TestCompareStruct(unittest.TestCase):
    def test_identical_speakers(self):
        r = {'speaker': '温永立', 'location': '理6栋302'}
        a = {'speaker': '温永立', 'location': '理6栋302'}
        self.assertEqual(compare_struct(r, a), [])

    def test_speaker_conflict(self):
        r = {'speaker': '张三', 'location': '理6栋302'}
        a = {'speaker': '李四', 'location': '理6栋302'}
        self.assertIn('speaker', compare_struct(r, a))

    def test_location_norm(self):
        r = {'location': '理6栋302室'}
        a = {'location': '理6栋302'}
        self.assertEqual(compare_struct(r, a), [])


class TestThreeScenarios(unittest.TestCase):
    """场景 1: 一致 -> 采用 A；场景 2: A 失效 -> 规则保底；场景 3: 分歧 -> B 裁决。"""

    def _make_result(self):
        return {
            'sourceUrl': 'http://example.com/a/1.html',
            'speaker': '规则主讲人',
            'topic': '规则题目',
            'lectureStart': '2026-09-02 14:00:00',
            'location': '理6栋302',
            'speakerSource': 'label',
        }

    def test_scenario_1_consistent(self):
        """A 与规则一致 -> 采用 A 丰富结果，无裁决调用。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result={
            'speaker': {'value': '规则主讲人', 'snippet': '主讲人：规则主讲人'},
            'abstract': {'value': '摘要内容', 'snippet': '本文讨论...'},
            'lectureStart': {'value': '2026-09-02 14:00:00', 'snippet': '时间：...'},
        })
        apply_llm_text_hybrid(rule_result, '这是一条讲座通知正文测试数据。', None, provider, None)
        self.assertTrue(rule_result.get('llmTextEnhanced'))
        self.assertEqual(rule_result['llmVerdict'], 'consistent')
        self.assertEqual(rule_result['abstract'], '摘要内容')  # 采用 A 新增字段
        self.assertEqual(rule_result['speakerSource'], 'llm')

    def test_scenario_2_a_fails(self):
        """A 失效（返回 None）-> 规则保底，llmTextEnhanced=False。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result=None)
        apply_llm_text_hybrid(rule_result, '讲座正文。', None, provider, None)
        self.assertFalse(rule_result.get('llmTextEnhanced'))
        self.assertIsNone(rule_result.get('llmVerdict'))
        self.assertEqual(rule_result['speaker'], '规则主讲人')  # 保留规则

    def test_scenario_3_disagreement_judge_rule(self):
        """分歧 -> B 裁决支持 rule -> 保留规则，打 needsHumanReview 标记。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result={
            'speaker': {'value': 'A主讲人', 'snippet': '演讲人：A主讲人'},
        })
        judge = MockProvider(verdict={
            'verdict': 'rule',
            'reason': '规则主讲人与标题一致',
            'fields': {},
        })
        apply_llm_text_hybrid(rule_result, '讲座正文测试数据。', None, provider, judge)
        self.assertFalse(rule_result.get('llmTextEnhanced'))
        self.assertEqual(rule_result['llmVerdict'], 'rule')
        self.assertTrue(rule_result.get('needsHumanReview'))  # 标记争议
        self.assertEqual(rule_result['speaker'], '规则主讲人')  # 保守保留规则

    def test_scenario_3_disagreement_judge_llm(self):
        """分歧 -> B 裁决支持 llm -> 采用 A，llmTextEnhanced=True。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result={
            'speaker': {'value': 'A主讲人', 'snippet': '主讲人：A主讲人'},
            'abstract': {'value': 'A的摘要', 'snippet': '摘要...'},
        })
        judge = MockProvider(verdict={
            'verdict': 'llm',
            'reason': '正文明确写的是A主讲人',
            'fields': {'speaker': 'A主讲人', 'abstract': 'A的摘要'},
        })
        apply_llm_text_hybrid(rule_result, '讲座正文测试数据。', None, provider, judge)
        self.assertTrue(rule_result.get('llmTextEnhanced'))
        self.assertEqual(rule_result['llmVerdict'], 'llm')
        self.assertEqual(rule_result['speaker'], 'A主讲人')
        self.assertEqual(rule_result['abstract'], 'A的摘要')


if __name__ == '__main__':
    unittest.main()
