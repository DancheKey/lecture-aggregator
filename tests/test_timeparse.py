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


if __name__ == '__main__':
    unittest.main()
