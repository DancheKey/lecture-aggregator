# -*- coding: utf-8 -*-
"""方案 A：按「职称词边界」切姓名（2026-10-01）。

背景：姓名原先按 4→3→2 字**贪心截取**，职称词边界完全不参与 → 切在职称中间
就剩首字（「王增建特聘研究员」→「王增建特」，全库回源页实证 9 条）。
现改为先定位职称词起始位置作切点。

⚠ 本套件锁的核心反向约束：
  1. **真人名不得被误切**——「周博教授」的切点必须落在 2（周博），不能因为
     「博」是「博士」的前缀就把「周博」切掉。这是方案 B（事后剥残片词表）会踩的雷，
     全库 42 条候选里 29 条是这样的正常人名。
  2. **单位里的职称不得被当切点**——「张三北京大学教授」的「教授」在位置 7，
     切点必须落在 [2,4] 才采纳；括号内的职务（「中国心理学会副理事长」）一律不参与。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402

CUT = P._speaker_name_cut


def cut(s):
    """简化入口：sp 与 sp_clean 同串（主路径下两者前缀一致，位置可互换）。"""
    return CUT(s, s)


class CutPointTest(unittest.TestCase):
    """切点定位本身：应修好的形态。"""

    def test_01_学术职称_完整词(self):
        self.assertEqual(cut('周博教授'), 2)
        self.assertEqual(cut('刘志学教授'), 3)
        self.assertEqual(cut('王增建副教授'), 3)

    def test_02_行政职务(self):
        """「刘维泉副总裁」——职务不在 _TITLE_ALT_FULL 里，靠本表补上。"""
        self.assertEqual(cut('刘维泉副总裁'), 3)

    def test_03_修饰词组合(self):
        """「特聘研究员」不在 _TITLE_ALT_FULL（该表只收特聘教授/特聘研究员二选一），
        而源页同样常见「特聘副研究员」（蒋雅丽 psy/1633、于洛迪 psy/1683 实证）。"""
        self.assertEqual(cut('王增建特聘研究员'), 3)
        self.assertEqual(cut('蒋雅丽特聘副研究员'), 3)
        self.assertEqual(cut('孙开佳青年研究员'), 3)

    def test_04_长职称整体命中_不切在中间(self):
        """「丁洁瑶助理教授」必须切在 3，不能因「教授」在后面而切成 5。"""
        self.assertEqual(cut('丁洁瑶助理教授'), 3)

    def test_05_岗位学历类(self):
        """maths/7620 实证：张炼研究科学家 —— 切点须落在 2 而非 4。"""
        self.assertEqual(cut('张炼研究科学家'), 2)
        self.assertEqual(cut('黄玲硕博连读'), 2)

    def test_06_出版序列职称(self):
        """psy/196 实证：刘曙光编审、研究员。"""
        self.assertEqual(cut('刘曙光编审'), 3)

    def test_07_字段标签也构成边界(self):
        """em/9240 实证：多出的「工」来自标签「工作单位」而非职称。"""
        self.assertEqual(cut('赵蕙心工作单位'), 3)


class NoFalseCutTest(unittest.TestCase):
    """反向约束一：真人名不得被误切。"""

    def test_11_尾字是职称词首字的人名(self):
        """这批是扫描里 29 条误报的代表：字形上尾字都像职称残片，
        但源页里就是完整人名（无职称连写）→ 切点应为 None，回落原贪心。"""
        for name in ('谢久书', '周博', '胡理', '王硕', '夏杰长',
                     '刘志学', '陈中科', '邱维理', '黄科', '王国长'):
            self.assertIsNone(cut(name), '%s 被误切（真人名）' % name)

    def test_12_切点不得落在_1(self):
        """单字切点会切出单字姓名，一律不采纳。"""
        self.assertIsNone(cut('李经理'))
        self.assertIsNone(cut('王主席'))

    def test_13_职称在单位里_不得被当切点(self):
        """「张三北京大学教授」：教授在位置 7 > 4，属单位串而非姓名后缀。"""
        self.assertIsNone(cut('张三北京大学教授'))


class BracketGuardTest(unittest.TestCase):
    """反向约束二：括号内（单位）的职称一律不参与切点搜索。

    psy127 实证：职务词在**单位串**里是合法构成分——
    「沈模卫（中国心理学会副理事长，浙江大学心理与行为科学系）」若被切成
    「沈模卫（中国心理学会」，括号不闭合会让 affiliation 取错段。

    ⚠ 本组是**纵深防御**：主防线其实是切点必须落在 [2,4] 的位置约束——
    单位里的职称词通常远在 4 字之后，光靠位置约束就已经挡住了。
    实测把括号保护整段删掉，本组仍全绿（破坏验证 `no_bracket` 不可观测），
    这是**预期行为**而非覆盖盲区：保留括号保护是为了在位置约束将来被放宽时
    仍有一道防线。别把它当成「这条测试能独立抓到回归」的依据。
    """

    def test_21_括号内职务不参与(self):
        self.assertIsNone(cut('沈模卫（中国心理学会副理事长，浙江大学）'))
        self.assertIsNone(cut('张三(北京大学教授)'))

    def test_22_括号前的职称仍生效(self):
        """姓名后的职称在括号**之前**，应当生效。"""
        self.assertEqual(cut('刘维泉副总裁（杭州乒乓智能技术有限公司）'), 3)
        self.assertEqual(cut('王增建特聘研究员（心理学院）'), 3)


class VocabSourceTest(unittest.TestCase):
    """切点词表必须从 field_vocab 派生，不得手抄第三份（G4 收敛的教训）。"""

    def test_31_派生自主表而非手抄(self):
        import field_vocab as FV
        words = P._SPK_CUT_TITLE_RE.pattern
        for w in ('教授', '副教授', '研究员', '副总裁', '博士'):
            self.assertIn(w, words, '%s 未进切点词表' % w)
        self.assertTrue(hasattr(FV, 'NAME_TITLE_SUFFIXES'))

    def test_32_切点补的词不参与归一剥离(self):
        """切点表只影响「姓名切多长」，**不得**改动讲者归一键的剥离口径，
        否则跨源合并目标会整体变化。

        证明方式：切点表新增的「研究/编审/科学家」等词，在归一剥离里必须
        仍然不被剥（NAME_TITLE_SUFFIXES 未动）。
        """
        import field_vocab as FV
        self.assertEqual(FV.normalize_speaker_key('周博教授'), ['周博'])
        for s in ('张炼研究', '刘曙光编审', '张炼科学家'):
            self.assertEqual(FV.strip_name_title_suffix(s), s,
                             '切点词 %r 渗进了归一剥离' % s)


if __name__ == '__main__':
    unittest.main(verbosity=2)
