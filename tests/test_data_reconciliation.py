#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据对账门禁测试（2026-10-10 改造 5）。

背景：此前 scripts/test_invariants.py 只有固定常数 `< 3000` 的塌方兜底。
这个门槛的致命盲区是——**只在总数塌到 3000 以下时才报警**：
  · 某个源被改版清空，总数掉 5% → 全绿，静默丢数无人知晓；
  · 这个下限是拍脑袋的常数，与「库里实际有什么」无关。

改造后新增两道对账门禁：
  ① 按源对账（主力）：增量模式下每个 college 只增不减。任何源归零或缩水 >30%
     都是故障，且能精确定位到是哪个源——这是按源抓取架构下的天然优势。
  ② 总量对账（辅助）：与上一个 CI 产物比，只拦超容差的塌方。

⚠ 与「本轮 >= 上轮」的直白写法不同：本项目维护者会**主动**做拆分/去重清洗
  （git 历史里同一天就出现过 3814 → 3812 的合法下降），简单比较会大量误报。
  故基线逐源取 max() 维护（有意清理不会被基线「记住」而反复报警）。

本测试锁住三件事：
  · 单源归零必须报警（哪怕总数只掉 5%）
  · 单源小幅缩水必须报警
  · 单源正常增长 / 有意清理后回落——不得误报
"""
import io
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVARIANTS = os.path.join(ROOT, 'scripts', 'test_invariants.py')

sys.path.insert(0, os.path.join(ROOT, 'scripts'))


def _run_invariants():
    """跑真实门禁脚本，返回 (exit_code, 输出文本)。

    必须跑真脚本而非直接调函数：门禁的价值在于「它是否真的让 CI 红」，
    直接调函数可能漏掉 sys.exit / print / 文件读写等副作用。

    ⚠ 解码用 utf-8 + replace：Windows 控制台默认 GBK，直接按本地编码解会抛
      UnicodeDecodeError。门禁自身用 sys.stdout 写中文，在 GBK 控制台下会被
      替换成 '?'，故断言**只依赖 exit code**，不依赖中文文案。
    """
    p = subprocess.run([sys.executable, INVARIANTS], capture_output=True, cwd=ROOT,
                       env=_clean_env())
    out = ((p.stdout or b'').decode('utf-8', 'replace')
           + (p.stderr or b'').decode('utf-8', 'replace'))
    return p.returncode, out


def _clean_env():
    """PYTHONIOENCODING=utf-8：让子进程输出 UTF-8，便于断言中文文案。

    不设的话 GBK 控制台下中文会被替换成 '?'，断言'应点名是哪个源'就假失败。
    """
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


class _DataSandbox:
    """在临时目录里造一份最小可用的仓库布局，替换真数据。

    不能直接改data/lectures.json：那是 8MB 主库，且本测试要能反复
    构造「某源被清空」这类场景，直接动真库风险太高。

    ⚠ 沙箱必须造够 3000 条（PAD_RECORDS）才可用于对账断言——因为真实门禁里
      还留着「< 3000 视为塌方」的兜底闸门，沙箱数据太小时它会先于对账逻辑
      触发，导致「本该通过」的用例因无关原因变红。填充源用独立的
      __pad__ 学院名，不干扰被测源的计数。
    """

    PAD_COLLEGE = '__pad__'

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix='recon_')
        os.makedirs(os.path.join(self.tmp, 'data'), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, 'scripts'), exist_ok=True)
        # 复制真实脚本（它靠相对定位 ROOT 找 data/）
        for rel in ('scripts/test_invariants.py',):
            shutil.copy(os.path.join(ROOT, rel), os.path.join(self.tmp, rel))
        # 沙箱还必须备齐两处被 import 的模块，否则门禁脚本会在 import 阶段就炸：
        #   ① scripts/excluded_urls.py —— scraper.py 会 import
        #   ② scraper/ 下的一组模块    —— incremental_merge 单元测试会 import
        #
        # ⚠ 一律 shutil.copy，**不要用 os.symlink**（2026-10-10）：本机 Windows
        #   无符号链接特权时 os.symlink 直接抛 OSError [WinError 1314]
        #   （实测如此，不是「静默创建坏链」）。此前靠 `except OSError → copy`
        #   兜底虽也能跑通，但把正确性押在「异常一定抛」上——换环境行为不可预期。
        #   且软链会让本机与 CI(Linux) 走不同代码路径，两端行为不一致。
        #   全部依赖合计约 200KB，复制成本可忽略。
        for fn in ('excluded_urls.py',):
            src = os.path.join(ROOT, 'scripts', fn)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(self.tmp, 'scripts', fn))
        real_scraper = os.path.join(ROOT, 'scraper')
        os.makedirs(os.path.join(self.tmp, 'scraper'), exist_ok=True)
        for fn in ('scraper.py', 'parsers.py', 'timeparse.py',
                   'field_vocab.py', 'env_flags.py', 'hybrid.py',
                   'cited_judge.py', 'llm_cache.py'):
            src = os.path.join(real_scraper, fn)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(self.tmp, 'scraper', fn))

    def write(self, recs, baseline=None, pad=True):
        if pad:
            # 补足到 3000 条以上，避开塌方兜底闸门对沙箱的干扰。
            # 填充源的 URL 必须与被测源不同域，否则复合键唯一性检查会误报。
            pad_n = max(0, 3100 - len(recs))
            recs = list(recs) + _rec(self.PAD_COLLEGE, pad_n,
                                     url_prefix='http://pad')
        with io.open(os.path.join(self.tmp, 'data', 'lectures.json'), 'w',
                     encoding='utf-8') as f:
            json.dump({'updatedAt': '2026-10-10T00:00:00+08:00', 'data': recs},
                      f, ensure_ascii=False)
        if baseline is not None:
            with io.open(os.path.join(self.tmp, 'data', 'reconcile_baseline.json'), 'w',
                         encoding='utf-8') as f:
                json.dump(baseline, f, ensure_ascii=False, indent=2)

    def run(self):
        p = subprocess.run([sys.executable, os.path.join(self.tmp, 'scripts',
                                                          'test_invariants.py')],
                           capture_output=True, cwd=self.tmp, env=_clean_env())
        out = ((p.stdout or b'').decode('utf-8', 'replace')
               + (p.stderr or b'').decode('utf-8', 'replace'))
        return p.returncode, out

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def _rec(college, n, url_prefix='http://x'):
    return [{'sourceUrl': f'{url_prefix}/{college}/{i}.html',
             'college': college, 'title': f'T{i}',
             'lectureStart': '2026-09-01' + str(i % 28).zfill(2),
             'listTitle': f'T{i}'} for i in range(n)]


def _base(counts):
    """构造基线文档。

    ⚠ **故意不写 total**：门禁的 check_total_reconciliation 读 baseline.total
      做总量对账，而沙箱的填充量随场景变化（3100 或 3100+被测源条数）。
      写死 total 会与真实填充量不符 → 总量门禁在「本该通过」的用例里误报，
      掩盖真正要测的按源逻辑。total 缺失时该检查整体跳过，只测按源对账；
      总量对账由 test_06 用真实主库覆盖。
    """
    return {'version': 1, 'sourceCounts': dict(counts)}


class SourceReconciliationTest(unittest.TestCase):
    """按源对账：增量模式下「某个源不该减少」。"""

    def setUp(self):
        self.sb = _DataSandbox()
        self.addCleanup(self.sb.cleanup)

    def test_01_单源归零必须报警(self):
        """P0-1 场景：源站改版导致该源抓回 0 条，总数仍远超 3000 门槛。"""
        base = _base({'甲学院': 200, '乙学院': 150})
        self.sb.write(_rec('甲学院', 200) + _rec('乙学院', 150), base)
        rc, out = self.sb.run()
        self.assertEqual(rc, 0, f'基线与数据一致，应通过：{out}')

        # 甲学院被清空：只剩乙学院 + 填充，总数仍 > 3000
        self.sb.write(_rec('乙学院', 150), base)
        rc, out = self.sb.run()
        self.assertNotEqual(rc, 0,
                            '单源归零必须让门禁变红（总数仍在 3000 以上但该源数据全丢）')
        self.assertIn('甲学院', out, '报警信息应点名是哪个源出了问题')

    def test_02_单源大幅缩水必须报警(self):
        base = _base({'甲学院': 200})
        self.sb.write(_rec('甲学院', 100), base)   # 200 → 100，跌 50%
        rc, out = self.sb.run()
        self.assertNotEqual(rc, 0, '单源缩水超 30% 必须报警（疑似解析器回归）')

    def test_03_正常增长不得误报(self):
        """增量模式下每个源只增不减——这是日常主路径，绝不能误报。"""
        base = _base({'甲学院': 200})
        self.sb.write(_rec('甲学院', 260), base)
        rc, out = self.sb.run()
        self.assertEqual(rc, 0, f'正常增长被误报：{out}')

    def test_04_首次运行只落基线不报警(self):
        """无基线文件时应落基线并通过，否则首次上线即全红。"""
        self.sb.write(_rec('甲学院', 200))
        rc, out = self.sb.run()
        self.assertEqual(rc, 0, f'首次运行应只落基线：{out}')
        bp = os.path.join(self.sb.tmp, 'data', 'reconcile_baseline.json')
        self.assertTrue(os.path.exists(bp), '首次运行应写出基线文件')
        doc = json.load(open(bp, encoding='utf-8'))
        self.assertEqual(doc['sourceCounts'].get('甲学院'), 200)

    def test_05_小样本源不设防(self):
        """1~2 条记录的源波动大（如仅 1 场讲座的学院），不应触发门禁。"""
        base = _base({'小学院': 2})
        self.sb.write(_rec('小学院', 2), base)
        rc, out = self.sb.run()
        self.assertEqual(rc, 0, f'小样本源归零不应报警（噪音源）：{out}')

    def test_06_总量门禁拦塌方(self):
        """基线 total 显著高于当前总数 → 总量对账报警。

        这里显式写 total（其余用例靠「不写 total」跳过总量检查），
        且差额拉到 10%，远超 3% 容差。
        """
        base = _base({'甲学院': 200})
        base['total'] = 4000          # 假装上次 4000 条
        self.sb.write(_rec('甲学院', 200), base)   # 本次实际 ~3300 条
        rc, out = self.sb.run()
        self.assertNotEqual(rc, 0, '总量跌超 3% 应报警')
        self.assertIn('总量', out, '报警信息应标明是总量问题')


class RealDataSanityTest(unittest.TestCase):
    """用真实主库跑一次，确保新增门禁不误报真实数据。"""

    def test_07_真实数据通过门禁(self):
        rc, out = _run_invariants()
        self.assertEqual(rc, 0,
                         f'真实 data/lectures.json 未通过对账门禁：\n{out[-2000:]}')


def _load_scraper_mod():
    """从文件显式加载 scraper/scraper.py（scraper/ 无 __init__.py，直接 import
    会命中同名命名空间包；同 tests/test_listdate.py 范式）。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'scraper', 'scraper.py')
    spec = importlib.util.spec_from_file_location('scraper.scraper', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class IncrementalMergeIndexTest(unittest.TestCase):
    """增量融合跨源索引必须只含「实际在库」记录（2026-10-14 评审 P1-3）。

    场景（无主讲人的大会多轮通知）：existing 里的第一轮通知被本轮第二轮
    通知替换（del base_map[old_key]），但 existing 列表仍持有旧轮次。若跨源
    索引从 existing 构建，随后到达的第三条同会议记录会与这条「死记录」
    （college, date）命中 → merge 进已不在输出中的幽灵记录 → 第三条无声丢失。

    为什么用无主讲人记录构造：cross_source_dedup 第一轮只给有有效主讲人的
    记录分组（scraper.py:648-651），无主讲人记录不会在 new 内部先去重，
    可稳定走到跨源索引判定这一步。
    """

    def test_08_索引不含被替换删除的死记录(self):
        sc = _load_scraper_mod()
        a1 = {'sourceUrl': 'http://x/math/round1.html', 'college': '数学科学学院',
              'campus': '石牌', 'speaker': '',
              'title': '控制理论学术研讨会：第一轮会议通知',
              'lectureStart': '2026-10-01', 'publishTime': '2026-09-01'}
        a2 = dict(a1, sourceUrl='http://x/math/round2.html',
                  title='控制理论学术研讨会：第二轮会议通知',
                  publishTime='2026-09-15')
        b3 = dict(a1, sourceUrl='http://x/math/round3.html',
                  title='控制理论学术研讨会：会议通知',
                  publishTime='2026-09-20')
        # 前置自检：构造数据必须真的触发「替换 + 死记录命中」两条机制
        self.assertTrue(sc._has_round_marker(a2['title']), 'A2 应含轮次标记')
        self.assertFalse(sc._has_round_marker(b3['title']), 'B3 不应含轮次标记')
        self.assertGreaterEqual(
            sc._topic_similarity(b3['title'], a1['title']), 0.6,
            'B3 与 A1 相似度须 >=0.6（无主讲人路径命中判据），否则场景不成立')

        out = sc.incremental_merge([a1], [a2, b3])
        urls = {r.get('sourceUrl') for r in out}
        self.assertNotIn(a1['sourceUrl'], urls, '旧轮次应已被第二轮通知替换删除')
        self.assertIn(a2['sourceUrl'], urls, '第二轮通知应保留')
        self.assertIn(
            b3['sourceUrl'], urls,
            '第三条记录被 merge 进已删除的幽灵记录而静默丢失（P1-3 回归：'
            '跨源索引含死记录）')
        self.assertEqual(len(out), 2, f'输出应恰为 第二轮+第三条，实得 {urls}')


if __name__ == '__main__':
    unittest.main(verbosity=2)