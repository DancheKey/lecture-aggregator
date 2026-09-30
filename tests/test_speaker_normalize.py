# -*- coding: utf-8 -*-
"""讲者归一（B6）守卫：field_vocab 归一口径 + 落库判重/前端归一三者一致。

背景：审计发现讲者归一有两套互不一致的实现——
  scraper._normalize_speaker：剥英文头衔前缀 + 自带 10 项中文职称表
  field_vocab.speaker_keys：只去中文职称后缀，不剥英文前缀
且前者自身有两个真实缺陷：
  1) 职称表未按长度降序，「副教授」先被「教授」命中 → '张三副教授' 剥成 '张三副'
     （库内实测已污染 2 条：module/3685「刘维泉副」、iqm/189「陈洁 助理」）；
  2) 表缺 '特聘/特任/长聘教授'、'助理研究员'、'老师'、'导师'、'博导'、'硕导'、'硕士'。
后果：同一人在「落库判重」与「前端归一键」两侧算出不同键，跨源合并静默失效。

修法：口径收敛到 field_vocab.normalize_speaker_key，三方共用。

⚠ 本套件最要紧的是「不得误剥真实人名」这条反向约束：真实姓名可以以头衔缩写
开头（Jean-Claude **Dr**eher、**Dr**Eher），去头正则若漏词边界就会产出 'eher'
这种垃圾键——比不剥更坏（它会主动制造一个错误的合并目标）。
"""
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRAPER = os.path.join(_ROOT, 'scraper')
if _SCRAPER not in sys.path:
    sys.path.insert(0, _SCRAPER)

import field_vocab as fv  # noqa: E402


def _load_scraper():
    path = os.path.join(_SCRAPER, 'scraper.py')
    spec = importlib.util.spec_from_file_location('scraper.t_spk', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StripEnTitlePrefixTest(unittest.TestCase):
    """英文头衔前缀剥离。"""

    def test_01_标准写法被剥(self):
        for raw, want in (('Dr. Daniel Winney', 'Daniel Winney'),
                          ('Dr Daniel Winney', 'Daniel Winney'),
                          ('Dr.Velychko', 'Velychko'),
                          ('Prof. John Smith', 'John Smith'),
                          ('Prof John Smith', 'John Smith'),
                          ('Professor Alan Turing', 'Alan Turing'),
                          ('Ph.D. Alan Turing', 'Alan Turing'),
                          ('PhD. Alan Turing', 'Alan Turing'),
                          ('Md. Jane Roe', 'Jane Roe'),
                          ('Mr. John Doe', 'John Doe'),
                          ('Ms. Jane Roe', 'Jane Roe'),
                          ('Mrs. Jane Roe', 'Jane Roe'),
                          ('Dr X', 'X')):
            self.assertEqual(fv.strip_en_title_prefix(raw), want, raw)

    def test_02_组合头衔(self):
        for raw, want in (('Associate Professor John Smith', 'John Smith'),
                          ('Assistant Professor Jane Roe', 'Jane Roe'),
                          ('Full Professor Alan Turing', 'Alan Turing'),
                          ('Dr. Prof. Alan Turing', 'Alan Turing')):
            self.assertEqual(fv.strip_en_title_prefix(raw), want, raw)

    def test_03_中文连写保守不剥(self):
        """「Dr张伟」不剥 —— 且这是 \\b 的正确行为，不是缺陷。

        Python re 的 \\w 含 CJK，故「dr」与「张」之间**没有**词边界、\\b 不成立
        （实测 re.search(r'dr\\b张', 'Dr张') 为 None）。源页里中英头衔连写极罕见
        （「Dr 张伟」带空格才是常态，会正常剥），取保守侧（不剥）——
        误剥会凭空造出错误的合并目标，比不剥更坏。
        """
        for raw in ('Dr张伟', 'Prof李明', 'Dr张伟教授'):
            self.assertEqual(fv.strip_en_title_prefix(raw), raw, raw)
        # 带空格 / 带点的常规写法必须能剥
        self.assertEqual(fv.strip_en_title_prefix('Dr 张伟'), '张伟')
        self.assertEqual(fv.strip_en_title_prefix('Dr.张伟'), '张伟')

    def test_04_不得误剥真实人名(self):
        """反向约束：漏词边界会把 Dreher 剥成 'eher'，比不剥更坏。"""
        for raw in ('Jean-Claude Dreher', 'DrEher', 'Drachen', 'Dreyer',
                    'Rainer Hedrich', 'Drucker', 'Moss'):
            self.assertEqual(fv.strip_en_title_prefix(raw), raw,
                             f'{raw!r} 是人名，不得被剥头衔')

    def test_05_光头衔不剥出空串(self):
        """整个串只是头衔 → 属解析残值，保留原串交上层判脏，不产出空键。"""
        for raw in ('Dr', 'Dr.', 'Prof', 'Professor', 'Ph.D.'):
            self.assertEqual(fv.strip_en_title_prefix(raw), raw,
                             f'{raw!r} 剥空会产生空键')
            self.assertTrue(fv.normalize_speaker_key(raw),
                            f'{raw!r} 归一后不应变成空键组')

    def test_06_幂等(self):
        for raw in ('Dr. Prof. Alan Turing', 'Dr. Daniel Winney', 'Jean-Claude Dreher'):
            once = fv.strip_en_title_prefix(raw)
            self.assertEqual(fv.strip_en_title_prefix(once), once, raw)

    def test_07_括号内头衔不剥(self):
        """「伟利奇科(Dr.Velychko)」是尾缀形态，不在处理范围（须保持原样）。"""
        raw = '伟利奇科(Dr.Velychko)'
        self.assertEqual(fv.strip_en_title_prefix(raw), raw)


class StripCnTitleSuffixTest(unittest.TestCase):
    """中文职称尾部剥离：长词必须优先。"""

    def test_10_长词优先(self):
        for raw, want in (('张三副教授', '张三'), ('张三教授', '张三'),
                          ('李四特聘教授', '李四'), ('李四特任教授', '李四'),
                          ('李四长聘教授', '李四'), ('王五助理教授', '王五'),
                          ('赵六副研究员', '赵六'), ('赵六助理研究员', '赵六'),
                          ('钱七博士后', '钱七'), ('钱七博士生导师', '钱七'),
                          ('钱七硕士生导师', '钱七'), ('孙八院士', '孙八'),
                          ('孙八老师', '孙八'), ('孙八导师', '孙八'),
                          ('周九博导', '周九'), ('周九硕导', '周九'),
                          ('吴十硕士', '吴十')):
            self.assertEqual(fv.strip_name_title_suffix(raw), want, raw)

    def test_11_只剥一个后缀(self):
        self.assertEqual(fv.strip_name_title_suffix('张三副教授'), '张三')

    def test_12_整串职称不剥空_交判脏拦(self):
        """整串就是职称时不得剥成空串。

        语义澄清（本测试锁定的是分工，不是行为缺陷）：strip_name_title_suffix
        **只剥一个后缀**——「博士生导师」→「博士生」、「副教授」→「副」都是
        既有且预期的行为；判「这整串是职称而非人名」是 is_title_only 的职责。
        """
        for raw in ('教授', '博导', '硕士', '院士', '老师', '导师'):
            self.assertEqual(fv.strip_name_title_suffix(raw), raw,
                             f'{raw!r} 剥空会产生空键')
        for raw in ('教授', '博导', '硕士', '院士', '老师', '导师'):
            self.assertTrue(fv.is_title_only(raw), f'{raw!r} 应被认作职称而非姓名')

    def test_13_已知缺口_剥剩的职称残值未必被认作职称(self):
        """**已知遗留缺口，本轮不修**（修它要动判脏正则，风险外溢到全库）。

        现象：「博士生导师」剥一个后缀得「博士生」，而 TITLE_ONLY_RE 要求整串
        由职称字构成，「生」不在词表 → is_title_only('博士生') 为 False，
        即这类残值既没被剥干净、也没被判脏。
        修这个需要区分「剥得掉但剥不干净」与「本身就是人名」，属判脏口径调整，
        应单独评估对 3810 条库的影响，不在讲者归一收敛里顺手改。
        本用例的作用是把缺口**显式钉住**，避免将来误以为已修而无人跟进。
        """
        self.assertEqual(fv.strip_name_title_suffix('博士生导师'), '博士生')
        self.assertFalse(
            fv.is_title_only('博士生'),
            '若此断言转红，说明 is_title_only 已能认这类残值——'
            '请更新本用例 docstring 并移除「已知缺口」标注')

    def test_13_非后缀不动(self):
        for raw in ('张三', '李四', 'Daniel Winney', '王教授的 学生'):
            self.assertEqual(fv.strip_name_title_suffix(raw), raw, raw)


class NormalizeSpeakerKeyTest(unittest.TestCase):
    """完整归一口径：全角转半角 → 去英文头衔 → 去职称 → 多人拆键 → 英文 lower。"""

    def test_20_全角转半角(self):
        self.assertEqual(fv.normalize_speaker_key('Ｄｒ．Ｊｏｈｎ Ｄｏｅ'),
                         ['john doe'])

    def test_21_中文职称(self):
        self.assertEqual(fv.normalize_speaker_key('张三副教授'), ['张三'])
        self.assertEqual(fv.normalize_speaker_key('李四特聘教授'), ['李四'])

    def test_22_英文头衔加中文职称(self):
        self.assertEqual(fv.normalize_speaker_key('Dr. 张三副教授'), ['张三'])

    def test_23_多人逐人拆键(self):
        self.assertEqual(fv.normalize_speaker_key('张三、李四副教授'),
                         ['张三', '李四'])
        self.assertEqual(fv.normalize_speaker_key('Dr. John Smith, Jane Doe'),
                         ['john smith', 'jane doe'])

    def test_24_空值返回空列表(self):
        for raw in (None, '', 0):
            self.assertEqual(fv.normalize_speaker_key(raw), [])

    def test_25_中文名不做lower(self):
        self.assertEqual(fv.normalize_speaker_key('王五'), ['王五'])

    def test_26_人名不误剥(self):
        for raw, want in (('Jean-Claude Dreher', ['jean-claude dreher']),
                          ('Rainer Hedrich', ['rainer hedrich']),
                          ('Claude Dreher', ['claude dreher']),
                          ('Andrew Ip', ['andrew ip'])):
            self.assertEqual(fv.normalize_speaker_key(raw), want, raw)


class ParityWithScraperTest(unittest.TestCase):
    """落库判重与前端归一必须同口径（本项是 B6 的核心目标）。"""

    @classmethod
    def setUpClass(cls):
        cls.S = _load_scraper()

    def test_30_两方对同一批输入结果一致(self):
        """scraper._normalize_speaker 取首个键；speaker_keys 是完整键组。

        逐条断言首个键相等——不相等即意味着同一人在两侧被判成两个人。
        """
        samples = ['张三副教授', '李四特聘教授', '王五助理研究员', '钱七老师',
                   '孙八博士生导师', 'Dr. Daniel Winney', 'Prof. John Smith',
                   'Dr. Prof. Alan Turing', 'Jean-Claude Dreher',
                   'Rainer Hedrich', 'DrEher', 'Andrew Ip', '周九博导',
                   'Ｄｒ．Ｊｏｈｎ', 'Dr. 张三副教授', '刘维泉副', '陈洁 助理',
                   '伟利奇科(Dr.Velychko)']
        for raw in samples:
            self.assertEqual(self.S._normalize_speaker(raw),
                             fv.speaker_keys(raw)[0] if fv.speaker_keys(raw) else '',
                             f'{raw!r} 两侧口径不一致')

    def test_31_两方对空值一致(self):
        for raw in (None, ''):
            self.assertEqual(self.S._normalize_speaker(raw), '')
            self.assertEqual(fv.speaker_keys(raw), [])

    def test_32_scraper不再自带内联实现(self):
        """防回退：_normalize_speaker 里不得再出现内联头衔正则/职称列表。

        ⚠ 只看函数源码是不够的：把函数体换成另一段仍可能绕过。真正的守门是
        test_30 的**行为**一致性（两侧对同一批输入必须同结果）——本用例是
        对「实现形态」的第二道锁：即使行为碰巧一致，只要内联表回来了，
        下次新增职称时就会只改一处、重新分叉。
        """
        import inspect
        src = inspect.getsource(self.S._normalize_speaker)
        self.assertIn('field_vocab.normalize_speaker_key', src)
        # 取函数体（去 docstring）后再查内联痕迹，避免命中 docstring 里的举例
        body = src.split('"""')[-1] if src.count('"""') >= 2 else src
        self.assertNotIn('re.sub', body, '内联英文头衔正则又回来了')
        self.assertNotIn('endswith(suffix)', body, '内联中文职称表又回来了')
        self.assertNotIn("'副教授'", body, '内联中文职称表又回来了')

    def test_33_scraper导入了field_vocab(self):
        """_normalize_speaker 依赖模块级 field_vocab 名，故 import 必须在。"""
        self.assertTrue(hasattr(self.S, 'field_vocab'),
                        'scraper.py 未 import field_vocab（_normalize_speaker 会 NameError）')
        self.assertIs(self.S.field_vocab, fv,
                      'scraper 引用的 field_vocab 与本测试加载的不是同一模块实例')


class SpeakerKeysBackCompatTest(unittest.TestCase):
    """speaker_keys 的既有行为不得被B6 改动（前端一致性守卫依赖它）。"""

    def test_40_既有断言保持(self):
        for raw, want in (('张三教授', ['张三']),
                          ('张三副教授', ['张三']),
                          ('Daniel Winney', ['daniel winney']),
                          (None, []),
                          ('张三、李四', ['张三', '李四'])):
            self.assertEqual(fv.speaker_keys(raw), want, raw)

    def test_41_speaker_keys即normalize别名(self):
        for raw in ('Dr. Daniel Winney', '张三副教授', 'Jean-Claude Dreher'):
            self.assertEqual(fv.speaker_keys(raw), fv.normalize_speaker_key(raw), raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
