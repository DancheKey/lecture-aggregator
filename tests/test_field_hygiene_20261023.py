# -*- coding: utf-8 -*-
"""字段卫生：单位 / 主讲人 / 地点 / 主题编号（2026-10-23）。

来源：`scripts/audit_data_quality.py` 报出的 4 高 3 中，逐条回源定位后发现
**不是 7 条孤立脏数据，而是 6 个规则漏洞**（全部集中在经济与管理学院老页，
2015-2023）。本文件把每个漏洞的最小复现形态固化成用例。

| 用例 | 源页 | 原症状 |
|---|---|---|
| T1 | em/9891 | 单位取成第 4 个机构「南方金融创新研究院」 |
| T2 | em/4753 | 单位「中国人民银行广州分行主任科员」（含职级） |
| T3 | em/6966 | 主讲人抽成期刊名「Fuzzy Sets」、单位是期刊残片 |
| T4 | em/5104 | 地点吞掉后面无标签的简介整段 |
| T5 | em/3987 | 姓名「伟利奇科(Dr.Velychko)」括号原文未剥 |
| T6 | em/4753 | 「主题一：题目」形态不拆，topic 留编号残片「二:…」 |
| T7 | em/9637 | bio 用「是」连接机构，单位反推失效（空） |

离线单测：不发网络请求、不走 LLM。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scraper'))
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers  # noqa: E402


def _html(*paras):
    return ('<html><body><div class="content">'
            + ''.join('<p>%s</p>' % p for p in paras)
            + '</div></body></html>')


def _parse(body, title=''):
    return parsers.parse_detail(
        body, 'http://em.scnu.edu.cn/a/20200101/1.html',
        '经济与管理学院', '大学城', list_title=title, skip_news_filter=True)


def _recs(out):
    if isinstance(out, list):
        return out
    return [out] if out else []


class AffiliationUnitTest(unittest.TestCase):
    """单位抽取：非高校机构 + 尾部职级剥离。"""

    def test_t1_enterprise_group_not_4th_institute(self):
        """T1（em/9891）：业界主讲人 → 取**第一个**机构，且不能是含「研究院」的第 4 个。

        根因：`_UNIT_RE` 原来只有高校/科研院所后缀（大学|学院|研究院|研究所…），
        「广东宏巨投资集团」不含任何一项 → 一路跳过，命中第 4 个机构
        「南方金融创新研究院」，单位完全跑偏。
        """
        body = _html(
            '【题目】私募股权投资实务与案例',
            '【时间】2023年12月15日（周五）14:30',
            '【地点】文二栋536会议室',
            '【主讲人】胡挺',
            '【主讲人简介】胡挺，博士，广东宏巨投资集团合伙人，广东国民资产管理公司董事，'
            '深圳红色大业科技有限公司合伙人，南方金融创新研究院专家，肇庆农商银行独立董事。',
        )
        r = _recs(_parse(body))[0]
        self.assertEqual(r.get('speaker'), '胡挺')
        self.assertEqual(r.get('speakerAffiliation'), '广东宏巨投资集团')

    def test_t2_strip_role_from_affiliation(self):
        """T2（em/4753）：括号内单位带职级「主任科员」→ 剥掉。"""
        body = _html(
            '主题二：自贸区的发展历程与广东自贸区金融改革创新',
            '主讲人：李剑（中国人民银行广州分行主任科员）',
            '时间：3月24日（周四）下午14：30',
            '地点：学院五楼会议室',
        )
        r = _recs(_parse(body))[0]
        self.assertEqual(r.get('speaker'), '李剑')
        self.assertEqual(r.get('speakerAffiliation'), '中国人民银行广州分行')

    def test_t7_bio_affiliation_via_shi(self):
        """T7（em/9637）：bio「姓名**是**机构…」——分隔符表原只有逗号/顿号/空格。"""
        body = _html(
            '题目：Innovation Strategy and Industrial Policy',
            '时间：6月20日（周二）15:00',
            '地点：文二栋301会议室',
            '主讲人：Marco DITOMMASO教授',
            '主讲人简介：Marco DITOMMASO是意大利博洛尼亚大学应用经济学的全职教授，'
            '意大利高校联合应用经济研究中心（c.MET05）联合创始人兼主任。',
        )
        r = _recs(_parse(body))[0]
        self.assertEqual(r.get('speaker'), 'Marco DITOMMASO')
        self.assertIn('博洛尼亚大学', r.get('speakerAffiliation') or '')


class SpeakerGuardTest(unittest.TestCase):
    """主讲人：不能从简介里抽英文期刊名 / 括号外文名要剥。"""

    def test_t3_journal_name_is_not_speaker(self):
        """T3（em/6966）：bio 里的「Fuzzy Sets」曾被当成英文姓名。

        根因：OCR 兜底路径把**整段简介**喂给 `_split_english_speaker`，而该函数
        只按「首字母大写 + 空格 + 大写」找英文姓名，简介里的期刊名完全符合形态。
        修复是在函数入口加「简介特征」守卫（长度 > 60 或含「，男，/研究方向/发表论文」等）。
        """
        body = _html(
            '主题：基于谈判能力的旅游O2O供应链定价与服务策略研究',
            '时间：2019年5月9日（周四）下午',
            '主讲人：谭春桥教授（中南大学商学院）',
            '报告人：谭春桥，男，中南大学商学院教授，博士生导师，管理学博士，'
            '中国运筹学会员。主要研究方向是经济理论与建模、博弈论及应用。'
            '已在国内外重要期刊Fuzzy Sets and Systems、IEEE Transactions on Fuzzy System、'
            'Asia-Pacific Journal of Operational Research 等发表论文40多篇。',
        )
        r = _recs(_parse(body))[0]
        self.assertEqual(r.get('speaker'), '谭春桥')
        self.assertEqual(r.get('speakerAffiliation'), '中南大学商学院')
        self.assertNotIn('Fuzzy', r.get('speakerAffiliation') or '')

    def test_t5_strip_latin_parenthetical(self):
        """T5（em/3987）：姓名后括号内是拉丁原名 → 剥离，且不被 F3 姓氏表误杀。

        「伟利奇科」首字「伟」不在百家姓表，F3 终检会判 doubt 并清空 speaker；
        括注本身即「这是音译名」的确证，据此放行（`_translit_ok`）。
        """
        body = _html(
            '题目：转型经济分析：以中国为例',
            '主讲人：伟利奇科（Dr. Velychko）博士',
            '时间：4月9日(周四)下午14：30',
            '地点：学院三楼301会议室',
        )
        r = _recs(_parse(body))[0]
        self.assertEqual(r.get('speaker'), '伟利奇科')


class LocationLeakTest(unittest.TestCase):
    """地点值不得吞掉后续无标签的正文段。"""

    def test_t4_location_stops_before_bio(self):
        """T4（em/5104）：「地点：X课室」后面紧跟无标签简介段 → 地点吞到 60 字上限。"""
        body = _html(
            '第一场',
            '题目：The Tragedy of Trust',
            '主讲人：姜树广博士(浙江财经大学)',
            '时间：2016年10月13日（周四）下午2：00-3:30',
            '地点：文三栋MBA中心501课室',
            '姜树广，浙江财经大学杰出青年学者，深圳证券交易所博士后，'
            '山东大学经济学博士，法国国家科研中心访问学者，研究领域为行为经济学和实验经济学。',
        )
        r = _recs(_parse(body))[0]
        self.assertEqual(r.get('location'), '文三栋MBA中心501课室')
        self.assertEqual(r.get('speakerAffiliation'), '浙江财经大学')


class TopicNumberTest(unittest.TestCase):
    """「主题N：题目」——编号后直接跟题目，无「题目：」标签。"""

    def test_t6_split_and_no_number_residue(self):
        """T6（em/4753）：应拆 2 条，且 topic 不得残留编号前缀「二:」。"""
        body = _html(
            '主题一：中国期货市场的现状及发展',
            '主讲人：肖成（广发期货有限公司副总董事长）',
            '主题二：自贸区的发展历程与广东自贸区金融改革创新',
            '主讲人：李剑（中国人民银行广州分行主任科员）',
            '时间：3月24日（周四）下午14：30',
            '地点：学院五楼会议室',
        )
        recs = _recs(_parse(body))
        self.assertEqual(len(recs), 2, '应拆成 2 场')
        topics = sorted(r.get('topic') or '' for r in recs)
        self.assertEqual(topics, ['中国期货市场的现状及发展',
                                  '自贸区的发展历程与广东自贸区金融改革创新'])
        for r in recs:
            self.assertFalse((r.get('topic') or '').startswith('二'),
                             'topic 不得残留编号：%r' % r.get('topic'))
        self.assertEqual(sorted(r.get('speaker') or '' for r in recs),
                         ['李剑', '肖成'])

    def test_t6_single_number_does_not_split(self):
        """防回归：只出现一处「主题一：」时不得拆场。"""
        body = _html(
            '主题一：中国期货市场的现状及发展',
            '主讲人：肖成（广发期货有限公司）',
            '时间：3月24日（周四）下午14：30',
            '地点：学院五楼会议室',
        )
        self.assertEqual(len(_recs(_parse(body))), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
