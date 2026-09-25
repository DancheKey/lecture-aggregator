# -*- coding: utf-8 -*-
"""引证裁决（cited_judge）生产模块离线单元测试。

用 stub provider 预置 B 的返回，验证值级闸门的各种放行/拒绝路径，
不依赖真实 API、不发网络请求。
运行：python tests/test_cited_judge.py
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scraper'))

import cited_judge
from cited_judge import CitedJudgeProvider, _norm_meeting_format, _strip_job_title
from llm_provider import MockProvider

BODY = ('讲座通知。题目：深度学习前沿。主讲人：温永立 教授（清华大学计算机系）。'
        '时间：2026-09-02 14:00。地点：理6栋302。'
        '摘要：本报告介绍深度学习的最新进展与应用。')


class _StubBase:
    """只实现 _post 的桩 provider，模拟 B 模型原始 chat 返回。"""

    name = 'stub'

    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    def _post(self, messages, temperature):
        self.calls.append(messages)
        return self.raw

    def extract_verdict(self, body_text, rule_fields, llm_fields, *, temperature=0.0):
        return {'verdict': 'unknown', 'fields': {}}


def _raw(judgements, self_extract=None):
    obj = {'judgements': judgements}
    if self_extract is not None:
        obj['self_extract'] = self_extract
    return json.dumps(obj, ensure_ascii=False)


class TestDecidePaths(unittest.TestCase):
    def test_no_diff_returns_rule_without_calling_b(self):
        """无分歧：直接 rule，且完全不调 B（零成本）。"""
        base = _StubBase(_raw([]))
        j = CitedJudgeProvider(base)
        rule = {'speaker': '温永立', 'location': '理6栋302'}
        llm = {'speaker': '温永立', 'location': '理6栋302'}
        v = j.extract_verdict(BODY, rule, llm)
        self.assertEqual(v['verdict'], 'rule')
        self.assertEqual(v['fields'], {})
        self.assertEqual(base.calls, [])

    def test_value_in_body_adopted(self):
        """B 值在原文且边界良好 → 采纳，注入 llm_fields 并 force。"""
        base = _StubBase(_raw([{
            'field': 'speaker', 'value': '温永立',
            'citation': '主讲人：温永立 教授（清华大学计算机系）', 'reason': '原文如此'}]))
        j = CitedJudgeProvider(base)
        rule = {'speaker': '向大家分', 'location': '理6栋302'}
        llm = {'speaker': '温永立', 'location': '理6栋302'}
        v = j.extract_verdict(BODY, rule, llm)
        self.assertEqual(v['verdict'], 'llm')
        self.assertEqual(v['fields'], {'speaker': 'llm'})
        self.assertEqual(llm['speaker'], '温永立')

    def test_hallucinated_value_rejected(self):
        """B 值不在原文（幻觉）→ 拒绝采纳，保留规则值。"""
        base = _StubBase(_raw([{
            'field': 'speaker', 'value': '张三丰',
            'citation': '主讲人：张三丰 教授', 'reason': '编造'}]))
        j = CitedJudgeProvider(base)
        rule = {'speaker': '向大家分', 'location': '理6栋302'}
        llm = {'speaker': '温永立', 'location': '理6栋302'}
        v = j.extract_verdict(BODY, rule, llm)
        self.assertEqual(v['verdict'], 'rule')
        self.assertEqual(v['fields'], {})
        self.assertEqual(llm['speaker'], '温永立')  # A 值未被污染

    def test_parse_fail_returns_unknown(self):
        """B 返回非 JSON → unknown（机制未决），主链路按未获支持处理。"""
        base = _StubBase('这不是 JSON')
        j = CitedJudgeProvider(base)
        rule = {'speaker': '向大家分'}
        llm = {'speaker': '温永立'}
        v = j.extract_verdict(BODY, rule, llm)
        self.assertEqual(v['verdict'], 'unknown')
        self.assertEqual(v['fields'], {})

    def test_unknown_field_ignored(self):
        """B 返回不在 CITED_FIELDS 的字段名 → 忽略。"""
        base = _StubBase(_raw([{'field': 'abstract', 'value': '任意', 'citation': '随意'}]))
        j = CitedJudgeProvider(base)
        rule = {'speaker': '向大家分'}
        llm = {'speaker': '温永立'}
        v = j.extract_verdict(BODY, rule, llm)
        self.assertEqual(v['fields'], {})

    def test_base_without_post_is_delegated(self):
        """底层无 _post（如 MockProvider）→ 原样委托，不包装。"""
        base = MockProvider(verdict={'verdict': 'unknown', 'fields': {}})
        j = CitedJudgeProvider(base)
        self.assertFalse(hasattr(base, '_post'))
        v = j.extract_verdict(BODY, {'speaker': '向大家分'}, {'speaker': '温永立'})
        self.assertEqual(v['verdict'], 'unknown')

    def test_self_extract_passthrough(self):
        """B 附带 self_extract → 透传给主链路（保留 B 填空能力）。"""
        se = {'speaker': '温永立', 'speakerTitle': '教授'}
        base = _StubBase(_raw([], self_extract=se))
        j = CitedJudgeProvider(base)
        v = j.extract_verdict(BODY, {'speaker': '向大家分'}, {'speaker': '温永立'})
        self.assertEqual(v.get('self_extract'), se)


class TestMeetingFormat(unittest.TestCase):
    def test_norm_9digit(self):
        self.assertEqual(_norm_meeting_format('腾讯会议:921754818'),
                         '腾讯会议 921-754-818')

    def test_norm_with_space_in_prefix(self):
        self.assertEqual(_norm_meeting_format('腾讯 会议 号: 669-2471-6718'),
                         '腾讯 会议 号: 669-2471-6718')  # 非"腾讯会议"连写，不处理

    def test_norm_idempotent(self):
        once = _norm_meeting_format('腾讯会议:921754818')
        self.assertEqual(_norm_meeting_format(once), once)

    def test_non_meeting_untouched(self):
        self.assertEqual(_norm_meeting_format('理6栋302'), '理6栋302')


class TestJobTitleStrip(unittest.TestCase):
    def test_strip_director(self):
        self.assertEqual(_strip_job_title('东莞市图书馆馆长'), '东莞市图书馆')

    def test_strip_multi_layer(self):
        self.assertEqual(_strip_job_title('产业经济研究所所长'), '产业经济研究所')

    def test_no_title_returns_empty(self):
        self.assertEqual(_strip_job_title('东莞市图书馆'), '')


class TestInfoLoss(unittest.TestCase):
    def test_meeting_number_loss_rejected(self):
        """B 值丢掉会议号（只留"腾讯会议"）→ 判信息丢失，拒绝采纳。"""
        body = '地点：线上 腾讯会议 281-667-675 密码 1234'
        base = _StubBase(_raw([{
            'field': 'location', 'value': '腾讯会议',
            'citation': '腾讯会议 281-667-675', 'reason': '简化'}]))
        j = CitedJudgeProvider(base)
        rule = {'location': '线上 腾讯会议 281-667-675 密码 1234'}
        llm = {'location': '腾讯会议281667675'}
        v = j.extract_verdict(body, rule, llm)
        self.assertEqual(v['fields'], {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
