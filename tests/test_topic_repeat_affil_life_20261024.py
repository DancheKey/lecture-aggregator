# -*- coding: utf-8 -*-
"""英文题目不被字段标签截断 + 重复主题标签取完整份 + bio 反推单位补全（2026-10-24）。

cs/1818 实测三连修：
① topic='Collaborative'——TOPIC_STOP 的英文字段标签（Topic/Title/Venue…）无冒号
   判据，英文题目 "Collaborative Topic Ranking: …" 里的 "Topic" 被当字段标签
   截断。修法：英文标签要求后跟冒号。
② 收紧后 topic 又吞了整页两遍主题文本（CMS 把主题同时写进 meta description 与
   正文 title，第一遍被 HTML 截断而不完整）。修法：STOP 命中重复「主题：/题目：」
   标签时，从重复标签起再提取一份，两份取更长者（正文完整份优先）。
③ affiliation 丢国别——规则层 _AFFIL_CURRENT_RE 不认「来自X大学」现职表述，
   只靠 LLM（还会省略国别成「维多利亚大学工程与科学学院」，与加拿大 University
   of Victoria 混淆）。修法：加「来自」锚点 + _apply_bio_fallback 补全式覆盖
   （bio 反推值以现有值为真后缀且更长才覆盖）。

离线单测：不发网络请求、不走 LLM（SCNU_LLM_*=0）。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scraper'))
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import hybrid  # noqa: E402
import parsers  # noqa: E402

# cs/1818 核心片段：meta description 与正文各写一遍主题（第一遍尾部被截断），
# 「主讲人：何静 博士」+「详细内容：」简介段。保留 CMS 双份主题的排版形态。
_HTML_1818 = '''<html><head>
<meta name="description" content="主题：Collaborative Topic Ranking: Leveraging Item Meta-data for Sparsity Reduction（协同话题排序：借助项目元数据减少稀疏"/>
</head><body><div class="content">
<h1>学术讲座信息</h1>
<p>主题： Collaborative Topic Ranking: Leveraging Item Meta-data for Sparsity Reduction（协同话题排序：借助项目元数据减少稀疏性）</p>
<p>主讲人：何静 博士</p>
<p>时间： 4月23日(本周四)下午4点</p>
<p>地点： 计算机学院院楼 102会议室</p>
<p>详细内容： 何静博士来自澳大利亚维多利亚大学工程与科学学院，主要研究方向为数据挖掘、人工智能。</p>
</div></body></html>'''


def _parse(html, url='http://cs.scnu.edu.cn/a/20150422/1818.html'):
    out = parsers.parse_detail(html, url, '计算机学院', '石牌',
                               default_year=2015, list_title='学术讲座信息',
                               skip_news_filter=True)
    return out if isinstance(out, list) else ([out] if out else [])


class TopicEnglishLabelTest(unittest.TestCase):
    """英文题目里的字段标签词（Topic/Title/Bio…）不得截断题目。"""

    def test_topic_not_cut_by_english_label(self):
        recs = _parse(_HTML_1818)
        self.assertEqual(len(recs), 1)
        tp = recs[0]['topic']
        self.assertIn('Topic Ranking', tp)
        self.assertIn('Leveraging Item Meta-data', tp)
        self.assertIn('减少稀疏性', tp)

    def test_topic_repeat_label_takes_complete_copy(self):
        """重复「主题：」标签时取完整份（meta 不完整份被正文完整份顶替）。"""
        recs = _parse(_HTML_1818)
        tp = recs[0]['topic']
        self.assertTrue(tp.rstrip().endswith('减少稀疏性)'), '应取正文完整份: %r' % tp)
        self.assertLess(tp.count('Collaborative'), 2, '不应把两遍主题都吞进来')


# em/8924 形态：主题含版本号「WEB 3.0下的…」——旧章节序号清理正则把 '3.'
# 当序号从根部截断（topic 只剩 'WEB'）。序号分隔收紧为「、」与全角「．」后恢复。
_HTML_8924 = '''<html><body><div class="content">
<p>主讲人：陈涛</p>
<p>时间：4月1日(周五）14:00</p>
<p>地址：文二栋536会议室</p>
<p>主题：WEB 3.0下的文化遗产图像智能应用-让价值触手可及</p>
<p>邀请人：张素芳 副教授</p>
</div></body></html>'''


class TopicVersionNumberTest(unittest.TestCase):
    """主题里的版本号（WEB 3.0）不得被章节序号清理误杀。"""

    def test_version_number_not_cut(self):
        recs = _parse(_HTML_8924, url='http://em.scnu.edu.cn/a/20220401/8924.html')
        self.assertEqual(recs[0]['topic'], 'WEB 3.0下的文化遗产图像智能应用-让价值触手可及')


class InferAffiliationLaiziTest(unittest.TestCase):
    """_infer_affiliation「来自」现职锚点（cs/1818）。"""

    def test_laizi_anchor_extracts_full_affiliation(self):
        aff = hybrid._infer_affiliation(
            '何静博士来自澳大利亚维多利亚大学工程与科学学院，主要研究方向为数据挖掘。')
        self.assertEqual(aff, '澳大利亚维多利亚大学工程与科学学院')

    def test_degree_school_still_rejected(self):
        """学历/历史任职表述仍不作依据（既有负例不回归）。"""
        self.assertEqual(hybrid._infer_affiliation('2016年获得大连理工大学博士学位。'), '')
        self.assertEqual(hybrid._infer_affiliation('李四 曾任 XX 大学教授'), '')

    def test_lead_form_still_works(self):
        """LEAD 形态（姓名，机构，职称）仍工作；姓名后紧跟学位词被学历守卫跳过
        （2026-10-22 既有口径，非本次改动回归）。"""
        self.assertEqual(hybrid._infer_affiliation('张三，华南师范大学教授。'),
                         '华南师范大学')
        self.assertEqual(hybrid._infer_affiliation('张三，博士，华南师范大学教授。'), '')


class BioFallbackCompleteTest(unittest.TestCase):
    """_apply_bio_fallback 补全式覆盖：LLM 省略国别时用 bio 现职原文补全。"""

    def test_completes_llm_truncated_value(self):
        result = {'speakerBio': '何静博士来自澳大利亚维多利亚大学工程与科学学院，主要研究方向为数据挖掘。',
                  'speakerAffiliation': '维多利亚大学工程与科学学院'}
        hybrid._apply_bio_fallback(result)
        self.assertEqual(result['speakerAffiliation'],
                         '澳大利亚维多利亚大学工程与科学学院')

    def test_no_cross_replace_when_not_suffix(self):
        """非后缀关系不动（防「北京大学」被「东京大学」张冠李戴）。"""
        result = {'speakerBio': '王五，博士，现任东京大学教授。',
                  'speakerAffiliation': '北京大学'}
        hybrid._apply_bio_fallback(result)
        self.assertEqual(result['speakerAffiliation'], '北京大学')

    def test_empty_affiliation_filled(self):
        result = {'speakerBio': '何静博士来自澳大利亚维多利亚大学工程与科学学院，主要研究数据挖掘。',
                  'speakerAffiliation': ''}
        hybrid._apply_bio_fallback(result)
        self.assertEqual(result['speakerAffiliation'],
                         '澳大利亚维多利亚大学工程与科学学院')


if __name__ == '__main__':
    unittest.main(verbosity=2)
