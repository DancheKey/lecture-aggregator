# -*- coding: utf-8 -*-
"""英文主讲人姓名形态守卫（2026-10-06，maths8855 回归）。

## 病灶

源页「报告人: Quoc-Hung NGUYEN 副研究员 (邀请人：袁源)」，抓回的记录
`speaker=''`，但 speakerTitle/speakerAffiliation/speakerBio 全部正常——
名字这一项丢了。三处口径同时挡住它：

  ① `_looks_like_real_name('Quoc-Hung NGUYEN')` → False
     英文分支的分隔符集 `[.'·]` 不含 `-`，`Quoc-Hung` 断在连字符处；
  ② `_split_english_speaker` 的姓名 token `[A-Z][a-z]+` 既不认连字符段，
     也不认全大写词（`NGUYEN` 无小写 → 失配），且
  ③ 它的职称 lookahead 枚举里没有「副研究员」（只有 教授/副教授/助理教授/研究员）。

链条：规则抓空 → F3-EN 标题兜底也走同一函数（标题里名字同样是
`Quoc-Hung NGUYEN`）→ 仍空 → LLM 提的名字被 B 以溯源闸门拒绝
（`llmRejected: speaker`）→ 落库为空。

## 本套件锁四件事

  ① 连字符 + 全大写姓的姓名能被认作真实姓名；
  ② 职位词仍被拦截（放开门闩后的反向用例：Postdoctoral-Fellow 等）；
  ③ `_split_english_speaker` 对「姓名 + 副研究员 + 括号单位」与纯标题两种入参
     都能取出姓名；
  ④ 端到端：合成同构页面解析后 speaker / speakerSource 正确。

运行：python tests/test_speaker_latin_name.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

# ⚠ 必须在 import parsers 之前关掉双轨（parsers._USE_LLM_* 是导入即求值的模块常量）。
# 本套件断言的是**规则路径**的抽取结果；开着 LLM 时结论会掺进模型产出，
# 本地有 .env 密钥时通过、CI 无密钥时失败——正是要避免的那种环境漂移。
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers as P  # noqa: E402

PAGE = (
    '<html><body><div class="content">'
    '<h1>勷勤数学•专家报告-Quoc-Hung NGUYEN</h1>'
    '<p>题　　　目：Flexibility through moving vortices</p>'
    '<p>报　告　人： Quoc-Hung NGUYEN 副研究员 (邀请人：袁源)</p>'
    '<p>　　　　　　中国科学院数学与系统科学研究院</p>'
    '<p>时　　间： 10月6日 14:00-15:00</p>'
    '<p>地　　点：数科院西楼111报告厅</p>'
    '<p>报告人简介: Prof. Quoc-Hung NGUYEN is an Associate Professor at AMSS, CAS.</p>'
    '</div></body></html>'
)

URL = 'http://maths.scnu.edu.cn/a/20261006/8855.html'


def parse(html=PAGE, url=URL):
    out = P.parse_detail(html, url, '数学科学学院', '石牌', default_year=2026)
    rec = out[0] if isinstance(out, list) and out else out
    return rec if isinstance(rec, dict) else {}


class LatinNameValidatorTest(unittest.TestCase):
    """①② 姓名校验：放开门闩的同时必须继续拦住职位词。"""

    def test_01_连字符加全大写姓通过(self):
        self.assertTrue(P._looks_like_real_name('Quoc-Hung NGUYEN'),
                        '连字符名/全大写姓被判非人名 —— maths8855 主讲人被清空的根因之一')
        self.assertTrue(P._looks_like_real_name('Quoc Hung NGUYEN'))
        self.assertTrue(P._looks_like_real_name('Tsz-Kei Wong'))

    def test_02_既有英文名不受影响(self):
        for good in ('Yan Zhang', 'Tamás Dalmay', 'Eric T. Chung',
                     'Masanori Hanada', 'Bryan Strange'):
            self.assertTrue(P._looks_like_real_name(good), good)

    def test_03_职位词仍被拦截(self):
        """放开门闩后，连写职位仍须逐词命中拦截表（分词分隔符已同步补 `-`）。"""
        for bad in ('Postdoctoral Associate', 'Postdoctoral-Fellow',
                    'Associate Professor', 'Visiting-Scholar',
                    'Keynote Speaker', 'University of Oslo'):
            self.assertFalse(P._looks_like_real_name(bad), bad)


class WordlistRepairTest(unittest.TestCase):
    """2026-10-07 拆闸配套词表修复：存量体检发现的真名逐一钉死（防回退）。"""

    def test_01_弯引号名(self):
        """Timi O’Neill（aol 1029，存量真名）：分隔符集补 `’`。"""
        self.assertTrue(P._looks_like_real_name('Timi O’Neill'), 'Timi O’Neill')
        self.assertTrue(P._looks_like_real_name("Timi O'Neill"), '直引号变体')

    def test_02_括号注记名(self):
        """Yiu Por (Vincent) Chen（em 6428，存量真名）：括号段剥离后按空格名匹配。"""
        self.assertTrue(P._looks_like_real_name('Yiu Por (Vincent) Chen'))

    def test_03_新补姓氏(self):
        """关/奉/员（关淑华/奉国和/员巧云，存量真名）补进姓氏表。"""
        for name in ('关淑华', '奉国和', '员巧云'):
            self.assertTrue(P._looks_like_real_name(name), name)

    def test_04_地名黑名单消歧(self):
        """吴重庆（geography 46，存量真名）：重庆从子串黑名单迁至整串名单。"""
        self.assertTrue(P._looks_like_real_name('吴重庆'), '吴重庆')
        self.assertFalse(P._looks_like_real_name('重庆'), '孤立「重庆」仍须拦截')

    def test_05_长题目串被结构上限拦截(self):
        """论文题目长句（实验三漏网脏样本）：词数/长度硬上限，不靠人肉加词。"""
        self.assertFalse(P._looks_like_real_name(
            'The One-Child Policy and Intergenerational Mobility'))
        self.assertFalse(P._looks_like_real_name(
            'Juergen Stuhler Vice President Quantum Technologies'))
        # 边际内不受影响（生产库最长 5 词/28 字符）
        self.assertTrue(P._looks_like_real_name('A.M.A. van Deemen'))

    def test_06_叙事脏值进整串硬否决名单(self):
        """现为台北/为喜迎华/日程安排（psy 1251/221/283）：拆闸后须硬否决而非 doubt。"""
        for bad in ('现为台北', '为喜迎华', '日程安排'):
            self.assertEqual(P._name_shape_verdict(bad), 'reject', bad)

    def test_07_三值判定语义(self):
        """姓氏表查无从否决降为 doubt；严格口径对 doubt 仍 False（规则路径不变）。"""
        self.assertEqual(P._name_shape_verdict('郄贵洲'), 'doubt')
        self.assertFalse(P._looks_like_real_name('郄贵洲'))


class EnglishSpeakerSplitTest(unittest.TestCase):
    """③ 姓名切分：正文值与标题两种入参。"""

    def test_01_正文值_姓名职称括号单位(self):
        name, aff, title = P._split_english_speaker(
            'Quoc-Hung NGUYEN 副研究员 (  中国科学院数学与系统科学研究院')
        self.assertEqual(name, 'Quoc-Hung NGUYEN')
        self.assertEqual(aff, '', '尾随职称不应被当单位')

    def test_02_标题入参_标题兜底路径(self):
        name, aff, title = P._split_english_speaker('勷勤数学•专家报告-Quoc-Hung NGUYEN')
        self.assertEqual(name, 'Quoc-Hung NGUYEN',
                         'F3-EN 标题兜底也走本函数，标题里的名字必须取得到')

    def test_03_副研究员等职称在lookahead内(self):
        """姓名后的职称枚举已收敛到职称主表，副研究员/助理研究员须能收口。"""
        for tail in ('Quoc-Hung NGUYEN 副研究员', 'Quoc-Hung NGUYEN 助理研究员',
                     'Quoc-Hung NGUYEN 特聘教授', 'Quoc-Hung NGUYEN 教授'):
            name, _, _ = P._split_english_speaker(tail)
            self.assertEqual(name, 'Quoc-Hung NGUYEN', tail)

    def test_04_既有形态不回归(self):
        self.assertEqual(P._split_english_speaker('Yan Zhang, University of Oslo')[0],
                         'Yan Zhang')
        self.assertEqual(P._split_english_speaker('Yi Zhou（University of California, '
                                                  'Berkeley）')[0], 'Yi Zhou')


class EndToEndTest(unittest.TestCase):
    """④ 同构页面端到端。"""

    def test_01_主讲人被提取(self):
        r = parse()
        self.assertEqual(r.get('speaker'), 'Quoc-Hung NGUYEN',
                         '端到端仍取不到主讲人：%r' % r.get('speaker'))
        self.assertEqual(r.get('speakerSource'), 'label')
        self.assertEqual(r.get('inviter'), '袁源', '邀请人分离被破坏')
        self.assertEqual(r.get('speakerTitle'), '副研究员', '职称后缀分离被破坏')
        self.assertTrue(r.get('location'), '地点抽取被破坏')

    def test_02_中文主讲人不回归(self):
        html = PAGE.replace('报　告　人： Quoc-Hung NGUYEN 副研究员 (邀请人：袁源)',
                            '报　告　人： 陈树敏 教授 (邀请人：杨舟)')
        r = parse(html, 'http://maths.scnu.edu.cn/a/20261013/8854.html')
        self.assertEqual(r.get('speaker'), '陈树敏')


if __name__ == '__main__':
    unittest.main(verbosity=2)
