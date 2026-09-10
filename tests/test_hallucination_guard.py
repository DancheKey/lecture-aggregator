#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""幻觉守卫单元测试（2026-09-10 round-12/16 回归锁定）。

覆盖历次实测踩坑：
- round-12：VLM 对无文字人像照编造「李四/王五，北京大学/清华大学计算机系…」家族假值
  （cs5487 实测，speaker 甚至被编成「约翰·史密斯」）→ _is_hallucinated 指纹拦截；
- seri59：站点通用模板海报以「XXX」填充讲者/题目 → _PLACEHOLDER_RE 占位守卫；
- parsers._vlm_fields_useful 历史 bug：全空 dict 在 Python 中为 truthy，被误判「VLM 成功」，
  旁路文本多讲座拆分器与 rebackfill → 空 dict 必须判 False；
- 占位/幻觉字段「视为空」语义：全部字段均幻 → 整体判 VLM 失败，放行正文权威标签/OCR 兜底。

真名保护：指纹只匹配「占位名+冒号」「确定性占位裸名」「名校/模板句」组合，
不得误杀真实姓名（张三丰/张伟/温永立 等真人名）。

运行：python tests/test_hallucination_guard.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P


class IsHallucinatedTest(unittest.TestCase):
    """_is_hallucinated：命中即丢弃，宁缺勿错（仅用于 LLM/VLM 生成值校验）。"""

    def test_placeholder_name_with_colon(self):
        # 占位名 + 冒号后接模板介绍 = round-12 实测幻觉家族
        self.assertTrue(P._is_hallucinated('李四，北京大学计算机科学技术系教授'))
        self.assertTrue(P._is_hallucinated('张三,清华大学计算机系长聘教授'))
        self.assertTrue(P._is_hallucinated('王五：著名学者'))
        self.assertTrue(P._is_hallucinated('李华，长期从事量子计算研究'))
        self.assertTrue(P._is_hallucinated('约翰·史密斯，知名专家'))
        self.assertTrue(P._is_hallucinated('John Smith, Professor'))

    def test_bare_placeholder_name(self):
        # 确定性占位名可整值匹配（无歧义），拦截 speaker='李四' 这类裸名
        for name in ('张三', '李四', '王五', '约翰·史密斯', 'John Smith'):
            self.assertTrue(P._is_hallucinated(name), name)

    def test_template_sentences(self):
        # 模板句指纹（bio/abstract 场景）：与具体人名无关，命中即幻
        self.assertTrue(P._is_hallucinated('长期从事数据安全和隐私保护研究'))
        self.assertTrue(P._is_hallucinated('本讲座将探讨量子计算的最新进展'))
        self.assertTrue(P._is_hallucinated('北京大学计算机科学与技术学院教授'))

    def test_real_names_not_killed(self):
        # 真名保护：真名 + 正常介绍不得误杀（同名真人歧义一律放行）
        self.assertFalse(P._is_hallucinated('张三丰，武当派创始人'))
        self.assertFalse(P._is_hallucinated('温永立'))
        self.assertFalse(P._is_hallucinated('李四光，地质学家'))     # 真实历史人物
        self.assertFalse(P._is_hallucinated('王五常，华南师范大学教授'))
        self.assertFalse(P._is_hallucinated(''))

    def test_empty_and_none(self):
        self.assertFalse(P._is_hallucinated(''))
        self.assertFalse(P._is_hallucinated(None))
        self.assertFalse(P._is_hallucinated('   '))


class PlaceholderTest(unittest.TestCase):
    """_PLACEHOLDER_RE：站点模板海报以「XXX」填充讲者/题目（seri59 实测）。"""

    def test_placeholder_matches(self):
        for v in ('XXX', 'xx', 'ＸＸＸ', 'XxX'):
            self.assertTrue(P._PLACEHOLDER_RE.match(v), v)

    def test_non_placeholder(self):
        for v in ('X', '张三', '温永立', 'x光', ''):
            self.assertFalse(P._PLACEHOLDER_RE.match(v), v)


class VlmFieldsUsefulTest(unittest.TestCase):
    """_vlm_fields_useful：决定「VLM 是否成功」——空/占位/全幻都必须视为失败。"""

    def test_empty_dict_is_not_useful(self):
        # 历史 bug 回归：空 dict 为 truthy，曾被误判 VLM 成功，旁路多场拆分器（ctld4290 卡死根因）
        self.assertFalse(P._vlm_fields_useful({}))
        self.assertFalse(P._vlm_fields_useful(None))
        self.assertFalse(P._vlm_fields_useful([]))

    def test_placeholder_filled_dict_is_not_useful(self):
        self.assertFalse(P._vlm_fields_useful({'speaker': 'XXX', 'title': 'XXX'}))

    def test_all_hallucinated_dict_is_not_useful(self):
        # cs5487 实测：无文字人像照被解出 2 场「李四/王五」假讲座 → 全幻 = VLM 失败
        self.assertFalse(P._vlm_fields_useful(
            {'speaker': '李四', 'topic': '本讲座将探讨量子计算'}))
        self.assertFalse(P._vlm_fields_useful(
            [{'speaker': '张三'}, {'speaker': '王五'}]))     # 数组内全幻同样失败

    def test_useful_cases(self):
        self.assertTrue(P._vlm_fields_useful({'speaker': '温永立'}))
        self.assertTrue(P._vlm_fields_useful({'location': '心理学院201'}))
        # 数组：任一元素有用即 True
        self.assertTrue(P._vlm_fields_useful(
            [{'speaker': ''}, {'location': '理6栋302'}]))
        # 幻觉字段混真实字段：真实字段救活
        self.assertTrue(P._vlm_fields_useful(
            {'speaker': '李四', 'location': '心理学院201'}))

    def test_non_dict(self):
        self.assertFalse(P._vlm_fields_useful('李四'))
        self.assertFalse(P._vlm_fields_useful(123))


if __name__ == '__main__':
    unittest.main(verbosity=2)
