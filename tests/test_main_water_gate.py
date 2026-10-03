# -*- coding: utf-8 -*-
"""scraper.main() 的水位闸门**行为级**测试（2026-10-02，P1-3）。

背景
----
`failed_sources` 非空时**绝不推进** `last_scrape`（MEMORY.md 列的铁律），是整个
增量管线最关键的正确性不变量：水位一旦越过失败时段，该时段内发布的讲座在后续
增量里**永远补不回来**（源站列表页已翻过去，不会再出现在增量窗口里）。

此前的覆盖只有**静态断言**——tests/test_list_page_failure.py::test_08 用
`assertIn('if failed_sources:', src)` 检查源码里有没有那几行字。它能挡住"整段被删"，
却挡不住任何**行为层面**的改动：
  · 把 `if failed_sources:` 改成 `if failed_sources and False`（断言照样过，水位却推进了）
  · 失败分支里漏写 `payload['last_scrape'] = since`（断言抓不到，因为字面量还在别处）
  · 成功/失败两个分支互换

本测试改为**真实调用 `main()`**：用临时目录当ROOT、桩掉 `fetch`/`parse_detail`/
`_process_source`，让 main() 真跑到写 `last_scrape.json` 那一刻，然后**读文件核对
内容**。行为变了文件内容就变，与源码长相无关。

离线保证：不联网、不读真实 data/、不改仓库任何文件。

运行：python tests/test_main_water_gate.py
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_scraper():
    """显式从文件加载 scraper.py（scraper/ 无 __init__.py，直接 import 会命中命名空间包）。"""
    path = os.path.join(ROOT, 'scraper', 'scraper.py')
    spec = importlib.util.spec_from_file_location('sc_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MainWaterGateTest(unittest.TestCase):
    """main() 的水位推进/冻结行为。"""

    def setUp(self):
        self.s = _load_scraper()
        self.tmp = tempfile.mkdtemp(prefix='main_water_gate_')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.data_dir = os.path.join(self.tmp, 'data')
        os.makedirs(self.data_dir, exist_ok=True)

        # ROOT 重定向到临时目录：main() 内的 data/ 与 last_scrape.json 都落在这里
        self.s.ROOT = self.tmp
        self.s._LEDGER = {}
        self.s._LEDGER_STATS = {'skipped': 0, 'added': 0, 'expired': 0, 'dirty': 0}
        self.s._LISTDATE_STATS = {'skipped': 0}

    # ---------- 工具 ----------
    def _write_last(self, obj):
        p = os.path.join(self.data_dir, 'last_scrape.json')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
        return p

    def _read_last(self):
        p = os.path.join(self.data_dir, 'last_scrape.json')
        if not os.path.exists(p):
            return None
        with open(p, encoding='utf-8') as f:
            return json.load(f)

    def _stub_sources(self, results):
        """桩掉 _process_source：results = [(name, err), ...]，err=None 表示成功。

        同时桩掉 fetch/parse_detail/collect_links/_build_item_date_map/_next_page_url，
        确保 main() 完全离线走到写 last_scrape.json。
        """
        S = self.s
        S.fetch = lambda *a, **k: '<html></html>'
        S.parse_detail = lambda *a, **k: None
        S.collect_links = lambda *a, **k: []
        S._build_item_date_map = lambda *a, **k: {}
        S._next_page_url = lambda *a, **k: None
        S.load_ledger = lambda *a, **k: {}
        S.report_ledger = lambda *a, **k: None

        def fake_process(src, year, existing_urls, is_incremental, *a, **k):
            name = src['name']
            for (n, err) in results:
                if n == name:
                    local = {}
                    if err is None:
                        local = {'%s/a.html' % name: {
                            'sourceUrl': '%s/a.html' % name,
                            'title': 'T', 'lectureStart': '2026-01-01 10:00:00',
                            'college': name, 'campus': '',
                        }}
                    return local, err
            return {}, '未预置: %s' % name

        S._process_source = fake_process
        return S

    def _run_main(self, argv):
        """调用 main() 并吞掉它的 stdout/stderr。

        main() 会打大量进度日志（每个源几十行），不吞的话本套件的输出会被
        淹没、真正的断言结果看不见。这里只在**测试内部**重定向，不影响被测逻辑。
        """
        import contextlib
        old_argv = sys.argv
        sys.argv = argv
        buf_out, buf_err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                self.s.main()
        finally:
            sys.argv = old_argv
        self._last_out = buf_out.getvalue()
        self._last_err = buf_err.getvalue()

    def _two_sources(self):
        return [{'name': 'A学院', 'campus': '石牌',
                 'base': 'http://a.scnu.edu.cn',
                 'list_urls': ['http://a.scnu.edu.cn/list/']},
                {'name': 'B学院', 'campus': '石牌',
                 'base': 'http://b.scnu.edu.cn',
                 'list_urls': ['http://b.scnu.edu.cn/list/']}]

    def _patch_sources_cfg(self, sources):
        """让 main() 读到预置的 sources 配置。"""
        import yaml as _y
        p = os.path.join(self.tmp, 'scraper', 'sources.yaml')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            _y.safe_dump({'sources': sources}, f, allow_unicode=True)

    def _patch_scrape_ledger_cfg(self):
        """main() 会 load_ledger()/report_ledger()，确保路径指向临时目录。"""
        self.s._ledger_file = lambda: os.path.join(self.data_dir, 'scrape_ledger.json')

    # ---------- ① 全部成功 → 水位推进 ----------
    def test_01_全部成功时推进水位(self):
        self._patch_scrape_ledger_cfg()
        self._patch_sources_cfg(self._two_sources())
        self._stub_sources([('A学院', None), ('B学院', None)])
        self._run_main(['scraper.py'])
        last = self._read_last()
        self.assertIsNotNone(last, '成功后应写 last_scrape.json')
        self.assertIn('last_scrape', last, '成功时必须推进水位')
        self.assertNotIn('failed_sources', last, '成功时不应有 failed_sources')

    # ---------- ② 有失败源 → 水位冻结（核心铁律） ----------
    def test_02_有失败源时水位冻结(self):
        self._patch_scrape_ledger_cfg()
        self._patch_sources_cfg(self._two_sources())
        self._stub_sources([('A学院', None), ('B学院', 'B学院: 源站超时')])
        self._run_main(['scraper.py'])
        last = self._read_last()
        self.assertIsNotNone(last, '失败时也要写 last_scrape.json（记录失败源）')
        self.assertIn('failed_sources', last, '失败时必须记录 failed_sources')
        self.assertTrue(last['failed_sources'], 'failed_sources 不应为空')
        # 关键：没有旧水位时不得凭空造一个 last_scrape
        self.assertNotIn('last_scrape', last,
                         '首次运行即失败时不得写 last_scrape——'
                         '否则水位凭空前进，该时段讲座永久漏抓')

    def test_03_有失败源时保留旧水位(self):
        """已有旧水位 + 本轮失败 → 旧水位必须被沿用（而非被 now 覆盖）。"""
        self._patch_scrape_ledger_cfg()
        self._patch_sources_cfg(self._two_sources())
        old = '2026-09-01T08:00:00+08:00'
        self._write_last({'last_scrape': old, 'mode': 'incremental'})
        self._stub_sources([('A学院', None), ('B学院', 'B学院: 源站 500')])
        self._run_main(['scraper.py'])
        last = self._read_last()
        self.assertIsNotNone(last, '失败时仍应写 last_scrape.json')
        self.assertEqual(last.get('last_scrape'), old,
                         '失败时旧水位被改写——水位越过失败时段，'
                         '该时段讲座后续增量永远补不回')
        self.assertIn('failed_sources', last, '失败时必须记录失败源')

    # ---------- ④ 局部重抓模式：完全不碰水位 ----------
    def test_04_局部重抓不写水位(self):
        self._patch_scrape_ledger_cfg()
        self._patch_sources_cfg(self._two_sources())
        old = '2026-09-01T08:00:00+08:00'
        self._write_last({'last_scrape': old, 'mode': 'incremental'})
        self._stub_sources([('A学院', None)])
        out_path = os.path.join(self.tmp, 'out.json')
        self._run_main(['scraper.py', '--source', 'A学院', '--out', out_path])
        self.assertTrue(os.path.exists(out_path), '--out 应产出结果文件')
        last = self._read_last()
        self.assertEqual(last.get('last_scrape'), old,
                         '局部重抓不得改动全局水位（否则下次增量被局部任务的基准污染）')

    # ---------- ⑤ --since 显式传入时，失败也必须沿用它 ----------
    def test_05_显式since时失败沿用(self):
        self._patch_scrape_ledger_cfg()
        self._patch_sources_cfg(self._two_sources())
        self._stub_sources([('A学院', 'A学院: 500'), ('B学院', 'B学院: 超时')])
        self._run_main(['scraper.py', '--since', '2026-08-01T00:00:00+08:00'])
        last = self._read_last()
        self.assertEqual(last.get('last_scrape'), '2026-08-01T00:00:00+08:00',
                         '显式 --since 且全部源失败时，水位必须沿用传入值')


class MainWaterGateSourceShapeTest(unittest.TestCase):
    """静态锁：闸门本身的结构不得被改坏。"""

    def setUp(self):
        with open(os.path.join(ROOT, 'scraper', 'scraper.py'), encoding='utf-8') as f:
            self.src = f.read()

    def test_06_闸门在写入lectures之后(self):
        """闸门必须位于 _atomic_write_json(lectures.json) 之后。

        若被移到之前，失败时数据不会落盘——「已抓到的部分结果」丢失，
        下次从旧水位重来，属于另一种数据损失。
        """
        i_data = self.src.index("_atomic_write_json(os.path.join(data_dir, 'lectures.json')")
        i_gate = self.src.index('if failed_sources:')
        self.assertGreater(i_gate, i_data,
                           'failed_sources 闸门被移到了写数据之前——'
                           '失败时本轮已抓到的结果将不会落盘')

    def test_07_失败分支含告警输出(self):
        """失败必须打WARN（否则 CI 日志里看不到哪几个源失败）。"""
        tail = self.src[self.src.index('if failed_sources:'):]
        self.assertIn("[WARN]", tail[:1200],
                      '失败分支缺少 WARN 输出——CI 日志将无法定位失败源')
        self.assertIn('水位未推进', tail[:1200],
                      '失败告警未点明「水位未推进」，读日志的人会误判为正常增量')

    def test_08_局部重抓分支不受闸门影响(self):
        """`if not args.source:` 必须包住闸门——局部重抓不该写全局水位。"""
        i_not_src = self.src.index('if not args.source:')
        i_gate = self.src.index('if failed_sources:')
        self.assertLess(i_not_src, i_gate,
                        '闸门已不在 `if not args.source` 保护内——'
                        '局部重抓会污染全局水位')


if __name__ == '__main__':
    unittest.main(verbosity=2)