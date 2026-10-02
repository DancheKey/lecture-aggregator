#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
解析器行为快照测试 —— 渐进重构第 2 步（2026-09-29）。

## 为什么需要它

`test_parser_golden.py` 是**抽查**：每页只断言 4~6 项（条数 / speaker 列表 / topic 子串 /
bio 泄漏 / 尾部垃圾），而解析管线产出 20+ 字段。`location` / `lectureStart` / `abstract` /
`speakerAffiliation` / `images` / OCR 标记等**全都没锁**。后果：拆 `_parse_detail_impl`
（2230 行）时某个未断言字段悄悄坏掉，现有 171 项门禁照样全绿。

本模块改为**全字段快照**：把每个 fixture 的解析结果按记录序列化，比对基线 JSON。
重构期间 = 零行为变更，任何字段的意外变化都会红。

## 三条设计纪律（Trae 复核意见，已采纳）

1. **只锁纯规则基线，不锁双轨。**
   `_USE_LLM_TEXT` 是 parsers 的**模块级常量**（导入时求值），故桩必须在 `import parsers`
   **之前**生效：本模块在导入 parsers 前把 `SCNU_LLM_TEXT=0` / `SCNU_JLM_RICH=0` 写进 os.environ。
   锁的正是「模型 A/B 全不可用时」的行为 —— 与项目铁律「纯规则必须始终兜底」同构。
   融合路径（A/B 抽取与裁决）由 `test_hybrid.py` 在单元级覆盖，此处不重复。

2. **OCR/VLM 确定性陷阱。**
   - VLM：复用 `test_parser_golden` 已有的录制回放（`tests/fixtures/vlm_cache.json`）。
   - RapidOCR：**引擎版本差异是快照测试最常见的假红来源**，故把
     `ocrExtracted` / `imageParseMethod` / `vlmExtracted` / `hasPosterImage`
     从比对字段中**剔除**（它们由「是否走了图像通道」决定，与字段值正确性无关）。

3. **基线含已知缺陷是特性，不是缺陷。**
   基线由**当前代码**产出，其中 psy1305 等已知缺陷原样冻结；期望行为由既有的
   `KnownDefectTest`（xfail 台账）继续背书。未来修复会使某个 xfail 意外通过并报红提醒。

## 归一化规则（避免无意义的假红）

- 剥抓取时间戳（`crawlTime` / `fetchedAt` 等一切时间戳类字段）。
- 列表类字段排序（`images` 顺序依赖收集顺序，非语义）。
- 逐条记录按内容排序（多场拆分的顺序由解析细节决定，非语义关键 —— 与 golden 的
  `assertCountEqual` 口径一致）。
- 浮点/None/空串统一。

## 用法

    python -m pytest tests/test_parser_snapshot.py -q      # 校验（门禁用这个）
    python tests/test_parser_snapshot.py --bless          # 有意变更后刷新基线
    python tests/test_parser_snapshot.py --bless --only ctld4391   # 只刷某一例

⚠️ `--bless` 只应在**确认变更是预期的**（例如修了一个 bug）时使用；重构过程中的意外失败
应先查明原因，绝不能用 --bless 抹平。
"""
import argparse
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
sys.path.insert(0, os.path.join(ROOT, 'tests'))

FIXDIR = os.path.join(ROOT, 'tests', 'fixtures', 'html')
VLM_CACHE_FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'vlm_cache.json')
BASELINE = os.path.join(ROOT, 'tests', 'fixtures', 'parser_snapshot.json')

# ── 纪律 1：必须在 import parsers 之前关掉 LLM 文本链路 ──────────────
# parsers.py 的 `_USE_LLM_TEXT` / `_USE_LLM_RICH` 是模块级常量（导入即求值），
# 晚于 import 打桩无效 → 只能用环境变量在导入前关。
os.environ['SCNU_LLM_TEXT'] = '0'      # 模型 A（文本抽取）关 → 纯规则
os.environ['SCNU_LLM_RICH'] = '0'      # 摘要/简介富化关

import parsers as P                    # noqa: E402  必须在上文之后

# ⚠ 顺序独立性：pytest 按文件名字母序收集，同进程内若**别的测试先导入了 parsers**
# （实测 test_parser_golden.py 在前时会带着 LLM 开启导入 parsers），上面的环境变量
# 就来不及生效 —— `_USE_LLM_TEXT` 早已被求值为 True。
# 故除了环境变量，还必须在**每次运行解析前**强制回写模块常量，否则整套快照会带上
# LLM 痕迹（守卫用例会红）。
_LLM_CONSTANTS = ('_USE_LLM_TEXT', '_USE_LLM_RICH')


def _force_pure_rules():
    """把 parsers 的 LLM 模块级常量强制回退到纯规则。

    必须在每个用例解析前调用，而非只在导入时设一次 —— 这是本模块能与其他测试
    同进程共存、且与加载顺序无关的前提。
    """
    for name in _LLM_CONSTANTS:
        if hasattr(P, name):
            setattr(P, name, False)


# 模型 B（引证裁决）：**不能**用 SCNU_JUDGE_MODE 关 —— 实测 llm_provider.py:714
# 只认 legacy/old/classic，其余值（含 'off'）都落默认分支仍返回 CitedJudgeProvider。
# 故直接桩掉工厂函数返回 None（等价于「无 key」的真实降级路径）。
import llm_provider as _LP             # noqa: E402
_LP.get_judge_provider = lambda: None
_LP.get_text_provider = lambda: None
# parsers 若已 `from llm_provider import get_judge_provider` 则需同步覆盖其本地引用
if hasattr(P, 'get_judge_provider'):
    P.get_judge_provider = lambda: None
if hasattr(P, 'get_text_provider'):
    P.get_text_provider = lambda: None

# VLM 密封化：与 test_parser_golden 同一套手法 —— provider 指向不可达地址，
# 命中录制缓存即返回（零真实 API 调用）；未命中则快速失败而不是静默跳过。
P._load_vlm_configs = lambda: [{'model': 'snapshot-replay', 'api_key': 'unused',
                                'base_url': 'http://snapshot-test.invalid/vlm'}]
P._vlm_cache_path = lambda: VLM_CACHE_FIXTURE
P._vlm_cache_set = lambda key, val: None
# ⚠ 2026-10-02：缓存读写已收敛到 scraper/llm_cache.py，parsers 与 llm_provider
# **共享同一个函数对象**（原先是两份独立实现，可分别打桩）。只桩 P 一侧时，
# llm_provider 仍会写真实 data/.vlm_cache.json —— 于是「读」命中录制 fixture、
# 「写」落到真实缓存，两套状态被本测试搅在一起（实测症状：xz65 的 speakerBio
# 分隔符由空格变逗号，因缓存被前序用例的写入污染而走了不同分支）。
# 故两侧都要桩。这也解释了 test_llm_cache.py 为何一律指向临时目录。
import llm_cache as _LC          # noqa: E402
# 2026-09-27：原在此处（导入时）改写 _LC.cache_path/cache_set 且从不还原——
# 污染同进程其他测试模块（test_llm_cache 并发写全落空桩）。改由
# ParserSnapshotTest 的 setUp/tearDown 打桩/还原。

# ── 快照覆盖的用例（与 golden 的 CASES 同源，URL 即键）─────────────────
# 复用 golden 的 CASES 而非复制一份，避免两处漂移。
import test_parser_golden as G          # noqa: E402

# ── 字段策略 ────────────────────────────────────────────────────────
# 1) 剔除非确定性：抓取时间戳
_DROP_FIELDS = {
    'crawlTime', 'fetchTime', 'fetchedAt', 'crawledAt', 'scrapedAt',
    'parseTime', 'updatedAt',
}
# 2) 剔除图像通道标记：OCR 引擎版本差异是快照假红首源（纪律 2）
_DROP_IMAGE_FLAGS = {
    'ocrExtracted', 'imageParseMethod', 'vlmExtracted', 'hasPosterImage',
    'imageParseMethodApplied',
}
# 3) 剔除 LLM 链路标记（纪律 1 下本应全空；剔除以防残留噪声影响比对）
_DROP_LLM_FLAGS = {
    'llmTextEnhanced', 'llmVerdict', 'llmAdopted', 'llmRejected',
    'llmSelfExtract', 'vlmCacheHit',
}
DROP = _DROP_FIELDS | _DROP_IMAGE_FLAGS | _DROP_LLM_FLAGS

# 无序列表字段（顺序非语义）
_UNORDERED = {'images'}


def _norm_value(field, val):
    """单值归一化。"""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        items = [_norm_value(field, v) for v in val]
        if field in _UNORDERED:
            return sorted(items, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
        return items
    if isinstance(val, dict):
        return {k: _norm_value(k, v) for k, v in sorted(val.items())}
    if isinstance(val, float):
        # 避免不同平台/版本的浮点尾数差异造成假红
        return round(val, 6)
    return val


def _norm_record(rec):
    """单条记录归一化：剔 DROP 字段、逐字段归一。"""
    out = {}
    for k in sorted(rec.keys()):
        if k in DROP:
            continue
        out[k] = _norm_value(k, rec[k])
    return out


def _norm_snapshot(recs):
    """整页归一化：逐条归一 + 按内容排序（拆分顺序非语义）。"""
    normed = [_norm_record(r) for r in recs]
    return sorted(normed, key=lambda r: json.dumps(r, ensure_ascii=False, sort_keys=True))


def _load_html(fixture):
    path = os.path.join(FIXDIR, fixture)
    with open(path, 'rb') as f:
        raw = f.read()
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('gb18030', errors='replace')


def _parse(case):
    _force_pure_rules()          # 每次解析前强制纯规则（顺序独立，见 _force_pure_rules 注释）
    html = _load_html(case['fixture'])
    recs = P.parse_detail(html, case['url'], college='', campus='', default_year=None)
    return _norm_snapshot(recs if isinstance(recs, list) else [recs])


def _bless(all_snap):
    payload = {
        '_note': '解析器行为基线 —— 由 tests/test_parser_snapshot.py --bless 生成。'
                 '锁纯规则基线（LLM A/B 关闭）。含已知缺陷，由 KnownDefectTest xfail 背书。'
                 '重构期间不得刷新；仅在确认变更预期时刷新。',
        'cases': all_snap,
    }
    with open(BASELINE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f'基线已刷新：{BASELINE}（{len(all_snap)} 个用例）')


def _load_baseline():
    with open(BASELINE, encoding='utf-8') as f:
        return json.load(f)['cases']


class ParserSnapshotTest(unittest.TestCase):
    def setUp(self):
        self._lc_orig = (_LC.cache_path, _LC.cache_set)
        _LC.cache_path = lambda: VLM_CACHE_FIXTURE
        _LC.cache_set = lambda key, val: None      # 写桩：不让本测试写真实缓存

    def tearDown(self):
        _LC.cache_path, _LC.cache_set = self._lc_orig

    """全字段行为比对。基线由 --bless 生成，键为 URL。"""

    @classmethod
    def setUpClass(cls):
        # baseline[url] = 该用例的**归一化记录列表**（不是整个 case 对象）
        cls.baseline = {c['url']: c['records'] for c in _load_baseline()}
        cls.only = getattr(cls, '_only_filter', None)

    def _cases(self):
        for case in G.CASES:
            fx = os.path.join(FIXDIR, case['fixture'])
            if not os.path.exists(fx):
                continue  # 唯一 skip 例外：ctld4409 原页 404 无本地副本
            if self.only and self.only not in case['fixture']:
                continue
            yield case

    def test_00_guard_no_llm_leakage(self):
        """守卫：确认「强制回到纯规则」的机制本身可用且已生效。

        背景：本模块与 test_parser_golden.py 同进程共存，而 golden 不关 LLM。
        pytest 按文件名字母序收集，golden(g) 在 snapshot(s) 之前 → parsers 可能
        已被带着 LLM 开启导入过一次，此时仅靠导入前的环境变量打桩不够。
        故解析前必调 _force_pure_rules() 回写模块常量（见其注释）。

        本用例**主动制造污染**再验证能纠回，从而证明该机制有效 —— 这是它的价值：
        若哪天 _force_pure_rules 被误删，本用例会红。
        """
        import llm_provider as LP
        # 故意打开 LLM，模拟「被别的测试先导入」的污染态
        P._USE_LLM_TEXT = True
        P._USE_LLM_RICH = True
        # 机制应能把它们纠回纯规则
        _force_pure_rules()
        self.assertFalse(P._USE_LLM_TEXT, '_force_pure_rules 未把 _USE_LLM_TEXT 纠回 False')
        self.assertFalse(P._USE_LLM_RICH, '_force_pure_rules 未把 _USE_LLM_RICH 纠回 False')
        # provider 工厂必须恒为 None（桩在模块级，任何顺序下都成立）
        self.assertIsNone(LP.get_judge_provider(), 'judge provider 应为 None（纯规则基线）')
        self.assertIsNone(LP.get_text_provider(), 'text provider 应为 None（纯规则基线）')

    def test_snapshot_all(self):
        for case in self._cases():
            with self.subTest(url=case['url'], fixture=case['fixture']):
                got = _parse(case)
                want = self.baseline.get(case['url'])
                self.assertIsNotNone(want, f'基线缺该用例：{case["url"]}（新增 fixture 需 --bless）')
                self.assertEqual(
                    got, want,
                    f'{case["fixture"]} 行为变化。\n'
                    f'  若为预期的 bug 修复 → 确认无误后运行 --bless 刷新基线；\n'
                    f'  若在重构过程中 → 这正是本测试要抓的「未断言字段被改坏」，'
                    f'  禁止 --bless 抹平。\n'
                    f'  实际={_short(got, want)}')


def _short(got, want):
    """差异摘要：只列出值不同的字段路径，避免刷屏。"""
    gk = {k for g in got for k in g}
    wk = {k for w in want for k in w}
    only_g, only_w = sorted(gk - wk), sorted(wk - gk)
    parts = []
    if only_g:
        parts.append(f'实际多出字段 {only_g}')
    if only_w:
        parts.append(f'实际缺少字段 {only_w}')
    if not parts:
        # 逐条逐字段找第一处不同
        for i, (g, w) in enumerate(zip(got, want)):
            for k in sorted(set(g) & set(w)):
                if g[k] != w[k]:
                    parts.append(f'第{i+1}条 {k}: 实际={_clip(g[k])} 基线={_clip(w[k])}')
                    break
            if parts:
                break
    return '；'.join(parts) if parts else '（结构相同，逐字段比较见上）'


def _clip(v, n=90):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + '…'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bless', action='store_true', help='刷新基线（仅限确认变更预期时）')
    ap.add_argument('--only', help='只处理 fixture 名含该子串的用例')
    args = ap.parse_args()

    cases = [c for c in G.CASES
             if os.path.exists(os.path.join(FIXDIR, c['fixture']))
             and (not args.only or args.only in c['fixture'])]

    if args.bless:
        all_snap = []
        for c in cases:
            all_snap.append({'url': c['url'], 'fixture': c['fixture'],
                             'records': _parse(c)})
        _bless(all_snap)
        return 0

    if args.only:
        ParserSnapshotTest._only_filter = args.only
    suite = unittest.TestLoader().loadTestsFromTestCase(ParserSnapshotTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    if sys.platform == 'win32':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.exit(main())
