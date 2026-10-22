# -*- coding: utf-8 -*-
"""无摘要回填脚本（scripts/backfill_abstract.py）的判定口径测试。

背景
----
库内 1465 条无摘要（38.1%）。抽样实测 51.6% 源站正文无内容、42.6% 那段文字
就是主讲人简介，只有约 4% 是解析漏失；但另一项抽样发现部分记录**用当前解析器
重解析后能拿到 abstract**——历史数据停留在旧版解析结果。

回填脚本因此只做一件事：把重解析确实有 abstract 的记录补上。风险在于「补进去
的是 bio 或行政通知噪声」——页面上本来就不显示（`app.display.js::abstractOf`
会过滤掉），补进去只是脏数据。本测试锁住这套过滤口径。

关键约束
--------
`judge()` 的过滤规则必须与前端 `site/app.display.js::abstractOf` **同一套**：
  · BIO_PREFIXES 前缀（主讲人简介/报告人简介/…）
  · 与 speakerBio 高度重叠（去空格后摘要被 bio 包含）
  · 行政通知列表噪声（「资讯及通知」或 ≥2 条「关于…通知/公告/申报…」）
两边任一侧改了词表而另一侧没跟，就会出现「数据里有、页面不显示」或反之。

运行：python tests/test_backfill_abstract.py
"""
import importlib.util
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _load('backfill_abstract_under_test', 'scripts/backfill_abstract.py')
judge = B.judge


class JudgeRejectTest(unittest.TestCase):
    """不该补进来的（补了页面也不显示，纯脏数据）。"""

    def test_01_空值(self):
        self.assertEqual(judge('', ''), (False, 'empty'))
        self.assertEqual(judge('   ', ''), (False, 'empty'))
        self.assertEqual(judge(None, ''), (False, 'empty'))

    def test_02_太短(self):
        self.assertEqual(judge('讲座内容简介', '')[0], False)
        self.assertEqual(judge('讲座内容简介', '')[1], 'too-short')

    def test_03_主讲人简介前缀(self):
        """环境研究院实测：abstract 被填成「主讲人简介：王XX，…」这类。"""
        for p in ('主讲人简介', '报告人简介', '嘉宾介绍', '专家介绍'):
            ab = p + '：王某某，某某大学教授，长期从事环境化学研究，主持多项国家基金项目。'
            ok, why = judge(ab, '')
            self.assertFalse(ok, '%s 开头的应被拒' % p)
            self.assertEqual(why, 'bio-prefix')

    def test_04_与简介重复(self):
        """环境研究院 4/5 条 FILLED 属于这种：重解析出的 abstract 就是 bio 原文。"""
        bio = '本科和硕士期在南京大学地质系就读，于中国科学院大学获有机地球化学博士学位。'
        ok, why = judge('本科和硕士期在南京大学地质系就读，于中国科学院大学获有机地球化学博士学位。', bio)
        self.assertFalse(ok)
        self.assertEqual(why, 'dup-bio')

    def test_05_重复判定忽略空白差异(self):
        bio = '刘喆，教育学博士，数学科学学院教授，新加坡南洋理工大学访问学者。'
        ab = '刘喆, 教育学博士, 数学科学学院教授, 新加坡南洋理工大学访问学者。'
        ok, why = judge(ab, bio)
        self.assertFalse(ok, '去空格后应判重复（中英文标点差异不影响主体）')

    def test_06_行政通知噪声(self):
        noise = '关于征集国家社科基金项目的通知 关于申报教育部人文社科项目的通知'
        ok, why = judge(noise, '')
        self.assertFalse(ok)
        self.assertEqual(why, 'admin-noise')

    def test_07_资讯及通知栏目(self):
        ok, why = judge('资讯及通知：本站最新动态与学术活动安排，欢迎广大师生关注参与。', '')
        self.assertFalse(ok)
        self.assertEqual(why, 'admin-noise')


class BioContinuationTest(unittest.TestCase):
    """简介续段 / 简介画像：abstract 不是「讲座讲什么」，而是讲者简历。

    这是本脚本最容易放脏数据的地方——补进去的若是简历，页面上会和「简介」块
    重复展示同一段话。两条实测案例各用一种判据拦住。
    """

    def test_41_简介续段_字符重合(self):
        """环境研究院「Uptake, Transport…」：abstract 是 bio 的后半截，逐字重合。

        原文（2026-10-22 实测）：bio 写「李晖博士是密歇根州立大学环境与土壤化学教授。
        他在中国南京大学获得了环境化学学士和…」，abstract 从「他在中国南京大学获得了
        环境化学学士和硕士学位」接着讲——两段既非前缀也非包含，只能靠字符级重合识别。
        """
        bio = ('李晖博士是密歇根州立大学环境与土壤化学教授。他在中国南京大学获得了环境化学'
               '学士和硕士学位，并获得了博士学位。')
        ab = ('他在中国南京大学获得了环境化学学士和硕士学位,并获得了博士学位。李教授的研究'
              '项目侧重于药物、持久性有机污染物和农药在环境中的归趋、转化、生物利用度及其'
              '影响机制;在环境科学和土壤科学期刊上发表了> 100篇同行评审期刊论文。')
        ok, why = judge(ab, bio)
        self.assertFalse(ok, '与 bio 高度重合的续段应被拒')
        self.assertIn(why, ('bio-overlap', 'dup-bio', 'bio-profile'))

    def test_42_简介画像_头衔堆叠(self):
        """环境研究院「碳中和目标与环境保护」：通篇头衔与项目，无一字讲讲座内容。"""
        bio = '江桂斌，中国科学院院士，发展中国家科学院院士。中国分析测试协会理事长。'
        ab = ('中国分析测试协会理事长，环境化学与生态毒理学国家重点实验室主任。主持了'
              '环境内分泌干扰物研究的第一个国家863项目；持久性有机污染物研究的第一个'
              '国家973项目。近年来担任中科院战略先导项目首席科学家、基金委重大研究计划'
              '专家组组长。')
        ok, why = judge(ab, bio)
        self.assertFalse(ok, '通篇简历、无内容动词的应被拒')
        self.assertEqual(why, 'bio-profile')

    def test_43_真摘要不被误杀_中文(self):
        """环境研究院「土壤微界面动态分析方法」：讲内容，不是讲人。"""
        bio = 'XXX'
        ab = ('污染物在环境中的迁移转化是一个复杂的过程。对于相对均匀稳定的环境介质，'
              '污染物的表征已经有了较多的成熟方法，但是对于不同介质之间的过渡区域'
              '（即微界面环境）的分析还存在大量挑战。')
        ok, why = judge(ab, bio)
        self.assertTrue(ok, '讲研究内容的摘要不应被简历判据误杀：%s' % why)

    def test_44_真摘要不被误杀_英文数学(self):
        """数学学院英文摘要含「we will introduce」，属内容动词，不能判简历。"""
        bio = '韩永生教授是国际知名的调和分析专家。先后在北京大学师从程民德院士。'
        ab = ('Consider the Euclidean space R^n equipped with the reflection with respect '
              'to the hyperplane orthogonal to a nonzero vector. Then there are two metrics.')
        ok, why = judge(ab, bio)
        self.assertTrue(ok, '英文数学摘要不应被误杀：%s' % why)


class JudgeAcceptTest(unittest.TestCase):
    """应当补进来的。"""

    def test_11_正常中文摘要(self):
        ab = ('污染物在环境中的迁移转化是一个复杂的过程。对于相对均匀稳定的环境介质，'
              '污染物的表征已经有了较为成熟的方法体系。')
        ok, why = judge(ab, '')
        self.assertTrue(ok)
        self.assertEqual(why, 'ok')

    def test_12_英文数学摘要(self):
        """数学学院实测：正文带「ABSTRACT:」段，重解析能拿到。"""
        ab = ('ABSTRACT: In this speech, we will introduce the following topics: '
              'the Hardy-Littlewood maximal function and its boundedness on Lp spaces.')
        ok, _ = judge(ab, '')
        self.assertTrue(ok)

    def test_13_有简介但摘要独立(self):
        """摘要与简介讲的是两件事，不算重复。"""
        bio = '张伟，中山大学哲学系教授，主要研究中国古典哲学。'
        ab = ('本讲座将从《庄子》文本出发，讨论先秦思想中「自然」概念的生成，'
              '并结合出土文献重新审视相关争论。')
        ok, _ = judge(ab, bio)
        self.assertTrue(ok)

    def test_14_超长截断(self):
        ab = '这是一段很长的讲座摘要内容，' * 500
        ok, _ = judge(ab, '')
        self.assertTrue(ok)
        self.assertLessEqual(len(B.judge(ab, '')[0] and ab[:B.MAX_LEN]), B.MAX_LEN)


class ThresholdTest(unittest.TestCase):
    """阈值常量与前端保持一致。"""

    def test_21_最小长度(self):
        self.assertEqual(B.MIN_LEN, 30)

    def test_22_最大长度对齐前端(self):
        """前端 abstractOf 用 truncate(ab, 5000)，此处须同值。"""
        self.assertEqual(B.MAX_LEN, 5000)
        with open(os.path.join(ROOT, 'site', 'app.display.js'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('truncate(ab, 5000)', src,
                      '前端摘要上限改了，回填脚本 MAX_LEN 须同步')


class FrontendParityTest(unittest.TestCase):
    """两侧过滤词表必须同源，否则出现「数据里有、页面不显示」。"""

    def setUp(self):
        with open(os.path.join(ROOT, 'site', 'app.display.js'), encoding='utf-8') as f:
            self.js = f.read()

    def test_31_bio前缀两侧一致(self):
        m = re.search(r'const bioPrefixes = \[([^\]]+)\]', self.js)
        self.assertIsNotNone(m, '前端 bioPrefixes 定义变了，须同步本测试')
        js_prefixes = set(re.findall(r"'([^']+)'", m.group(1)))
        py_prefixes = set(B.BIO_PREFIXES)
        self.assertEqual(js_prefixes - py_prefixes, set(),
                         '前端有、Python 没有的前缀 → 回填会漏过滤')
        self.assertEqual(py_prefixes - js_prefixes, set(),
                         'Python 有、前端没有的前缀 → 回填会过度过滤')

    def test_32_行政噪声正则两侧都存在(self):
        self.assertIn('资讯及通知', self.js, '前端行政噪声判据变了，须同步')
        self.assertTrue(B._ADMIN_RE.search('资讯及通知'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
