# -*- coding: utf-8 -*-
"""「报告人：姓名+学位+逗号」连写履历段 bio 提取 + 数字 span 伪空格清理
+ 叙事兜底子串判据归一化（2026-10-24 cs/5472 实测）。

用户报障：cs/5472（第55期学术报告，张天威）源页「报告人：张天威博士，
新加坡南洋理工大学计算机学院助理教授。他于2011年…」连写长段未提取出 bio。

三个根因与修法：
① bio_pat 的「报告人：」履历段 lookahead 要求「冒号+2~4 字姓名+逗号」——
   「张天威博士，」姓名+学位共 5 字，超 {2,4} 上限不匹配 → bio 漏抓。
   修法：姓名后允许再跟**一个**职称/学位词（词表按语义引
   _fv.TAIL_TITLE_ANY_RE，禁手抄）；纯姓名短标签（无逗号）仍不匹配。
② 源页把年份包成嵌套 span（`<span>2</span><span>011</span>`），
   get_text(' ') 拼成「2 011 年」。修法：bio 出口调 _despace_cjk_digits
   折叠（与地点系统级清理同函数同语义）。
③ physics807 回归锁：② 让 bio 与 body_text 不再逐字一致，叙事兜底的
   「摘要=简介」子串判据（_narr_abs in _bio / _bio in _narr_abs）断裂 →
   元信息骨架「嘉宾:刘玉鑫 主题:…」被当摘要写入 abstract → gate 截剩
   9 字置空，只剩 qaGate='abstract:emptied' 噪声。修法：子串判据改为
   「去空白+统一全半角标点」后比较（对一切出口清理鲁棒）。

离线单测：不发网络请求、不走 LLM（SCNU_LLM_*=0）。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scraper'))
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers  # noqa: E402

# cs/5472 形态：五字段行页面，履历写在「报告人：」标签后连写（无简介类标签），
# 年份为嵌套 span 拆号形态（get_text(' ') 后成「2 011 年」）。
_HTML_5472 = '''<html><head><title>学术报告</title></head><body>
<div class="article-content">
<p>报告时间： 5月29日上午10:30-12:00</p>
<p>报告地点： 华南师范大学计算机学院学术报告厅</p>
<p>报告题目： 深度模型窃取的深度研究</p>
<p>报告摘要： 深度学习因其处理复杂任务和环境的强大能力而受到欢迎。各种算法和方法已经被设计用于在不同的人工智能领域和应用中。</p>
<p>报告人： 张天威博士，新加坡南洋理工大学计算机学院助理教授。 他于<span style="font-family: Calibri;">2</span><span style="font-family: Calibri;">011</span>年在北京大学获得本科学位,并与<span style="font-family: Calibri;">2</span><span style="font-family: Calibri;">017</span>年在普林斯顿的电子系获得博士学位。主要研究方向是计算机系统安全,对人工智能系统,机器人系统和分布式系统的安全都有兴趣。 他在安全,人工智能和系统的顶会和顶刊发表了<span style="font-family: Calibri;">1</span><span style="font-family: Calibri;">00</span>余篇文章,获得包括 ASPLOS 在内的多个最佳论文奖。</p>
</div></body></html>'''

_URL_5472 = 'http://cs.scnu.edu.cn/a/20230522/5472.html'


def _parse5472():
    return parsers.parse_detail(
        _HTML_5472, _URL_5472, '计算机学院', '石牌', default_year=2023,
        list_title='第55期学术报告--深度模型窃取的深度研究专题学术报告会',
        skip_news_filter=True)


class BioSpeakerPrefixLifeTest(unittest.TestCase):
    """cs/5472 修复三件套：姓名+学位连写 bio、数字 span 伪空格、807 回归锁。"""

    def test_name_plus_degree_resume_bio(self):
        """「报告人：张天威博士，新加坡…」连写履历段应提取出 bio（核心修复）。"""
        recs = _parse5472()
        recs = recs if isinstance(recs, list) else [recs]
        self.assertGreaterEqual(len(recs), 1)
        bio = recs[0].get('speakerBio') or ''
        self.assertIn('新加坡南洋理工大学计算机学院助理教授', bio)
        self.assertIn('主要研究方向是计算机系统安全', bio)
        self.assertIn('最佳论文奖', bio)

    def test_digit_span_pseudo_space_folded(self):
        """嵌套 span 拆号的「2 011 年」应折叠为「2011年」（出口 _despace）。"""
        recs = _parse5472()
        recs = recs if isinstance(recs, list) else [recs]
        bio = recs[0].get('speakerBio') or ''
        self.assertIn('2011年', bio)
        self.assertIn('2017年', bio)
        self.assertIn('100余篇', bio)
        self.assertNotIn('2 011', bio)
        self.assertNotIn('1 00', bio)

    def test_speaker_and_affiliation_intact(self):
        """speaker/单位不受 bio 修复影响（仅填空不覆盖的回归锁）。"""
        recs = _parse5472()
        recs = recs if isinstance(recs, list) else [recs]
        self.assertEqual(recs[0].get('speaker'), '张天威')
        self.assertEqual(recs[0].get('speakerAffiliation'), '新加坡南洋理工大学计算机学院')

    def test_bare_name_label_not_treated_as_bio(self):
        """负例：「报告人：张三」（姓名后无逗号）不得触发履历段 bio 提取。"""
        html = '''<html><head><title>讲座</title></head><body>
<div class="article-content">
<p>报告时间： 3月18日上午9:00</p>
<p>报告地点： 理6栋302</p>
<p>报告题目： 数据库理论与应用前沿</p>
<p>报告人： 张三</p>
<p>报告人： 李四</p>
</div></body></html>'''
        recs = parsers.parse_detail(
            html, 'http://cs.scnu.edu.cn/a/20230315/9998.html',
            '计算机学院', '石牌', default_year=2023,
            list_title='讲座', skip_news_filter=True)
        recs = recs if isinstance(recs, list) else [recs]
        for r in recs:
            self.assertFalse((r.get('speakerBio') or '').strip(),
                             '纯姓名短标签不应产出 bio：%r' % (r.get('speakerBio') or '')[:40])

    def test_narrative_substring_judgement_survives_exit_cleaning(self):
        """physics807 回归锁：bio 经出口清理后，「摘要=简介」子串判据不得断裂。

        判据若对空白敏感（逐字比较），元信息骨架「嘉宾:X 主题:Y」会被当摘要
        写入 abstract，遭 gate 截剩 9 字置空 → qaGate='abstract:emptied'。
        归一化（去空白+统一全半角标点）后比较对此鲁棒。
        """
        fx = os.path.join(_ROOT, 'tests', 'fixtures', 'html', 'physics807.html')
        if not os.path.exists(fx):
            self.skipTest('physics807 fixture 缺失')
        with open(fx, encoding='utf-8') as f:
            html = f.read()
        recs = parsers.parse_detail(
            html, 'https://physics.scnu.edu.cn/a/20191118/807.html',
            college='', campus='', default_year=None)
        recs = recs if isinstance(recs, list) else [recs]
        self.assertGreaterEqual(len(recs), 2)
        for r in recs:
            self.assertNotIn('abstract:emptied', r.get('qaGate') or '')
            self.assertFalse((r.get('abstract') or '').strip(),
                             '元信息骨架不应残留为 abstract：%r' % (r.get('abstract') or '')[:40])


if __name__ == '__main__':
    unittest.main(verbosity=2)
