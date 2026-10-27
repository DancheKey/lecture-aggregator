#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回归：一页多场把「多人并列的海报简介」整段复制给每一场（2026-10-27）。

用户反馈（swc/collaborative/2021/1026/28）：同一页拆出 3 场，三张卡片显示的是
**同一段**简介——把三个人的履历连在一起：

    教学研究与课程建设莫逊男:本科教学研究与改革教务处副处长…胡小勇:一流课程建设
    教授、博导,华南师范大学教育信息技术学院副院长…潘家辉:课程思政改革建设教授,
    华南师范大学软件学院副院长…

点开任何一场都看到别人的履历。

根因：该页正文只有「莫逊男《…》/胡小勇《…》/潘家辉《…》」三行，简介全部来自海报
OCR/VLM。VLM 把海报上三个人连着写的介绍原样拼成一段写进 base 记录，
`split_record_by_sessions` 又把它整段复制给 3 条拆分记录。R7（`_extract_bio_map`）
靠正文「简介：」标签划区，这种无标签的 VLM 值走不到，于是出口补一层
`_split_shared_speaker_bio`。

判据是**宁缺毋滥**的：只要有一条不满足就整段原样不动（宁可显示整段，也不能把
别人的履历切给这个人）。
"""
import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402

# 真实数据里那一整段（589 字，逐字取自 data/lectures.json 修复前）
SHARED_BIO = (
    '教学研究与课程建设莫逊男:本科教学研究与改革教务处副处长分管业务:教学研究、'
    '教学质量保障胡小勇:一流课程建设教授、博导,华南师范大学教育信息技术学院副院长'
    '兼任教育部高校教育技术专业教学指导分委员会秘书长、中国教育学会中小学信息技术'
    '教育专业委员会副理事长、广东省基础教育与信息化研究院副院长、科大讯飞智能教育'
    '专家委员会专家等职; 研究领域:教育信息化理论与政策、信息化教学教研创新、智能'
    '教育理论与应用。 主持10余项国家省部级课题,曾多次获国家级、省级教育教学成果奖;'
    '在中国大学 MOOC开设《人工智能教育应用》教师教育幕课。 潘家辉:课程思政改革建设'
    '教授,华南师范大学软件学院副院长广东医学会数字医学分会常务委员,珠江科技新星,'
    '华南师范大学教学名师。主要从事脑机接口、模式识别与智能系统方面的研究,先后主持'
    '3项国家自然科学基金项目、2项广东省自然科学基金和3项市厅级科技计划项目,'
    '共发表论文126篇(2篇ESI高被引), 先后获得2020年世界机器人大赛优秀青年论文一等奖、'
    '2016年广东省科学技术奖自然科学奖一等奖、2010年广东省科学技术奖科技进步奖一等奖、'
    '中华医学科技奖三等奖等。 讲授课程《软件构造》获首批国家级一流本科课程,'
    '广东省课程思政示范课程;《数据结构与算法》获广东省课程思政示范课堂;'
    '《脑机接口原理与实践》获广东省研究生示范课程。研究领域:模式识别与智能系统。'
)

SPEAKERS = ['莫逊男', '胡小勇', '潘家辉']


def _recs(names=SPEAKERS, bio=SHARED_BIO):
    return [{'sourceUrl': 'https://swc.scnu.edu.cn/collaborative/2021/1026/28.html',
             'speaker': n, 'speakerBio': bio, 'lectureIndex': i + 1}
            for i, n in enumerate(names)]


class SharedBioSplitTest(unittest.TestCase):
    """① 真实数据：三人共享简介切成各自一段。"""

    def test_01_真实数据切成三段(self):
        recs = P._split_shared_speaker_bio(_recs())
        bios = [r['speakerBio'] for r in recs]
        self.assertEqual(len(set(bios)), 3, '三条记录仍共用同一段简介')
        for r in recs:
            others = [n for n in SPEAKERS if n != r['speaker']]
            for o in others:
                self.assertNotIn(o, r['speakerBio'],
                                 '%s 的简介里出现了 %s（切分串味）' % (r['speaker'], o))

    def test_02_每段以自己的履历开头(self):
        recs = P._split_shared_speaker_bio(_recs())
        by = {r['speaker']: r['speakerBio'] for r in recs}
        self.assertTrue(by['莫逊男'].startswith('本科教学研究与改革'), by['莫逊男'][:20])
        self.assertTrue(by['胡小勇'].startswith('一流课程建设'), by['胡小勇'][:20])
        self.assertTrue(by['潘家辉'].startswith('课程思政改革建设'), by['潘家辉'][:20])
        # 三人简介长度应与原文占比一致（不是均分）
        self.assertLess(len(by['莫逊男']), len(by['胡小勇']))
        self.assertLess(len(by['胡小勇']), len(by['潘家辉']))

    def test_03_切分后总长不超过原文(self):
        recs = P._split_shared_speaker_bio(_recs())
        total = sum(len(r['speakerBio']) for r in recs)
        self.assertLessEqual(total, len(SHARED_BIO), '切分后字数反而变多，说明拼接出错了')
        # 丢掉的只能是「页级题目前缀 9 字 + 3 个姓名 9 字 + 3 个冒号 + 空白」，约 22 字
        self.assertGreaterEqual(total, len(SHARED_BIO) - 40, '切分丢字过多')


class SharedBioGuardTest(unittest.TestCase):
    """② 守卫：任何一条不满足就整段不动（宁缺毋滥）。"""

    def _unchanged(self, recs):
        before = [r['speakerBio'] for r in recs]
        out = P._split_shared_speaker_bio(recs)
        self.assertEqual([r['speakerBio'] for r in out], before)
        return out

    def test_10_单条记录不动(self):
        self._unchanged(_recs(['莫逊男']))

    def test_11_简介不一致不动(self):
        recs = _recs()
        recs[0]['speakerBio'] = '完全不同的另一段简介，长度也够二十个字以上。'
        self._unchanged(recs)

    def test_12_简介为空不动(self):
        self._unchanged(_recs(bio=''))

    def test_13_主讲人重复不动(self):
        # 同一人多场：切了没有意义，也不该切
        self._unchanged(_recs(['莫逊男', '莫逊男']))

    def test_14_主讲人为空不动(self):
        self._unchanged(_recs(['莫逊男', '']))

    def test_15_姓名在简介里出现多次不动(self):
        # 「胡小勇」在潘家辉那段的正文里被提到 → 出现 2 次，锚点歧义，整段放弃
        bio = SHARED_BIO.replace('研究领域:模式识别与智能系统。',
                                 '研究领域:模式识别与智能系统,与胡小勇合作。')
        self._unchanged(_recs(bio=bio))

    def test_16_姓名后不跟冒号不动(self):
        bio = SHARED_BIO.replace('莫逊男:', '莫逊男，')
        self._unchanged(_recs(bio=bio))

    def test_17_切出的段过短不动(self):
        # 把莫逊男那段压到 5 字 → 判为误锚，整段不动
        bio = SHARED_BIO.replace('本科教学研究与改革教务处副处长分管业务:教学研究、教学质量保障',
                                 '教务处')
        self._unchanged(_recs(bio=bio))

    def test_18_非list或空输入不炸(self):
        self.assertIsNone(P._split_shared_speaker_bio(None))
        self.assertEqual(P._split_shared_speaker_bio([]), [])


class SharedBioWiringTest(unittest.TestCase):
    """③ 接线：出口必须真的调用它（否则函数写得再对也不生效）。"""

    def test_20_parse_detail里调用了切分(self):
        src = io.open(os.path.join(ROOT, 'scraper', 'parsers.py'), encoding='utf-8').read()
        body = src[src.index('def parse_detail('):]
        body = body[:body.index('\ndef ', 10)]
        self.assertIn('_split_shared_speaker_bio(_out_recs)', body,
                      'parse_detail 出口没有调用 _split_shared_speaker_bio')

    def test_21_切分在speaker清洗之后(self):
        """姓名锚定依赖干净姓名：必须先 _strip_speaker_title_tail，再切分。"""
        src = io.open(os.path.join(ROOT, 'scraper', 'parsers.py'), encoding='utf-8').read()
        body = src[src.index('def parse_detail('):]
        body = body[:body.index('\ndef ', 10)]
        self.assertLess(body.index('_strip_speaker_title_tail'),
                        body.index('_split_shared_speaker_bio'),
                        '切分跑在姓名清洗之前，带职称尾巴的姓名会锚不到')


if __name__ == '__main__':
    unittest.main(verbosity=2)
