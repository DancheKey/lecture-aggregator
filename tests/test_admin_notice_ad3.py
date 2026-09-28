#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AD3 学术事务/会务类通知负向规则测试（2026-09-28）。

背景：2026-09-28 增量水位缺口审计发现 8 条「通知类非讲座」内容规则未拦住
（哲社 4 条学位答辩事务、国际文化学院 4 条会务通知），补 AD3 规则拦之。
详见 reports/gap_audit_20260928.html B类。

三个易踩的坑，由下面的反例守住：
  ① title 常常只是栏目名「学术科研」（list_title 分支）→ 须能靠 topic/body 命中；
  ② 「学位论文答辩」是**真学术报告**常用词（「我国学位论文答辩制度的改革」），
     不能收，只能收事务性名词「答辩安排表」；
  ③ 裸「答辩安排」同样是学术报告常见宾语（「从答辩安排看…管理」），也须排除。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402

AD3 = P.is_academic_admin_notice


class Ad3PositiveTest(unittest.TestCase):
    """通知类必须判True（否则脏数据入库）"""

    def test_degree_arrange_table(self):
        self.assertTrue(AD3('哲学与社会发展学院2025年夏季硕士学位论文答辩安排表'))

    def test_pre_notice(self):
        # cicgz 1072：去掉「预通知」后缀就是一场真研讨会 → 尾缀是唯一判据
        self.assertTrue(AD3('一带一路视域下东南亚汉语人才培养国际研讨会预通知'))

    def test_supplement_notice(self):
        # cicgz 1069
        self.assertTrue(AD3('关于组织广东省第七届哲学社会科学优秀成果奖补报通知'))

    def test_defense_announcement(self):
        # zhx 2465：正文只有教务系统外链，信号只在这里
        self.assertTrue(AD3('', '', '研究生学位论文答辩公告（一直更新）各学院：…'))

    def test_numbered_notice(self):
        # cicgz 1066 / 1061：学会年会系列「第N号通知」，须带「通知」才算
        self.assertTrue(AD3('[会讯]国际汉语教育与文化学术研讨会暨第五届汉语国际传播研究会年会第一号通知'))
        self.assertTrue(AD3('领域外语能力与外语教育（[会讯]第二届语言研究青年学者海上论坛一号通知）'))

    def test_section_title_needs_body(self):
        """坑①：list_title 落栏目名时，title 匹配必然失效，须靠短正文兜底"""
        self.assertFalse(AD3('学术科研', '', ''))
        self.assertTrue(AD3('学术科研', '',
                            '关于2025年秋季学期硕士学位论文答辩安排表的通知'))

    def test_topic_signal(self):
        """topic 常在 title 失效时仍有值"""
        self.assertTrue(AD3('学术科研', '研究生学位论文答辩安排表'))


class Ad3FalsePositiveTest(unittest.TestCase):
    """真学术报告不得误杀——漏抓是脏数据，误杀是真讲座丢失，更不可接受"""

    def test_thesis_defense_policy_is_real_talk(self):
        # 坑②：「学位论文答辩」过宽，已从词表移除
        self.assertFalse(AD3('我国学位论文答辩制度的改革'))

    def test_bare_arrange_phrase_is_real_talk(self):
        # 坑③：裸「答辩安排」过宽，只保留「答辩安排表」
        self.assertFalse(AD3('从答辩安排看高校研究生教育管理'))

    def test_other_real_titles(self):
        for t in ('学位论文答辩的规范与实践',
                  '答辩委员会在研究生培养中的角色',
                  '研究生开题报告写作方法',
                  '中国半导体产业的技术演进',
                  '人工智能与计算社会学的交叉研究',
                  '一带一路视域下东南亚汉语人才培养国际研讨会'):
            self.assertFalse(AD3(t), t)

    def test_empty_input_keeps_record(self):
        """宁留不误杀：信号全空时不得判True"""
        self.assertFalse(AD3('', '', ''))
        self.assertFalse(AD3('', '', ''))

    def test_body_mention_does_not_kill(self):
        """坑④：真讲座正文顺带提及事务词（长正文）不得误杀

        body 兜底带长度门（_AD3_BODY_MAX=300）：只有「整段就是那行公告标题」
        的短正文才采信。真讲座正文动辄上千字，顺带提一句不影响判定。
        """
        filler = '本次报告聚焦研究生教育的制度演进与实践路径。' * 20
        self.assertGreater(len(filler), P._AD3_BODY_MAX)
        self.assertFalse(AD3('研究生教育制度的三十年演进', '研究生教育制度的三十年演进',
                             filler + '硕士学位论文答辩安排表见附件。'))

    def test_long_body_notice_is_known_tradeoff(self):
        """已知取舍（宁留不误杀）：长正文里的纯外链公告页会漏过

        若日后这类页面变多，再考虑加「外链占比」等更强特征，而不是放宽长度门。
        """
        filler = '正文内容。' * 100
        # 事务词落在第 400 字，已在 _AD3_BODY_MAX 窗口之外
        self.assertFalse(AD3('', '', filler[:400] + '硕士学位论文答辩安排表' + filler))


if __name__ == '__main__':
    unittest.main()
