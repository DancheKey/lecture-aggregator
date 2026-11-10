#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主数据不变式守卫（2026-09-10 round-16）——锁定 data/lectures.json 已修复的干净状态。

背景：round-14~16 人工修复了 2955 条数据中的多场拆分、串页污染、幻觉单位、页脚尾部
垃圾等问题。爬虫重抓/回填/再修复脚本若引入同类污染，本测试直接红，防止脏数据悄悄回流。

不变式（宁缺勿错，全部源自历次实测污染模式）：
1) speaker/speakerAffiliation 无幻觉指纹（占位名/模板句；长文本字段不扫模板句，避免误伤真实内容）；
2) speaker 非确定性占位名（张三/李四/John Smith…）；
3) abstract/speakerBio/location 尾部无「讲座一/二、心理学学术沙龙、版权所有」等栏目/页脚垃圾；
4) 字段不为纯空白。

运行：python tests/test_data_quality.py
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')

import parsers as P

# 与 test_parser_golden._TAIL_GARBAGE_RE 同源的尾部垃圾词表（历次修复实录）
TAIL_GARBAGE_RE = re.compile(
    r'(讲座一|讲座二|学术讲座一|心理学学术沙龙|版权所有|Copyright|'
    r'关于华南师范大学|上一篇|下一篇)\s*$')


def load_records():
    if not os.path.exists(DATA_PATH):
        raise unittest.SkipTest('缺少 data/lectures.json')
    with open(DATA_PATH, encoding='utf-8') as f:
        doc = json.load(f)
    return doc.get('data', [])


class DataQualityTest(unittest.TestCase):
    recs = None

    @classmethod
    def setUpClass(cls):
        cls.recs = load_records()

    def test_no_hallucinated_short_fields(self):
        """speaker/speakerAffiliation 命中幻觉指纹即为脏数据（ctld952 郭华平事件回归）。"""
        bad = []
        for i, r in enumerate(self.recs):
            for fld in ('speaker', 'speakerAffiliation'):
                if P._is_hallucinated(r.get(fld)):
                    bad.append(f'[{i}].{fld}={r.get(fld)!r} @ {r.get("sourceUrl")}')
        self.assertEqual(bad, [], f'{len(bad)} 条字段命中幻觉指纹：\n' + '\n'.join(bad[:20]))

    def test_no_placeholder_speaker(self):
        """speaker 不得为占位值（XXX）或确定性占位名。"""
        bad = []
        for i, r in enumerate(self.recs):
            v = (r.get('speaker') or '').strip()
            if not v:
                continue
            if P._PLACEHOLDER_RE.match(v) or P._HALLUC_NAME_FULL_RE.match(v):
                bad.append(f'[{i}].speaker={v!r} @ {r.get("sourceUrl")}')
        self.assertEqual(bad, [], f'{len(bad)} 条占位 speaker：\n' + '\n'.join(bad[:20]))

    def test_no_tail_garbage(self):
        """abstract/speakerBio/location 尾部无栏目/页脚垃圾
        （psy 沙龙页「心理学学术沙龙」、多场页「讲座一/二」尾巴、版权页脚——均为修复实录）。"""
        bad = []
        for i, r in enumerate(self.recs):
            for fld in ('abstract', 'speakerBio', 'location', 'speakerAffiliation'):
                v = (r.get(fld) or '').strip()
                if not v:
                    continue
                m = TAIL_GARBAGE_RE.search(v)
                if m:
                    bad.append(f'[{i}].{fld} 尾部 {m.group(0)!r} @ {r.get("sourceUrl")}')
        self.assertEqual(bad, [], f'{len(bad)} 条尾部垃圾：\n' + '\n'.join(bad[:20]))

    def test_fields_not_blank_only(self):
        """字段值不得为纯空白（空串允许——缺失语义；空白串是清洗事故）。"""
        bad = []
        for i, r in enumerate(self.recs):
            for fld in ('speaker', 'topic', 'location', 'abstract', 'speakerBio'):
                v = r.get(fld)
                if v is not None and v.strip() == '' and v != '':
                    bad.append(f'[{i}].{fld}={v!r} @ {r.get("sourceUrl")}')
        self.assertEqual(bad, [], f'{len(bad)} 条空白字段：\n' + '\n'.join(bad[:20]))

    def test_multi_lecture_marker_consistency(self):
        """多场标记一致性：isMultiLecture=True 则 lectureIndex/lectureCount 必须成对有效。"""
        bad = []
        for i, r in enumerate(self.recs):
            if not r.get('isMultiLecture'):
                continue
            idx, cnt = r.get('lectureIndex'), r.get('lectureCount')
            if not (isinstance(idx, int) and 1 <= idx <= (cnt or 0)):
                bad.append(f'[{i}] idx={idx!r} cnt={cnt!r} @ {r.get("sourceUrl")}')
        self.assertEqual(bad, [], f'{len(bad)} 条多场标记异常：\n' + '\n'.join(bad[:20]))


class ThreeRecordsBackfillTest(unittest.TestCase):
    """2026-11-10 三条历史记录回填锁（乙方案的数据回填部分）。

    这三条来自用户逐条回源核对，值取自源页原文，不是规则层能重新解析出来的：
      · life/6021  真题目只在已失效的海报图上（旧 VLM 把它写进了 title）；
      · ggy/5492   正文「第一讲：题目」此前没被识别，bio 尾部还粘了这句题目；
      · physics/11723 同一主题三位分享人，speaker 此前只留了第一位。
    规则加固已覆盖后两类新页，但历史数据不会被自动重抓；此处锁死回填值，
    并同层核对生成器派生结果（site/lectures.json 的 speakerKeys）。
    """
    TAILS = ('6021.html', '5492.html', '11723.html')

    @classmethod
    def setUpClass(cls):
        cls.data = {t: cls._pick(load_records(), t) for t in cls.TAILS}
        site_path = os.path.join(ROOT, 'site', 'lectures.json')
        if not os.path.exists(site_path):
            raise unittest.SkipTest('缺少 site/lectures.json')
        with open(site_path, encoding='utf-8') as f:
            site = json.load(f)
        cls.site = {t: cls._pick(site.get('data', []), t) for t in cls.TAILS}

    @staticmethod
    def _pick(recs, tail):
        hits = [r for r in recs if (r.get('sourceUrl') or '').endswith(tail)]
        if len(hits) != 1:
            raise unittest.SkipTest('%s 在数据中不唯一（%d 条）' % (tail, len(hits)))
        return hits[0]

    def test_01_life6021_title_and_topic(self):
        r = self.data['6021.html']
        self.assertEqual(r['title'], r['listTitle'])
        self.assertEqual(r['title'], '学术讲座：西北农林科技大学教授“勷勤论坛”专场讲座')
        self.assertEqual(r['topic'], '一个新的植物衰老调控途径的机制研究')

    def test_02_ggy5492_topic_and_clean_bio(self):
        r = self.data['5492.html']
        self.assertEqual(r['topic'], '城市贫困人口兜底保障的焦点、难点与路径选择')
        bio = r.get('speakerBio') or ''
        self.assertNotIn('第一讲', bio)
        self.assertTrue(bio.endswith('风险治理、全球正义。'), bio[-60:])

    def test_03_physics11723_three_speakers(self):
        self.assertEqual(self.data['11723.html']['speaker'], '薛正远、刘勇、肖洋')

    def test_04_frontend_same_layer_and_derived_keys(self):
        """data 与 site 同层对账；多主讲须派生出 3 个 speakerKeys 供前端显示「等」。"""
        for tail in self.TAILS:
            for field in ('title', 'topic', 'speaker'):
                self.assertEqual(self.data[tail].get(field), self.site[tail].get(field),
                                 '%s 的 %s 与前端切片不一致' % (tail, field))
        self.assertEqual(self.site['11723.html'].get('speakerKeys'),
                         ['薛正远', '刘勇', '肖洋'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
