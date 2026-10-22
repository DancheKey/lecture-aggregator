# -*- coding: utf-8 -*-
"""摘要溯源约束：abstract 必须逐字来自网页原始资料，禁止生成式补全。

用户标准（2026-10-22）：
  「有没有摘要并不重要，唯一的判别标准是一定要基于网页里面的原始资料去提取，
    如果它里面有，那就提取，没有就不提取。」

本测试锁住这条纪律的四个实现点：
  1. 规则路径 `_extract_narrative`：abstract 是正文句子的拼接，每句都能在正文里找到
  2. VLM 路径：提示词明确「逐字摘自原文 / 禁止改写扩写虚构 / 正文没有就留空」
  3. 幻觉守卫：VLM 与 LLM 写入 abstract 前都过 `_is_hallucinated`
  4. 出口清洗 `_gate_record`：占位符、简介复制、短残段一律置空（宁缺勿滥）

这些断言不测「提取率」，只测「提取的每个字是否来自原文」。
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402


def norm(s):
    return re.sub(r'[\s\u3000\xa0]+', '', s or '')


class NarrativeFromSourceTest(unittest.TestCase):
    """规则兜底路径：abstract 的每一句都必须是正文里的原句。"""

    BODY = (
        '2021年12月13日晚，我院在文三栋二楼会议室举办南粤讲坛第46期学术讲座。'
        '随着互联网和大数据技术的发展，数据的收集和使用日益广泛。'
        '然而，随之而来的隐私泄露和数据滥用问题也日益严重，如何在利用数据的同时保护个人隐私成为重要议题。'
        '本次讲座将从技术与制度两个层面展开分析。'
        '主讲人：张三教授。时间：2021年12月13日19:00。地点：文三栋二楼会议室。'
    )

    def test_01_摘要每句都能在正文中找到(self):
        r = P._extract_narrative(self.BODY, '讲座预告|南粤讲坛第46期')
        ab = (r.get('abstract') or '').strip()
        self.assertTrue(ab, '正文有实质段落时应提取到摘要')
        body = norm(self.BODY)
        for sent in [s for s in ab.split('。') if s.strip()]:
            self.assertIn(norm(sent), body,
                          '摘要句「%s」不在正文里——说明发生了改写或生成' % sent[:30])

    def test_02_摘要不含字段标签(self):
        r = P._extract_narrative(self.BODY, '讲座预告')
        ab = r.get('abstract') or ''
        for label in ('主讲人：', '时间：', '地点：'):
            self.assertNotIn(label, ab, '摘要里不应出现字段标签 %s' % label)

    def test_03_正文无实质段落时留空(self):
        body = '主讲人：张三教授。时间：2021年12月13日19:00。地点：文三栋二楼会议室。'
        r = P._extract_narrative(body, '讲座预告')
        self.assertFalse((r.get('abstract') or '').strip(),
                         '正文只有字段标签时必须留空，不得拼凑')

    def test_04_空正文不崩且留空(self):
        r = P._extract_narrative('', '标题')
        self.assertFalse((r.get('abstract') or '').strip())


class VlmPromptVerbatimTest(unittest.TestCase):
    """VLM 路径：提示词必须约束「逐字 / 不虚构 / 没有就留空」。"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'scraper', 'parsers.py'), encoding='utf-8') as f:
            cls.src = f.read()

    def test_11_提示词禁止改写扩写虚构(self):
        self.assertIn('禁止改写、扩写、意译或虚构', self.src,
                      'VLM 提示词的「禁止改写扩写虚构」约束被删了')

    def test_12_提示词要求没有就留空(self):
        self.assertRegex(self.src, r'正文没有就留空',
                         'VLM 提示词的「正文没有就留空」约束被删了')

    def test_13_海报无独立摘要时留空(self):
        self.assertRegex(self.src, r'若海报只有主讲人简介而无独立摘要，则留空',
                         '海报页「无独立摘要则留空」的约束被删了')

    def test_14_提示词要求逐字摘自原文(self):
        self.assertRegex(self.src, r'必须逐字摘自正文原文',
                         'VLM 提示词的「逐字摘自正文原文」约束被删了')


class HallucinationGuardWiringTest(unittest.TestCase):
    """幻觉守卫必须挂在两条生成式路径的写入点之前。"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'scraper', 'parsers.py'), encoding='utf-8') as f:
            cls.src = f.read()
            cls.lines = cls.src.splitlines()

    def _index_of(self, needle):
        for i, ln in enumerate(self.lines):
            if needle in ln:
                return i
        self.fail('找不到代码行：%s' % needle)

    def _index_exact(self, stripped):
        """整行精确匹配。`in` 匹配会让 `_ab` 命中 `_abs`，必须精确。"""
        for i, ln in enumerate(self.lines):
            if ln.strip() == stripped:
                return i
        self.fail('找不到代码行（精确）：%s' % stripped)

    def test_21_VLM写abstract前有守卫(self):
        i_guard = self._index_exact("if _is_hallucinated(_ab):")
        i_write = self._index_exact("result['abstract'] = _ab")
        self.assertLess(i_guard, i_write, 'VLM 写 abstract 前必须先过幻觉守卫')

    def test_22_LLM增强后abstract回滚(self):
        i_guard = self._index_of("if _is_hallucinated(result.get('abstract')):")
        i_roll = self._index_of("result['abstract'] = _pre_llm_abs")
        self.assertLess(i_guard, i_roll, 'LLM 文本增强后必须能回滚被编造的 abstract')

    def test_23_VLM字段有用性判定排除幻觉(self):
        seg = self.src[self.src.index('def _vlm_fields_useful'):]
        seg = seg[:seg.index('\n\n\n')] if '\n\n\n' in seg else seg
        self.assertIn('_is_hallucinated(v)', seg,
                      'VLM 结果有效性判定必须排除幻觉字段，否则编造值会被当成功')


class GateEmptiesFakeAbstractTest(unittest.TestCase):
    """出口清洗：宁可空着，也不留假摘要。"""

    def test_31_占位符置空(self):
        r = {'abstract': 'XXX', 'speakerBio': ''}
        P._gate_record(r, url='u')
        self.assertEqual(r['abstract'], '', '占位符摘要必须置空')

    def test_32_摘要与简介重合置空(self):
        bio = '1977年生于台湾辅仁大学，1985年获美国加州大学伯克利分校博士学位，长期从事社会学研究。'
        r = {'abstract': bio[:40], 'speakerBio': bio}
        P._gate_record(r, url='u')
        self.assertEqual(r['abstract'], '', '摘要与简介前 30 字重合时应置空')

    def test_33_纯短残段置空(self):
        r = {'abstract': '学术讲座', 'speakerBio': ''}
        P._gate_record(r, url='u')
        self.assertEqual(r['abstract'], '', '纯短中文残段不可能是真实摘要')

    def test_34_真实摘要不受影响(self):
        ab = ('随着互联网和大数据技术的发展，数据的收集和使用日益广泛。'
              '然而，随之而来的隐私泄露和数据滥用问题也日益严重。')
        r = {'abstract': ab, 'speakerBio': '张三，北京大学教授，长期从事数据治理研究。'}
        P._gate_record(r, url='u')
        self.assertTrue(r['abstract'], '真实摘要不应被误清')


if __name__ == '__main__':
    unittest.main(verbosity=2)
