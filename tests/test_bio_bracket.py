#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回归：bio/abstract 开头孤立闭括号（2026-09-25 经管 45 条实测病根）。

根因：源页标签「【主讲人简介】/【该校简介】：」N1 规范化后为「[主讲人简介]/[该校简介]:」，
bio_pat/abs_pat 的标签词匹配不完备（如「该校简介」只吃到「简介」二字）时，闭合的 ]
残留在值开头。修复：标签后分隔符并入 ]（[\s\]:：]*）+ 出口 F-BRACKET 兜底。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402

_MIN_HTML = """
<html><head><title>讲座预告</title></head><body>
<div class="content">
<p>讲座题目：测试讲座</p>
<p>主讲人：张三</p>
<p>讲座时间：2026年7月2日 10:00</p>
<p>讲座地点：文二栋301会议室</p>
<p>【主讲人简介】张三，某某大学副教授。主要研究领域为测试科学与工程。</p>
<p>【讲座摘要】本讲座将介绍测试方法与工程实践案例。</p>
<p>【该校简介】：某某大学是教育部直属高校。</p>
</div>
</body></html>
"""


class BioBracketTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = P.parse_detail(_MIN_HTML, 'http://example.com/a/1.html',
                                 '测试学院', '石牌', skip_news_filter=True)
        recs = cls.out if isinstance(cls.out, list) else [cls.out]
        cls.rec = recs[0] if recs else {}

    def test_bio_no_leading_bracket(self):
        bio = (self.rec.get('speakerBio') or '').strip()
        self.assertTrue(bio, 'speakerBio 不应为空')
        self.assertFalse(bio.startswith((']', '[')), 'bio 开头残留方括号: %r' % bio[:20])
        self.assertTrue(bio.startswith('张三'), bio[:30])

    def test_abstract_no_leading_bracket(self):
        ab = (self.rec.get('abstract') or '').strip()
        self.assertTrue(ab, 'abstract 不应为空')
        self.assertFalse(ab.startswith((']', '[')), 'abstract 开头残留方括号: %r' % ab[:20])
        self.assertTrue(ab.startswith('本讲座'), ab[:30])

    def test_label_variant_not_in_vocab(self):
        """「该校简介」不在 bio 标签词表，也不得把残括号带进任何字段值"""
        for r in (self.out if isinstance(self.out, list) else [self.out]):
            for f in ('speakerBio', 'abstract', 'location', 'topic'):
                v = (r.get(f) or '').strip()
                self.assertFalse(v.startswith(']'), '%s 开头残留 ]: %r' % (f, v[:20]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
