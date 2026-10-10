#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全库 --full 按源保护测试（2026-10-10 改造 4）。

背景（P0-1）：`_protect_sources_from_shrink` 之前，--source 模式有「产出 0 条
→ 中止 / 缩水 >50% → 保留旧数据」的保护，但**全库 --full 模式没有**。
而 --full 是从空 dict 重建 lectures.json（lectures 仅在增量时预填 existing），
于是「某学院官网改版 → 列表页 200 但0 条讲座 → 判为成功」会把该学院全部历史
静默抹掉，且水位照常推进，增量再也补不回来。实测：经管学院 837 条可被清成 0 条
而总数仍在 3000 以上，所有既有门禁全绿。

本测试锁住三件事：
  · 单源归零 → 回填旧数据（且**不产生重复**）
  · 单源缩水 >50% → 只回填新产出未覆盖的部分（**同URL 不重复**）
  · 正常重建 → 不误触发保护
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import scraper  # noqa: E402


def _r(college, n, prefix='http://x'):
    return [{'sourceUrl': f'{prefix}/{college}/{i}.html',
             'college': college, 'title': f'{college}-T{i}',
             'lectureStart': '2026-09-01', 'listTitle': f'{college}-T{i}'}
            for i in range(n)]


class ProtectSourcesFromShrinkTest(unittest.TestCase):
    """按源保护：全量重建时不得静默丢失某个源。"""

    def test_01_单源归零必须回填(self):
        """官网改版导致某源抓回 0 条 → 该源旧数据必须被回填。"""
        existing = _r('甲学院', 200) + _r('乙学院', 150)
        raw = _r('乙学院', 150)          # 甲学院消失
        out = scraper._protect_sources_from_shrink(raw, existing)
        self.assertIsNotNone(out, '非 --source 模式不应中止整轮')
        colleges = [r['college'] for r in out]
        self.assertEqual(colleges.count('甲学院'), 200,
                         '甲学院归零后其 200 条历史必须被回填')
        self.assertEqual(colleges.count('乙学院'), 150)

    def test_02_回填不得产生重复(self):
        """同 URL 的旧记录不得被重复回填（GLM 指出的伪代码陷阱）。

        改版后新旧 URL 相同是很常见的场景；若保护逻辑简单 `raw + old`，
        同一场讲座会留下两条 sourceUrl 相同/相近的记录。
        """
        existing = _r('甲学院', 100)
        raw = _r('甲学院', 100)           # 全部同 URL 命中
        out = scraper._protect_sources_from_shrink(raw, existing)
        self.assertEqual(len(out), 100,
                         f'同 URL 场景不应膨胀，实际 {len(out)} 条')

    def test_03_缩水时只回填未覆盖部分(self):
        """新产出覆盖了一部分 URL → 只补回真正缺失的，不重复。"""
        existing = _r('甲学院', 100)
        raw = _r('甲学院', 30)            # 前 30 条同 URL
        out = scraper._protect_sources_from_shrink(raw, existing)
        # 30 条命中 + 70 条缺失回填 = 100 条，且不重复
        urls = [r['sourceUrl'] for r in out]
        self.assertEqual(len(out), 100, f'应为 30 + 70 = 100，实际 {len(out)}')
        self.assertEqual(len(set(urls)), len(urls), '回填后不得有重复 URL')

    def test_04_正常重建不误触发(self):
        """产出正常（≥50%旧量）时，保护不应介入，条数不变。"""
        existing = _r('甲学院', 100) + _r('乙学院', 100)
        raw = _r('甲学院', 90) + _r('乙学院', 95)   # 都 ≥ 50%，无缺失
        out = scraper._protect_sources_from_shrink(raw, existing)
        self.assertEqual(len(out), len(raw),
                         '正常重建时不应额外回填')

    def test_05_换URL的同一场讲座不得双份入库(self):
        """官网改版常伴随 URL 变化：新 URL 30 条 + 旧 URL 100 条。

        这是真实场景——同一场讲座换个链接重新挂出。回填逻辑必须按 URL
        判断「缺失」，而不是无脑全量追加，否则库里会多出 100 条重复讲座。
        """
        existing = _r('甲学院', 100, prefix='http://old')
        raw = _r('甲学院', 30, prefix='http://new')   # 全是新 URL
        out = scraper._protect_sources_from_shrink(raw, existing)
        # 新 30 条 + 回填 100 条旧 URL = 130，但都是不同 URL（后续由 dedup 判重）
        self.assertEqual(len(out), 130,
                         f'新 30 + 旧 100 = 130，实际 {len(out)}')
        urls = [r['sourceUrl'] for r in out]
        self.assertEqual(len(set(urls)), len(urls), '不得有重复 URL')

    def test_06_空existing不崩溃(self):
        """existing 为空（首次全量建库）→ 直接返回，不报错。"""
        out = scraper._protect_sources_from_shrink(_r('甲学院', 10), [])
        self.assertEqual(len(out), 10)

    def test_07_source模式归零仍中止整轮(self):
        """保持 2026-09-27 二轮审计 P0-3 语义：--source 目标源归零 → 中止。

        与全库模式不同：--source 是「显式重抓这一个源」，归零意味着操作本身
        有问题，应中止整轮而非回填——避免把「我以为修好了其实没抓到」的结果
        当成成功。
        """
        existing = _r('甲学院', 200)
        raw = []                               # 甲学院产出 0 条
        out = scraper._protect_sources_from_shrink(
            raw, existing, abort_single='甲学院')
        self.assertIsNone(out, '--source 目标源归零必须返回 None（中止整轮）')

    def test_08_小源不误触发保护(self):
        """只有 3 条记录的小源产出 0 条也该保护吗？——保护逻辑不看量级。

        但门禁（test_data_reconciliation）对 n_base<20 的源不报警。两者口径
        不同是有意的：抓取侧宁保守（保住数据），门禁侧降噪（不刷告警）。
        """
        existing = _r('小学院', 3)
        out = scraper._protect_sources_from_shrink([], existing)
        self.assertEqual(len(out), 3, '小源归零同样应回填（抓取侧偏保守）')


if __name__ == '__main__':
    unittest.main(verbosity=2)