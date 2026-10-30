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


class BracketBalanceTest(unittest.TestCase):
    """括号配对兜底（2026-10-30）。

    真实案例 mks/20261029/21：海报原文「马克思主义学院（桃李园唯实厅）」，
    VLM 输出丢了右括号；同一张图本地 RapidOCR 识别完整。全库 3783 条地点
    仅此 1 条不配对，属模型偶发漏标点，不是清洗规则的系统性缺陷。
    """

    def test_missing_right_bracket_filled(self):
        """有实质内容时补右括号——本轮用户报的那条"""
        self.assertEqual(
            P._clean_location('马克思主义学院（桃李园唯实厅',
                              '在“生活史”视野下理解1920年代的中国革命'),
            '马克思主义学院（桃李园唯实厅）')

    def test_balanced_value_untouched(self):
        """已配对的不得改动（幂等）"""
        for src in ('马克思主义学院（桃李园唯实厅）',
                    '理8栋（东侧）301会议室',
                    '第二教学楼(研究生院)210会议厅'):
            self.assertEqual(P._balance_location_brackets(src), src, src)

    def test_empty_bracket_dropped(self):
        """括号内无内容：删括号而不是补成「（）」"""
        self.assertEqual(P._balance_location_brackets('桃李园（'), '桃李园')
        self.assertEqual(P._balance_location_brackets('桃李园）'), '桃李园')

    def test_halfwidth_bracket(self):
        """半角括号同理，且不误伤成对值"""
        self.assertEqual(P._balance_location_brackets('A栋(B区'), 'A栋(B区)')
        self.assertEqual(P._balance_location_brackets('A栋(B区)'), 'A栋(B区)')

    def test_no_bracket_untouched(self):
        for src in ('文2栋301会议室', '线上', ''):
            self.assertEqual(P._balance_location_brackets(src), src, src)


class LocationTailRuleTest(unittest.TestCase):
    """_clean_location 尾部/中段规则的误伤回归（2026-10-30 全库重清洗评估）。

    背景：全库 3848 条地点过一遍 _clean_location，有 66 条会变。逐条 trace 后
    发现其中几条是规则过宽造成的**丢信息**，不是「存量没跟上新规则」：

      · 章节序号规则把房间号列表当章节标题：「南教学楼102、南教学楼101」→「南教学楼」
      · 章节序号规则把图片文件名当序号：「理六栋525未标题-2.jpg」→「理六栋525未标题-」
      · 人名判据把地点续写当人名：「第一课室大楼，东102」→「第一课室」
      · 标签残片剥离把「在线讲座」剥成「在线」

    以上全部按「宁可少截，不可丢信息」收紧；真正的章节标题/人名残留仍照旧截断
    （见本类末尾的正面用例），否则会重新引入 BS4 把换行压成空格导致的整段吞并。
    """

    def test_room_number_list_not_cut_as_section(self):
        """房间号列表里的「N、」不是章节序号"""
        for src in ('南教学楼102、南教学楼101',
                    '理六栋201、202、203',
                    '第一教学楼101,102,103'):
            self.assertEqual(P._clean_location(src), src, src)

    def test_image_filename_not_cut_as_section(self):
        """图片文件名残留（「未标题-2.jpg」）不是章节序号"""
        self.assertEqual(
            P._clean_location('理六栋525未标题-2.jpg'), '理六栋525未标题-2.jpg')

    def test_section_heading_still_cut(self):
        """正面：真正的章节标题仍截断（BS4 把换行压成空格后的典型吞并）"""
        cases = {
            '文学院会议室 二、参与方式': '文学院会议室',
            '理8栋118报告厅 一、工作坊安排': '理8栋118报告厅',
            '第一课室 1. 报名': '第一课室',
            '教学楼301 10、议程安排': '教学楼301',
            '心理学院5楼报告厅二、座谈会': '心理学院5楼报告厅二、座谈会',
        }
        for src, want in cases.items():
            self.assertEqual(P._clean_location(src), want, src)

    def test_location_continuation_not_cut_as_person_name(self):
        """残留开头以地点词收尾 → 是地点续写，不是「人名,」"""
        cases = {
            '第一课室大楼，东102': '第一课室大楼，东102',
            '周一至周四 一楼大会议室，周五 二楼会议室':
                '周一至周四一楼大会议室，周五二楼会议室',
        }
        for src, want in cases.items():
            self.assertEqual(P._clean_location(src), want, src)

    def test_person_name_tail_still_cut(self):
        """正面：真正的「人名,履历」残留仍截断（em 5104 场景）"""
        cases = {
            '文三栋MBA中心501课室姜树广,浙江财经大学杰出青年学者,深圳证券交易所博士后':
                '文三栋MBA中心501课室',
            '大学城文2栋301室李程骅教授': '大学城文2栋301室',
            '本部行政楼七楼第六会议室周立群教授': '本部行政楼七楼第六会议室',
        }
        for src, want in cases.items():
            self.assertEqual(P._clean_location(src), want, src)

    def test_pure_english_location_keeps_spaces(self):
        """纯英文场馆名保留词间空格（地点无空格约定只适用于含中文的值）"""
        for src in ('Room 536, Wen II Building',
                    'Room 201, School of Psychology',
                    'Lecture Hall on the 5th floor of School of Psychology'):
            self.assertEqual(P._clean_location(src), src, src)

    def test_chinese_location_spaces_collapsed(self):
        """含中文的地点仍按「无内部空格」约定折叠"""
        cases = {
            '理 6 栋 302': '理6栋302',
            '国际文化学院 B503 会议室': '国际文化学院B503会议室',
            '石牌校区文科楼二层 211 讲堂': '石牌校区文科楼二层211讲堂',
            '第二教学楼(研究生院) 210 会议厅': '第二教学楼(研究生院)210会议厅',
        }
        for src, want in cases.items():
            self.assertEqual(P._clean_location(src), want, src)

    def test_online_lecture_keeps_lecture_word(self):
        """「在线讲座」的「讲座」是地点语义，不是被吸入的字段标签"""
        self.assertEqual(P._clean_location('在线讲座（腾讯会议 895-309-857）'),
                         '在线讲座（腾讯会议 895-309-857）')
        # 正面：真正的标签残片仍剥
        self.assertEqual(P._clean_location('理8栋118报告厅讲座'), '理8栋118报告厅')

    def test_tail_noise_re_keeps_closing_bracket(self):
        """去尾部装饰字符的负向断言：右括号及其前空格不得被当噪声剥掉"""
        # 唯实厅 ）：右括号前多一个空格时，原正则会把「 ）」整段当噪声剥掉
        self.assertEqual(
            P._OCR_TAIL_NOISE_RE.sub('', '马克思主义学院（桃李园唯实厅 ）'),
            '马克思主义学院（桃李园唯实厅 ）')
        # 装饰符照剥（原意保留）
        self.assertEqual(P._OCR_TAIL_NOISE_RE.sub('', '文2栋301会议室 ·'),
                         '文2栋301会议室')
        self.assertEqual(P._OCR_TAIL_NOISE_RE.sub('', '文2栋301会议室 ※'),
                         '文2栋301会议室')


if __name__ == '__main__':
    unittest.main(verbosity=2)
