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


if __name__ == '__main__':
    unittest.main(verbosity=2)
