#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""P0 摘要提取回归：锁住 abstract/speakerBio 的简介-摘要混淆修复（2026-10-26）。

背景：用户核对 34 条摘要缺口时发现，abstract 与 speakerBio 两字段被系统性混淆：
  - A 类（根因 parsers.py:5911 的 bio_pat 裸「简介」守卫）：
    「报告简介 / 讲座简介 / 内容简介 / 讲座主题简介」下的讲座正文，被 bio_pat 当成
    讲者简介抓进 speakerBio，而 abstract 因同页无「摘要」标签被留空。
    典型站点 cs.scnu.edu.cn（「报告简介」= 讲座内容，却被装进 bio）。
  - B 类（根因 parsers.py:5877 的 NOISE_MARKERS 含裸词「分享|标签」+ 5992 行终止边界
    裸「主讲人」）：
    摘要正文里的正当高频词「通过分享…」「标签系统」「本次讲座将由主讲人…」被当噪声
    截断成 6~8 字残句，再被出口闸门清空。

修复（同 commit）：
  1. bio_pat 的裸「简介」排除讲座类前缀（报告/讲座/主题/内容/学术报告/课程/培训/工作坊/活动/会议/论坛/沙龙/系列）
  2. 新增 ABS_NOISE（由 NOISE_MARKERS 派生），裸「分享/标签」仅在按钮/栏目标签形态下截断
  3. abstract 的 split 清理与终止边界改用 ABS_NOISE
  4. abstract 终止边界裸「主讲人|报告人|讲座人|主讲|主持人|邀请人」改为结构形态（后接冒号/方括号）

本测试相对既有门禁多锁三点（test_bio_stop_words 只锁 BIO_STOP 裸「报名/咨询」；
test_abstract_provenance 只锁溯源/幻觉/出口清洗，均不覆盖简介-摘要混淆与噪声词截断）。
"""
import os
import sys
import unittest

# ⚠ 必须在 import parsers 前关闭 LLM 文本增强/富化，否则会触发外部调用
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402


_HEAD = """<html><head><title>讲座预告</title></head><body>
<div class="content">
<p>讲座题目：{title}</p>
<p>主讲人：{speaker}</p>
<p>讲座时间：2026年7月2日 10:00</p>
<p>讲座地点：文二栋301会议室</p>
"""


def _parse(html, url='http://example.com/a/1.html'):
    out = P.parse_detail(html, url, '测试学院', '石牌', skip_news_filter=True)
    recs = out if isinstance(out, list) else [out]
    return recs[0] if recs else {}


class AbstractBioConfusionTest(unittest.TestCase):
    """A 类：讲座类「简介」标签下的讲座内容须进 abstract，而非被 bio_pat 吞进 bio。"""

    def test_报告简介不误装进_bio(self):
        html = (_HEAD.format(title='AI 与教育', speaker='张三')
                + '<p>报告简介：本次讲座将探讨人工智能在教育的应用前景与主要挑战。</p>'
                + '<p>报告人简介：张三，教授，博士生导师，研究方向为机器学习。</p>'
                + '</div></body></html>')
        r = _parse(html)
        ab = r.get('abstract') or ''
        bio = r.get('speakerBio') or ''
        self.assertIn('人工智能在教育的应用前景', ab, '报告简介（讲座内容）应进 abstract')
        self.assertNotIn('人工智能在教育的应用前景', bio, '报告简介不得被 bio_pat 误吞')
        self.assertIn('博士生导师', bio, '报告人简介（讲者履历）应进 speakerBio')

    def test_讲座简介不误装进_bio(self):
        html = (_HEAD.format(title='心理', speaker='李四')
                + '<p>讲座简介：本讲座介绍认知行为疗法的核心理念与日常应用。</p>'
                + '<p>主讲人简介：李四，心理学教授，长期从事临床咨询研究。</p>'
                + '</div></body></html>')
        r = _parse(html)
        ab = r.get('abstract') or ''
        bio = r.get('speakerBio') or ''
        self.assertIn('认知行为疗法', ab, '讲座简介（摘要）应进 abstract')
        self.assertNotIn('认知行为疗法', bio)

    def test_内容简介不误装进_bio(self):
        html = (_HEAD.format(title='数学', speaker='王五')
                + '<p>内容简介：本报告系统梳理现代概率论的发展脉络及其在金融风控中的运用。</p>'
                + '<p>主讲人简介：王五，数学学院教授，研究方向为随机过程。</p>'
                + '</div></body></html>')
        r = _parse(html)
        ab = r.get('abstract') or ''
        bio = r.get('speakerBio') or ''
        self.assertIn('概率论的发展脉络', ab, '内容简介（摘要）应进 abstract')
        self.assertNotIn('概率论的发展脉络', bio)


class AbstractNoiseWordTest(unittest.TestCase):
    """B 类：摘要正文里的正当高频词「分享/标签」不得被当噪声截断。"""

    def test_裸分享不截断摘要(self):
        html = (_HEAD.format(title='国际贸易', speaker='李四')
                + '<p>摘要：本次讲座将通过分享国际贸易领域创新创业的个人宝贵经验，'
                  '进一步探讨相关行业国际贸易领域的发展趋势。标签系统的应用十分广泛。</p>'
                + '</div></body></html>')
        r = _parse(html)
        ab = r.get('abstract') or ''
        self.assertIn('通过分享国际贸易领域创新创业', ab, '摘要里的「分享」是正当用词，不得截断')
        self.assertIn('标签系统的应用十分广泛', ab, '摘要里的「标签」是正当用词，不得截断')
        self.assertGreater(len(ab), 40, '摘要被噪声词截断得过短')

    def test_标签系统正文完整保留(self):
        html = (_HEAD.format(title='信息组织', speaker='赵六')
                + '<p>摘要：服务于信息组织的标签系统集中并提供了可用于分类、共享和查找信息的标注。</p>'
                + '</div></body></html>')
        r = _parse(html)
        ab = r.get('abstract') or ''
        self.assertIn('标签系统集中并提供了', ab, '摘要里的「标签系统」须完整保留')


class AbstractStopBoundaryTest(unittest.TestCase):
    """B2 类：摘要正文里的「本次讲座将由主讲人…」裸词不得截断摘要。"""

    def test_主讲人裸词不截断摘要(self):
        html = (_HEAD.format(title='金融', speaker='王五')
                + '<p>摘要：本次讲座将由主讲人向大家分享自己的从业经历与风险管理经验。</p>'
                + '</div></body></html>')
        r = _parse(html)
        ab = r.get('abstract') or ''
        self.assertIn('将由主讲人向大家分享', ab, '摘要正文的「主讲人」是正当内容，不得截断')
        self.assertGreater(len(ab), 20)


class RegressionSafeTest(unittest.TestCase):
    """回归保护：正常摘要完整保留，不因本次改动被误清/误截。"""

    def test_普通摘要完整保留(self):
        html = (_HEAD.format(title='数据治理', speaker='赵六')
                + '<p>摘要：随着互联网和大数据技术的发展，数据的收集和使用日益广泛。'
                  '然而，随之而来的隐私泄露和数据滥用问题也日益严重。</p>'
                + '</div></body></html>')
        r = _parse(html)
        ab = r.get('abstract') or ''
        self.assertIn('隐私泄露和数据滥用问题', ab, '正常摘要须完整保留')

    def test_结构形态分享到仍截断(self):
        """ABS_NOISE 仅放宽裸词；「分享到」「微信分享」等按钮形态仍应截断（原意图不退化）。"""
        html = (_HEAD.format(title='活动', speaker='钱七')
                + '<p>摘要：本次讲座介绍读书方法。分享到微信｜标签云</p>'
                + '</div></body></html>')
        r = _parse(html)
        ab = r.get('abstract') or ''
        self.assertNotIn('分享到微信', ab, '按钮形态「分享到」仍应截断')
        self.assertIn('读书方法', ab)


if __name__ == '__main__':
    unittest.main(verbosity=2)
