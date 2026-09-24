#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地点前缀剥离回归测试（2026-09-24 用户裁定）。

覆盖 _clean_location 出口的两级前缀剥离：
  ① 校名前缀：华南师范大学 / 华南师大 / 华师（「华师大厦」为酒店专名，不剥）
  ② 学院名前缀：仅在「校区前缀 + 学院名 + 栋号」结构下剥（有楼栋时单位名冗余）

② 的保留条件同样是硬要求——学院名后只跟房间号（心理学院301）或本就是楼名
一部分（网络教育学院楼206 / 心理学院大楼301）时剥除会丢信息，必须原样保留。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402


class SchoolPrefixTest(unittest.TestCase):
    """① 校名前缀剥离"""

    def test_school_prefix_stripped(self):
        cases = {
            # 校名剥除后若后面还有栋号，学院名会一并剥（见 CollegePrefixTest）
            '华南师范大学环境学院理3栋607会议室': '理3栋607会议室',
            '华南师大第一课室大楼东102': '第一课室大楼东102',
            '华师大学城校区理3栋，环境研究院6楼会议室': '大学城校区理3栋，环境研究院6楼会议室',
            '华师石牌校区国际文化学院B103室': '石牌校区国际文化学院B103室',
        }
        for src, want in cases.items():
            self.assertEqual(P._clean_location(src), want, src)

    def test_hotel_special_name_kept(self):
        """「华师大厦」是酒店专名，不是校名前缀"""
        src = '华师大厦酒店(广州市天河区中山大道西55号华南师范大学石牌校区)会议'
        self.assertEqual(P._clean_location(src), src)

    def test_pure_school_name_kept(self):
        """剥后无实质信息则原样保留"""
        self.assertEqual(P._clean_location('华南师范大学'), '华南师范大学')


class CollegePrefixTest(unittest.TestCase):
    """② 学院名前缀剥离（仅栋号结构）"""

    def test_strip_when_building_follows(self):
        cases = {
            '大学城校园经济与管理学院文二栋301会议室': '大学城校园文二栋301会议室',
            '经管学院文二栋301会议室': '文二栋301会议室',
            '经济与管理学院文2栋五楼会议室': '文2栋五楼会议室',
            '环境学院理3栋607会议室': '理3栋607会议室',
            '大学城校区环境学院理3栋410会议室': '大学城校区理3栋410会议室',
            '物电学院理六栋405会议室': '理六栋405会议室',
            '政治与公共管理学院文3栋一楼演讲厅': '文3栋一楼演讲厅',
        }
        for src, want in cases.items():
            self.assertEqual(P._strip_college_name(src), want, src)

    def test_three_char_college_keeps_campus(self):
        """回归：3 字学院名（法学院）不得把左侧「校区」一并吃掉"""
        self.assertEqual(
            P._strip_college_name('大学城校区法学院文1栋208'), '大学城校区文1栋208')
        self.assertEqual(
            P._strip_college_name('石牌校区法学院文1栋208'), '石牌校区文1栋208')

    def test_keep_when_only_room_number(self):
        """学院名后只有房间号/楼层：剥了只剩「301会议室」，必须保留"""
        for src in ('心理学院301会议室', '生命科学学院102会议室',
                    '地理科学学院三楼会议室', '心理学院201室'):
            self.assertEqual(P._strip_college_name(src), src)

    def test_keep_when_college_is_part_of_building(self):
        """学院名是楼名一部分：网络教育学院楼 / 心理学院大楼"""
        for src in ('石牌校区网络教育学院楼206', '心理学院大楼301会议室',
                    '计算机学院院楼102会议室'):
            self.assertEqual(P._strip_college_name(src), src)

    def test_keep_when_no_building_marker(self):
        """无栋号标识：学术报告厅 / 东楼 / 阶梯教室等，学院名即定位信息"""
        for src in ('计算机学院学术报告厅', '数学科学学院东楼401',
                    '美术学院二楼报告厅(大会议室)', '田家炳教育书院B503'):
            self.assertEqual(P._strip_college_name(src), src)

    def test_keep_pure_college_name(self):
        for src in ('心理学院', '国际商学院', '广东邮电职业技术学院'):
            self.assertEqual(P._strip_college_name(src), src)

    def test_middle_college_name_untouched(self):
        """学院名在中间（非开头）不处理，避免误伤"""
        src = '文二栋经济与管理学院301会议室'
        self.assertEqual(P._strip_college_name(src), src)

    def test_idempotent(self):
        samples = ('大学城校园经济与管理学院文二栋301会议室',
                   '经管学院文二栋301会议室', '心理学院301会议室',
                   '石牌校区网络教育学院楼206', '文二栋301会议室')
        for src in samples:
            once = P._strip_college_name(src)
            self.assertEqual(P._strip_college_name(once), once, src)

    def test_end_to_end_two_level_strip(self):
        """校名 + 学院名两级连剥"""
        self.assertEqual(
            P._clean_location('华南师范大学大学城校园经济与管理学院文2栋401会议室'),
            '大学城校园文2栋401会议室')
        """无栋号则只剥校名、学院名保留"""
        self.assertEqual(
            P._clean_location('华南师范大学生命科学学院102会议室'),
            '生命科学学院102会议室')


if __name__ == '__main__':
    unittest.main(verbosity=2)
