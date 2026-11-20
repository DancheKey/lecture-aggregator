#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""「大赛/竞赛/比赛」误杀真讲座的回归（2026-11-20，xz/366 实测）。

背景：行知书院「全国市场调研大赛写作指导讲座」（a/20261026/366.html）
是完整讲座——海报上主讲人林雨、时间 10-29 16:00、地点南区教学楼 102。
但标题含「大赛」，被 is_non_lecture_title 的 _NON_LECTURE_KW 一票否决，
整页丢弃并记入拒绝台账，至今未入库。

判据（本次修复）：真赛事标题以「大赛/竞赛/比赛」**收尾**（如「研究生职业
规划大赛」「算法和程序设计竞赛」），而「XX大赛写作指导讲座」的比赛词后
仍有讲座词（LECTURE_KW 单一事实源）——后者是真讲座，放行。
保守：仅当唯一命中的非讲座词是比赛词、且比赛词之后仍有讲座词才豁免；
同时命中喜讯/征文/答辩等其它非讲座词时照旧一票否决。

运行：python tests/test_competition_title_guard_20261120.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 与 golden 同纪律：import parsers 前锁纯规则基线，避免本机 .env 触发真实大模型
os.environ.setdefault('SCNU_LLM_TEXT', '0')
os.environ.setdefault('SCNU_LLM_RICH', '0')
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
import parsers as P  # noqa: E402


class CompetitionTitleGuardTest(unittest.TestCase):
    """比赛词不再一票否决「XX大赛写作指导讲座」类真讲座。"""

    def test_xz366_real_lecture_allowed(self):
        # 本次事故目标：全国市场调研大赛写作指导讲座（真实讲座，海报完整）
        self.assertFalse(P.is_non_lecture_title('全国市场调研大赛写作指导讲座'))

    def test_competition_word_at_tail_still_blocked(self):
        # 真赛事：比赛词收尾，没有后续讲座词 → 照旧拦
        for t in ('研究生职业规划大赛', '算法和程序设计竞赛',
                  '第八届“勷勤论坛”之“四院联合学术报告比赛”决赛',
                  '全国大学生创新创业大赛'):
            with self.subTest(title=t):
                self.assertTrue(P.is_non_lecture_title(t), t)

    def test_competition_then_lecture_word_allowed(self):
        # 比赛词后接 LECTURE_KW 中的讲座词 → 真讲座，放行。
        # 注意：「教学竞赛示范课及参赛经验交流分享」不含 LECTURE_KW（"分享"≠"分享会"），
        # 按保守口径仍拦——不为个例放宽规则，避免误放真赛事。
        for t in ('教学大赛优秀选手经验分享工作坊',
                  '知识产权竞赛专题讲座',
                  '教学竞赛研讨会将于下周举行'):
            with self.subTest(title=t):
                self.assertFalse(P.is_non_lecture_title(t), t)

    def test_other_nonlecture_keywords_still_blocked(self):
        # 其它非讲座词不受豁免影响：与比赛词共现时仍一票否决
        for t in ('喜讯：我校获全国大赛一等奖', '征文比赛通知',
                  '关于举办教学大赛的通知', '获奖名单公示（大赛）'):
            with self.subTest(title=t):
                self.assertTrue(P.is_non_lecture_title(t), t)

    def test_plain_lecture_unaffected(self):
        # 普通讲座/赛事无关标题行为不变
        for t in ('学术讲座', '量子力学前沿报告会', ''):
            with self.subTest(title=t):
                self.assertFalse(P.is_non_lecture_title(t), t)

    def test_lecture_kw_single_source(self):
        # 豁免判据必须复用 LECTURE_KW 单一事实源，不另写副本
        self.assertIn('讲座', P.LECTURE_KW)
        self.assertIn('工作坊', P.LECTURE_KW)
        self.assertIn('报告会', P.LECTURE_KW)


if __name__ == '__main__':
    unittest.main(verbosity=2)
