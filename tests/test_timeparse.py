#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""timeparse 单元测试：时段词与数字矛盾的自动纠正（2026-09-10，ctld598 用户裁定）。

运行：python tests/test_timeparse.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

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
    ⚠ 存量影响实测 0 条（3810 条库中 lectureStart/End 无一落在 01–05 点），
    故只影响今后的抓取，无需回改历史数据。
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

    def test_zero_placeholder_untouched(self):
        """00:00 是占位，不得被推断成 12:00。"""
        r = self._p('时间：2025年7月2日 00:00')
        self.assertEqual(r['start'].strftime('%H:%M'), '00:00')

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


if __name__ == '__main__':
    unittest.main()
