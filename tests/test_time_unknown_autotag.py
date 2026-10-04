# -*- coding: utf-8 -*-
"""解析器自动产出 `timeUnknown` 的守卫（2026-10-03，方案 A）。

## 背景

`08:00` / `00:00` 是本项目的**占位约定**（源页只给日期时解析器统一填充），
但确有讲座真在 8:00 开始。此前该区分全靠**人工**在 data/lectures.json 里打
`timeUnknown`——而爬虫根本不产出这个字段，于是**全量重抓会把这批标注全部丢失**，
全部退回「一律时间待定」。

本套件锁定三件事：
  ① 打标方向正确：拿到时钟 → False（已知）；只有日期/无信息 → True（未知）；
  ② 人工标注不被覆盖（人工判断优先于自动推断）；
  ③ 内部键 `_hasClock` 不泄漏进最终记录。

判据的单一事实源是 field_vocab.is_placeholder_time（前后端共用），
本套件只管「产出」这一侧。

运行：python tests/test_time_unknown_autotag.py
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers  # noqa: E402
import field_vocab as FV  # noqa: E402

URL = 'http://x.scnu.edu.cn/a/20260520/1.html'


def _page(time_line):
    return (
        '<html><body><div class="content">'
        '<h1>学术报告通知</h1>'
        '<p>讲座题目：深度学习的最新进展</p>'
        '<p>主讲人：张三 教授</p>'
        + ('<p>%s</p>' % time_line if time_line else '')
        + '<p>地点：理6栋302</p>'
        '</div></body></html>'
    )


def parse(time_line):
    out = parsers.parse_detail(_page(time_line), URL, '测试学院', '石牌',
                               default_year=2026)
    rec = out[0] if isinstance(out, list) and out else out
    return rec if isinstance(rec, dict) else {}


class TimeUnknownAutoTagTest(unittest.TestCase):

    def test_70_页面写了时刻应标为已知(self):
        r = parse('时间：2026年5月20日 14:30-16:00')
        self.assertIn('lectureStart', r, '未解析出时间，本用例失去意义')
        self.assertEqual(r.get('timeUnknown'), False,
                         '源页明确写了 14:30-16:00，应标为「时刻已知」')
        self.assertIn('14:30', str(r['lectureStart']))

    def test_71_只有日期应标为未知(self):
        r = parse('时间：2026年5月20日')
        self.assertEqual(r.get('timeUnknown'), True,
                         '源页只给日期、没给时刻，08:00/00:00 是占位填充值，'
                         '必须标为「时间未知」否则会向用户显示编造的时刻')

    def test_72_完全没有时间信息应标为未知(self):
        r = parse('')
        self.assertEqual(r.get('timeUnknown'), True,
                         '页面无任何时间线索，应标为未知')

    def test_73_打标与field_vocab口径一致(self):
        """产出的 timeUnknown 与消费端的 is_placeholder_time 不得矛盾。

        即：产出 False 时，is_placeholder_time 必须返回 False（前端会显示具体时间）；
        产出 True 时，必须返回 True（前端显示时间待定）。
        """
        for line in ('时间：2026年5月20日 14:30-16:00', '时间：2026年5月20日', ''):
            r = parse(line)
            tag = r.get('timeUnknown')
            self.assertIsNotNone(tag, 'timeUnknown 未产出：%r' % line)
            expect_placeholder = bool(tag)
            self.assertEqual(
                FV.is_placeholder_time(r), expect_placeholder,
                '打标与消费端口径矛盾（timeUnknown=%s）：%r' % (tag, line))

    def test_74_人工标注不被覆盖(self):
        """已有 timeUnknown 时原样保留——人工判断优先于自动推断。

        回归 2026-10-03 的两条人工标注：真在 8:00 开始的讲座被标 False，
        重抓时若被自动推断改回 True，页面又会显示「时间待定」，等于修复被撤销。
        """
        r = {'lectureStart': '2025-12-28 08:00:00', 'timeUnknown': False}
        parsers._mark_time_unknown(r)
        self.assertIs(r['timeUnknown'], False, '人工标注 False 被覆盖了')

        r2 = {'lectureStart': '2025-12-28 08:00:00', 'timeUnknown': True}
        parsers._mark_time_unknown(r2)
        self.assertIs(r2['timeUnknown'], True, '人工标注 True 被覆盖了')

    def test_75_内部键不泄漏(self):
        """`_hasClock` 是内部临时键，出口必须 pop 掉，不得进入最终记录。"""
        for line in ('时间：2026年5月20日 14:30-16:00', '时间：2026年5月20日', ''):
            r = parse(line)
            self.assertNotIn('_hasClock', r,
                             '_hasClock 泄漏进最终记录（会污染数据与前端下发）')

    def test_76_无hasClock时退回保守判定(self):
        """缺少 _hasClock（解析器未透出时钟信息）时不得乐观标 False。"""
        # 落在占位值上 -> True
        r = {'lectureStart': '2026-05-20 08:00:00'}
        parsers._mark_time_unknown(r)
        self.assertIs(r['timeUnknown'], True, '占位值上缺信号时应保守判为未知')

        # 明显不是占位的具体时刻 -> False
        r2 = {'lectureStart': '2026-05-20 14:30:00'}
        parsers._mark_time_unknown(r2)
        self.assertIs(r2['timeUnknown'], False)

        # 无时间 -> True
        r3 = {}
        parsers._mark_time_unknown(r3)
        self.assertIs(r3['timeUnknown'], True)

    def test_77_有hasClock时以它为准(self):
        """_hasClock=True 但时刻落在 00:00（理论矛盾）时，仍以 hasClock 为准。"""
        r = {'lectureStart': '2026-05-20 00:00:00', '_hasClock': True}
        parsers._mark_time_unknown(r)
        self.assertIs(r['timeUnknown'], False, 'hasClock=True 应产出 False')
        self.assertNotIn('_hasClock', r, '_hasClock 未被 pop')

        r2 = {'lectureStart': '2026-05-20 08:00:00', '_hasClock': False}
        parsers._mark_time_unknown(r2)
        self.assertIs(r2['timeUnknown'], True, 'hasClock=False 应产出 True')


if __name__ == '__main__':
    unittest.main(verbosity=2)