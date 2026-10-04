# -*- coding: utf-8 -*-
"""时间链路方案 A（2026-10-04）的守卫：口径收敛 + 守卫补漏 + 人工标注回填。

本套件锁四组改动，任何一组被回退都会红：

  ① CV3 / hybrid 两处 end 守卫
      · end == start → 置空（库里曾积压 72 条，现行 CV3 只修 end < start）
      · hybrid 采纳 LLM 的 lectureEnd 必须 end > start（此前只校验年份）
  ② 占位口径收敛到单一事实源 field_vocab.is_placeholder_time
      · is_news_record 不再写死 00:00/08:00、不看 timeUnknown
      · 地点时间回填对 08:00 占位同样生效（此前只认 00:00，
        会给 08:00 占位留下「08:00-17:00」半真半假的区间）
  ③ timeConfidence 词表归一到 {high, mid, low}（孤儿值 medium → mid）
  ④ timeUnknownSource 溯源 + 全量重抓时人工标注回填
      （全量从空 dict 重建，_mark_time_unknown 的「不覆盖已有标注」救不了它）

另锁 scripts/audit_data_quality.py 新增的两条体检档：
  · end == start（此前落不进 <0 / >24 / 8~24 任何一档，对体检隐身）
  · timeConfidence=high 却命中 cv-publish-after-lecture（错年信号无人降级）

运行：python tests/test_time_fixes_20261004.py
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers  # noqa: E402


def _load(name, relpath):
    """按文件路径显式加载（scraper/ scripts/ 均无 __init__.py）。"""
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CV3EndGuardsTest(unittest.TestCase):
    """① CV3：end==start 置空；end<start 仍走原有的 +12h/交换修正。"""

    def test_01_end等于开始时置空(self):
        r = {'lectureStart': '2026-04-20 12:15:00',
             'lectureEnd': '2026-04-20 12:15:00'}
        notes = parsers._cross_validate(r, None, None, None, None)
        self.assertIsNone(r['lectureEnd'],
                          'end==start 应被置空（时长为 0 不是真实区间）')
        self.assertIn('cv-end-eq-start-cleared', notes, '置空必须留 note 溯源')

    def test_02_end早于开始仍修正(self):
        r = {'lectureStart': '2026-04-20 19:30:00',
             'lectureEnd': '2026-04-20 09:00:00'}
        parsers._cross_validate(r, None, None, None, None)
        self.assertIsNotNone(r['lectureEnd'], '不得把 end<start 也一并清空')
        self.assertGreater(r['lectureEnd'], r['lectureStart'],
                           '修正后仍倒挂——原 CV3 的 +12h/交换逻辑被破坏')

    def test_03_end缺失不报错(self):
        r = {'lectureStart': '2026-04-20 12:15:00', 'lectureEnd': None}
        parsers._cross_validate(r, None, None, None, None)
        self.assertIsNone(r['lectureEnd'])


class IsNewsRecordVocabTest(unittest.TestCase):
    """② is_news_record 与占位单一事实源收敛。"""

    def test_01_真实08时钟按时刻比较(self):
        """人工核实过的真 8:00 讲座（timeUnknown=False）→ 用时刻比。

        当天 08:00 讲、09:00 才发 = 事后回顾稿 → 应判新闻。
        收敛前这里被写死的 08:00 拦进「占位」分支，同日白天发布一律放行。
        """
        rec = {'lectureStart': '2026-05-20 08:00:00',
               'publishTime': '2026-05-20 09:00:00',
               'timeUnknown': False}
        self.assertTrue(parsers.is_news_record(rec),
                        '真 08:00 讲座发布于讲座开始之后，应判为回顾稿')

    def test_02_未标注的08仍是占位分支(self):
        """未标注 08:00 = 铁律占位 → 退化为日期比，同日白天发布不判（防误杀预告）。"""
        rec = {'lectureStart': '2026-05-20 08:00:00',
               'publishTime': '2026-05-20 09:00:00'}
        self.assertFalse(parsers.is_news_record(rec),
                         '占位 + 同日白天发布 = 正常预告，不得判为回顾稿')

    def test_03_占位同日晚间发布仍判回顾(self):
        """2026-08-01 用户裁定的规则必须保持。"""
        rec = {'lectureStart': '2026-05-20 08:00:00',
               'publishTime': '2026-05-20 19:00:00'}
        self.assertTrue(parsers.is_news_record(rec))

    def test_04_明确时刻的记录行为不变(self):
        rec = {'lectureStart': '2026-05-20 14:30:00',
               'publishTime': '2026-05-20 15:00:00'}
        self.assertTrue(parsers.is_news_record(rec))
        rec2 = {'lectureStart': '2026-05-20 14:30:00',
                'publishTime': '2026-05-20 09:00:00'}
        self.assertFalse(parsers.is_news_record(rec2))

    def test_05_人工标注优先于值(self):
        """timeUnknown=True（人工说时刻未知）→ 即使值不是 08:00/00:00 也走日期比。"""
        rec = {'lectureStart': '2026-05-20 14:30:00',
               'publishTime': '2026-05-20 15:00:00',
               'timeUnknown': True}
        self.assertFalse(parsers.is_news_record(rec),
                         '人工标注「时刻未知」时应退化为日期比，不按 14:30 大小判')


class LocationBackfillPlaceholderTest(unittest.TestCase):
    """② 地点时间回填：占位判定改调 is_placeholder_time。"""

    class _St(object):
        text = ''
        ocr_text = ''

    def test_01_08占位的start被补全(self):
        r = {'lectureStart': '2026-05-20 08:00:00', 'lectureEnd': None,
             '_hasClock': False, 'topic': None, 'location': None}
        parsers._extract_topic_location(self._St(), r, '标题', [(14, 30, 17, 0)])
        self.assertEqual(r['lectureStart'], '2026-05-20 14:30:00',
                         '08:00 占位应与 00:00 同等补全 start')
        self.assertEqual(r['lectureEnd'], '2026-05-20 17:00:00')
        self.assertIs(r['_hasClock'], True, '时刻来自地点原文 → 应置真时钟')

    def test_02_00占位行为不变(self):
        r = {'lectureStart': '2026-05-20 00:00:00', 'lectureEnd': None,
             '_hasClock': False, 'topic': None, 'location': None}
        parsers._extract_topic_location(self._St(), r, '标题', [(14, 30, 17, 0)])
        self.assertEqual(r['lectureStart'], '2026-05-20 14:30:00')
        self.assertEqual(r['lectureEnd'], '2026-05-20 17:00:00')

    def test_03_真实08时钟不被覆盖(self):
        """_hasClock=True → 08:00 是源页真时刻，不得被地点区间改写 start。"""
        r = {'lectureStart': '2026-05-20 08:00:00', 'lectureEnd': None,
             '_hasClock': True, 'topic': None, 'location': None}
        parsers._extract_topic_location(self._St(), r, '标题', [(14, 30, 17, 0)])
        self.assertEqual(r['lectureStart'], '2026-05-20 08:00:00',
                         '真实 08:00 被地点回填覆盖了')

    def test_04_非占位时刻不改start(self):
        r = {'lectureStart': '2026-05-20 14:30:00', 'lectureEnd': None,
             '_hasClock': True, 'topic': None, 'location': None}
        parsers._extract_topic_location(self._St(), r, '标题', [(9, 0, 11, 0)])
        self.assertEqual(r['lectureStart'], '2026-05-20 14:30:00')


class ConfidenceVocabTest(unittest.TestCase):
    """③ timeConfidence 词表归一。"""

    def _parse(self, confidence):
        orig = parsers.resolve_lecture_time
        parsers.resolve_lecture_time = lambda **kw: {
            'start': '2026-05-20 14:30:00', 'end': '2026-05-20 16:00:00',
            'confidence': confidence, 'note': 'unit-test'}
        try:
            html = ('<html><body><div class="content"><h1>学术报告通知</h1>'
                    '<p>讲座题目：深度学习的最新进展</p><p>主讲人：张三 教授</p>'
                    '<p>时间：2026年5月20日 14:30-16:00</p><p>地点：理6栋302</p>'
                    '</div></body></html>')
            out = parsers.parse_detail(html, 'http://x.scnu.edu.cn/a/20260520/1.html',
                                       '测试学院', '石牌', default_year=2026)
        finally:
            parsers.resolve_lecture_time = orig
        rec = out[0] if isinstance(out, list) and out else out
        return rec if isinstance(rec, dict) else {}

    def test_01_越界取值归一为mid(self):
        self.assertEqual(self._parse('medium').get('timeConfidence'), 'mid',
                         "孤儿值 'medium' 应在出口归一为 mid")

    def test_02_合法取值原样保留(self):
        for conf in ('high', 'mid', 'low'):
            self.assertEqual(self._parse(conf).get('timeConfidence'), conf)

    def test_03_无置信度不凭空造(self):
        self.assertIsNone(self._parse(None).get('timeConfidence'))


class TimeUnknownProvenanceTest(unittest.TestCase):
    """④ timeUnknownSource 溯源产出。"""

    def test_01_自动打标写auto(self):
        r = {'lectureStart': '2026-05-20 14:30:00', '_hasClock': True}
        parsers._mark_time_unknown(r)
        self.assertIs(r['timeUnknown'], False)
        self.assertEqual(r['timeUnknownSource'], 'auto')
        self.assertNotIn('_hasClock', r)

    def test_02_继承的标注补legacy且不覆盖(self):
        r = {'lectureStart': '2026-05-20 08:00:00', 'timeUnknown': False}
        parsers._mark_time_unknown(r)
        self.assertIs(r['timeUnknown'], False, '已有标注被覆盖')
        self.assertEqual(r['timeUnknownSource'], 'legacy',
                         '已有标注但无来源 → 应补 legacy（保守按人工对待）')

    def test_03_已有来源不改写(self):
        r = {'lectureStart': '2026-05-20 08:00:00', 'timeUnknown': False,
             'timeUnknownSource': 'human'}
        parsers._mark_time_unknown(r)
        self.assertEqual(r['timeUnknownSource'], 'human')


class HybridEndGuardTest(unittest.TestCase):
    """① hybrid 采纳 LLM 的 lectureEnd 必须 end > start。"""

    def setUp(self):
        import hybrid
        self.hybrid = hybrid
        self.body = '讲座通知。时间：2026-03-05 14:30-16:00。地点：理6栋302。'

    def _a(self, start, end=None):
        a = {'lectureStart': start, 'lectureStartSnippet': start}
        if end:
            a['lectureEnd'] = end
            a['lectureEndSnippet'] = end
        return a

    def test_01_end等于start不采纳(self):
        result = {'lectureStart': '2026-03-05 00:00:00'}
        a = self._a('2026-03-05 14:30:00', '2026-03-05 14:30:00')
        adopted = self.hybrid._merge_a_into_result(
            result, a, body_text=self.body,
            force_fields={'lectureStart', 'lectureEnd'})
        self.assertEqual(result['lectureStart'], '2026-03-05 14:30:00',
                         'start 应照常被补全')
        self.assertNotIn('lectureEnd', result,
                         'end==start 的模型幻觉被采纳了（库里 72 条的来源口子）')
        self.assertNotIn('lectureEnd', adopted)

    def test_02_end早于start不采纳(self):
        result = {'lectureStart': '2026-03-05 00:00:00'}
        a = self._a('2026-03-05 14:30:00', '2026-03-05 09:00:00')
        self.hybrid._merge_a_into_result(
            result, a, body_text=self.body,
            force_fields={'lectureStart', 'lectureEnd'})
        self.assertNotIn('lectureEnd', result, 'end<start 的模型值被采纳了')

    def test_03_正常end仍采纳(self):
        result = {'lectureStart': '2026-03-05 00:00:00'}
        a = self._a('2026-03-05 14:30:00', '2026-03-05 16:00:00')
        self.hybrid._merge_a_into_result(
            result, a, body_text=self.body,
            force_fields={'lectureStart', 'lectureEnd'})
        self.assertEqual(result.get('lectureEnd'), '2026-03-05 16:00:00',
                         '合法 end 被新守卫误伤')


class RestoreHumanTimeTest(unittest.TestCase):
    """④ 全量重抓后人工标注回填。"""

    @classmethod
    def setUpClass(cls):
        cls.s = _load('sc_restore_test', os.path.join('scraper', 'scraper.py'))

    def test_01_人工标注全量后保留(self):
        existing = [{'sourceUrl': 'http://a/1.html', 'lectureIndex': None,
                     'timeUnknown': False, 'timeUnknownSource': 'human'}]
        new = [{'sourceUrl': 'http://a/1.html', 'lectureIndex': None,
                'lectureStart': '2026-05-20 08:00:00',
                'timeUnknown': True, 'timeUnknownSource': 'auto'}]
        out = self.s.restore_human_time_annotations(new, existing)
        self.assertIs(out[0]['timeUnknown'], False, '人工标注被全量重抓冲掉了')
        self.assertEqual(out[0]['timeUnknownSource'], 'human')

    def test_02_auto标注以最新解析为准(self):
        existing = [{'sourceUrl': 'http://a/2.html', 'lectureIndex': None,
                     'timeUnknown': True, 'timeUnknownSource': 'auto'}]
        new = [{'sourceUrl': 'http://a/2.html', 'lectureIndex': None,
                'lectureStart': '2026-05-20 14:30:00',
                'timeUnknown': False, 'timeUnknownSource': 'auto'}]
        out = self.s.restore_human_time_annotations(new, existing)
        self.assertIs(out[0]['timeUnknown'], False,
                      'auto 标注应让位给最新解析（否则旧版错标永久锁死）')

    def test_03_legacy等同人工保留(self):
        existing = [{'sourceUrl': 'http://a/3.html', 'lectureIndex': None,
                     'timeUnknown': True, 'timeUnknownSource': 'legacy'}]
        new = [{'sourceUrl': 'http://a/3.html', 'lectureIndex': None,
                'timeUnknown': False, 'timeUnknownSource': 'auto'}]
        out = self.s.restore_human_time_annotations(new, existing)
        self.assertIs(out[0]['timeUnknown'], True,
                      'legacy（10-03 前的人工标注）应保守保留')

    def test_04_多讲座按lectureIndex区分(self):
        existing = [
            {'sourceUrl': 'http://a/4.html', 'lectureIndex': 1,
             'timeUnknown': True, 'timeUnknownSource': 'human'},
            {'sourceUrl': 'http://a/4.html', 'lectureIndex': 2,
             'timeUnknown': False, 'timeUnknownSource': 'human'},
        ]
        new = [
            {'sourceUrl': 'http://a/4.html', 'lectureIndex': 1,
             'timeUnknown': False, 'timeUnknownSource': 'auto'},
            {'sourceUrl': 'http://a/4.html', 'lectureIndex': 2,
             'timeUnknown': True, 'timeUnknownSource': 'auto'},
        ]
        out = self.s.restore_human_time_annotations(new, existing)
        self.assertIs(out[0]['timeUnknown'], True, '场次 1 的标注被场次 2 覆盖')
        self.assertIs(out[1]['timeUnknown'], False, '场次 2 的标注被场次 1 覆盖')

    def test_05_空基底安全(self):
        self.assertEqual(
            self.s.restore_human_time_annotations([{'sourceUrl': 'u'}], []),
            [{'sourceUrl': 'u'}])

    def test_06_全量分支确实调用了回填(self):
        """静态锁：调用被挪出全量分支（或整段删掉）时这里会红。"""
        with open(os.path.join(ROOT, 'scraper', 'scraper.py'), encoding='utf-8') as f:
            src = f.read()
        i_else = src.index('    else:\n        out = dedup(raw)')
        i_call = src.index('restore_human_time_annotations(out, existing)')
        i_sort = src.index("out.sort(key=lambda x: x.get('lectureStart') or '', reverse=True)")
        self.assertGreater(i_call, i_else, '回填调用不在全量分支内')
        self.assertLess(i_call, i_sort, '回填必须发生在写盘/排序之前')


class AuditNewRulesTest(unittest.TestCase):
    """体检新增两档 + 修复建议可匹配。"""

    @classmethod
    def setUpClass(cls):
        cls.audit = _load('audit_under_test', os.path.join('scripts', 'audit_data_quality.py'))

    def _descs(self, recs):
        return [i[3] for i in self.audit.scan(recs)]

    def test_01_end等于开始会被报出(self):
        rec = {'sourceUrl': 'http://a/1.html', 'title': '报告',
               'speaker': '张三', 'location': '理6栋302',
               'lectureStart': '2026-04-20 12:15:00',
               'lectureEnd': '2026-04-20 12:15:00'}
        descs = self._descs([rec])
        self.assertTrue(any('结束时间与开始时间相同' in d for d in descs),
                        'end==start 对体检隐身（此前只报 <0/>24/8~24 三档）')
        kind, _ = self.audit.fixability('时间', '结束时间与开始时间相同（时长为 0）')
        self.assertEqual(kind, 'auto', 'end==start 应判为可自动修（置空）')

    def test_02_high与错年信号矛盾会被报出(self):
        rec = {'sourceUrl': 'http://a/2.html', 'title': '报告',
               'speaker': '张三', 'location': '理6栋302',
               'lectureStart': '2013-01-10 12:00:00',
               'publishTime': '2014-01-10 11:07',
               'timeConfidence': 'high',
               'timeNote': 'authoritative-label;cv-publish-after-lecture'}
        descs = self._descs([rec])
        self.assertTrue(any('置信度 high 但已命中' in d for d in descs),
                        '错年信号算了却没人降级——该档缺失')

    def test_03_已核实晚发的记录不再告警(self):
        rec = {'sourceUrl': 'http://a/3.html', 'title': '报告',
               'speaker': '张三', 'location': '理6栋302',
               'lectureStart': '2015-01-06 14:30:00',
               'publishTime': '2016-01-06 11:41',
               'timeConfidence': 'high',
               'timeNote': 'authoritative-label;cv-publish-after-lecture;'
                           'cv-verified-published-late'}
        descs = self._descs([rec])
        self.assertFalse(any('置信度 high 但已命中' in d for d in descs),
                         '已回源页核过的「页面晚发」不应重复告警')

    def test_04_词表越界会被报出(self):
        rec = {'sourceUrl': 'http://a/4.html', 'title': '报告',
               'speaker': '张三', 'location': '理6栋302',
               'lectureStart': '2018-11-23 08:00:00', 'timeUnknown': True,
               'timeConfidence': 'medium'}
        descs = self._descs([rec])
        self.assertTrue(any('置信度取值越界' in d for d in descs))
        kind, _ = self.audit.fixability('时间', '置信度取值越界(medium)，应为 high/mid/low')
        self.assertEqual(kind, 'manual')


class DisplayLayerConsumptionTest(unittest.TestCase):
    """⑤ 展示层消费置信度：白名单 / 判定 / 角标三处必须同步。

    三处任何一处漏改，症状都是「字段发了但前端不显示」或反过来
    「前端读了但白名单剥离 → 恒 undefined」——两种都零报错，只能靠静态锁。
    """

    @classmethod
    def setUpClass(cls):
        cls.ff = _load('ff_under_test', os.path.join('scripts', 'frontend_fields.py'))
        with open(os.path.join(ROOT, 'site', 'app.display.js'), encoding='utf-8') as f:
            cls.display_js = f.read()
        with open(os.path.join(ROOT, 'site', 'index.html'), encoding='utf-8') as f:
            cls.index_html = f.read()

    def test_01_白名单放行timeConfidence(self):
        self.assertIn('timeConfidence', self.ff.FRONTEND_KEEP,
                      'timeConfidence 不在白名单 → 前端恒为 undefined，角标永不出现')

    def test_02_前端判定读该字段(self):
        self.assertIn('isDateSuspect', self.display_js, '判定方法缺失')
        self.assertIn('timeConfidence', self.display_js, 'isDateSuspect 未读 timeConfidence')
        self.assertIn("timeConfidence === 'low'", self.display_js,
                      '角标阈值应为 low（mid 属正常降级，全亮即噪音）')

    def test_03_两处卡片模板都挂了角标(self):
        self.assertGreaterEqual(
            self.index_html.count('isDateSuspect('), 2,
            '普通卡与论坛卡（u.head）都应能显示「日期待核」角标')

    def test_04_角标用的是已编译的tailwind类(self):
        """vendor CSS 是预编译的：用了没编译的类 = 角标渲染成裸文本。"""
        css = open(os.path.join(ROOT, 'site', 'vendor', 'tailwind.css'),
                   encoding='utf-8').read()
        for cls_ in ('bg-amber-50{', 'text-amber-600{', 'border-amber-200{'):
            self.assertIn(cls_, css, 'tailwind 未编译 %s —— 角标会没底色' % cls_)


if __name__ == '__main__':
    unittest.main(verbosity=2)
