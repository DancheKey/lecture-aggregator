# -*- coding: utf-8 -*-
"""双轨解析 + 分歧裁决的离线单元测试。

用 MockProvider 验证核心场景，不依赖真实 API，不触碰 parsers.golden 测试。
运行：python tests/test_hybrid.py

2026-09-02 下午修订：融合语义改为「仅填空 + snippet 溯源闸门」，测试用例同步更新，
并补充职称/单位补全、幻觉拒绝、单位粘连截断三组用例。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scraper'))
from llm_provider import MockProvider
from hybrid import apply_llm_text_hybrid, compare_struct

# 正文含可溯源片段，供 snippet 闸门匹配
BODY = ('讲座通知。题目：深度学习前沿。主讲人：温永立 教授（清华大学计算机系）。'
        '时间：2026-09-02 14:00。地点：理6栋302。'
        '摘要：本报告介绍深度学习的最新进展与应用。'
        '简介：温永立，清华大学教授，主要从事人工智能研究。')


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


class TestHybridScenarios(unittest.TestCase):
    """场景 1: 一致 -> 放行；场景 2: A 失效 -> 规则保底；场景 3: 分歧 -> B 裁决。"""

    def _make_result(self):
        return {
            'sourceUrl': 'http://example.com/a/1.html',
            'speaker': '温永立',
            'topic': '规则题目',
            'lectureStart': '2026-09-02 14:00:00',
            'location': '理6栋302',
            'speakerSource': 'label',
        }

    def test_scenario_1_consistent(self):
        """A 与规则一致 -> 放行，规则为空的 abstract/bio 由 A 补全。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'abstract': {'value': '本报告介绍深度学习的最新进展与应用。',
                         'snippet': '本报告介绍深度学习的最新进展与应用。'},
            'speakerBio': {'value': '温永立，清华大学教授，主要从事人工智能研究。',
                           'snippet': '温永立，清华大学教授，主要从事人工智能研究。'},
        })
        apply_llm_text_hybrid(rule_result, BODY, None, provider, None)
        self.assertTrue(rule_result.get('llmTextEnhanced'))
        self.assertEqual(rule_result['llmVerdict'], 'consistent')
        self.assertEqual(rule_result['abstract'], '本报告介绍深度学习的最新进展与应用。')
        self.assertEqual(rule_result['speakerBio'], '温永立，清华大学教授，主要从事人工智能研究。')
        self.assertEqual(rule_result['speaker'], '温永立')

    def test_scenario_2_a_fails(self):
        """A 失效（返回 None）-> 规则保底，llmTextEnhanced=False。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result=None)
        apply_llm_text_hybrid(rule_result, BODY, None, provider, None)
        self.assertFalse(rule_result.get('llmTextEnhanced'))
        self.assertIsNone(rule_result.get('llmVerdict'))
        self.assertEqual(rule_result['speaker'], '温永立')

    def test_scenario_3_judge_rule(self):
        """分歧 -> B 裁决支持 rule -> 保留规则，打 needsHumanReview 标记。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result={
            'speaker': {'value': '李四', 'snippet': '主讲人：温永立 教授'},
        })
        judge = MockProvider(verdict={
            'verdict': 'rule', 'reason': '规则主讲人与标题一致', 'fields': {},
        })
        apply_llm_text_hybrid(rule_result, BODY, None, provider, judge)
        self.assertFalse(rule_result.get('llmTextEnhanced'))
        self.assertEqual(rule_result['llmVerdict'], 'rule')
        self.assertTrue(rule_result.get('needsHumanReview'))
        self.assertEqual(rule_result['speaker'], '温永立')  # 保守保留规则

    def test_scenario_3_judge_llm(self):
        """分歧 -> B 裁决支持 llm：仍只填空，规则已有值不被覆盖。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result={
            'speaker': {'value': '李四', 'snippet': '主讲人：温永立 教授'},
            'abstract': {'value': '本报告介绍深度学习的最新进展与应用。',
                         'snippet': '本报告介绍深度学习的最新进展与应用。'},
        })
        judge = MockProvider(verdict={
            'verdict': 'llm', 'reason': '正文明确写的是李四',
            'fields': {'speaker': '李四', 'abstract': 'A的摘要'},
        })
        apply_llm_text_hybrid(rule_result, BODY, None, provider, judge)
        self.assertTrue(rule_result.get('llmTextEnhanced'))
        self.assertEqual(rule_result['llmVerdict'], 'llm')
        self.assertEqual(rule_result['speaker'], '温永立')  # 已有值不覆盖
        self.assertEqual(rule_result['abstract'], '本报告介绍深度学习的最新进展与应用。')


class TestFillEmptyOnly(unittest.TestCase):
    """仅填空语义：规则已有值的字段，A 绝不覆盖。"""

    def _rule(self):
        return {'speaker': '温永立', 'topic': '规则题目',
                'location': '理6栋302', 'abstract': '规则摘要'}

    def test_existing_fields_not_overridden(self):
        r = self._rule()
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'topic': {'value': 'A的题目', 'snippet': '题目：深度学习前沿'},
            'location': {'value': 'A的地点', 'snippet': '地点：理6栋302'},
            'abstract': {'value': 'A的摘要', 'snippet': '本报告介绍深度学习的最新进展与应用。'},
        })
        apply_llm_text_hybrid(r, BODY, None, provider, None)
        self.assertEqual(r['speaker'], '温永立')
        self.assertEqual(r['topic'], '规则题目')
        self.assertEqual(r['location'], '理6栋302')
        self.assertEqual(r['abstract'], '规则摘要')  # 规则已有摘要，A 不覆盖

    def test_speaker_title_and_affiliation_filled(self):
        """职称/单位属填空型字段：规则没抓到时由 A 补上。"""
        r = {'speaker': '温永立', 'topic': '规则题目'}
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'speakerTitle': {'value': '教授', 'snippet': '主讲人：温永立 教授'},
            'speakerAffiliation': {'value': '清华大学计算机系',
                                   'snippet': '主讲人：温永立 教授（清华大学计算机系）'},
        })
        apply_llm_text_hybrid(r, BODY, None, provider, None)
        self.assertEqual(r['speakerTitle'], '教授')
        self.assertEqual(r['speakerAffiliation'], '清华大学计算机系')

    def test_affiliation_cut_at_metadata(self):
        """单位粘连后续元数据标记时，按标记截断，避免吞进日期/地点。"""
        r = {'speaker': '温永立'}
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'speakerAffiliation': {'value': '清华大学计算机系）日期：2026-09-02 地点：理6栋302',
                                   'snippet': '主讲人：温永立 教授（清华大学计算机系）'},
        })
        apply_llm_text_hybrid(r, BODY, None, provider, None)
        self.assertEqual(r['speakerAffiliation'], '清华大学计算机系')


class TestSnippetGate(unittest.TestCase):
    """溯源闸门：值能在原文中找到出处才采用，否则判幻觉。"""

    def test_hallucination_rejected(self):
        """snippet 在原文中找不到 -> 拒绝采用，并记入 llmRejected。"""
        r = {'speaker': '温永立'}
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'abstract': {'value': '一段凭空编造的摘要内容', 'snippet': '这段原文里根本没有'},
        })
        apply_llm_text_hybrid(r, BODY, None, provider, None)
        self.assertNotIn('abstract', r)
        self.assertIn('abstract', r.get('llmRejected', ''))

    def test_missing_snippet_rejected(self):
        """A 未附 snippet -> 无法溯源 -> 拒绝。"""
        r = {'speaker': '温永立'}
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'abstract': {'value': '无出处的摘要'},
        })
        apply_llm_text_hybrid(r, BODY, None, provider, None)
        self.assertNotIn('abstract', r)
        self.assertIn('abstract', r.get('llmRejected', ''))


if __name__ == '__main__':
    unittest.main()
