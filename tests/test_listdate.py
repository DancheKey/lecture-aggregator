# -*- coding: utf-8 -*-
"""列表页条目日期过滤判据（_effective_listdate_cutoff）守卫。

背景（2026-09-28 gap 审计）：原实现只用全局水位（since=last_scrape），本源严重
滞后（哲社落后 4876 天）时把本源 2024~2026 新公告误挡在 fetch 之前 → 漏抓。
修复取「全局水位」与「本源基线」的更早者，判据更宽松。

注意：主模块位于 scraper/scraper.py（scraper/ 目录无 __init__.py，是命名空间包），
直接 `import scraper` 会命中该命名空间包而非主模块，故用 importlib 从文件显式加载
scraper/scraper.py（scraper/ 下的 parsers 等在被 load 时已注入 path）。
"""
import importlib.util
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_scraper():
    # 主模块在 scraper/scraper.py：从文件显式加载，绕开同名命名空间包歧义。
    path = os.path.join(_ROOT, 'scraper', 'scraper.py')
    spec = importlib.util.spec_from_file_location('scraper.scraper', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _load_scraper()


class EffectiveListdateCutoffTest(unittest.TestCase):
    def test_src_older_than_global_takes_src(self):
        # 本源基线远早于全局水位（哲社类滞后场景）→ 取本源基线，不误挡新公告
        self.assertEqual(
            P._effective_listdate_cutoff('2026-09-28', '2013-05-23'),
            '2013-05-23')

    def test_global_older_than_src_takes_global(self):
        # 本源基线较新 → 仍取更早的全局水位（不反向放宽到比全局还新）
        self.assertEqual(
            P._effective_listdate_cutoff('2024-01-01', '2026-08-01'),
            '2024-01-01')

    def test_no_src_baseline_falls_back_to_global(self):
        # 本源无数据（首次抓或 name 未匹配）→ 退化为全局水位（现状）
        self.assertEqual(
            P._effective_listdate_cutoff('2026-09-28', ''), '2026-09-28')
        self.assertEqual(
            P._effective_listdate_cutoff('2026-09-28', None), '2026-09-28')

    def test_no_global_cutoff_falls_back_to_empty(self):
        # 全量模式（无全局水位）→ 退化为空，不做条目级跳过（宁多抓不漏抓）
        self.assertEqual(P._effective_listdate_cutoff('', '2026-08-01'), '')
        self.assertEqual(P._effective_listdate_cutoff(None, '2026-08-01'), '')

    def test_equal_dates(self):
        self.assertEqual(
            P._effective_listdate_cutoff('2026-09-28', '2026-09-28'),
            '2026-09-28')


if __name__ == '__main__':
    unittest.main()
