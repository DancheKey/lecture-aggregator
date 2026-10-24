#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回归：跨 URL 同一讲座的存量重复检测 + 「报告人：」履历段 bio 提取（2026-10-24）。

背景（用户实测 em/6973 与 em/6966）：
  2019-05-09 谭春桥那场讲座被两个页面发布——6973 是「五四青年论坛议程」第 3 场
  （有精确时间 14:15 + 地点），6966 是单场预告页（时间待定）。前端显示成两张卡。

  (1) 入库侧去重（scraper._cross_source_dup_with_existing）只在「新记录 vs 已入库
      基底」时生效，**基底内部的存量重复永远不会被复查** → 机制建立前入库的重复对
      长期残留。本次在 audit_data_quality.scan 里补跨 URL 重复检查（高），把存量
      重复显式报出来，不再依赖人工偶然发现。
  (2) 6966 页把履历写在「报告人：姓名，男，…」标签后（而非「主讲人简介：」），
      原 bio 词表只认「报告人简介」→ bio 漏抓；同时 affiliation 会从履历里的
      期刊名（Fuzzy Sets and Systems…）误截，表现为「单位 = and Systems、IEEE
      Transactions on Fuzzy S」。补「报告人(?=：姓名，)」分支后 bio 正常提取。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import parsers as P  # noqa: E402
import audit_data_quality as A  # noqa: E402
import scraper as S  # noqa: E402
import generate_frontend_data as G  # noqa: E402

_HEAD = """<html><head><title>学术活动</title></head><body>
<div class="content">
<p>主题：基于谈判能力的旅游O2O供应链定价与服务策略研究</p>
<p>时间：2019年5月9日（周四）下午</p>
<p>主讲人：谭春桥教授（中南大学商学院）</p>
"""


def _bio_from_report_label(extra=''):
    html = (_HEAD
            + '<p>报告人：谭春桥，男，中南大学商学院教授，博士生导师，管理学博士，'
              '中国运筹学会员，中国运筹学会对策论专业委员会理事。'
              '主要研究方向是：经济理论与建模、博弈论及应用、决策理论与方法。</p>'
            + extra + '</div></body></html>')
    out = P.parse_detail(html, 'http://example.com/a/6966.html',
                         '经济与管理学院', '大学城', skip_news_filter=True)
    recs = out if isinstance(out, list) else [out]
    return (recs[0].get('speakerBio') or '') if recs else ''


def _rec(url, spk, start, topic, college='测试学院', loc=''):
    return {
        'sourceUrl': url, 'speaker': spk, 'lectureStart': start,
        'topic': topic, 'title': topic, 'college': college, 'location': loc,
    }


def _dup_issues(recs):
    return [it for it in A.scan(recs, excluded=[]) if it[0] == '重复']


class ReportLabelBioTest(unittest.TestCase):
    def test_report_label_with_resume_extracted(self):
        """「报告人：姓名，男，…」履历段应作为 speakerBio（6966 实测病根）"""
        bio = _bio_from_report_label()
        self.assertGreater(len(bio), 40)
        self.assertIn('中南大学商学院教授', bio)
        self.assertIn('决策理论与方法', bio)

    def test_plain_name_label_not_bio(self):
        """「报告人：张三」纯姓名短标签（无逗号）不得被当简介"""
        html = _HEAD + '<p>报告人：张三</p></div></body></html>'
        out = P.parse_detail(html, 'http://example.com/a/1.html',
                             '测试学院', '石牌', skip_news_filter=True)
        recs = out if isinstance(out, list) else [out]
        bio = (recs[0].get('speakerBio') or '') if recs else ''
        self.assertNotIn('张三', bio)


class CrossUrlDupAuditTest(unittest.TestCase):
    def test_same_lecture_two_urls_reported(self):
        """同主讲 + 同日 + 同题、两个不同页面 → 报「重复」高"""
        recs = [
            _rec('http://a.edu.cn/6973.html', '谭春桥', '2019-05-09 14:15:00',
                 '基于谈判能力的旅游 O2O 供应链定价与服务策略研究'),
            _rec('http://a.edu.cn/6966.html', '谭春桥', '2019-05-09 00:00:00',
                 '基于谈判能力的旅游O2O供应链定价与服务策略研究'),
        ]
        issues = _dup_issues(recs)
        self.assertEqual(len(issues), 1, issues)
        self.assertEqual(issues[0][2], '高')

    def test_different_precise_time_not_reported(self):
        """双方都是非占位精确时刻且不同 → 同日不同场次，不报"""
        recs = [
            _rec('http://a.edu.cn/1.html', '谭春桥', '2019-05-09 09:00:00', '同一题目研究'),
            _rec('http://a.edu.cn/2.html', '谭春桥', '2019-05-09 14:00:00', '同一题目研究'),
        ]
        self.assertEqual(_dup_issues(recs), [])

    def test_placeholder_time_compared(self):
        """一方占位时刻(00:00) + 另一方精确时刻 → 仍视为可重复（6966 实测形态）"""
        recs = [
            _rec('http://a.edu.cn/1.html', '谭春桥', '2019-05-09 14:15:00', '同一题目研究'),
            _rec('http://a.edu.cn/2.html', '谭春桥', '2019-05-09 00:00:00', '同一题目研究'),
        ]
        self.assertEqual(len(_dup_issues(recs)), 1)

    def test_same_url_not_reported(self):
        """同 URL 多场（多讲座页拆分）不算跨 URL 重复"""
        recs = [
            _rec('http://a.edu.cn/1.html', '谭春桥', '2019-05-09 09:00:00', '同一题目研究'),
            _rec('http://a.edu.cn/1.html', '谭春桥', '2019-05-09 00:00:00', '同一题目研究'),
        ]
        self.assertEqual(_dup_issues(recs), [])

    def test_different_topic_not_reported(self):
        """同主讲同日但题目不同 → 两场不同讲座，不报"""
        recs = [
            _rec('http://a.edu.cn/1.html', '谭春桥', '2019-05-09 09:00:00', '供应链定价研究'),
            _rec('http://a.edu.cn/2.html', '谭春桥', '2019-05-09 09:00:00', '博弈论与行为决策'),
        ]
        self.assertEqual(_dup_issues(recs), [])

    def test_different_day_not_reported(self):
        """同主讲同题但不同日 → 巡回讲座，不报"""
        recs = [
            _rec('http://a.edu.cn/1.html', '谭春桥', '2019-05-09 09:00:00', '同一题目研究'),
            _rec('http://a.edu.cn/2.html', '谭春桥', '2019-05-10 09:00:00', '同一题目研究'),
        ]
        self.assertEqual(_dup_issues(recs), [])


def _base_rec(url, spk, start, topic, **kw):
    r = _rec(url, spk, start, topic)
    r.update(kw)
    return r


class BaseHealDupTest(unittest.TestCase):
    """incremental_merge 收尾的基底内部自愈（2026-10-24 用户要求）。

    用户原话：「以后每天爬取，有可能一个讲座先发布，过几天又发了……如果不去除的话，
    会把相关的讲座重复出现，这个也是不合理的」。此前的两路去重只覆盖「新 vs 旧」，
    两条都已在库时永不复查，故补第三路。
    """

    def test_two_base_records_healed(self):
        """两条都在基底、无新增记录 → 自愈为一条，并保留两个来源链接"""
        a = _base_rec('http://a.edu.cn/6973.html', '谭春桥', '2019-05-09 14:15:00',
                      '基于谈判能力的旅游 O2O 供应链定价与服务策略研究',
                      lectureIndex=3, location='文科3栋501',
                      lectureEnd='2019-05-09 15:15:00')
        b = _base_rec('http://a.edu.cn/6966.html', '谭春桥', '2019-05-09 00:00:00',
                      '基于谈判能力的旅游O2O供应链定价与服务策略研究',
                      timeUnknown=True, speakerAffiliation='中南大学商学院',
                      speakerTitle='教授', speakerBio='谭春桥，男，中南大学商学院教授……')
        out = S.incremental_merge([a, b], [])
        self.assertEqual(len(out), 1)
        rec = out[0]
        self.assertTrue(rec.get('merged'))
        self.assertEqual(rec.get('sourceCount'), 2)
        self.assertEqual([s['sourceUrl'] for s in rec['sources']],
                         ['http://a.edu.cn/6966.html'])

    def test_primary_keeps_precise_time(self):
        """主记录必须是「有精确时刻」的那条，不能被 bio/单位更全的待定记录顶替"""
        a = _base_rec('http://a.edu.cn/6973.html', '谭春桥', '2019-05-09 14:15:00',
                      '同一题目研究', lectureIndex=3, location='文科3栋501')
        b = _base_rec('http://a.edu.cn/6966.html', '谭春桥', '2019-05-09 00:00:00',
                      '同一题目研究', timeUnknown=True,
                      speakerAffiliation='中南大学商学院', speakerTitle='教授',
                      speakerBio='谭春桥，男，中南大学商学院教授，博士生导师……')
        out = S.incremental_merge([a, b], [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['sourceUrl'], 'http://a.edu.cn/6973.html')
        self.assertEqual(out[0]['lectureStart'], '2019-05-09 14:15:00')
        # 待定记录的字段用于补全，不丢信息
        self.assertEqual(out[0]['speakerAffiliation'], '中南大学商学院')
        self.assertEqual(out[0]['location'], '文科3栋501')
        self.assertTrue(out[0].get('speakerBio'))

    def test_base_different_topic_untouched(self):
        """同人同日但题目不同 → 基底不动（两场不同讲座）"""
        a = _base_rec('http://a.edu.cn/1.html', '谭春桥', '2019-05-09 09:00:00', '供应链定价研究')
        b = _base_rec('http://a.edu.cn/2.html', '谭春桥', '2019-05-09 09:00:00', '博弈论与行为决策')
        out = S.incremental_merge([a, b], [])
        self.assertEqual(len(out), 2)

    def test_base_precise_times_differ_untouched(self):
        """双方都是非占位精确时刻且不同 → 同日两场，基底不动"""
        a = _base_rec('http://a.edu.cn/1.html', '谭春桥', '2019-05-09 09:00:00', '同一题目研究')
        b = _base_rec('http://a.edu.cn/2.html', '谭春桥', '2019-05-09 14:00:00', '同一题目研究')
        out = S.incremental_merge([a, b], [])
        self.assertEqual(len(out), 2)


_ONE_HEAD = """<html><head><title>学术讲座</title></head><body>
<div class="content">
<p>【题目】破产法庭与债券发行市场化</p>
<p>【时间】2022年3月4日（周五）上午10：00</p>
<p>【地点】文二栋五楼会议室</p>
<p>【主讲人】{speaker}</p>
<p>【报告人简介】</p>
{body}
</div></body></html>"""


def _parse_bio(speaker, body):
    html = _ONE_HEAD.format(speaker=speaker, body=body)
    out = P.parse_detail(html, 'http://example.com/a/8855.html',
                         '经济与管理学院', '大学城', skip_news_filter=True)
    recs = out if isinstance(out, list) else [out]
    return (recs[0].get('speakerBio') or '') if recs else ''


class CopyrightTailAndMultiBioTest(unittest.TestCase):
    """版权清理正则误伤（CICPA）+ 多主讲人简介只留第一位（2026-10-24 用户实测）。"""

    def test_cicpa_not_treated_as_icp_beian(self):
        """简介里的「CICPA（中国注册会计师）」不得被裸 ICP 规则清空（em/8855 病根）"""
        bio = _parse_bio(
            '辛宇教授',
            '<p>辛宇，博士，中山大学管理学院教授、博士生导师。中国注册会计师协会'
            '非执业会员，曾任及现任多家公司独立董事。</p>')
        self.assertGreater(len(bio), 30)
        self.assertIn('中山大学管理学院', bio)
        self.assertIn('独立董事', bio)

    def test_real_icp_beian_still_truncated(self):
        """真实备案号「粤ICP备12345678号」仍须截断（修复不能放过版权尾巴）"""
        bio = _parse_bio(
            '辛宇教授',
            '<p>辛宇，博士，中山大学管理学院教授、博士生导师，主要研究公司治理。</p>'
            '<p>粤ICP备12345678号 版权所有</p>')
        self.assertIn('公司治理', bio)
        self.assertNotIn('12345678', bio)
        self.assertNotIn('版权所有', bio)

    def test_multi_speaker_bio_keeps_first_only(self):
        """一场讲座两位主讲、各带履历 → 简介只保留第一位（用户口径）"""
        bio = _parse_bio(
            '辛宇教授、徐莉萍教授',
            '<p>辛宇，博士，中山大学管理学院教授、博士生导师。主要研究领域为公司治理'
            '与会计财务，发表论文六十余篇。</p>'
            '<p>徐莉萍，女，博士，中山大学管理学院会计系教授、博士生导师，'
            '中国注册会计师（CICPA）非执业会员。</p>')
        self.assertIn('辛宇', bio)
        self.assertIn('公司治理', bio)
        self.assertNotIn('徐莉萍', bio)
        self.assertNotIn('CICPA', bio)


class CollegeCountDedupTest(unittest.TestCase):
    """统计口径（用户 2026-10-24 要求）：

    「同一个学院的讲座数量只能算一次，不能算两次；不同学院/部门各算自己的」。
    合并记录在 sources 里可能挂着同学院 URL（同院两页发布同一场），统计时必须
    按单位集合去重；跨学院则各计一次（联合发布）。
    """

    def _item(self, sources):
        return {
            'sourceUrl': 'http://a.edu.cn/1.html',
            'college': '经济与管理学院',
            'campus': '大学城',
            'lectureStart': '2019-05-09 14:15:00',
            'lectureEnd': '2019-05-09 15:15:00',
            'speaker': '谭春桥',
            'topic': '同一场讲座',
            'title': '同一场讲座',
            'merged': True,
            'sourceCount': len(sources) + 1,
            'sources': sources,
        }

    def test_same_college_source_counts_once(self):
        """同学院两个页面合并 → 该学院只计 1 场（不得算 2 次）"""
        st = G.build_stats([self._item([
            {'sourceUrl': 'http://a.edu.cn/2.html', 'college': '经济与管理学院',
             'campus': '大学城', 'title': '同一场讲座'}]),
        ], '2026-10-24T00:00:00+08:00')
        self.assertEqual(st['matrix']['经济与管理学院']['2019'], 1)
        self.assertEqual(st['yearTotals']['2019'], 1)
        self.assertEqual(st['lectureCount'], 1)

    def test_cross_college_source_counts_each(self):
        """跨学院联合发布 → 各学院各计 1 场"""
        st = G.build_stats([self._item([
            {'sourceUrl': 'http://b.edu.cn/3.html', 'college': '物理学院',
             'campus': '大学城', 'title': '同一场讲座'}]),
        ], '2026-10-24T00:00:00+08:00')
        self.assertEqual(st['matrix']['经济与管理学院']['2019'], 1)
        self.assertEqual(st['matrix']['物理学院']['2019'], 1)
        # 单位计数之和大于唯一讲座数属既定口径（统计页有说明）
        self.assertEqual(st['yearTotals']['2019'], 2)
        self.assertEqual(st['lectureCount'], 1)

    def test_same_and_cross_mixed(self):
        """同学院 + 跨学院混合：主学院仍只计 1 次"""
        st = G.build_stats([self._item([
            {'sourceUrl': 'http://a.edu.cn/2.html', 'college': '经济与管理学院',
             'campus': '大学城', 'title': '同一场讲座'},
            {'sourceUrl': 'http://b.edu.cn/3.html', 'college': '物理学院',
             'campus': '大学城', 'title': '同一场讲座'},
        ])], '2026-10-24T00:00:00+08:00')
        self.assertEqual(st['matrix']['经济与管理学院']['2019'], 1)
        self.assertEqual(st['matrix']['物理学院']['2019'], 1)


if __name__ == '__main__':
    unittest.main()