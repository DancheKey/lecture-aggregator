# -*- coding: utf-8 -*-
"""多场拆分候选9（第N场+字段块）/候选10（议程时段行）离线单测。

不发网络请求：直接对构造的正文文本调用 _detect_* 与 detect_multi_session。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scraper'))
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers  # noqa: E402


# ── 候选10：议程时段行（em/7636 型）──
AGENDA = (
    '学术活动当前位置: 首页 » 学术活动 » 2020年新年论坛 2020-01-09 13:45:00 '
    '来源: 院科研办 经济与工商管理分论坛地点:文科 2 栋 301 '
    '时间: 2020 年 1 月 9 日(周四)下午第一节: 13:30-15:30 '
    '13:30-14:10 张鹏: Time-consistent strategies for multiperiod mean–VaR portfolio selection '
    '14:10-14:50 刘愿、张磊:星星之火,可以燎原——洋务企业与近代中国民族工业发展 '
    '14:50-15:30 连洪泉、李佳敏:工资上涨和幸福感茶歇: 15:30-15:40 第二节: 15:40-17:40 '
    '15:40-16:20 徐思:银行贷款还是发行债券？利率市场化与融资渠道选择 '
    '16:20-17:00 李增福、曾林:环境管制与企业的策略性负债 '
    '17:00-17:40 李铭杰:央行购买企业债券与中小企业融资缺口'
)

# ── 候选9：第N场 + 字段块（em/3705 型，两场时间相同）──
NTH_FIELD = (
    '学术活动当前位置: 首页 » 学术活动 » 第107期和第108期"华南经济论坛"开讲通知 '
    '2013-12-20 11:04:00 来源: 院科研办 '
    '第一场题目:Are Managers Allied with Shareholders in the Treatment of Employees? '
    'Evidence from China 主讲人:钟宁桦副教授同济大学'
    '时间:2013年12月20日(周五)上午9:00-12:00 地点:文二栋五楼会议室'
    '第二场题目:agglomeration and markup 主讲人:陆毅教授新加坡国立大学'
    '时间:2013年12月20日(周五)上午9:00-12:00 地点:文二栋五楼会议室'
)

# 单讲座页：含一个「第3期」标记，不应拆
SINGLE = (
    '学术活动 2024-05-01 10:00:00 讲座通知 '
    '题目:机器学习前沿 主讲人:王五 教授 清华大学 '
    '时间:2024年5月10日(周五)下午2:30 地点:计算机学院101'
)


class AgendaSlotTest(unittest.TestCase):
    def test_agenda_splits_reports_only(self):
        """7636 型：6 个报告入选，节次区间(120分)/茶歇(10分)行剔除。"""
        r = parsers._detect_agenda_slot_sessions(AGENDA)
        self.assertEqual(len(r), 6, '应拆出 6 个报告')
        self.assertEqual([s['start'].strftime('%H:%M') for s in r],
                         ['13:30', '14:10', '14:50', '15:40', '16:20', '17:00'])
        # 节次区间 13:30-15:30 / 15:40-17:40 不得入选
        self.assertFalse(any(s['end'] - s['start'] > __import__('datetime').timedelta(minutes=60)
                             for s in r))
        # 茶歇 15:30-15:40 不得入选
        self.assertFalse(any(s['start'].strftime('%H:%M') == '15:30' for s in r))

    def test_agenda_speaker_extracted(self):
        """姓名串由候选给出（议程表无「主讲人:」标签，逐块解析取不到）。"""
        r = parsers._detect_agenda_slot_sessions(AGENDA)
        spk = [s.get('speaker') for s in r]
        self.assertIn('张鹏', spk)
        self.assertIn('刘愿、张磊', spk)     # 多人合报保持原样
        self.assertIn('李增福、曾林', spk)

    def test_agenda_topic_clean(self):
        """题目尾部不得残留下一节标题/茶歇（「工资上涨和幸福感茶歇」→「工资上涨和幸福感」）。"""
        r = parsers._detect_agenda_slot_sessions(AGENDA)
        topics = [s['topic'] for s in r]
        self.assertIn('工资上涨和幸福感', topics)
        self.assertFalse(any('茶歇' in t for t in topics))

    def test_single_slot_no_split(self):
        """仅 1 个时段行不触发。"""
        r = parsers._detect_agenda_slot_sessions(
            '讲座通知 9:00-10:00 张三:题目A')
        self.assertEqual(len(r), 0)


class NthFieldTest(unittest.TestCase):
    def test_nth_field_splits_two(self):
        """3705 型：两个「第N场」+ 字段块，识别 2 场。"""
        r = parsers._detect_nth_field_sessions(
            NTH_FIELD, title='第107期和第108期“华南经济论坛”开讲通知')
        self.assertEqual(len(r), 2)
        self.assertIn('agglomeration and markup', [s['topic'] for s in r])

    def test_nth_field_no_title_marker_leak(self):
        """title 里的「第107期/第108期」是模板重复，不得成为场次。"""
        r = parsers._detect_nth_field_sessions(
            NTH_FIELD, title='第107期和第108期“华南经济论坛”开讲通知')
        nos = [s['_no'] for s in r]
        self.assertTrue(all(('107' not in n and '108' not in n) for n in nos), nos)

    def test_nth_field_numbered_flag(self):
        """编号型多场须打 _numbered，以豁免「时间互异」守卫。"""
        r = parsers._detect_nth_field_sessions(
            NTH_FIELD, title='第107期和第108期“华南经济论坛”开讲通知')
        self.assertTrue(all(s.get('_numbered') for s in r))

    def test_nth_field_requires_two_blocks(self):
        """单场页不触发。"""
        r = parsers._detect_nth_field_sessions(
            '第一场题目:主题A 主讲人:张三 时间:2024年5月10日 9:00-10:00 地点:101',
            title='讲座通知')
        self.assertEqual(len(r), 0)

    def test_nth_field_requires_fields(self):
        """只有「第N场」标记、无题目/主讲人字段 → 不触发（防面包屑误拆）。"""
        r = parsers._detect_nth_field_sessions(
            '第一场 9:00-10:00 第二场 10:00-11:00', title='讲座通知')
        self.assertEqual(len(r), 0)


class NumberedExemptionTest(unittest.TestCase):
    def test_same_time_two_sessions_kept(self):
        """两场时间相同但为显式编号型 → 不被「时间互异」守卫清空。"""
        r = parsers.detect_multi_session(
            NTH_FIELD, title='第107期和第108期“华南经济论坛”开讲通知')
        self.assertGreaterEqual(len(r), 2, '编号型多场即便同时间也应保留拆分')

    def test_single_page_not_split(self):
        """普通单讲座页不得被新候选拆出多场。"""
        r = parsers.detect_multi_session(SINGLE, title='讲座通知')
        self.assertEqual(len(r), 0)



# ── 2026-09-26 新增：候选10泛化(V2/V4)、候选11编号议程行、候选1多人异名兜底、质量闸门 ──

# em/7264 型：时段 人名(单位) 题目：X（候选10 V2）
SLOT_V2 = (
    '学术活动 社会主义经济理论前沿论坛议程安排 2019-10-12 09:28:00 '
    '时间:2019年10月12日地点:教育科学学院125会议室 '
    '第一节:专家大会发言主持人:李仁贵(经济学动态编辑部) '
    '8:30-9:00 郭克莎(华侨大学经济与金融学院院长) 题目:推动制造业高质量发展的战略思考 '
    '9:00-9:30 黄少安(山东大学经济研究院院长) 题目:新中国经济学发展历程 '
    '9:30-10:00 史晋川(浙江大学文科资深教授) 题目:中国经济学发展的回顾与展望 '
    '茶歇:10:30-10:40 第二节:专家大会发言主持人:董志强(华南师范大学经济与管理学院)'
)

# em/5983 型：编号行 N. 人名（单位） 题目：X（候选11）
NUMBERED_AGENDA = (
    '2017岭南经济论坛会议主题:市场经济与新技术革命会 议议 程 '
    '12 月 2 日上午 08:30--09:20 报到 (地点:华师图书馆后栋一楼国际会议厅) '
    '09:30-10:00 大会开幕式 主持人:赵祥 10:10-12:00 大会主题报告 '
    '1. 张曙光 (著名经济学家、中国社科院研究员) 题目:新经济对经济学理论的挑战 '
    '2. 晏智杰 (著名经济学家、北京大学经济学院前院长) 题目:坚持市场化改革方向 '
    '3. 董志强 (华南师范大学经济与管理学院副院长) 题目:理解企业家精神和营商环境 '
    '12:30-13:30 午餐 16:25-17:10 获奖作者代表主题发言: '
    '1. 王鹏 (暨南大学特区港澳经济研究所副所长) 题目:"一带一路"背景下粤港澳联合打造综合创新试验田研究 '
    '2. 罗明忠 (华南农业大学经济管理学院教授) 题目:社会资本、风险容忍与农民创业组织形式选择'
)

# em/3771 型：主题：X 主讲人：Y 时间：30分钟（候选1 多人异名兜底）
LABEL_SPEAKERS = (
    '华南经济论坛之2015年新年学术研讨会议程 2015-01-10 11:42:00 '
    '主题:市场、法治与增长时间: 2015 年 1 月 10 日地点:大学城文科 3 栋 501课室 '
    '第二节: 9:20-10:20 主持人: 王智波 '
    '主题:政府的补贴偏好与企业的盈余管理行为主讲人:李增福时间:30分钟 '
    '主题:新基金发行:一场拆东墙补西墙的游戏？ 主讲人:彭文平时间:30分钟 '
    '主题:货币政策、时变预期与融资成本主讲人:张勇时间:30分钟'
)

# ctld/1280 型：议程脚手架行（质量闸门应拦下）
JUNK_SCAFFOLD = (
    '高校公共计算机课程教学改革与创新高峰论坛暨教指委2020年学术年会 2020-12-22 15:00:00 '
    '会议日程 1. 前置会议: 2. 会议报到: 3. 合影留念'
)


class AgendaSlotV2Test(unittest.TestCase):
    def test_v2_name_aff_topic(self):
        """「时段 名(单位) 题目：X」行：时间与人名对齐本场（不串到下一场）。"""
        r = parsers._detect_agenda_slot_sessions(SLOT_V2, title='议程安排')
        self.assertEqual(len(r), 3)
        self.assertEqual(r[0]['speaker'], '郭克莎')
        self.assertEqual(r[0]['start'].strftime('%H:%M'), '08:30')
        self.assertEqual(r[0]['topic'], '推动制造业高质量发展的战略思考')
        self.assertEqual(r[1]['speaker'], '黄少安')
        self.assertEqual(r[1]['start'].strftime('%H:%M'), '09:00')

    def test_v2_affiliation_cleaned(self):
        """括号内单位剥尾部职务词。"""
        r = parsers._detect_agenda_slot_sessions(SLOT_V2, title='议程安排')
        self.assertEqual(r[0].get('affiliation'), '华侨大学经济与金融学院')


class NumberedAgendaTest(unittest.TestCase):
    def test_numbered_rows_split(self):
        """「N. 名(单位) 题目：」编号议程行 → 各场共享前方环节时段。"""
        r = parsers._detect_numbered_agenda_sessions(NUMBERED_AGENDA, title='2017岭南经济论坛')
        self.assertEqual(len(r), 5)
        self.assertEqual([s['speaker'] for s in r],
                         ['张曙光', '晏智杰', '董志强', '王鹏', '罗明忠'])
        # 上午 3 场共享 10:10（主题报告环节窗），下午 2 场 16:25（获奖发言环节窗）
        self.assertEqual([s['start'].strftime('%H:%M') for s in r],
                         ['10:10', '10:10', '10:10', '16:25', '16:25'])
        self.assertTrue(all(s.get('_numbered') for s in r))

    def test_aff_token_institution(self):
        """括号内「著名经济学家、中国社科院研究员」取含机构关键词的分段。"""
        r = parsers._detect_numbered_agenda_sessions(NUMBERED_AGENDA, title='2017岭南经济论坛')
        self.assertEqual(r[0].get('affiliation'), '中国社科院')

    def test_single_numbered_not_split(self):
        """仅 1 条编号行不触发。"""
        r = parsers._detect_numbered_agenda_sessions(
            '10:10-12:00 大会主题报告 1. 张曙光 (中国社科院研究员) 题目:X',
            title='论坛')
        self.assertEqual(len(r), 0)


class LabelSpeakersTest(unittest.TestCase):
    def test_duration_only_rows_split(self):
        """「主题：X 主讲人：Y 时间：30分钟」多人异名 → 兜底拆分，共享页眉时钟。"""
        r = parsers.detect_multi_session(LABEL_SPEAKERS, title='新年学术研讨会议程')
        self.assertEqual(len(r), 3)
        self.assertEqual([s['speaker'] for s in r], ['李增福', '彭文平', '张勇'])
        self.assertTrue(all(s.get('_same_time_ok') for s in r))
        self.assertEqual(r[0]['start'].strftime('%H:%M'), '09:20')

    def test_single_speaker_not_split(self):
        """同一主讲人的多题清单不拆（互异率守卫）。"""
        t = ('研讨会议程 2015-01-10 09:00:00 时间: 2015 年 1 月 10 日 '
             '主题:题目A主讲人:李增福时间:30分钟 '
             '主题:题目B主讲人:李增福时间:30分钟')
        r = parsers.detect_multi_session(t, title='议程')
        self.assertEqual(len(r), 0)


class MsGateFilterTest(unittest.TestCase):
    def test_junk_scaffold_rejected(self):
        """「前置会议/会议报到/合影」脚手架候选整体拦下 → 落至下一候选。"""
        r = parsers.detect_multi_session(JUNK_SCAFFOLD, title='年会通知')
        self.assertEqual(len(r), 0)

    def test_junk_topic_clock_logistics(self):
        """时钟+后勤词的伪场次主题被剔（em/5983 实测特征）。"""
        self.assertTrue(parsers._is_junk_session_topic(
            '市场经济与新技术革命会议议程 12 月 2 日上午 08:30--09:20 报到'))
        self.assertTrue(parsers._is_junk_session_topic(
            '中国三大湾区经济带比较 12:30-13:30 午餐 ('))
        self.assertFalse(parsers._is_junk_session_topic(
            '中国三大湾区经济带比较及深圳在粤港澳大湾区经济带中的地位与作用'))

    def test_same_monthday_diff_year_rejected(self):
        """同 (月,日) 跨年并存 → 日期必有一场取错年，候选整体放弃。"""
        import datetime
        cand = [
            {'topic': '报告A', 'start': datetime.datetime(2020, 1, 8, 15, 0)},
            {'topic': '报告B', 'start': datetime.datetime(2021, 1, 8, 15, 0)},
        ]
        self.assertEqual(parsers._ms_gate_filter(cand), [])

    def test_topic_trailing_slot_stripped(self):
        """题目尾部粘连的时段残片被清理。"""
        self.assertEqual(
            parsers._clean_session_topic('财政"股权投资":现状、矛盾与出路 17:10-17:20'),
            '财政"股权投资":现状、矛盾与出路')


class DedupGuardExemptionTest(unittest.TestCase):
    def test_same_time_ok_not_killed(self):
        """共享页眉时间的多人异名兜底结果不被「speaker 重复+时间全同」守卫清空。"""
        import datetime
        sessions = [
            {'topic': '报告A', 'start': datetime.datetime(2016, 1, 9, 11, 33),
             'speaker': '黄炜', '_same_time_ok': True},
            {'topic': '报告B', 'start': datetime.datetime(2016, 1, 9, 11, 33),
             'speaker': '黄炜', '_same_time_ok': True},
        ]
        self.assertEqual(len(parsers._ms_dedup_guard(sessions)), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)


class Ms5RenumberTest(unittest.TestCase):
    """MS5 逐条剔除后 index/count 重算（2026-09-26 幽灵计数修复）。"""

    HTML = (
        '<html><head><title>系列讲座通知</title></head><body>'
        '<div class="wp_articlecontent">'
        '发布时间：2024-06-01 10:00'
        '第一场题目:Past Session Topic 主讲人:张三教授北京大学 '
        '时间:2024年5月1日(周三)上午9:00-12:00 地点:文二栋五楼会议室'
        '第二场题目:Future Session Topic 主讲人:李四教授清华大学 '
        '时间:2024年7月1日(周一)上午9:00-12:00 地点:文二栋五楼会议室'
        '</div></body></html>'
    )

    @classmethod
    def setUpClass(cls):
        parsers.parse_detail.cache_clear() if hasattr(parsers.parse_detail, 'cache_clear') else None
        out = parsers.parse_detail(
            cls.HTML, 'http://em.scnu.edu.cn/a/20240601/9000.html',
            '经济与管理学院', None)
        cls.recs = out if isinstance(out, list) else [out] if out else []

    def test_retro_session_dropped(self):
        """过期场次被 MS5 剔除，仅剩未来场次。"""
        self.assertEqual(len(self.recs), 1)
        self.assertEqual(self.recs[0].get('topic'), 'Future Session Topic')

    def test_single_remaining_cleared(self):
        """仅剩 1 条时编号清空、isMultiLecture=False（幽灵计数不再出现）。"""
        r = self.recs[0]
        self.assertIsNone(r.get('lectureIndex'))
        self.assertIsNone(r.get('lectureCount'))
        self.assertFalse(r.get('isMultiLecture'))
