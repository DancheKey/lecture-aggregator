# -*- coding: utf-8 -*-
"""系列讲座海报（正文总览行 + VLM 多场拆分）守卫豁免回归（2026-10-23，lswh 20261022/86）。

## 根因链（生产实录）

http://lswh.scnu.edu.cn/a/20261022/86.html 落库 1 条（李红岩、10-26 00:00、
文科楼423、428），实际海报是同一主讲人 4 场系列讲座（各场独立题目/日期/
地点/主持人）。三因叠加：

1. **正文是总览行**：「主讲人:李红岩 时间:2026年10月26-29日」——4 场共享
   一位主讲人 + 一个日期区间，并非单场权威值。
2. **「M月D-D日」区间 end 丢失**：timeparse 只解出 start=10-26，end=None，
   下游守卫失去「正文是区间总览」的直接证据。
3. **多场退回守卫误判**（parsers._finalize_record）：result.speaker（正文
   标签）+ lectureStart 均非空 → VLM 正确拆出的 4 场被当幻觉整体丢弃。
   守卫建于 cs 5487（无字图被编造成多场），隐含假设「正文有讲者+时间=单场
   权威值」在系列总览页不成立。

另有并发缺陷：VLM_PROMPT 未声明「主持人≠主讲人」，海报每场的「主持人：X」
被就近写进各场 speaker（旧缓存实测 4 场 speaker=肖自力/易建平/渠桂萍/张庆海，
真正的主讲人李红岩反而丢失）。缓存键不含 prompt 版本，改 prompt 后须删对应
缓存条目才生效（本次数据修复已手工删除）。

## 本套件锁定

  ① 正例：总览行 + VLM 4 场（主讲人全同、题目/日期互异）→ 拆 4 条、
     speaker 全=主讲人、topic 各异、单位剥职称（「华南师范大学兼职特聘教授」
     →「华南师范大学」）；
  ② 负例（cs 5487 回归保护）：正文单日权威值 + VLM 编造 4 场 → 仍丢弃、
     只出 1 条；
  ③ timeparse「M月D-D日」区间补 end（同日钟点区间不覆盖、单日不变、跨月跳过）；
  ④ field_vocab 提供不含尊称的纯 COMPLEX 组（此前 bd1b064 修复的锁，原在
     test_hybrid，本套件从守卫视角复锁一层）。

运行：python tests/test_poster_series_multisession.py
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

# 环境开关须在 import parsers 之前设置（模块顶层读取）
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers as P  # noqa: E402
import timeparse as TP  # noqa: E402
import field_vocab as FV  # noqa: E402

URL = 'http://lswh.scnu.edu.cn/a/20261022/86.html'
IMG = 'http://statics.scnu.edu.cn/pics/lswh/2026/1022/1792657075357422.jpg'

# 正文复刻 lswh/86 形态：字段标签行 + 短学院散文（剥字段后 <50 字 → poster 判定）
_OVERVIEW_BODY = (
    '<div class="content">'
    '<p>主讲人:李红岩</p>'
    '<p>时间:2026年10月26-29日</p>'
    '<p>地点:文科楼423、428会议室</p>'
    '<p>历史学是华南师范大学1933年建校之初最早设立的学科之一。</p>'
    '<img src="%s">' % IMG +
    '</div>'
)

# VLM 桩：4 场系列讲座，主讲人全同、title 各异（VLM 常把单场题目写进 title）、
# 主持人姓名只出现在 sessionNumber 之外的字段不存在——主持人绝不进 speaker
_VLM_4_SESSIONS = [
    {'sessionNumber': '', 'title': '关于中国近代史的主题与线索问题', 'topic': '',
     'speaker': '李红岩', 'speakerTitle': '教授',
     'speakerAffiliation': '华南师范大学兼职特聘教授',
     'lectureStart': '10-26 15:00', 'lectureEnd': '10-26 17:45',
     'location': '文科楼423', 'abstract': '', 'speakerBio': ''},
    {'sessionNumber': '', 'title': '新中国史学的“五朵金花”', 'topic': '',
     'speaker': '李红岩', 'speakerTitle': '教授',
     'speakerAffiliation': '华南师范大学兼职特聘教授',
     'lectureStart': '10-27 15:00', 'lectureEnd': '10-27 17:45',
     'location': '文科楼428', 'abstract': '', 'speakerBio': ''},
    {'sessionNumber': '', 'title': '十七年史学关于历史人物的评价讨论', 'topic': '',
     'speaker': '李红岩', 'speakerTitle': '教授',
     'speakerAffiliation': '华南师范大学兼职特聘教授',
     'lectureStart': '10-28 15:00', 'lectureEnd': '10-28 17:45',
     'location': '文科楼423', 'abstract': '', 'speakerBio': ''},
    {'sessionNumber': '', 'title': '中国社会性质论战与中国马克思主义史学的形成', 'topic': '',
     'speaker': '李红岩', 'speakerTitle': '教授',
     'speakerAffiliation': '华南师范大学兼职特聘教授',
     'lectureStart': '10-29 15:00', 'lectureEnd': '10-29 17:45',
     'location': '文科楼423', 'abstract': '', 'speakerBio': ''},
]


# cs 5487 负例桩：VLM 编造的多场（讲者各异、题目虚构）
_FAKE_CS5487 = [
    {'sessionNumber': '', 'title': '量子力学导论', 'topic': '',
     'speaker': '李四', 'speakerTitle': '', 'speakerAffiliation': '',
     'lectureStart': '12-16 10:00', 'lectureEnd': '',
     'location': '生科院102', 'abstract': '', 'speakerBio': ''},
    {'sessionNumber': '', 'title': '相对论浅说', 'topic': '',
     'speaker': '王五', 'speakerTitle': '', 'speakerAffiliation': '',
     'lectureStart': '12-17 10:00', 'lectureEnd': '',
     'location': '生科院102', 'abstract': '', 'speakerBio': ''},
]


def _parse(html, url=URL, vlm=None):
    # 密封双桩：OCR 返回空（海报无可识别文字 → speaker 只来自正文标签）+
    # VLM 用用例自带桩。不依赖网络、不依赖 test_parser_golden 的 OCR 回放
    # 密封状态（否则全套运行时未录制图 URL 会撞 replay 硬抛，单独跑又会
    # 走真实网络 OCR——两种环境行为都不确定，2026-10-23 全套实测踩坑）。
    if vlm is None:
        vlm = [dict(s) for s in _VLM_4_SESSIONS] if url == URL else _FAKE_CS5487
    with mock.patch.object(P, '_img_to_text', lambda img: ''), \
         mock.patch.object(P, '_vlm_extract_fields', lambda urls, cfgs: vlm):
        out = P.parse_detail(html, url, '历史文化学院', '石牌', 2026,
                             list_title='第十九届“勷勤论坛”之历史文化学院分论坛',
                             skip_news_filter=True)
    return out if isinstance(out, list) else [out]


class TestSeriesOverviewExempt(unittest.TestCase):
    """① 正例：正文总览行不拦 VLM 系列拆分。"""

    def test_overview_row_allows_vlm_split(self):
        recs = _parse(_OVERVIEW_BODY)
        self.assertEqual(len(recs), 4, 'VLM 正确拆出的 4 场不得被守卫丢弃')
        for r in recs:
            self.assertEqual(r.get('speaker'), '李红岩',
                             '各场 speaker 须为主讲人李红岩（主持人不得顶替）')
            self.assertEqual(r.get('isMultiLecture'), True)
        topics = {r.get('topic') for r in recs}
        self.assertEqual(len(topics), 4, '各场 topic 须互异（item.title→topic 转移）')
        starts = sorted(r.get('lectureStart') or '' for r in recs)
        self.assertEqual(starts[0][:10], '2026-10-26')
        self.assertEqual(starts[-1][:10], '2026-10-29')

    def test_affiliation_title_suffix_stripped(self):
        """单位字段「单位+职称」整串须剥职称（含聘任修饰词）。"""
        recs = _parse(_OVERVIEW_BODY)
        for r in recs:
            self.assertEqual(r.get('speakerAffiliation'), '华南师范大学',
                             '「华南师范大学兼职特聘教授」须剥成「华南师范大学」')

    def test_same_speaker_bio_reused_across_sessions(self):
        """系列海报简介只出现一次（VLM 常挂首场）→ 同主讲人其余场次补全 bio。"""
        # 桩里仅首场有 bio（复刻 lswh/86 实际 VLM 返回形态）
        sessions = [dict(s) for s in _VLM_4_SESSIONS]
        for s in sessions[1:]:
            s['speakerBio'] = ''
        sessions[0]['speakerBio'] = '李红岩，华南师范大学兼职特聘教授，中国编辑学会副会长。'
        recs = _parse(_OVERVIEW_BODY, vlm=sessions)
        self.assertEqual(len(recs), 4)
        for r in recs:
            self.assertIn('中国编辑学会副会长', r.get('speakerBio') or '',
                          '同主讲人各场 speakerBio 须复用首场简介')

    def test_bio_not_crossed_between_speakers(self):
        """不同主讲人之间不得串用简介（bio 归属以讲者键为准）。"""
        sessions = [dict(s) for s in _VLM_4_SESSIONS]
        for s in sessions[1:]:
            s['speaker'] = '张三'
            s['speakerBio'] = ''
        sessions[0]['speakerBio'] = '李红岩，华南师范大学兼职特聘教授，中国编辑学会副会长。'
        recs = _parse(_OVERVIEW_BODY, vlm=sessions)
        self.assertEqual(len(recs), 4)
        self.assertIn('中国编辑学会副会长', recs[0].get('speakerBio') or '')
        for r in recs[1:]:
            self.assertEqual(r.get('speakerBio'), '',
                             '张三的 bio 为空时不得复用李红岩的简介')


class TestCs5487GuardStillWorks(unittest.TestCase):
    """② 负例：正文单日权威值时守卫照常生效（cs 5487 回归保护）。"""

    def test_single_day_body_still_drops_vlm_sessions(self):
        body = (
            '<div class="content">'
            '<p>报告人:朱永官</p>'
            '<p>时间:2023年12月16日</p>'
            '<p>地点:生科院102会议室</p>'
            '<p>朱永官，中国科学院院士，长期从事环境科学研究。</p>'
            '<img src="%s">' % IMG +
            '</div>'
        )
        # VLM 桩由 _parse 按 URL 分派为 _FAKE_CS5487（编造的 2 场、讲者各异）
        recs = _parse(body, 'http://cs.scnu.edu.cn/a/20231022/5487.html')
        self.assertEqual(len(recs), 1, '正文单日权威值 + VLM 编造多场 → 守卫须丢弃')
        self.assertEqual(recs[0].get('speaker'), '朱永官')


class TestTimeparseRangeEnd(unittest.TestCase):
    """③「M月D-D日」区间补 end。"""

    def test_range_end_parsed(self):
        for s in ('时间:2026年10月26-29日', '2026年10月26日至29日', '10月26—29日',
                  '2026年10月26日-29日'):
            r = TP.parse_cn_time(s, 2026)
            self.assertIsNotNone(r, s)
            self.assertEqual(str(r['start'])[:10], '2026-10-26', s)
            self.assertEqual(str(r['end'])[:10], '2026-10-29', s)

    def test_same_day_clock_range_not_overridden(self):
        r = TP.parse_cn_time('2026年10月26日 15:00-17:45', 2026)
        self.assertEqual(str(r['end'])[:10], '2026-10-26',
                         '同日钟点区间不得被跨日区间覆盖')

    def test_single_day_unchanged(self):
        r = TP.parse_cn_time('2026年10月26日', 2026)
        self.assertIsNone(r.get('end'), '单日不得凭空补 end')

    def test_cross_month_range_skipped(self):
        r = TP.parse_cn_time('2026年11月30-10月2日', 2026)
        self.assertIsNone(r.get('end'), '跨月区间（尾日<首日）保守跳过')


class TestFieldVocabPureComplex(unittest.TestCase):
    """④ field_vocab 提供「纯 COMPLEX 组」入口（不含尊称）。"""

    def test_pure_complex_entry(self):
        self.assertTrue(FV.COMPLEX_TITLE_PURE_RE.search('青年英才'))
        self.assertIsNone(FV.COMPLEX_TITLE_PURE_RE.search('先生'))
        self.assertIsNone(FV.COMPLEX_TITLE_PURE_RE.search('女士'))
        # 对照：COMPLEX_TITLE_ALT_RE 含尊称（故不能当职称词表用）
        self.assertTrue(FV.COMPLEX_TITLE_ALT_RE.search('先生'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
