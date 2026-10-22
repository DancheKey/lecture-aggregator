# -*- coding: utf-8 -*-
"""入库时间门的「按源水位」修复（2026-10-22）的回归测试。

背景
----
列表页侧的跳过判据早已用 `_effective_listdate_cutoff = min(全局水位, 本源基线)`
（2026-09-28 gap 审计），但入库前最后一道门（main() 的 `is_recent`）仍只用全局
`since`。两者不同源 → 源中断一段时间后恢复，中断期讲座的 publishTime 与
lectureStart 都早于全局水位 → 全部 `[SKIP-OLD]` 丢弃，并 `ledger_add(_, 'old')`
锁 180 天；而水位每轮前移，空洞只增不减。

修复要点（两条，缺一不可）：
  ① 水位按源算：min(全局水位, 本源基线) —— 补回本源中断期；
  ② 加绝对下限 GATE_FLOOR_DAYS —— 否则基线停在远古的源（哲社 2013-05-23）
     会一次性灌入十年存量，部分回退 2026-08-01「杜绝远古旧讲座当新事件」的修复。

本测试分三层：
  · EffectiveGateCutoffUnitTest —— 纯函数口径（含下限、时区、脏输入）
  · GateWiringStaticTest        —— 主循环确实用了按源水位，而非又改回 cutoff
  · LedgerOldRegressionTest     —— 真实台账/数据离线复核：曾被误判 old 的条目
                                   在新判据下不再被丢（台账被清空时自动跳过）

运行：python tests/test_gate_source_base.py
"""
import datetime
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_scraper():
    """显式从文件加载 scraper.py（scraper/ 无 __init__.py）。"""
    path = os.path.join(ROOT, 'scraper', 'scraper.py')
    spec = importlib.util.spec_from_file_location('sc_gate_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _load_scraper()
D = datetime.datetime


class EffectiveGateCutoffUnitTest(unittest.TestCase):
    """`_effective_gate_cutoff(cutoff_dt, src_base_date)` 的口径。"""

    CUTOFF = D(2026, 10, 22, 4, 11, 0)

    def _floor(self):
        return self.CUTOFF - datetime.timedelta(days=S.GATE_FLOOR_DAYS)

    def test_01_无基线退化为全局水位(self):
        self.assertEqual(S._effective_gate_cutoff(self.CUTOFF, ''), self.CUTOFF)
        self.assertEqual(S._effective_gate_cutoff(self.CUTOFF, None), self.CUTOFF)

    def test_02_基线在下限之后_取基线(self):
        """本源滞后但不超下限 → 水位放宽到基线，补回中断期。"""
        got = S._effective_gate_cutoff(self.CUTOFF, '2025-04-09')
        self.assertEqual(got, D(2025, 4, 9, 0, 0, 0),
                         '基线 2025-04-09 在 2 年下限内，水位应放宽到基线')

    def test_03_基线早于下限_取下限(self):
        """基线停在远古（哲社 2013-05-23）→ 只能放宽到下限，不得灌入十年存量。"""
        got = S._effective_gate_cutoff(self.CUTOFF, '2013-05-23')
        self.assertEqual(got, self._floor(),
                         '基线早于下限时必须钳到 floor，否则一次性灌入十年历史')

    def test_04_基线比水位新_保持全局水位(self):
        """基线未来（数学科学学院曾被未来讲座拉到 2026-10-31）→ 不得抬高水位。"""
        got = S._effective_gate_cutoff(self.CUTOFF, '2026-10-31')
        self.assertEqual(got, self.CUTOFF,
                         '基线比全局水位新时不得抬高水位（否则近期讲座被误丢）')

    def test_05_基线带时区可解析(self):
        got = S._effective_gate_cutoff(self.CUTOFF, '2025-04-09T00:00:00+08:00')
        self.assertEqual(got, D(2025, 4, 9, 0, 0, 0),
                         '带时区基线应去时区后参与比较，否则 naive/aware 相减抛 TypeError')

    def test_06_脏输入不崩(self):
        # 注意 '20250409'（无分隔符）不在脏输入之列：Python 3.11+ 的
        # fromisoformat 支持基本格式，能正确解析为 2025-04-09，属有效输入。
        for bad in ('not-a-date', '2025-13-45', '   ', '2025-02-30'):
            got = S._effective_gate_cutoff(self.CUTOFF, bad)
            self.assertEqual(got, self.CUTOFF, '非法基线（%r）应退化为全局水位' % bad)

    def test_07_返回值恒不早于下限(self):
        """任何输入下都不得早于 floor —— 这是「不灌远古存量」的唯一保证。"""
        floor = self._floor()
        for base in ('1900-01-01', '2013-05-23', '2024-10-23', '2025-01-01',
                     '', '2026-10-31', 'garbage'):
            got = S._effective_gate_cutoff(self.CUTOFF, base)
            self.assertGreaterEqual(got, floor, '基线 %r 让水位越过了下限' % base)


class GateWiringStaticTest(unittest.TestCase):
    """静态锁：主循环必须真的用按源水位，不能又被改回全局 cutoff。"""

    def setUp(self):
        with open(os.path.join(ROOT, 'scraper', 'scraper.py'), encoding='utf-8') as f:
            self.src = f.read()

    def test_08_主循环用按源水位比较(self):
        """`is_recent` 的比较基准必须是 `_eff`，不是 `cutoff`。

        这是本修复的核心一行。若被改回 `>= cutoff`，本源中断期讲座又会全丢，
        而且没有任何报错——只能靠用户发现「这个学院好久没新讲座了」。
        """
        self.assertIn('is_recent = (pub is not None and pub >= _eff)',
                      self.src,
                      'is_recent 的比较基准已改回全局 cutoff —— '
                      '本源中断期讲座会再次被 [SKIP-OLD] 丢弃并锁 180 天')
        self.assertNotIn('is_recent = (pub is not None and pub >= cutoff)',
                         self.src,
                         '仍存在用全局 cutoff 的 is_recent —— 修复被回退')

    def test_09_按源水位由预建字典提供(self):
        """`_eff` 必须来自 `_gate_eff_by_col`（按 college 预建），不是就地硬算。"""
        self.assertIn('_gate_eff_by_col[_n] = _effective_gate_cutoff(', self.src,
                      '按源水位未由 _effective_gate_cutoff 预建')
        self.assertIn("_eff = _gate_eff_by_col.get(r.get('college') or '', cutoff)",
                      self.src,
                      '_eff 的取用口径变了——键必须是 rec["college"]，'
                      '与 _build_source_latest_date / new_source_names 同口径')

    def test_10_开关可回退(self):
        """SCNU_GATE_SRCBASE=0 必须能退回改前行为（便于线上对照排查）。"""
        self.assertIn("env_flag('SCNU_GATE_SRCBASE', default='1')", self.src,
                      '缺少回退开关——线上若入库量异常将无法快速对照')

    def test_11_SKIP_OLD日志带水位(self):
        """[SKIP-OLD] 必须打出实际使用的水位，否则无法区分「全局」与「按源」。"""
        # 定位真正的 print 语句（文件里多处注释提到 [SKIP-OLD]，不能取第一个）
        key = "print(f'[SKIP-OLD]"
        self.assertIn(key, self.src, '[SKIP-OLD] 的 print 语句不见了')
        i = self.src.index(key)
        seg = self.src[i:i + 500]
        self.assertIn('gate=', seg,
                      '[SKIP-OLD] 未打实际水位 —— 排查时无法判断被哪条水位砍掉')


class LedgerOldRegressionTest(unittest.TestCase):
    """真实数据离线复核：曾被误判 old 的条目，在新判据下不再被丢。

    台账被清空/条目全部过期时自动跳过——它是「回归护栏」，不是「数据断言」。
    """

    def setUp(self):
        self.ledger_path = os.path.join(ROOT, 'data', 'scrape_ledger.json')
        self.lectures_path = os.path.join(ROOT, 'site', 'lectures.json')
        for p in (self.ledger_path, self.lectures_path):
            if not os.path.exists(p):
                self.skipTest('缺少 %s' % os.path.basename(p))

    def _base_by_college(self):
        with open(self.lectures_path, encoding='utf-8') as f:
            data = json.load(f)['data']
        out = {}
        for r in data:
            c = r.get('college') or ''
            t = (r.get('lectureStart') or '')[:10]
            if c and t and t > out.get(c, ''):
                out[c] = t
        return out

    def test_12_曾被误判的old条目不再被丢(self):
        with open(self.ledger_path, encoding='utf-8') as f:
            entries = json.load(f).get('entries') or {}
        olds = {k: v for k, v in entries.items() if v.get('v') == 'old'}
        if not olds:
            self.skipTest('台账已无 old 条目（可能已被清理）')

        # 用与线上同一套判定：cutoff 取「现在」，floor 由 GATE_FLOOR_DAYS 决定
        cutoff = D.now()
        base = self._base_by_college()
        # 域名 → college：台账键是 URL，用 data 里的 sourceUrl 前缀反查
        with open(self.lectures_path, encoding='utf-8') as f:
            url2col = {}
            for r in json.load(f)['data']:
                u = r.get('sourceUrl') or ''
                if u:
                    url2col[u.split('/a/')[0]] = r.get('college') or ''

        misfire, still_old = [], []
        for url, e in olds.items():
            d = e.get('d') or ''
            if not d:
                continue
            host_prefix = url.split('/a/')[0]
            col = url2col.get(host_prefix)
            if not col:
                continue
            eff = S._effective_gate_cutoff(cutoff, base.get(col, ''))
            eff_date = eff.date().isoformat()
            (misfire if d >= eff_date else still_old).append((url, d, col, eff_date))

        # 断言：不再存在「条目日期 >= 按源水位」的 old 条目（它们本该被保留）
        self.assertEqual(
            [], misfire,
            '以下 old 条目在新判据下本该被保留（条目日期 >= 按源水位），'
            '说明台账未清理或修复未生效：\n' +
            '\n'.join('  %s  d=%s  %s  gate=%s' % x for x in misfire))


if __name__ == '__main__':
    unittest.main(verbosity=2)
