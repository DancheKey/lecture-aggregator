#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回归：VLM 把海报落款当摘要 + 正文「主讲人简介」整段漏提取（2026-10-27）。

用户截图（ctld/4021）：卡片摘要显示「O O 华南年彩大学教师发展中心 O」——
12 个汉字全是主办方名称（「年彩」还是「师范」的 OCR 错字），三个「O」是海报
装饰圆圈；同时正文里「主讲人简介：」后面明明有 185 字完整履历，却没提取。

两条根因：
  ① VLM 的 abstract 在合并时**无条件回填**（正文没抓到摘要就填 VLM 的读图结果），
     不像 speakerBio 有「未识别到主讲人则跳过」的守卫 → 落款噪声进了摘要；
  ② BIO_STOP 有「参与方式」但漏了「参与形式」，简介尾部把
     「参与形式：线上工作坊——腾讯会议…」粘了进来。

修复：
  · 新增 _vlm_abstract_ok：摘要只留汉字后，若几乎全部能在「主办/承办/主讲人单位/
    本校名」里找到（覆盖率 ≥0.8）且汉字 ≥8、汉字占比 ≥0.6 → 判为落款，丢弃。
    简单判据都实测过、全部误杀：长度阈值误杀 41 条合法短摘要、无标点误杀 25 条
    截断句、孤立单字母误杀 880 条英文摘要。
  · BIO_STOP 补「参与形式」。

fixture：tests/fixtures/html/ctld4021.html（python tests/fetch_fixtures.py 可刷新）
"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402

FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'html', 'ctld4021.html')
URL = 'http://ctld.scnu.edu.cn/a/20220607/4021.html'


def _rec(**over):
    """构造判定用的最小记录上下文（_vlm_abstract_ok 只读 organizer/college/affiliation）。"""
    r = {'organizer': '教师发展中心', 'college': '教师发展中心',
         'speakerAffiliation': '华南师范大学教育人工智能研究院'}
    r.update(over)
    return r


class VlmAbstractGuardTest(unittest.TestCase):
    """① _vlm_abstract_ok 判据本身。"""

    def test_海报落款噪声被丢弃(self):
        """用户截图原串：装饰圈 O + OCR 错字的校名 + 主办单位。"""
        self.assertFalse(P._vlm_abstract_ok('O O 华南年彩大学教师发展中心 O', _rec()))

    def test_单位名复述被丢弃(self):
        """没有装饰字符，但整串就是主办单位全称——同样不是讲座内容。"""
        self.assertFalse(P._vlm_abstract_ok('华南师范大学教师发展中心', _rec()))

    def test_合法短摘要保留(self):
        """库里 41 条合法摘要短到 5~19 字（正文截断），长度阈值会误杀，故必须保留。"""
        self.assertTrue(P._vlm_abstract_ok('睡眠占据我们一生大约三分之一的', _rec()))

    def test_英文摘要保留(self):
        """英文摘要里汉字极少，不能因「汉字覆盖率」被判成落款。"""
        self.assertTrue(P._vlm_abstract_ok(
            'A 21st Century Agenda for Research on the Geographies of Health', _rec()))

    def test_正常中文摘要保留(self):
        self.assertTrue(P._vlm_abstract_ok(
            '本报告围绕模糊半超群框架下的模糊超S-系展开，首先定义了模糊超S-系与模糊正则关系',
            _rec()))

    def test_无单位信息时不误杀(self):
        """organizer/college/affiliation 全空时无从比对，应放行（宁放不杀）。"""
        self.assertTrue(P._vlm_abstract_ok('华南师范大学教师发展中心', {}))


class BioStopParticipantFormTest(unittest.TestCase):
    """② BIO_STOP 补「参与形式」——简介尾部不再粘后续章节。"""

    _HEAD = """<html><head><title>讲座预告</title></head><body>
<div class="content">
<p>讲座题目：测试讲座</p>
<p>主讲人：唐琼</p>
<p>讲座时间：2026年7月2日 10:00</p>
<p>讲座地点：文二栋301会议室</p>
"""

    def _bio(self, tail):
        html = (self._HEAD + '<p>【个人简介】唐琼，中山大学信息管理学院教授，'
                '长期从事图书馆学研究。</p><p>' + tail + '</p></div></body></html>')
        out = P.parse_detail(html, 'http://example.com/a/1.html', '测试学院', '石牌',
                             skip_news_filter=True)
        recs = out if isinstance(out, list) else [out]
        return (recs[0].get('speakerBio') or '') if recs else ''

    def test_参与形式截断(self):
        bio = self._bio('参与形式：线上工作坊——腾讯会议（281-667-675）')
        self.assertIn('长期从事图书馆学研究', bio)
        self.assertNotIn('参与形式', bio)
        self.assertNotIn('腾讯会议', bio)

    def test_参与方式截断不退化(self):
        """原有的「参与方式」词条行为不变（补「参与形式」不能只加新的、丢了旧的）。"""
        bio = self._bio('参与方式：线下签到入场')
        self.assertIn('长期从事图书馆学研究', bio)
        self.assertNotIn('参与方式', bio)


class Ctld4021EndToEndTest(unittest.TestCase):
    """③ 端到端：用户报的那条记录，bio 完整、摘要不是落款噪声。"""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(FIXTURE):
            raise unittest.SkipTest('缺少 fixture：tests/fixtures/html/ctld4021.html')
        # 纯规则路径：清 key + 掐断 requests，避免测试依赖网络与外部模型。
        # 只清 key 不够——代码仍会发请求并等满 timeout，单次用例要 60s+。
        import requests
        for k in ('AGNES_API_KEY', 'ZHIPU_API_KEY'):
            os.environ.pop(k, None)
        cls._orig_post = requests.post

        def _no_net(*a, **k):
            raise requests.exceptions.ConnectionError('test: network disabled')

        requests.post = _no_net
        try:
            html = io.open(FIXTURE, encoding='utf-8').read()
            out = P.parse_detail(html, URL, '教师发展中心', '校级', default_year=2022)
            cls.rec = out if isinstance(out, dict) else out[0]
        finally:
            requests.post = cls._orig_post

    def test_主讲人简介被提取(self):
        bio = self.rec.get('speakerBio') or ''
        self.assertGreater(len(bio), 150, '简介应完整提取（约 185 字），实际 %d 字' % len(bio))
        self.assertTrue(bio.startswith('胡小勇'), '简介应以主讲人姓名开头，实际：%r' % bio[:20])

    def test_简介尾部无后续章节(self):
        bio = self.rec.get('speakerBio') or ''
        self.assertNotIn('参与形式', bio, '简介尾部粘了「参与形式」章节：%r' % bio[-40:])
        self.assertNotIn('腾讯会议', bio)
        self.assertTrue(bio.rstrip().endswith('省部级科研奖励。'),
                        '简介应在正文末句结束，实际结尾：%r' % bio[-30:])

    def test_摘要不是海报落款(self):
        ab = (self.rec.get('abstract') or '').strip()
        self.assertNotIn('年彩', ab, '摘要仍是 VLM 误读的海报落款：%r' % ab)


if __name__ == '__main__':
    unittest.main()
