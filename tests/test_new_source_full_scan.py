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

    def test_07_失效源检测已移除防止回归(self):
        """2026-10-10 生产修复：SRC-SILENT 检测已移除。

        首版判据「本轮零产出 + 库中≥20 条」在正常增量下误报率极高：
        源站一周没发新讲座完全正常，却被标为「疑似改版」刷屏（实测
        16/33 个正常源被误报）。真正的改版信号应是「列表页取回成功但
        零条目」，需要 _process_source 返回额外元数据才能精确检测。
        本测试防止该特性被无意重新引入。
        只检查 `silent_sources` 变量——注释里保留 `SRC-SILENT` 字样
        作为「此处曾有此功能」的文档是允许的。
        """
        self.assertNotIn('silent_sources', SRC,
                         'SRC-SILENT 检测不应存在（正常源零产出≠改版，误报率过高）')


class BootstrapTrackingTest(unittest.TestCase):
    """Bootstrap 追踪：防止「一直零记录的源每轮被反复全量扫描」。

    2026-10-10 生产修复：首版「新源」判据只看「库里是否零记录」，无法区分
    「刚加入需要补历史」和「一直存在但从未产出」——后者每轮触发 force_full，
    反复全量扫描 + 大量 SKIP-NEWS/SKIP-RETRO = 爬取项暴增 + 耗时暴增
    （CI 实测：426 项/10+ 分钟，20 源被反复全量扫描，仅补回 3 条）。
    """

    def test_15_bootstrap追踪逻辑存在(self):
        """新源检测必须排除已 bootstrap 过的源。"""
        self.assertIn('source_bootstrap.json', SRC,
                      '缺少 bootstrap 追踪文件（防反复全量扫描）')
        self.assertIn('nm not in bootstrapped', SRC,
                      '新源检测未排除已 bootstrap 过的源')

    def test_16_bootstrap在落库后写入(self):
        """bootstrap 标记必须在 lectures.json 落库之后写——
        若中途 abort（--source 归零中止、总量缩水保护），不应标记，
        下轮还会正常触发一次全量扫描。"""
        idx_lectures = SRC.find("_atomic_write_json(os.path.join(data_dir, 'lectures.json')")
        idx_bootstrap = SRC.find("bs[nm] = now_iso")
        self.assertGreater(idx_lectures, 0, '未找到 lectures.json 落库点')
        self.assertGreater(idx_bootstrap, 0, '未找到 bootstrap 写入点')
        self.assertGreater(idx_bootstrap, idx_lectures,
                           'bootstrap 标记必须在 lectures.json 落库之后写')

    def test_17_bootstrap路径与新源检测一致(self):
        """加载与写入必须用同一个 bootstrap_path 变量，防止路径漂移。"""
        import re
        paths = re.findall(r'bootstrap_path\s*=\s*os\.path\.join\([^)]+\)', SRC)
        self.assertEqual(len(paths), 1,
                         f'bootstrap_path 应只定义一次，实际 {len(paths)} 次')


class TimeGateExemptionTest(unittest.TestCase):
    """入库前最后一道闸门：新源必须豁免增量时间门。

    2026-10-10 补漏（实测发现的真缺陷）：改造 1 只放开了**列表页侧**三道闸门
    （水位线条目跳过 / 被拒台账 / PAGESTOP），但 main() 里还有最后一道
    「增量时间门」按 since 逐条过滤记录。若不一并豁免，新源抓回的历史讲座
    在这里会被 [SKIP-OLD] 丢弃**并记入台账锁 180 天**——实测 3 条新源记录
    只保留 1 条，「加了新源却补不回历史」的原症状依旧存在，force_full 等于白做。
    """

    def test_12_时间门须豁免新源(self):
        """从源码结构上锁住：时间门循环内必须先判 college 是否属新源。"""
        import ast
        tree = ast.parse(SRC)
        fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'main':
                fn = node
                break
        self.assertIsNotNone(fn, '未找到 main()')
        src_of_main = ast.get_source_segment(SRC, fn) or ''

        idx_gate = src_of_main.find('dropped_keys, kept_keys = set(), set()')
        self.assertGreater(idx_gate, 0, '未找到增量时间门循环')
        loop_body = src_of_main[idx_gate:idx_gate + 900]
        self.assertIn('new_source_names', loop_body,
                      '增量时间门循环内未豁免新源——新源历史讲座仍会被丢弃'
                      '（force_full 只放开了列表页侧闸门，入库前这道没放开）')
        self.assertLess(loop_body.find('new_source_names'),
                        loop_body.find('_parse_iso'),
                        '豁免判断须在日期解析之前，否则仍会先被日期逻辑拦下')

    def test_13_豁免分支须continue且计入kept(self):
        """豁免的记录必须进 kept（而不是既不丢也不留）。"""
        idx = SRC.find("if r.get('college') in new_source_names:")
        self.assertGreater(idx, 0, '未找到新源豁免分支')
        block = SRC[idx:idx + 320]
        self.assertIn('kept.append(r)', block, '豁免分支未把记录加入 kept')
        self.assertIn('continue', block, '豁免分支应 continue，跳过日期判定')

    def test_14_新源补回量须可见(self):
        """改造效果要看得见：打印补回条数，避免「加了源却不知有没有补回」。"""
        self.assertIn('[NEW-SOURCE] 已补回', SRC,
                      '缺少新源补回数量的日志（效果不可见）')


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