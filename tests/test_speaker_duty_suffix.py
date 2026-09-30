#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""讲者姓名尾部的行政职务残片（2026-09-30，B6 后续缺口）。

实证：module/3685 源页写「【主讲人】刘维泉副总裁（杭州乒乓智能技术有限公司）」，
落库 speaker='刘维泉副'、speakerAffiliation='总裁(杭州乒乓智能技术有限公司'
（括号不闭合、职务混进单位）。根因是讲者职称交替式只含**学术职称**、不含
**行政职务**；且姓名是按 4→2 字截取的，「刘维泉副总裁」截 4 字即「刘维泉副」。

⚠ fixture 用**源页正文片段原文**（tests/fixtures/spk_duty_*.html），不用手写合成
HTML——实测同一条文本在半角 `[]` 与全角 `【】` 标签下走不同分支，合成样本会把
真实缺陷掩盖掉（2026-09-30 实测：半角 speaker 对但拿不到 title，全角单位反而错）。

⚠ 本套件同时锁一条**反向**约束（psy127 快照实测踩到）：
职务词**刻意不进**_TITLE_ALT_FULL——那张表作用于 sp 全串，若含职务，
「沈模卫（中国心理学会副理事长，浙江大学心理与行为科学系…）」会被截到
「沈模卫（中国心理学会」（括号不闭合）→ speakerAffiliation 从
「浙江大学心理与行为科学系」错成「中国心理学会」。
即职务词在**姓名尾部**是残片、在**单位串**里是合法构成分，语义相反。
"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures')

# ⚠ 必须显式恢复 LLM 开关为**生产默认**（True）：同进程里 test_parser_snapshot
# 会调 _force_pure_rules() 把它们置 False 且不复原（那是它的约定，不是通用纪律）。
# 实测 2026-09-30：pytest tests/ 时本套件 4 例红，单跑却全绿，原因就是这份残留。
# 本套件锁的是**生产行为**（.github/workflows/daily.yml 设 SCNU_LLM_TEXT='1'）；
# 纯规则层另有残留缺口（见下方待办注释），不在此处断言。
_LLM_CONSTANTS = ('_USE_LLM_TEXT', '_USE_LLM_RICH')


def _reset_to_production_defaults():
    """把 parsers 的 LLM 开关恢复为生产默认（True）。"""
    for name in _LLM_CONSTANTS:
        if hasattr(P, name):
            setattr(P, name, True)

# 源页 URL / 学院 / 年份（年份按源页正文里的实际年份，URL 仅作去重/溯源用）
_PAGES = {
    'module_3685': ('spk_duty_module_3685.html',
                    'http://sdxy.scnu.edu.cn/a/202609/3685.html', '经济与管理学院', 2025),
    'iqm_189': ('spk_duty_iqm_189.html',
                'http://psy.scnu.edu.cn/a/202609/189.html', '心理学院', 2025),
}


def _parse_fixture(key):
    """用源页正文片段原文跑 parse_detail，返回单条结果。"""
    _reset_to_production_defaults()
    fname, url, college, year = _PAGES[key]
    path = os.path.join(FIXTURES, fname)
    if not os.path.exists(path):
        raise unittest.SkipTest(f'fixture 缺失：{fname}')
    body = io.open(path, encoding='utf-8').read()
    html = ('<html><head><title>讲座预告</title></head><body>'
            + body + '</body></html>')
    out = P.parse_detail(html, url, college, '石牌', year, skip_news_filter=True)
    if isinstance(out, list):
        out = out[0] if out else {}
    return out or {}


def _parse_synthetic(body):
    """仅供「词表/正则本身」的单元级断言用，不用于端到端字段断言。"""
    _reset_to_production_defaults()
    html = ('<html><head><title>讲座预告</title></head><body>'
            '<div class="content">' + body + '</div></body></html>')
    out = P.parse_detail(html, 'http://x.scnu.edu.cn/a/202609/1.html',
                         '某某学院', '石牌', 2026, skip_news_filter=True)
    if isinstance(out, list):
        out = out[0] if out else {}
    return out or {}


class RealPageDutySuffixTest(unittest.TestCase):
    """端到端：源页原文 → 三字段全部正确。"""

    def test_01_经管学院_剥副总裁(self):
        """module/3685：源页「【主讲人】刘维泉副总裁(杭州乒乓智能技术有限公司)」

        断言只覆盖**纯规则层能产出**的字段（speaker / speakerAffiliation）。
        speakerTitle 属 LLM 富化通道产物，无 key 时为空（见 test_05）。
        """
        r = _parse_fixture('module_3685')
        self.assertEqual(r.get('speaker'), '刘维泉',
                         f"职务残片未剥净：{r.get('speaker')!r}")
        self.assertEqual(r.get('speakerAffiliation'), '杭州乒乓智能技术有限公司')

    def test_02_物理学院_剥助理研究员(self):
        """iqm/189：报告人：陈洁 / 助理研究员（美国阿贡实验室）

        旧值 speaker='陈洁 助理'（含空格分词残片）。"""
        r = _parse_fixture('iqm_189')
        self.assertEqual(r.get('speaker'), '陈洁',
                         f"职称残片未剥净：{r.get('speaker')!r}")
        self.assertEqual(r.get('speakerAffiliation'), '美国阿贡实验室')

    def test_05_职称字段属富化通道_无key时为空(self):
        """speakerTitle 不由纯规则层产出——锁「分层」，避免误以为规则层漏抽。

        同进程里 test_parser_snapshot 桩掉 provider（等价于无 key），
        此时 speakerTitle 恒为空；有 key 时才是「副总裁」/「助理研究员」。
        故本套件不对它做等值断言，只断言它是字符串（不因剥离而崩坏）。
        """
        for key in _PAGES:
            got = _parse_fixture(key).get('speakerTitle')
            self.assertIsInstance(got, str,
                                  f'{key} 的 speakerTitle 非字符串：{got!r}')

    def test_03_单位括号必须闭合(self):
        """旧缺陷：speakerAffiliation='总裁(杭州乒乓智能技术有限公司'（括号不闭合）。

        只要单位里出现未闭合的半角/全角括号就是被截断的信号。
        """
        for key in _PAGES:
            aff = str(_parse_fixture(key).get('speakerAffiliation') or '')
            self.assertNotIn('(', aff, f'{key} 单位含未闭合半角括号：{aff!r}')
            self.assertNotIn('（', aff, f'{key} 单位含未闭合全角括号：{aff!r}')

    def test_04_单位不得含职务残字(self):
        """单位里不得残留职务首字（如「总裁(…」）。"""
        for key in _PAGES:
            aff = str(_parse_fixture(key).get('speakerAffiliation') or '')
            self.assertFalse(aff.startswith('总裁'), f'{key} 单位以职务开头：{aff!r}')


class ReverseConstraintTest(unittest.TestCase):
    """反向约束：职务词不得污染单位串（psy127 形态）。"""

    def test_05_单位里的副理事长不得被当职称截断(self):
        """「沈模卫(中国心理学会副理事长，浙江大学…)」→ 单位仍应是后半段。"""
        r = _parse_synthetic('<p>【题目】心理学前沿</p>'
                             '<p>【主讲人】沈模卫(中国心理学会副理事长，'
                             '浙江大学心理与行为科学系)</p>'
                             '<p>【时间】2026年10月10日 08:30</p>'
                             '<p>【地点】文三栋501会议室</p>')
        self.assertEqual(r.get('speaker'), '沈模卫')
        aff = str(r.get('speakerAffiliation') or '')
        self.assertNotEqual(aff, '中国心理学会',
                            f'单位被职务截断（副理事长进了职称表）：{aff!r}')

    def test_06_学术职称不受影响(self):
        r = _parse_synthetic('<p>【题目】测试</p><p>【主讲人】张三副教授</p>'
                             '<p>【时间】2026年10月10日 08:30</p>'
                             '<p>【地点】文五栋501</p>')
        self.assertEqual(r.get('speaker'), '张三')

    def test_07_单字人名被判空属既有行为_不在本次范围(self):
        """「王副教授」→ speaker=''：**改动前就是这样**（2026-09-30 git stash 对比实测）。

        根因是单字姓名过不了 _looks_like_real_name 这道早有的门槛，与本次职务剥离
        无关：单独的「王」同样为空。存量核查 data/lectures.json 单字 speaker 为 0 条，
        即真实源页几乎不产出该形态，既有空规则无实际影响。
        故此处**钉住现状**而非修改判脏逻辑——改它属另一次口径变更，须先评估存量。
        """
        r = _parse_synthetic('<p>【题目】测试</p><p>【主讲人】王副教授</p>'
                             '<p>【时间】2026年10月10日 08:30</p>'
                             '<p>【地点】文五栋501</p>')
        self.assertEqual(r.get('speaker') or '', '',
                         '若此断言变化，说明单字姓名门槛被改动——'
                         '那属另一次口径变更，须先评估存量再改')


class DutyVocabTest(unittest.TestCase):
    """词表本身的约束。"""

    def test_10_只收副字头职务(self):
        """_SPK_TAIL_DUTY_WORDS 必须全部以「副」开头。

        「总裁/经理/校长/会长/书记」的首字是常见人名字（经纬、经书…），
        放进宽松残片表会误伤人名，故一律不收。
        """
        for w in P._SPK_TAIL_DUTY_WORDS:
            self.assertTrue(w.startswith('副'), f'{w} 不是「副」字头，不应收入')

    def test_11_不含可作人名的职务(self):
        for w in ('总裁', '经理', '校长', '会长', '书记', '主编', '社长'):
            self.assertNotIn(w, P._SPK_TAIL_DUTY_WORDS,
                             f'{w} 可作人名/机构名，不得收入剥职务词表')

    def test_12_复合职务在表内且长于单体(self):
        """复合职务必须整体成词，否则「副总裁」被截成「副」——正是原缺陷。"""
        self.assertIn('副总裁', P._SPK_TAIL_DUTY_WORDS)
        self.assertIn('副总编辑', P._SPK_TAIL_DUTY_WORDS)
        m = P._SPK_TAIL_DUTY_RE.search('刘维泉副总裁')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(0).strip(), '副总裁')

    def test_13_严格版不匹配残片_宽松版才匹配(self):
        """残片形态（截断后只剩首字）只由宽松版处理。"""
        self.assertIsNone(P._SPK_TAIL_DUTY_RE.search('刘维泉副'))
        self.assertIsNotNone(P._SPK_TAIL_DUTY_PREFIX_RE.search('刘维泉副'))

    def test_14_宽松版首字唯一(self):
        """宽松版是「首字」正则，若首字种类过多会误伤。"""
        heads = {w[0] for w in P._SPK_TAIL_DUTY_WORDS}
        self.assertEqual(heads, {'副'},
                         f'宽松版首字应只有「副」，实际 {sorted(heads)}')

    def test_15_职务词不在TITLE_ALT_FULL(self):
        """防回退：职务词一旦进了 _TITLE_ALT_FULL，psy127 类页面的单位会被截断。"""
        for w in ('副理事长', '副校长', '副会长'):
            self.assertNotIn(w, P._TITLE_ALT_FULL,
                             f'{w} 不该在 _TITLE_ALT_FULL（会污染单位串）')

    def test_16_词表只能定义一次(self):
        """防静默覆盖：同名变量重复定义时，后一份静默覆盖前一份。

        2026-09-30 实踩：_SPK_TAIL_DUTY_WORDS 在文件里有两份（429 行新、
        3250 行旧），后者覆盖前者成为实际生效值 —— 而「改前一份测不出」
        这类破坏之所以假绿，正是因为真正生效的是被覆盖的那份。
        故此处锁「唯一定义」，让破坏验证能真正抓到。
        """
        path = os.path.join(ROOT, 'scraper', 'parsers.py')
        src = io.open(path, encoding='utf-8').read()
        n_assign = src.count('\n_SPK_TAIL_DUTY_WORDS = (')
        self.assertEqual(
            n_assign, 1,
            f'_SPK_TAIL_DUTY_WORDS 有 {n_assign} 处定义，'
            f'后者会静默覆盖前者（唯一定义在 _TITLE_ALT_FULL 之后）')
        n_re = src.count('\n_SPK_TAIL_DUTY_RE = re.compile(')
        self.assertEqual(n_re, 1, f'_SPK_TAIL_DUTY_RE 有 {n_re} 处定义')
        n_pre = src.count('\n_SPK_TAIL_DUTY_PREFIX_RE = re.compile(')
        self.assertEqual(n_pre, 1, f'_SPK_TAIL_DUTY_PREFIX_RE 有 {n_pre} 处定义')


if __name__ == '__main__':
    unittest.main(verbosity=2)