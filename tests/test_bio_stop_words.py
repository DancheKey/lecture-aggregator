#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回归：BIO_STOP 裸词「报名/咨询」误砍简介正文（2026-10-17 经管 11209 实测）。

根因：bio 提取正则（parsers.py 阶段 5c）把裸「咨询」「报名」当结构标记截断，
但这两个词可能是简介正文的正当组成部分：
  - 「…国际图联（IFLA）版权与其他法律事务咨询委员会委员…」——IFLA 委员会名，
    经管 11209（第550期）简介被砍成 53/229 字，前端只显 1 行；
  - 「…长期从事心理咨询研究…」——学科名。
修复：裸词须呈标签形态（后接冒号或联系方式类词）才截断；「二、报名方式」
「咨询电话：」等结构场景由章节序号/多字标签/标签形态裸词覆盖，行为不变。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402

_HEAD = """<html><head><title>讲座预告</title></head><body>
<div class="content">
<p>讲座题目：测试讲座</p>
<p>主讲人：{speaker}</p>
<p>讲座时间：2026年7月2日 10:00</p>
<p>讲座地点：文二栋301会议室</p>
"""


def _bio(bio_text, speaker='唐琼'):
    html = _HEAD.format(speaker=speaker) + '<p>【个人简介】' + bio_text + '</p></div></body></html>'
    out = P.parse_detail(html, 'http://example.com/a/1.html',
                         '测试学院', '石牌', skip_news_filter=True)
    recs = out if isinstance(out, list) else [out]
    return (recs[0].get('speakerBio') or '') if recs else ''


class BioStopWordsTest(unittest.TestCase):
    def test_ifla_committee_not_truncated(self):
        """IFLA「…法律事务咨询委员会委员…」不得被裸「咨询」砍断（11209 实测病根）"""
        bio = _bio('唐琼，管理学博士，中山大学信息管理学院教授，副院长，博士生导师。'
                   '兼任国际图联（IFLA）版权与其他法律事务咨询委员会委员，'
                   '中国图书馆学会图书馆学教育委员会副主任委员等。')
        self.assertIn('咨询委员会委员', bio)
        self.assertIn('副主任委员等', bio)
        self.assertGreater(len(bio), 60)

    def test_psych_consulting_not_truncated(self):
        """「从事心理咨询研究」学科名用词不得被砍"""
        bio = _bio('李四，心理学教授，长期从事心理咨询研究与临床督导工作，出版专著多部。', speaker='李四')
        self.assertIn('心理咨询研究', bio)
        self.assertIn('出版专著多部', bio)

    def test_baoming_mid_sentence_not_truncated(self):
        """正文中的「报名人数」等正当用词不得被砍"""
        bio = _bio('王五，教育学教授，其推动的社区项目报名人数超过千人，成效显著。', speaker='王五')
        self.assertIn('报名人数超过千人', bio)
        self.assertIn('成效显著', bio)

    def test_section_baomingfangshi_still_stops(self):
        """「二、报名方式：」结构章节仍截断（原保护意图不退化）"""
        bio = _bio('张三，某某大学副教授。主要研究领域为测试科学与工程。'
                   '二、报名方式：发送邮件至 test@example.com。', speaker='张三')
        self.assertIn('测试科学与工程', bio)
        self.assertNotIn('报名', bio)
        self.assertNotIn('test@example.com', bio)

    def test_zixun_phone_label_still_stops(self):
        """「咨询电话：」联系信息标签仍截断（原保护意图不退化）"""
        bio = _bio('王五，某某大学讲师。研究方向为软件测试与质量保障。'
                   '咨询电话：020-12345678。', speaker='王五')
        self.assertIn('软件测试与质量保障', bio)
        self.assertNotIn('020-12345678', bio)


class BioStopConcurrentTitleTest(unittest.TestCase):
    """bio 修复的连带守卫：简介变长后，「现任X特约研究员」类兼任头衔
    不得覆盖主单位（skc512 白凯实测：中国社会科学院 vs 陕西师范大学）。"""

    @classmethod
    def setUpClass(cls):
        fx = os.path.join(ROOT, 'tests', 'fixtures', 'html', 'skc512.html')
        if not os.path.exists(fx):
            raise unittest.SkipTest('fixture 缺失: skc512.html')
        html = open(fx, encoding='utf-8').read()
        out = P.parse_detail(html, 'http://skc.scnu.edu.cn/a/20191202/512.html',
                             '汕尾校区', '汕尾')
        recs = out if isinstance(out, list) else [out]
        cls.rec = recs[0] if recs else {}

    def test_bio_full_not_truncated_at_zixun(self):
        """旧病：简介被砍在「陕西省决策咨询委员会」中间（47 字）"""
        bio = self.rec.get('speakerBio') or ''
        self.assertIn('陕西省决策咨询委员会委员', bio)
        self.assertIn('中国社会科学院旅游研究中心特约研究员', bio)
        self.assertGreater(len(bio), 400)

    def test_concurrent_title_not_overriding_primary_unit(self):
        """「现任中国社会科学院旅游研究中心特约研究员」是兼任头衔，
        主单位应为简介首句的陕西师范大学"""
        self.assertEqual('陕西师范大学', self.rec.get('speakerAffiliation'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
