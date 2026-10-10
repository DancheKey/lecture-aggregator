#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新源强制全量测试（2026-10-10 改造 1）。

场景（使用者实际踩到的）：某学院官网改版/迁移 → 在 sources.yaml 里加了新地址。
此时该源在库里**一条历史都没有**，而官网上挂着过去一两年的讲座——它们的日期
早于全局水位线。若沿用普通增量逻辑，三道闸门叠加会把它们全部挡掉：
① 水位线判据（_should_skip_by_item_date）在 fetch 之前就跳过，连请求都不发；
② 被拒台账把改版期判 old 的页面锁 180 天；
③ PAGESTOP 翻两页就停。
结果就是「源加进去了，但一条历史数据都补不回来」——且全程无任何告警。

本测试锁住 force_full 的三个闸门放开，以及失效源告警的存在性。
"""
import ast
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import scraper  # noqa: E402

P = scraper
SRC = open(os.path.join(ROOT, 'scraper', 'scraper.py'), encoding='utf-8').read()


def _r(college, n, prefix='http://x', start='2026-09-01'):
    return [{'sourceUrl': f'{prefix}/{college}/{i}.html',
             'college': college, 'title': f'{college}-T{i}',
             'lectureStart': start, 'listTitle': f'{college}-T{i}'}
            for i in range(n)]


class ForceFullGateTest(unittest.TestCase):
    """force_full 必须同时放开三道闸门，缺一不可。"""

    def test_01_函数签名含force_full且默认False(self):
        """默认必须 False：老源行为完全不变，只有识别出的新源才开。"""
        import inspect
        sig = inspect.signature(P._process_source)
        self.assertIn('force_full', sig.parameters, '_process_source 缺少 force_full 参数')
        self.assertIs(sig.parameters['force_full'].default, False,
                      'force_full 默认必须为 False，否则所有源都会被全量重扫')

    def test_02_水位线判据在force_full下失效(self):
        """闸门①：条目级跳过判据必须让位于 force_full。

        这是最关键的一道——它作用在 fetch **之前**，不放开就等于一页都不抓。
        """
        self.assertRegex(
            SRC, r'skip_itemdate\s*=\s*\(bool\(cutoff_date_str\)[^)]*and not force_full\)',
            'skip_itemdate 未与 force_full 联动（新源仍会被水位线跳过）')
        self.assertRegex(
            SRC, r"eff_cutoff\s*=\s*\(''\s*if force_full\s+else",
            'eff_cutoff 未在 force_full 时置空（水位线仍会误挡新源历史）')

    def test_03_台账闸门在force_full下失效(self):
        """闸门②：改版期被判 old 的页面会被锁 180 天，必须一并放开。

        否则会出现「明明全量扫了却一条都没进来」——因为那些页面早就进了台账。
        """
        self.assertRegex(
            SRC, r'ledger_hit\(_canon_url_key\(href_norm\)\)\s*:\s*$|'
                 r'if is_incremental and not force_full and ledger_hit',
            '台账闸门未与 force_full 联动')

    def test_04_PAGESTOP在force_full下失效(self):
        """闸门③：翻页停止会因本源无数据而立刻触发，必须放开。"""
        self.assertRegex(
            SRC, r'if \(is_incremental and src_latest_date and item_date_map\s*\n?\s*and not force_full\)',
            'PAGESTOP 未与 force_full 联动（新源只会翻到遇见存量为止）')

    def test_05_新源识别逻辑存在(self):
        """main() 必须识别「库中无该 college 记录」的源并置 force_full。"""
        self.assertIn('new_source_names', SRC,
                      'main() 缺少新源识别（new_source_names）')
        # 识别判据：src_latest_date 取不到 + college 名在 existing 中查无此人
        self.assertRegex(SRC, r'if nm and not src_latest_date\.get\(nm\)',
                         '新源判据未使用「本源无已入库最晚日期」')
        self.assertRegex(
            SRC, r"if not any\(\(r\.get\('college'\)\s*or ''\)\s*==\s*nm for r in existing\)",
            '新源判据缺少 college 名精确复核（src_latest_date 可能因缺日期字段而误判）')

    def test_06_新源必须真的传到_process_source(self):
        """识别结果必须传进抓取函数，否则前面所有放开都白做。"""
        self.assertRegex(SRC, r"in new_source_names\)\s*:", 
                         'force_full 未随 submit 传入 _process_source')


class SilentSourceTest(unittest.TestCase):
    """失效源告警：老源零产出要有信号。"""

    def test_07_失效源检测逻辑存在(self):
        """「老源换 URL」场景：新源判据覆盖不到，必须靠零产出告警补上。

        这是评审指出的第2 条——新源判据只覆盖「库里无记录」，而
        「老源换了地址」时库里**有**旧记录，判据认定它是老源走常规增量，
        旧 URL 的基线日期会挡住新 URL 的页面。此时唯一的信号就是
        「本源本轮零产出但库里有大量存量」。
        """
        self.assertIn('silent_sources', SRC, '缺少失效源（零产出）告警')
        self.assertRegex(SRC, r'if \(not err and not local and is_incremental',
                         '失效源检测条件缺失（应为：无错误、无产出、增量模式）')
        self.assertRegex(SRC, r'if _lib >= 20:',
                         '失效源告警未设存量阈值（小样本源会刷噪音）')

    def test_08_告警必须写入last_scrape供CI读取(self):
        """告警不能只打日志：CI 读不到就等于没有。"""
        self.assertRegex(SRC, r"payload\['silent_sources'\] = silent_sources",
                         'silent_sources 未写入 last_scrape.json，CI 无法读取')

    def test_09_告警不得阻断数据提交(self):
        """零产出未必是故障（有些源当天确实没更新），不能因此拦住数据上线。"""
        self.assertNotIn('silent_sources:\n                    sys.exit', SRC)
        # 确认告警处没有 return/exit
        idx = SRC.find('if silent_sources:')
        self.assertGreater(idx, 0, '缺少 silent_sources 告警输出')
        block = SRC[idx:idx + 400]
        self.assertNotIn('sys.exit', block, '失效源告警不应导致进程退出')
        self.assertNotIn('return\n', block[:200], '失效源告警不应中断主流程')


class NoRegressionTest(unittest.TestCase):
    """确认没有把既有行为改坏。"""

    def test_10_默认参数下行为不变(self):
        """不传 force_full 时，_effective_listdate_cutoff 结果与旧版一致。"""
        self.assertEqual(P._effective_listdate_cutoff('2026-10-01', '2026-09-01'),
                         '2026-09-01', 'min() 语义应保持不变')
        self.assertEqual(P._effective_listdate_cutoff('2026-10-01', ''),
                         '2026-10-01', '本源无基线时仍退化为全局水位')

    def test_11_改造4按源保护仍在调用链上(self):
        """确认改造 1 没把改造 4 的保护挤掉。"""
        self.assertIn('_protect_sources_from_shrink(raw, existing', SRC,
                      '按源保护未在全库 --full 路径生效')


if __name__ == '__main__':
    unittest.main(verbosity=2)