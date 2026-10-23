# -*- coding: utf-8 -*-
"""多主讲人：同题合并 / 连写预检（2026-10-23）。

背景（用户拍板口径）：
- 「一场讲座、多位主讲人共讲同一题目」→ **合并为 1 条**，speaker 用「、」连接。
- 「多场讲座、各有独立题目」→ 仍按题目分段拆成 N 条（原行为，须防回归）。

两个实测案例：
- em/8855「【题目】破产法庭与债券发行市场化　【主讲人】辛宇教授、徐莉萍教授」
- cs/4145「题目：人工智能、大数据、教育科学论坛　报告人：国家杰青刘梦赤教授
  长江学者陈建二教授长江学者卢晓中教授」（页面 3 个简介表格只有姓名/单位/头衔，
  无各自题目 → 合并）

另锁一个连带修复：源页排版「报 告 人：陈钊 教授　主 持 人：刘愿 教授」在取文后
成「陈钊 教授主 持人:刘愿 教授」——「主 持人」不在 LABELS（只收连写「主持人」），
STOP 拦不住，连写预检会把主持人当成第二位主讲人一起合并（skc786/skc691 实测：
speaker 变成「陈钊、刘愿」、主持人丢失）。预检前按宽松标签截断后恢复正常。

离线单测：不发网络请求、不走 LLM。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scraper'))
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers  # noqa: E402


def _spk(*names):
    return [{'name': n, 'honorific': '教授', 'aff': ''} for n in names]


class SameTopicMergeTest(unittest.TestCase):
    """题目拆不出 n 段 → 合并为 1 条（em 8855 形态）。"""

    def test_merge_single_record(self):
        base = {'topic': '破产法庭与债券发行市场化',
                'title': '华师经管学术讲座第399期（管理）'}
        out = parsers._split_by_speakers(base, _spk('辛宇', '徐莉萍'))
        self.assertIsNotNone(out)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['speaker'], '辛宇、徐莉萍')
        self.assertEqual(out[0]['topic'], '破产法庭与债券发行市场化')

    def test_merge_keeps_plain_field_set(self):
        """合并结果必须是「1 条普通记录」：不带拆分专用标记，字段集合与单条路径一致。

        回归点：曾显式写入 isMultiLecture/sourceCount/notes，导致 golden 与快照
        比对多出字段（skc786 实测失败）。
        """
        base = {'topic': '人工智能、大数据、教育科学论坛', 'title': '学术报告'}
        out = parsers._split_by_speakers(base, _spk('刘梦赤', '陈建二', '卢晓中'))
        self.assertEqual(len(out), 1)
        rec = out[0]
        self.assertEqual(rec['speaker'], '刘梦赤、陈建二、卢晓中')
        for k in ('isMultiLecture', 'sourceCount', 'notes', 'lectureIndex', 'lectureCount'):
            self.assertNotIn(k, rec, '合并记录不应带拆分标记字段 %s' % k)
        self.assertEqual(rec['speakerSource'], 'label')

    def test_merge_takes_first_honorific(self):
        base = {'topic': '同一个题目', 'title': ''}
        spk = [{'name': '张三', 'honorific': '国家杰青', 'aff': 'A大学'},
               {'name': '李四', 'honorific': '长江学者', 'aff': 'B大学'}]
        out = parsers._split_by_speakers(base, spk)
        self.assertEqual(out[0]['speakerTitle'], '国家杰青')
        self.assertEqual(out[0]['speakerAffiliation'], 'A大学')

    def test_merge_keeps_base_affiliation_when_speaker_aff_empty(self):
        """8855 回归：派生路径 aff 恒空时，合并不得抹掉基底已有单位。

        端到端实测：hybrid 填入并采纳「中山大学管理学院」（llmAdopted 有记录），
        旧代码在共享合并分支用空 aff 覆写 → 终值为 ''，llmAdopted 与终值矛盾。
        """
        base = {'topic': '破产法庭与债券发行市场化',
                'title': '华师经管学术讲座第399期（管理）',
                'speakerAffiliation': '中山大学管理学院',
                'speakerTitle': '教授'}
        out = parsers._split_by_speakers(base, _spk('辛宇', '徐莉萍'))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['speakerAffiliation'], '中山大学管理学院')
        self.assertEqual(out[0]['speakerTitle'], '教授')

    def test_merge_fills_title_from_base_when_honorific_empty(self):
        base = {'topic': '同一个题目', 'title': '', 'speakerTitle': '副教授'}
        spk = [{'name': '张三', 'honorific': '', 'aff': ''},
               {'name': '李四', 'honorific': '', 'aff': ''}]
        out = parsers._split_by_speakers(base, spk)
        self.assertEqual(out[0]['speakerTitle'], '副教授')
        self.assertEqual(out[0]['speakerAffiliation'], '')


class SplitStillWorksTest(unittest.TestCase):
    """题目能拆成 n 段 → 仍拆 N 条（防本次改动误伤原行为）。"""

    def test_numbered_segments_split(self):
        base = {'topic': '1) 集成光子学与智能感知 2) 超快光学与微纳加工',
                'title': '青年学者论坛'}
        out = parsers._split_by_speakers(base, _spk('黄加耀', '刘轩奕'))
        self.assertEqual(len(out), 2)
        self.assertEqual([r['speaker'] for r in out], ['黄加耀', '刘轩奕'])
        self.assertEqual(out[0]['topic'], '集成光子学与智能感知')
        self.assertEqual(out[1]['topic'], '超快光学与微纳加工')
        self.assertTrue(out[0]['isMultiLecture'])
        self.assertEqual([r['sourceCount'] for r in out], [1, 0])

    def test_split_inherits_base_affiliation(self):
        """拆分支同为仅填空：本场无 aff 时保留基底单位（em/8855 同族修复）。"""
        base = {'topic': '1) 题目甲 2) 题目乙', 'title': '论坛',
                'speakerAffiliation': '中山大学管理学院', 'speakerTitle': '教授'}
        out = parsers._split_by_speakers(base, _spk('黄加耀', '刘轩奕'))
        self.assertEqual(len(out), 2)
        for r in out:
            self.assertEqual(r['speakerAffiliation'], '中山大学管理学院')
            self.assertEqual(r['speakerTitle'], '教授')


class GuardTest(unittest.TestCase):
    """原守卫不变：同一人重复 / 单主讲人 → 不拆也不合并。"""

    def test_duplicate_same_name_returns_none(self):
        base = {'topic': '某题目', 'title': ''}
        self.assertIsNone(parsers._split_by_speakers(base, _spk('辛宇', '辛宇')))

    def test_single_speaker_returns_none(self):
        base = {'topic': '某题目', 'title': ''}
        self.assertIsNone(parsers._split_by_speakers(base, _spk('辛宇')))


# 华师排版：「报 告 人／主 持 人」字间带空格，取文后成「主 持人」
_HTML_REPORT_HOST = '''<html><body><div class="content">
<p>报 告 人：陈钊  教授</p>
<p>主 持 人：刘愿  教授</p>
<p>时 间：2023年11月16日15:30</p>
<p>地 点：经济与管理学院文三栋415会议室</p>
<p>主办单位：华南师范大学</p>
</div></body></html>'''

# cs/4145 形态：头衔+姓名+职称 连写，三人无各自题目
_HTML_CONCAT = '''<html><body><div class="content">
<p>题 目：人工智能、大数据、教育科学论坛</p>
<p>报告人：国家杰青刘梦赤教授长江学者陈建二教授长江学者卢晓中教授</p>
<p>时 间：2019年7月11日9:00</p>
<p>地 点：计算机学院101会议室</p>
</div></body></html>'''


class ConcatPrecheckTest(unittest.TestCase):
    """连写多主讲人预检（cs 4145 引入）：走完整 parse_detail，锁端到端行为。"""

    def _parse(self, html):
        out = parsers.parse_detail(
            html, 'http://example.scnu.edu.cn/a/20230101/1.html',
            '计算机学院', '石牌', skip_news_filter=True)
        return out if isinstance(out, list) else ([out] if out else [])

    def test_host_not_swallowed_into_speaker(self):
        """主持人不得被当成第二位主讲人（skc786/skc691 回归）。"""
        recs = self._parse(_HTML_REPORT_HOST)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['speaker'], '陈钊')
        self.assertEqual(recs[0]['host'], '刘愿')

    def test_concat_speakers_all_extracted(self):
        """连写三人全部提取，不被 OCR 兜底的 2~4 字限制截断。"""
        recs = self._parse(_HTML_CONCAT)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['speaker'], '刘梦赤、陈建二、卢晓中')
        self.assertEqual(recs[0]['speakerTitle'], '国家杰青')
        self.assertEqual(recs[0]['topic'], '人工智能、大数据、教育科学论坛')


if __name__ == '__main__':
    unittest.main(verbosity=2)
