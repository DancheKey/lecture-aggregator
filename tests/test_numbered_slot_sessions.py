#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「第N场（时段）：人名职称，"题目"」多场拆分回归门禁（2026-11-10，em/3736）。

背景：经济与管理学院「第117期华南经济论坛」页面写了三场独立报告，库里却只有
1 条（14:00-17:00 整场、无主讲人）。2026-11-10 实测当前解析器重跑也只产 1 条：

  · 候选5 认出了「第N场」，但取题目时用「冒号后第一串文字」，被段首时段
    `14:00-15:00` 里的冒号骗到，抽出「00-15」这类残片，清洗后三场全丢；
  · 候选9/10/11 都要求「题目N：」「人名（单位）题目：」等字段块形态，
    本版式是「人名+职称，题目」，全不匹配。

修复：瀑布最末补候选12，以「第N场（HH:MM-HH:MM）」为强锚点，逐段显式给出
主讲人 / 题目 / 独立时段。同系列的 em/3716 是同一版式（题目无引号、后接
「评论人：」），历史上靠一次性脚本拆成 3 条，重解析会退化回 1 条，一并锁住。

本套件**全离线**：真实页面固化为 tests/fixtures/html/em3736.html、em3716.html；
折叠正文（parsers 实际传给 detect_multi_session 的形态）写成本模块常量；
图片/附件下载用 _safe_fetch 桩隔离，无网络无密钥。
"""
import datetime
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
FIX_HTML = os.path.join(ROOT, 'tests', 'fixtures', 'html')

# 与 golden 同纪律：import parsers 前锁纯规则基线，避免本机 .env 触发真实大模型
os.environ.setdefault('SCNU_LLM_TEXT', '0')
os.environ.setdefault('SCNU_LLM_RICH', '0')

import parsers as P  # noqa: E402

URL_3736 = 'http://em.scnu.edu.cn/a/20141011/3736.html'
URL_3716 = 'http://em.scnu.edu.cn/a/20140504/3716.html'
TITLE_3736 = '第117期“华南经济论坛”开讲通知'
TITLE_3716 = '第111期华南经济论坛暨青年节学术沙龙通知'
DAY_3736 = datetime.datetime(2014, 10, 11, 14, 0)
DAY_3716 = datetime.datetime(2014, 5, 4, 14, 0)

# 2026-11-10 实测折叠正文：全部标点已归一、换行被压平，CJK 间无空格。
TEXT_3736 = (
    '学术活动当前位置: 首页 » 学术活动 » 第117期"华南经济论坛"开讲通知 '
    '2014-10-11 11:27:00 来源: 院科研办点击: 收藏本文'
    '时间:2014年10月11日(周六)14:00-17:00 地点:学院5楼会议室主持人:刘愿副教授'
    '第一场(14:00-15:00):董志强教授,"再论公平偏好的演化起源:改进的仿真模型" '
    '第二场(15:00-16:00):吴明琴副教授,"Employee education and employer pension '
    'contribution:evidence from China" '
    '第三场(16:00-17:00):魏下海副教授,"腐败与企业生命力" '
    '*注:每篇报告45分钟,评论讨论15分钟,共60分钟欢迎广大师生(尤其研究生)积极参加！')

TEXT_3716 = (
    '学术活动当前位置: 首页 » 学术活动 » 第111期华南经济论坛暨青年节学术沙龙通知 '
    '2014-05-04 11:12:00 来源: 院科研办点击: 收藏本文'
    '时间:2014年5月4日(周日)14:00-17:00 地点:学院5楼会议室主持人:刘愿博士'
    '第一场(14:00-15:00):吴明琴博士, Does enviromental regulation drive away '
    'inbound foreign direct investment:evidence from a quasi-natural experiment in china '
    '评论人:殷宁宇博士第二场(15:00-16:00):魏下海博士,工会与雇佣评论人:朱琪教授'
    '第三场(16:00-17:00):董志强教授,经济增长可否促进官员晋升? 评论人:刘愿博士 '
    '*注:每篇报告45分钟,评论讨论15分钟,共60分钟')


def _cand(text, day, publish_time=None):
    return P._detect_numbered_slot_sessions(
        text, publish_time=publish_time, base_start=day)


def _e2e(fixture, url):
    with open(os.path.join(FIX_HTML, fixture), encoding='utf-8') as f:
        html = f.read()
    with patch.object(P, '_safe_fetch', return_value=None):
        recs = P.parse_detail(html, url, '经济与管理学院', None,
                              skip_news_filter=True)
    return recs if isinstance(recs, list) else [recs]


class SlotCandidateTest(unittest.TestCase):
    """候选12 本体：两版式都要拆对，且不得把职称/评论人/注释吞进字段。"""

    def test_01_three_sessions_with_quoted_topics(self):
        s = _cand(TEXT_3736, DAY_3736)
        self.assertEqual(len(s), 3)
        self.assertEqual([x['speaker'] for x in s], ['董志强', '吴明琴', '魏下海'])
        self.assertEqual([x['start'].strftime('%H:%M') for x in s],
                         ['14:00', '15:00', '16:00'])
        self.assertEqual([x['end'].strftime('%H:%M') for x in s],
                         ['15:00', '16:00', '17:00'])
        self.assertEqual(s[0]['topic'], '再论公平偏好的演化起源:改进的仿真模型')
        self.assertEqual(s[2]['topic'], '腐败与企业生命力')
        self.assertEqual(s[0]['splitMode'], 'numbered-slot')

    def test_02_long_english_topic_not_truncated(self):
        s = _cand(TEXT_3736, DAY_3736)
        self.assertEqual(
            s[1]['topic'],
            'Employee education and employer pension contribution:evidence from China',
            '引号内长英文题目不得被 40 字上限或冒号裁断')

    def test_03_vice_title_not_eaten_into_name(self):
        s = _cand(TEXT_3736, DAY_3736)
        self.assertEqual(s[1]['speaker'], '吴明琴',
                         '「副教授」的「副」不得被贪心姓名吞入')
        self.assertEqual(s[2]['speaker'], '魏下海')

    def test_04_bare_topic_stops_at_reviewer_label(self):
        s = _cand(TEXT_3716, DAY_3716)
        self.assertEqual(len(s), 3)
        self.assertEqual([x['speaker'] for x in s], ['吴明琴', '魏下海', '董志强'])
        self.assertEqual(s[1]['topic'], '工会与雇佣',
                         '题目与「评论人:」粘连时须截断')
        self.assertNotIn('评论人', s[0]['topic'])
        self.assertIn('quasi-natural experiment in china', s[0]['topic'])
        self.assertEqual(s[2]['topic'], '经济增长可否促进官员晋升?')

    def test_05_no_note_or_welcome_text_in_topic(self):
        for text, day in ((TEXT_3736, DAY_3736), (TEXT_3716, DAY_3716)):
            for x in _cand(text, day):
                self.assertNotIn('每篇报告45分钟', x['topic'])
                self.assertNotIn('欢迎广大', x['topic'])

    def test_06_year_from_base_start(self):
        s = _cand(TEXT_3736, DAY_3736)
        self.assertTrue(all(x['start'].year == 2014 and x['start'].month == 10
                            and x['start'].day == 11 for x in s))

    def test_07_publish_time_fallback_when_no_base_start(self):
        s = _cand(TEXT_3736, None, publish_time='2014-10-11 11:27')
        self.assertEqual(len(s), 3)
        self.assertEqual(s[0]['start'], datetime.datetime(2014, 10, 11, 14, 0))


class SlotGuardTest(unittest.TestCase):
    """候选12 是瀑布最末兜底，误命中必须让位给「单讲座」结论。"""

    def test_08_single_slot_is_not_split(self):
        one = ('时间:2014年10月11日(周六)14:00-17:00 '
               '第一场(14:00-15:00):董志强教授,"题目A" 欢迎广大师生积极参加！')
        self.assertEqual(_cand(one, DAY_3736), [])

    def test_09_repeated_number_rejected(self):
        dup = ('第一场(14:00-15:00):张三教授,"题目A" '
               '第一场(15:00-16:00):李四教授,"题目B" '
               '第一场(16:00-17:00):王五教授,"题目C"')
        self.assertEqual(_cand(dup, DAY_3736), [],
                         '同一编号重复出现多为模板复制/页眉，不得当成多场')

    def test_10_non_increasing_slots_rejected(self):
        bad = ('第一场(15:00-16:00):张三教授,"题目A" '
               '第二场(14:00-15:00):李四教授,"题目B"')
        self.assertEqual(_cand(bad, DAY_3736), [])

    def test_11_missing_speaker_rejected(self):
        no_name = ('第一场(14:00-15:00):"题目A" '
                   '第二场(15:00-16:00):"题目B"')
        self.assertEqual(_cand(no_name, DAY_3736), [])

    def test_12_ordinary_single_lecture_not_split(self):
        plain = ('学术活动 时间:2014年10月11日(周六)14:00-17:00 '
                 '地点:学院5楼会议室 主讲人:张三 题目:某某研究 欢迎参加')
        out = P.detect_multi_session(plain, title='某某研究讲座通知',
                                     publish_time='2014-10-11 11:27',
                                     base_start=DAY_3736)
        self.assertEqual(len(out or []), 0,
                         '普通单讲座页不得被候选12 误拆')

    def test_13_cascade_end_to_end(self):
        out = P.detect_multi_session(
            TEXT_3736, title=TITLE_3736, publish_time='2014-10-11 11:27',
            base_start=DAY_3736)
        self.assertEqual(len(out or []), 3,
                         '新候选须真正接上瀑布，且不被末尾守卫吃掉')
        self.assertEqual([x['no'] for x in out], ['1', '2', '3'])
        self.assertTrue(all(x.get('splitMode') == 'numbered-slot' for x in out))

    def test_14_cascade_position_guard(self):
        with open(os.path.join(ROOT, 'scraper', 'parsers.py'),
                  encoding='utf-8') as f:
            src = f.read()
        i11 = src.index('cand11 = _detect_numbered_agenda_sessions(')
        i12 = src.index('cand12 = _detect_numbered_slot_sessions(')
        i_dedup = src.index('# 去重：同 (topic, start) 视为同一场')
        self.assertLess(i11, i12, '候选12 必须在候选11 之后（不抢上游）')
        self.assertLess(i12, i_dedup, '候选12 必须在统一去重之前')


class EndToEndTest(unittest.TestCase):
    """真实 HTML 全链路：拆出的记录要能直接落库。"""

    def test_15_em3736_produces_three_records(self):
        recs = _e2e('em3736.html', URL_3736)
        self.assertEqual(len(recs), 3)
        self.assertEqual([r['lectureIndex'] for r in recs], [1, 2, 3])
        self.assertEqual([r['lectureStart'] for r in recs],
                         ['2014-10-11 14:00:00', '2014-10-11 15:00:00',
                          '2014-10-11 16:00:00'])
        self.assertEqual([r['speaker'] for r in recs],
                         ['董志强', '吴明琴', '魏下海'])
        self.assertEqual([r['sourceCount'] for r in recs], [1, 0, 0],
                         '同一公告拆出的 N 条只计 1 条来源通知')
        self.assertTrue(all(r['location'] == '学院5楼会议室' for r in recs))
        self.assertTrue(all(r['splitMode'] == 'numbered-slot' for r in recs))
        self.assertTrue(all(r['isMultiLecture'] for r in recs))
        self.assertFalse(any(k.startswith('_') for r in recs for k in r),
                         '不得把内部下划线字段写进库')

    def test_16_em3716_no_longer_degrades_on_reparse(self):
        recs = _e2e('em3716.html', URL_3716)
        self.assertEqual(len(recs), 3,
                         '历史已拆页重解析不得退回 1 条')
        self.assertEqual([r['lectureStart'] for r in recs],
                         ['2014-05-04 14:00:00', '2014-05-04 15:00:00',
                          '2014-05-04 16:00:00'])
        self.assertEqual([r['speaker'] for r in recs],
                         ['吴明琴', '魏下海', '董志强'])
        self.assertEqual(recs[1]['topic'], '工会与雇佣')


if __name__ == '__main__':
    unittest.main(verbosity=2)
