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

# 测试用合法台账键（必须是 _canon_url_key 的输出形态：URL 或 vsb::组::ID）
U = 'http://ggy.scnu.edu.cn/a/20211221/5701.html'
U2 = 'http://ggy.scnu.edu.cn/a/20230516/5991.html'


def _reset(entries=None):
    """把模块级台账恢复到已知状态（模块级可变状态，测试间必须重置）。"""
    S._LEDGER = dict(entries or {})
    S._LEDGER_STATS = {'skipped': 0, 'added': 0, 'expired': 0, 'dirty': 0}


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
        _reset({U: {'v': 'rejected', 'd': '', 't': old}})
        S.ledger_add(U, 'rejected')
        self.assertEqual(S._LEDGER[U]['t'], old)
        self.assertEqual(S._LEDGER_STATS['added'], 0)

    def test_add_records_verdict_and_date(self):
        S.ledger_add(U, 'old', '2024-11-01')
        self.assertEqual(S._LEDGER[U]['v'], 'old')
        self.assertEqual(S._LEDGER[U]['d'], '2024-11-01')
        self.assertEqual(S._LEDGER_STATS['added'], 1)


class LedgerKeyShapeTest(unittest.TestCase):
    """键形态闸门：脏键写入无效、读入即剔除。

    背景（2026-09-29 实测）：用日志构造首轮台账时，正则误把「URL[SKIP-RETRO]」
    整段当成 URL 写进台账 3 条。这类键运行时永远命中不了（运行时键来自
    _canon_url_key 的干净 URL），条目静默失效且日志里看不出来。
    """

    def setUp(self):
        _reset()

    def test_add_rejects_dirty_keys(self):
        for bad in ['http://ggy.scnu.edu.cn/a/20211221/5701.html[SKIP-RETRO]',
                    'http://a.com/x.html publishTime=2024',
                    'http://a.com/x.html=1',
                    '不是URL']:
            S.ledger_add(bad, 'rejected')
        self.assertEqual(S._LEDGER, {})
        self.assertEqual(S._LEDGER_STATS['added'], 0)

    def test_add_accepts_plain_and_vsb_keys(self):
        S.ledger_add('http://ggy.scnu.edu.cn/a/20211221/5701.html', 'rejected')
        S.ledger_add('vsb::em.scnu.edu.cn::12345', 'old')
        self.assertEqual(len(S._LEDGER), 2)
        self.assertTrue(S.ledger_hit('vsb::em.scnu.edu.cn::12345'))

    def test_load_drops_dirty_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            with open(p, 'w', encoding='utf-8') as f:
                json.dump({'entries': {
                    'http://good.com/a.html': {'v': 'rejected', 'd': '', 't': '2026-09-29'},
                    'http://good.com/a.html[SKIP-RETRO]': {'v': 'rejected', 'd': '', 't': '2026-09-29'},
                }}, f, ensure_ascii=False)
            S.load_ledger(p)
        self.assertEqual(list(S._LEDGER), ['http://good.com/a.html'])
        self.assertEqual(S._LEDGER_STATS['dirty'], 1)


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
                    U: {'v': 'rejected', 'd': '', 't': stale},
                    U2: {'v': 'rejected', 'd': '', 't': fresh}}}, f)
            S.load_ledger(p)
        self.assertNotIn(U, S._LEDGER)
        self.assertIn(U2, S._LEDGER)
        self.assertEqual(S._LEDGER_STATS['expired'], 1)
        self.assertFalse(S.ledger_hit(U))
        self.assertTrue(S.ledger_hit(U2))

    def test_boundary_ttl_minus_one_kept(self):
        # 恰好 TTL-1 天的条目仍在有效期内（边界不提前失效）
        edge = (datetime.date.today() - datetime.timedelta(days=TTL - 1)).isoformat()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            with open(p, 'w', encoding='utf-8') as f:
                json.dump({'entries': {U: {'v': 'old', 'd': '', 't': edge}}}, f)
            S.load_ledger(p)
        self.assertIn(U, S._LEDGER)

    def test_entry_without_timestamp_kept(self):
        # 缺 t 的异常条目保守保留，不静默丢弃（宁可多抓也不静默漏判据）
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            with open(p, 'w', encoding='utf-8') as f:
                json.dump({'entries': {U: {'v': 'rejected', 'd': ''}}}, f)
            S.load_ledger(p)
        self.assertIn(U, S._LEDGER)


class LedgerIoTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_roundtrip_through_disk(self):
        S.ledger_add(U2, 'old', '2024-01-01')
        S.ledger_add(U, 'rejected', '2023-02-02')
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            S.save_ledger(p)
            _reset()
            S.load_ledger(p)
        self.assertEqual(set(S._LEDGER), {U, U2})
        self.assertEqual(S._LEDGER[U]['v'], 'rejected')

    def test_saved_entries_are_sorted(self):
        # 条目按 key 排序写盘 → 相同集合产出相同字节，避免 CI 无谓提交
        S.ledger_add(U2, 'old')
        S.ledger_add(U, 'old')
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            S.save_ledger(p)
            raw = json.load(open(p, encoding='utf-8'))
        self.assertEqual(list(raw['entries']), sorted([U, U2]))
        self.assertEqual(raw['ttlDays'], TTL)
        self.assertIn('updatedAt', raw)

    def test_missing_file_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            S.load_ledger(os.path.join(d, 'nope.json'))
        self.assertEqual(S._LEDGER, {})

    # ---------- 写侧 TTL 过滤（2026-10-02，B9）----------
    #
    # 此前 save_ledger(merge=True) 会把盘上**已过期**条目重新并回 _LEDGER 并原样写盘，
    # 于是 load 报的「过期作废 N 条」在盘上一个都没少 → 台账永不瘦身。
    # 下面三例锁住「写盘前按 TTL 过滤」，且与读侧共用 _ledger_is_fresh 判据。

    def _stale_entry(self):
        d = datetime.date.today() - datetime.timedelta(days=TTL + 1)
        return {'v': 'rejected', 'd': '2020-01-01', 't': d.isoformat()}

    def test_expired_not_written_back_to_disk(self):
        """过期条目不得被 merge 回写盘——这是 B9 的核心回归。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            stale_key = U
            with open(p, 'w', encoding='utf-8') as f:
                json.dump({'version': 1, 'entries': {stale_key: self._stale_entry()}}, f)
            _reset()
            S.load_ledger(p)                       # 内存里已剔除（读侧本就正确）
            self.assertEqual(S._LEDGER, {}, '前提：读侧已剔除过期条目')
            S.ledger_add(U2, 'old')                # 触发一次写
            S.save_ledger(p)
            raw = json.load(open(p, encoding='utf-8'))
        self.assertNotIn(stale_key, raw['entries'],
                         '过期条目被写回盘上台账永不瘦身')
        self.assertIn(U2, raw['entries'], '有效条目应正常写入')

    def test_write_side_keeps_fresh_entries(self):
        """写侧过滤不得误伤有效期内的条目。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            S.ledger_add(U, 'rejected')
            S.save_ledger(p)
            raw = json.load(open(p, encoding='utf-8'))
        self.assertIn(U, raw['entries'])

    def test_write_side_drops_dirty_keys(self):
        """写侧过滤同时清掉形态异常键（与读侧口径一致）。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            S._LEDGER[U] = {'v': 'old', 't': datetime.date.today().isoformat()}
            S._LEDGER['URL[SKIP-RETRO]'] = {'v': 'old',
                                           't': datetime.date.today().isoformat()}
            S.save_ledger(p)
            raw = json.load(open(p, encoding='utf-8'))
        self.assertIn(U, raw['entries'])
        self.assertNotIn('URL[SKIP-RETRO]', raw['entries'],
                         '形态异常的键运行时永不命中，不该落盘')

    def test_entry_without_timestamp_kept_on_write(self):
        """缺 t 的异常条目保守保留（读侧既定语义，写侧必须一致）。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'scrape_ledger.json')
            S._LEDGER[U] = {'v': 'old', 'd': '2020-01-01'}      # 无 t
            S.save_ledger(p)
            raw = json.load(open(p, encoding='utf-8'))
        self.assertIn(U, raw['entries'], '缺 t 的条目应保守保留')

    def test_read_and_write_share_same_predicate(self):
        """静态锁：读侧与写侧必须共用 _ledger_is_fresh，杜绝判据分叉。"""
        src = open(os.path.join(_ROOT, 'scraper', 'scraper.py'), encoding='utf-8').read()
        self.assertIn('def _ledger_is_fresh(', src, '应抽出共享判据函数')
        # 读侧：load_ledger 到下一个顶层 def 之间，不得再有内联的 TTL 计算
        start = src.index('def load_ledger(')
        end = src.index('\ndef ', start + 1)
        load_body = src[start:end]
        self.assertIn('_ledger_is_fresh', load_body,
                      'load_ledger 未使用共享判据——读侧与写侧会分叉')
        self.assertNotIn('timedelta(days=_LEDGER_TTL_DAYS)', load_body,
                         'load_ledger 里仍有内联的 TTL 计算，应改用 _ledger_is_fresh')
        # 写侧：save_ledger 同理
        s2 = src.index('def save_ledger(')
        e2 = src.index('\ndef ', s2 + 1)
        save_body = src[s2:e2]
        self.assertIn('_ledger_is_fresh', save_body,
                      'save_ledger 未使用共享判据——写侧与读侧会分叉')

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
        S.ledger_add(U, 'rejected')
        self.assertEqual(S._LEDGER, {})           # 不写入
        _reset({U: {'v': 'rejected', 'd': '', 't': '2026-01-01'}})
        self.assertFalse(S.ledger_hit(U))         # 不命中（等价于台账失效）

    def test_enabled_by_default(self):
        os.environ.pop('SCNU_LEDGER_SKIP', None)
        S.ledger_add(U, 'rejected')
        self.assertTrue(S.ledger_hit(U))


class CutoffStillUnchangedTest(unittest.TestCase):
    """台账不得改变既有日期判据语义（两者互补，台账不参与日期计算）。"""

    def test_effective_cutoff_semantics_intact(self):
        self.assertEqual(S._effective_listdate_cutoff('2026-09-28', '2013-05-23'), '2013-05-23')
        self.assertEqual(S._effective_listdate_cutoff('2026-09-28', '2026-09-30'), '2026-09-28')
        self.assertEqual(S._effective_listdate_cutoff('2026-09-28', ''), '2026-09-28')
        self.assertEqual(S._effective_listdate_cutoff('', ''), '')


if __name__ == '__main__':
    unittest.main()
