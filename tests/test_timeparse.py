#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""timeparse 单元测试：时段词与数字矛盾的自动纠正（2026-09-10，ctld598 用户裁定）。

运行：python tests/test_timeparse.py
"""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import timeparse  # noqa: E402
from timeparse import parse_cn_time


class TestPeriodDigitsContradiction(unittest.TestCase):
    def _parse(self, text):
        return parse_cn_time(text, 2018)

    def test_afternoon_9_to_12_corrected_to_am(self):
        """「下午 09:00-12:00」→ 09:00-12:00（ctld598：时段词笔误，数字优先）"""
        r = self._parse('授课时间：2018年05月05日 （星期六） 下午 09:00-12:00')
        self.assertEqual(r['start'].strftime('%Y-%m-%d %H:%M'), '2018-05-05 09:00')
        self.assertEqual(r['end'].strftime('%H:%M'), '12:00')

    def test_evening_9_to_12_corrected_to_am(self):
        """「晚上9:00-12:00」同理纠正（讲座不会在晚上 9 点开始中午结束）"""
        r = self._parse('时间：2018年5月5日 晚上9:00-12:00')
        self.assertEqual(r['start'].strftime('%H:%M'), '09:00')
        self.assertEqual(r['end'].strftime('%H:%M'), '12:00')

    def test_evening_7_30_9_keeps_pm(self):
        """「晚7：30-9：00」→ 19:30-21:00（zhx340：真实晚间讲座，不误伤）"""
        r = self._parse('时间：2013年5月23日（周四）晚7：30-9：00')
        self.assertEqual(r['start'].strftime('%H:%M'), '19:30')
        self.assertEqual(r['end'].strftime('%H:%M'), '21:00')

    def test_afternoon_2_30_4_keeps_pm(self):
        """「下午2:30-4:00」→ 14:30-16:00（字面凌晨起点不合理，照常 +12）"""
        r = self._parse('时间：2024年4月20日 下午2:30-4:00')
        self.assertEqual(r['start'].strftime('%H:%M'), '14:30')
        self.assertEqual(r['end'].strftime('%H:%M'), '16:00')

    def test_afternoon_3_5_keeps_pm(self):
        """「下午3:00-5:00」→ 15:00-17:00（正挂区间不触发）"""
        r = self._parse('时间：2024年4月20日 下午3:00-5:00')
        self.assertEqual(r['start'].strftime('%H:%M'), '15:00')
        self.assertEqual(r['end'].strftime('%H:%M'), '17:00')

    def test_morning_untouched(self):
        """「上午9:00-11:30」→ 09:00-11:30（上午不受影响）"""
        r = self._parse('时间：2024年4月20日 上午9:00-11:30')
        self.assertEqual(r['start'].strftime('%H:%M'), '09:00')
        self.assertEqual(r['end'].strftime('%H:%M'), '11:30')

    def test_explicit_am_pm_suffix_untouched(self):
        """「09:00am-12:00pm」各时刻按自身后缀处理，不进矛盾分支"""
        r = self._parse('时间：2018年5月5日 09:00am-12:00pm')
        self.assertEqual(r['start'].strftime('%H:%M'), '09:00')
        self.assertEqual(r['end'].strftime('%H:%M'), '12:00')

    def test_late_single_char_evening(self):
        """「晚7：30-9：00」（缺「上」字，zhx340 源页原形态）→ 19:30-21:00"""
        r = self._parse('时间：2013年5月23日（周四）晚7：30-9：00')
        self.assertEqual(r['start'].strftime('%H:%M'), '19:30')
        self.assertEqual(r['end'].strftime('%H:%M'), '21:00')

    def test_afternoon_6_to_12_not_corrected(self):
        """「下午6:00-12:00」不纠正（起点 6 点，可能是 18:00 起的晚场）"""
        r = self._parse('时间：2024年4月20日 下午6:00-12:00')
        # 不应被纠正为 06:00 字面；倒挂交给 CV3 层处理
        self.assertNotEqual(r['start'].strftime('%H:%M'), '06:00')


class TestNoMarkerSmallHourInference(unittest.TestCase):
    """B5：无时段标记时1–5 点按下午推断（2026-09-30 修复死代码）。

    背景：_build 里 `period = 0`（非 None）使 _apply_period 的 else 分支恒不可达，
    注释承诺的「2:30-4:00 实为 14:30-16:00」（ibc/2779）从未生效，实际落库 02:30。
    修法：period 初值改 None（None=无标记/ 0 = 明确上午 / 12 = 下午晚上，三态不可合并）。

    存量影响：**3 条**（非 0）。用修复后的解析器重跑这三页的源页缓存，
    时间全部改变，故已于 2026-10-02 回填：
      seri/2019/1107/37.html  02:30 → 14:30
      em/20170504/5614.html   01:13 → 13:13
      em/20140626/3730.html   02:00 → 14:00
    ⚠ 本类 docstring 与 timeparse._build 处曾写着「存量影响经实测为 0 条」——
    该断言与实际数据矛盾，且已误导过一次评审（把真实存在的 3 条当成伪任务剔除）。
    """

    def _p(self, text):
        return parse_cn_time(text, 2018)

    def test_afternoon_inferred_without_marker(self):
        """核心断言：无标记「2:30-4:00」→ 14:30-16:00（修复前是 02:30-04:00）。"""
        r = self._p('时间：2025年7月2日 2:30-4:00')
        self.assertEqual(r['start'].strftime('%H:%M'), '14:30')
        self.assertEqual(r['end'].strftime('%H:%M'), '16:00')

    def test_fullwidth_colon_variant(self):
        r = self._p('时间：2025年7月2日 2：30-4：00')
        self.assertEqual(r['start'].strftime('%H:%M'), '14:30')

    def test_hour_one_to_five_all_inferred(self):
        for hh, want in ((1, '13'), (2, '14'), (3, '15'), (4, '16'), (5, '17')):
            r = self._p(f'时间：2025年7月2日 {hh}:13')
            self.assertEqual(r['start'].strftime('%H:%M'), f'{want}:13',
                             f'{hh}:13 无标记应推为 {want}:13')

    def test_six_and_above_untouched(self):
        """6 点及以后保持原值，不得被 +12 误伤成凌晨/深夜。"""
        for hh in (6, 7, 8, 9, 10, 11):
            r = self._p(f'时间：2025年7月2日 {hh}:00')
            self.assertEqual(r['start'].strftime('%H:%M'), f'{hh:02d}:00',
                             f'{hh}:00 无标记应保持原值')

    def test_explicit_midnight_untouched(self):
        """源页明确写 00:00 时保留真实午夜，不改成 08:00 占位。"""
        r = self._p('时间：2025年7月2日 00:00')
        self.assertEqual(r['start'].strftime('%H:%M'), '00:00')
        self.assertTrue(r['has_time'])

    def test_date_only_uses_tagged_8am_placeholder(self):
        r = self._p('时间：2025年7月2日')
        self.assertEqual(r['start'].strftime('%H:%M'), '08:00')
        self.assertFalse(r['has_time'])

    def test_explicit_morning_small_hour_not_shifted(self):
        """「上午1:30」必须留 01:30——这是 period 三态不可合并的关键反例。

        若把 period 初值改成 None 的同时把 PERIOD_OFFSET['上午'] 也改成 None（或
        反之把 None 当 0 处理），「上午1:30」会被错推成 13:30。
        """
        r = self._p('时间：2025年7月2日上午 1:30')
        self.assertEqual(r['start'].strftime('%H:%M'), '01:30')

    def test_explicit_morning_and_noon_sane(self):
        for text, want in (('2025年7月2日上午 9:00', '09:00'),
                           ('2025年7月2日中午 12:00', '12:00'),
                           ('2025年7月2日中午 1:00', '13:00'),
                           ('2025年7月2日下午 2:30-4:00', '14:30'),
                           ('2025年7月2日晚上 7:30-9:00', '19:30')):
            r = self._p('时间：' + text)
            self.assertEqual(r['start'].strftime('%H:%M'), want, text)

    def test_explicit_afternoon_unchanged_by_fix(self):
        """带时段标记的路径必须与修复前完全一致（+12 偏移照旧）。"""
        r = self._p('时间：2025年7月2日 下午 2:30-4:00')
        self.assertEqual(r['start'].strftime('%H:%M'), '14:30')
        self.assertEqual(r['end'].strftime('%H:%M'), '16:00')

    def test_source_no_dead_branch(self):
        """静态锁：period 初值必须是 None，否则 else 分支再次变成死代码。"""
        src = open(os.path.join(ROOT, 'scraper', 'timeparse.py'), encoding='utf-8').read()
        self.assertNotIn('\n    period = 0\n', src,
                         'period 初值退回 0 会让 1–5 点 +12 分支再次不可达')
        self.assertIn('\n    period = None\n', src)


class TestNoMarkerInvertedIntervalFixed(unittest.TestCase):
    """B5 后续：无时段标记时 +12 只作用于落进 1–5 区间的那一端，导致区间倒挂。

    现象（2026-10-02 实测）：period is None 时 _apply_period 对 1–5 点 +12、
    ≥6 点原值，于是只有**起点**被 +12 而终点没有：
        「4:00-6:00」→ start 16:00 / end 06:00   （end <= start，倒挂）
        「5:00-7:00」→ start 17:00 / end 07:00   （同上）
    而原有用例「2:30-4:00」两端都落在 1–5，恰好都 +12 所以正挂——测试盲区。

    修法：period is None 且 end <= start 时，尝试 end 补 +12h，
    补后时长 ≤6h（复用 C5 的 is_reasonable_span 与 COLLAPSED_SHIFT_MAX_MINUTES）
    则采用。不取消全部偏移：字面 04:00-06:00 是凌晨段，讲座不会在凌晨办。
    """

    def _p(self, text):
        return parse_cn_time(text, 2018)

    def test_start_in_1_5_end_6plus_not_inverted(self):
        """核心断言：起点在 1–5、终点 ≥6 时不得倒挂。"""
        cases = (('4:00-6:00', '16:00', '18:00'),
                 ('5:00-7:00', '17:00', '19:00'),
                 ('3:00-6:00', '15:00', '18:00'),
                 ('1:00-2:00', '13:00', '14:00'))
        for text, want_s, want_e in cases:
            r = self._p('时间：2025年7月2日 ' + text)
            self.assertIsNotNone(r['end'], text)
            self.assertEqual(r['start'].strftime('%H:%M'), want_s, text)
            self.assertEqual(r['end'].strftime('%H:%M'), want_e, text)
            self.assertGreater(r['end'], r['start'], f'{text} 结束早于开始')

    def test_both_ends_in_1_5_still_fine(self):
        """两端都落在 1–5（都 +12）时不得被新分支二次偏移。"""
        r = self._p('时间：2025年7月2日 2:30-4:00')
        self.assertEqual(r['start'].strftime('%H:%M'), '14:30')
        self.assertEqual(r['end'].strftime('%H:%M'), '16:00')

    def test_wide_span_falls_back_to_literal(self):
        """补 +12 后跨度超 6h（如 1:00-8:00 跨 7h）→ 回退两端字面值，仍不倒挂。"""
        r = self._p('时间：2025年7月2日 1:00-8:00')
        self.assertEqual(r['start'].strftime('%H:%M'), '01:00')
        self.assertEqual(r['end'].strftime('%H:%M'), '08:00')
        self.assertGreater(r['end'], r['start'])

    def test_nonsensical_range_drops_end(self):
        """字面值自身也倒挂/超限（如 5:00-4:00）→ 丢弃结束时间，绝不输出倒挂区间。"""
        r = self._p('时间：2025年7月2日 5:00-4:00')
        self.assertIsNone(r['end'], '字面即倒挂时应丢弃 end 而不是留下倒挂区间')
        self.assertEqual(r['start'].strftime('%H:%M'), '17:00')

    def test_explicit_marker_paths_untouched(self):
        """带时段标记的三条老路径行为不变（含原假倒挂纠正与不误伤）。"""
        r = self._p('时间：2025年7月2日 下午2:30-4:00')
        self.assertEqual((r['start'].strftime('%H:%M'), r['end'].strftime('%H:%M')),
                         ('14:30', '16:00'))
        r = self._p('时间：2018年05月05日 下午 09:00-12:00')
        self.assertEqual((r['start'].strftime('%H:%M'), r['end'].strftime('%H:%M')),
                         ('09:00', '12:00'))
        r = self._p('时间：2013年5月23日 晚7：30-9：00')
        self.assertEqual((r['start'].strftime('%H:%M'), r['end'].strftime('%H:%M')),
                         ('19:30', '21:00'))

    def test_no_marker_six_plus_range_untouched(self):
        """起点 ≥6 点区间不推断、也不进新分支（6:00-8:00 应保持 06:00-08:00）。"""
        r = self._p('时间：2025年7月2日 6:00-8:00')
        self.assertEqual((r['start'].strftime('%H:%M'), r['end'].strftime('%H:%M')),
                         ('06:00', '08:00'))

    def test_no_inverted_interval_in_sample(self):
        """扫描一组无标记区间，任何 end < start（真倒挂）都不允许残留。

        注：end == start（零时长，如「1:00-1:00」）不属于倒挂，不在此断言范围；
        跨度上限也不在此断言范围——「1:00-22:00」回退字面后跨 21h，
        源页本就如此，非本修复职责（只保证「不倒挂」）。
        """
        for h0 in range(1, 6):
            for h1 in range(0, 24):
                r = self._p(f'时间：2025年7月2日 {h0}:00-{h1}:00')
                if r['end']:
                    self.assertGreaterEqual(r['end'], r['start'],
                                            f'{h0}:00-{h1}:00 倒挂')


class TestMalformedDayRecovery(unittest.TestCase):
    def test_extra_day_digit_recovers_with_weekday(self):
        r = parse_cn_time('【时间】2026年11月614日（周六）18:30-20:30', 2026)
        self.assertIsNotNone(r)
        self.assertEqual(r['start'].strftime('%Y-%m-%d %H:%M'), '2026-11-14 18:30')
        self.assertEqual(r['end'].strftime('%Y-%m-%d %H:%M'), '2026-11-14 20:30')
        self.assertTrue(r['has_time'])

    def test_unique_calendar_candidate_recovers_without_weekday(self):
        r = parse_cn_time('时间：2026年11月614日18:30-20:30', 2026)
        self.assertIsNotNone(r)
        self.assertEqual(r['start'].strftime('%Y-%m-%d'), '2026-11-14')

    def test_weekday_disambiguates_multiple_calendar_candidates(self):
        r = parse_cn_time('时间：2026年11月114日（周六）18:30-20:30', 2026)
        self.assertIsNotNone(r)
        self.assertEqual(r['start'].strftime('%Y-%m-%d'), '2026-11-14')

    def test_ambiguous_calendar_candidates_without_weekday_not_guessed(self):
        self.assertIsNone(parse_cn_time('时间：2026年11月114日18:30', 2026))

    def test_later_weekday_not_used_to_disambiguate(self):
        self.assertIsNone(parse_cn_time('时间：2026年11月114日18:30，本周六有活动', 2026))

    def test_mismatched_weekday_rejects_recovery(self):
        self.assertIsNone(parse_cn_time('时间：2026年11月614日（周五）18:30', 2026))

    def test_invalid_calendar_candidate_not_recovered(self):
        self.assertIsNone(parse_cn_time('时间：2026年2月399日18:30', 2026))

    def test_normal_chinese_date_unchanged(self):
        r = parse_cn_time('时间：2026年11月14日（周六）18:30-20:30', 2026)
        self.assertEqual(r['start'].strftime('%Y-%m-%d %H:%M'), '2026-11-14 18:30')
        self.assertEqual(r['end'].strftime('%Y-%m-%d %H:%M'), '2026-11-14 20:30')


class TestInvalidDateCandidateFallback(unittest.TestCase):
    """首个日期候选非法时，继续查找后续合法日期，而非丢弃整段文本。"""

    def test_chinese_full_date_skips_invalid_candidate(self):
        r = parse_cn_time('2026年13月40日 会议 2026年11月28日 19:30', 2026)
        self.assertIsNotNone(r)
        self.assertEqual(r['start'].strftime('%Y-%m-%d %H:%M'), '2026-11-28 19:30')
        self.assertTrue(r['from_full'])

    def test_monthday_skips_invalid_candidate(self):
        r = parse_cn_time('11月31日 会议 11月28日 19:30', 2026)
        self.assertIsNotNone(r)
        self.assertEqual(r['start'].strftime('%Y-%m-%d %H:%M'), '2026-11-28 19:30')
        self.assertFalse(r['from_full'])

    def test_monthday_after_invalid_chinese_full_date_is_used(self):
        r = parse_cn_time('2026年11月114日 会议 11月28日 19:30', 2026)
        self.assertIsNotNone(r)
        self.assertEqual(r['start'].strftime('%Y-%m-%d %H:%M'), '2026-11-28 19:30')
        self.assertFalse(r['from_full'])

    def test_slash_monthday_skips_invalid_candidate(self):
        r = parse_cn_time('13/40 会议 11/28 19:30', 2026)
        self.assertIsNotNone(r)
        self.assertEqual(r['start'].strftime('%Y-%m-%d %H:%M'), '2026-11-28 19:30')
        self.assertFalse(r['from_full'])


class TestBeijingYearCompletion(unittest.TestCase):
    def test_utc_year_boundary_uses_beijing_date(self):
        real_datetime = dt.datetime

        class FixedDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 12, 31, 16, 30,
                            tzinfo=dt.timezone.utc)
                return value.astimezone(tz) if tz else value

            @classmethod
            def utcnow(cls):
                return cls(2026, 12, 31, 16, 30)

        with mock.patch.object(timeparse._dt_module, 'datetime', FixedDateTime):
            got = timeparse.beijing_now()
            self.assertEqual(timeparse.current_year(), 2027)
        self.assertEqual(got.date(), dt.date(2027, 1, 1))
        self.assertEqual(got.hour, 0)

    def test_publish_december_january_lecture_uses_next_year(self):
        r = parse_cn_time('时间：1月5日 09:00-10:00', None,
                          publish_time='2025-12-30 10:00:00')
        self.assertEqual(r['start'].strftime('%Y-%m-%d %H:%M'), '2026-01-05 09:00')
        self.assertEqual(r['end'].strftime('%Y-%m-%d %H:%M'), '2026-01-05 10:00')

    def test_publish_january_december_lecture_uses_previous_year(self):
        r = parse_cn_time('时间：12月28日 09:00', None,
                          publish_time='2026-01-02 10:00:00')
        self.assertEqual(r['start'].strftime('%Y-%m-%d'), '2025-12-28')

    def test_preview_january_publish_december_lecture_stays_in_publish_year(self):
        r = timeparse.resolve_lecture_time(
            '时间：12月28日 09:00', '学术报告预告', None, None,
            '2026-01-02 10:00:00', 1, None)
        self.assertEqual(r['start'], '2026-12-28 09:00:00')

    def test_retrospective_december_publish_january_lecture_stays_in_publish_year(self):
        r = timeparse.resolve_lecture_time(
            '时间：1月5日 09:00', '学术报告回顾', None, None,
            '2026-12-30 10:00:00', 1, None)
        self.assertEqual(r['start'], '2026-01-05 09:00:00')

    def test_current_year_december_january_lecture_uses_next_year(self):
        with mock.patch.object(timeparse, 'beijing_now',
                               return_value=dt.datetime(2026, 12, 31, 12, 0)):
            r = parse_cn_time('时间：1月5日 09:00')
        self.assertEqual(r['start'].strftime('%Y-%m-%d'), '2027-01-05')

    def test_current_year_january_december_lecture_uses_previous_year(self):
        with mock.patch.object(timeparse, 'beijing_now',
                               return_value=dt.datetime(2027, 1, 1, 12, 0)):
            r = parse_cn_time('时间：12月31日 09:00')
        self.assertEqual(r['start'].strftime('%Y-%m-%d'), '2026-12-31')

    def test_url_year_keeps_priority_over_title_and_publish(self):
        r = parse_cn_time('时间：6月1日 09:00', 2026,
                          publish_time='2025-12-30 10:00:00',
                          title_year=2025, url_year=2024)
        self.assertEqual(r['start'].strftime('%Y-%m-%d'), '2024-06-01')

    def test_title_year_keeps_priority_over_publish(self):
        r = parse_cn_time('时间：1月5日 09:00', 2026,
                          publish_time='2025-12-30 10:00:00', title_year=2024)
        self.assertEqual(r['start'].strftime('%Y-%m-%d'), '2024-01-05')

    def test_explicit_noncurrent_default_is_not_rebased(self):
        r = parse_cn_time('时间：1月5日 09:00', 2025)
        self.assertEqual(r['start'].strftime('%Y-%m-%d'), '2025-01-05')

    def test_explicit_full_year_is_never_shifted(self):
        r = parse_cn_time('时间：2024年1月5日 09:00', None,
                          publish_time='2025-12-30 10:00:00')
        self.assertEqual(r['start'].strftime('%Y-%m-%d'), '2024-01-05')

    def test_resolve_current_year_without_publish_uses_nearest(self):
        with mock.patch.object(timeparse, 'beijing_now',
                               return_value=dt.datetime(2026, 12, 31, 12, 0)):
            r = timeparse.resolve_lecture_time(
                '时间：1月5日 09:00', '讲座', None, None, None, None, None)
        self.assertEqual(r['start'], '2027-01-05 09:00:00')
        self.assertEqual(r['note'], 'nearest-year')

    def test_vlm_monthday_crosses_publish_year_both_directions(self):
        import parsers as P
        got = P._parse_vlm_datetime('01-05 09:30', None,
                                    '2025-12-30 10:00:00', None, None)
        self.assertEqual(got.strftime('%Y-%m-%d %H:%M'), '2026-01-05 09:30')
        got = P._parse_vlm_datetime('12-28 09:30', None,
                                    '2026-01-02 10:00:00', None, None)
        self.assertEqual(got.strftime('%Y-%m-%d %H:%M'), '2025-12-28 09:30')

    def test_vlm_current_year_crosses_beijing_year_boundary(self):
        import parsers as P
        with mock.patch.object(timeparse, 'beijing_now',
                               return_value=dt.datetime(2027, 1, 1, 0, 30)):
            got = P._parse_vlm_datetime('12-31 09:30', None, None, None, None)
        self.assertEqual(got.strftime('%Y-%m-%d'), '2026-12-31')

    def test_vlm_explicit_year_is_trusted(self):
        import parsers as P
        got = P._parse_vlm_datetime('2024-01-05 09:30', None,
                                    '2025-12-30 10:00:00', None, None)
        self.assertEqual(got.strftime('%Y-%m-%d'), '2024-01-05')


if __name__ == '__main__':
    unittest.main()
