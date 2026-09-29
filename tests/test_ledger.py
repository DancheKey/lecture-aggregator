# -*- coding: utf-8 -*-
"""被拒 URL 台账（_LEDGER / load_ledger / save_ledger / ledger_hit / ledger_add）守卫。

背景（2026-09-29 CI 审计）：ggy / zhx / ibrr 等源的新条目几乎全是回顾报道或回溯旧讲座，
判定后既不进库、也不推进本源基线；而条目判据取 min(全局水位, 本源基线) 被这些极旧基线
（2021-12 / 2013-05 / 2018-12）自我削弱 → 这些 URL 每轮被重复 fetch+parse，永不收敛。
实测单轮白抓 586 页、抓取阶段 18m45s、净新增 0 条。

台账是「按 URL」的第二本账：抓过且判定不入库的 URL，下一轮 fetch 前直接跳过。

注意：主模块位于 scraper/scraper.py（scraper/ 目录无 __init__.py，是命名空间包），
直接 `import scraper` 会命中命名空间包，故用 importlib 从文件显式加载。
"""
import datetime
import importlib.util
import json
import os
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_scraper():
    path = os.path.join(_ROOT, 'scraper', 'scraper.py')
    spec = importlib.util.spec_from_file_location('scraper.scraper', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _load_scraper()
TTL = S._LEDGER_TTL_DAYS


def _reset(entries=None):
    """把模块级台账恢复到已知状态（模块级可变状态，测试间必须重置）。"""
    S._LEDGER = dict(entries or {})
    S._LEDGER_STATS = {'skipped': 0, 'added': 0, 'expired': 0}


class LedgerBasicsTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_add_then_hit(self):
        S.ledger_add('http://ggy.scnu.edu.cn/a/20220415/5766.html', 'rejected', '2022-04-15')
        self.assertTrue(S.ledger_hit('http://ggy.scnu.edu.cn/a/20220415/5766.html'))
        self.assertFalse(S.ledger_hit('http://ggy.scnu.edu.cn/a/20990101/9999.html'))

    def test_hit_misses_on_empty_key(self):
        # None / '' 不得命中（避免把「无 URL」的调用当成已判过）
        self.assertFalse(S.ledger_hit(''))
        self.assertFalse(S.ledger_hit(None))

    def test_existing_entry_keeps_first_timestamp(self):
        # 已存在条目不刷新 t：TTL 从首次记录起算，保证会周期性重判
        old = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
        _reset({'k': {'v': 'rejected', 'd': '', 't': old}})
        S.ledger_add('k', 'rejected')
        self.assertEqual(S._LEDGER['k']['t'], old)
        self.assertEqual(S._LEDGER_STATS['added'], 0)

    def test_add_records_verdict_and_date(self):
        S.ledger_add('k', 'old', '2024-11-01')
        self.assertEqual(S._LEDGER['k']['v'], 'old')
        self.assertEqual(S._LEDGER['k']['d'], '2024-11-01')
        self.assertEqual(S._LEDGER_STATS['added'], 1)


class LedgerTtlTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_expired_entry_dropped(self):
        stale = (datetime.date.today() - datetime.timedelta(days=TTL + 1)).isoformat()
        fresh = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            with open(p, 'w', encoding='utf-8') as f:
                json.dump({'version': 1, 'entries': {
                    'stale': {'v': 'rejected', 'd': '', 't': stale},
                    'fresh': {'v': 'rejected', 'd': '', 't': fresh}}}, f)
            S.load_ledger(p)
        self.assertNotIn('stale', S._LEDGER)
        self.assertIn('fresh', S._LEDGER)
        self.assertEqual(S._LEDGER_STATS['expired'], 1)
        self.assertFalse(S.ledger_hit('stale'))
        self.assertTrue(S.ledger_hit('fresh'))

    def test_boundary_ttl_minus_one_kept(self):
        # 恰好 TTL-1 天的条目仍在有效期内（边界不提前失效）
        edge = (datetime.date.today() - datetime.timedelta(days=TTL - 1)).isoformat()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            with open(p, 'w', encoding='utf-8') as f:
                json.dump({'entries': {'edge': {'v': 'old', 'd': '', 't': edge}}}, f)
            S.load_ledger(p)
        self.assertIn('edge', S._LEDGER)

    def test_entry_without_timestamp_kept(self):
        # 缺 t 的异常条目保守保留，不静默丢弃（宁可多抓也不静默漏判据）
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            with open(p, 'w', encoding='utf-8') as f:
                json.dump({'entries': {'nots': {'v': 'rejected', 'd': ''}}}, f)
            S.load_ledger(p)
        self.assertIn('nots', S._LEDGER)


class LedgerIoTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_roundtrip_through_disk(self):
        S.ledger_add('b-key', 'old', '2024-01-01')
        S.ledger_add('a-key', 'rejected', '2023-02-02')
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            S.save_ledger(p)
            _reset()
            S.load_ledger(p)
        self.assertEqual(set(S._LEDGER), {'a-key', 'b-key'})
        self.assertEqual(S._LEDGER['a-key']['v'], 'rejected')

    def test_saved_entries_are_sorted(self):
        # 条目按 key 排序写盘 → 相同集合产出相同字节，避免 CI 无谓提交
        S.ledger_add('z-key', 'old')
        S.ledger_add('a-key', 'old')
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            S.save_ledger(p)
            raw = json.load(open(p, encoding='utf-8'))
        self.assertEqual(list(raw['entries']), ['a-key', 'z-key'])
        self.assertEqual(raw['ttlDays'], TTL)
        self.assertIn('updatedAt', raw)

    def test_missing_file_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            S.load_ledger(os.path.join(d, 'nope.json'))
        self.assertEqual(S._LEDGER, {})

    def test_corrupt_file_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            with open(p, 'w', encoding='utf-8') as f:
                f.write('{ this is not json')
            S.load_ledger(p)   # 不得抛异常：台账只影响耗时，不影响抓取正确性
        self.assertEqual(S._LEDGER, {})

    def test_unexpected_shape_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(['not', 'a', 'dict'], f)
            S.load_ledger(p)
        self.assertEqual(S._LEDGER, {})


class LedgerSwitchTest(unittest.TestCase):
    def setUp(self):
        _reset()
        self._old = os.environ.get('SCNU_LEDGER_SKIP')

    def tearDown(self):
        if self._old is None:
            os.environ.pop('SCNU_LEDGER_SKIP', None)
        else:
            os.environ['SCNU_LEDGER_SKIP'] = self._old

    def test_disabled_switch_blocks_hit_and_add(self):
        os.environ['SCNU_LEDGER_SKIP'] = '0'
        S.ledger_add('k', 'rejected')
        self.assertEqual(S._LEDGER, {})           # 不写入
        _reset({'k': {'v': 'rejected', 'd': '', 't': '2026-01-01'}})
        self.assertFalse(S.ledger_hit('k'))       # 不命中（等价于台账失效）

    def test_enabled_by_default(self):
        os.environ.pop('SCNU_LEDGER_SKIP', None)
        S.ledger_add('k', 'rejected')
        self.assertTrue(S.ledger_hit('k'))


class CutoffStillUnchangedTest(unittest.TestCase):
    """台账不得改变既有日期判据语义（两者互补，台账不参与日期计算）。"""

    def test_effective_cutoff_semantics_intact(self):
        self.assertEqual(S._effective_listdate_cutoff('2026-09-28', '2013-05-23'), '2013-05-23')
        self.assertEqual(S._effective_listdate_cutoff('2026-09-28', '2026-09-30'), '2026-09-28')
        self.assertEqual(S._effective_listdate_cutoff('2026-09-28', ''), '2026-09-28')
        self.assertEqual(S._effective_listdate_cutoff('', ''), '')


if __name__ == '__main__':
    unittest.main()
