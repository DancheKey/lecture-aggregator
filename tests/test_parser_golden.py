#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
golden-case 回归测试（Q3）——锁定 parser 关键案例，防止改候选A 悄悄破坏候选B。

用法：
  python tests/test_parser_golden.py            # 直接跑（stdlib unittest）
  python -m unittest tests.test_parser_golden   # 或这样

密封化（2026-08-05 体检修复 严重-6）：
- HTML 已固化为 tests/fixtures/html/*.html（python tests/fetch_fixtures.py 可刷新）。
  有 fixture 的 case 完全离线可跑；解析失败直接红（不再 skip），
  杜绝「网络抖动 → 整体 skip → 门禁空转、绿 ≠ 通过」。
- VLM 走「录制回放」：VLM 响应固化在 tests/fixtures/vlm_cache.json，
  测试把缓存路径指向该夹具、provider 配置打桩为不可达地址——
  命中缓存即返回（无任何真实 API 调用、零 key、零成本、CI/本地完全一致）；
  若某 case 的 VLM 响应未录制，会请求不可达地址并失败（红），而不是静默跳过。

  ⚠️ 新增 requires_vlm 用例时必须同步录制响应，否则 CI 必红。录制步骤：
    1) 本机配置真实 VLM key（.env 的 ZHIPU_API_KEY），正常跑一次解析让响应
       落入生产缓存 data/.vlm_cache.json（缓存键 = 图片 URL 集合的 md5）；
    2) 用探测脚本找出该用例命中的缓存键（monkeypatch _vlm_cache_get 记录 key），
       把对应条目追加进 tests/fixtures/vlm_cache.json 并提交。
- ctld4409 原页已下线(404)且无本地副本，保留 may_404 skip 语义（文档化的唯一例外）。

断言：条数 / speaker 列表 / topic 无垃圾值（如 ctld4391 旧值 '0- 17'）/
无跨讲者 bio 泄漏（psy899 式）。

案例来源：评审文档第六节 + 历次补丁验证（补丁4/16/17/9-10/abstract 泄漏）。
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
FIXDIR = os.path.join(ROOT, 'tests', 'fixtures', 'html')
VLM_CACHE_FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'vlm_cache.json')

import parsers as P

# VLM 密封化：provider 配置打桩为「存在但不可达」（.invalid 为 RFC 6761 保留、永不解析）。
# 配合缓存夹具：命中缓存直接返回（真实路径），缓存未命中才会请求该地址并快速失败。
P._load_vlm_configs = lambda: [{'model': 'golden-replay', 'api_key': 'unused',
                                'base_url': 'http://golden-test.invalid/vlm'}]
P._vlm_cache_path = lambda: VLM_CACHE_FIXTURE      # 只读仓库内夹具，不依赖/不污染本机生产缓存
P._vlm_cache_set = lambda key, val: None           # 测试期不落盘新缓存

# 垃圾 topic 特征（历史上由表格/页眉误读产生）：如 '0- 17' / '0 -12' / 纯序号
_FORBIDDEN_TOPIC = ('0-', '0 -', '0—')


def load_html(case):
    """优先读仓库 fixture；缺失且显式允许直播（GOLDEN_ALLOW_LIVE=1）才抓取。"""
    path = os.path.join(FIXDIR, case['fixture'])
    if os.path.exists(path):
        with open(path, 'rb') as f:
            raw = f.read()
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            return raw.decode('gb18030', errors='replace')
    if os.environ.get('GOLDEN_ALLOW_LIVE') == '1' and not case.get('may_404'):
        import urllib.request
        req = urllib.request.Request(case['url'], headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            return raw.decode('gb18030', errors='replace')
    raise FileNotFoundError(
        f'缺少 fixture：tests/fixtures/html/{case["fixture"]}。'
        f'请运行 python tests/fetch_fixtures.py 固化该页后重试。')


def parse(case):
    html = load_html(case)
    recs = P.parse_detail(html, case['url'], college='', campus='', default_year=None)
    return recs if isinstance(recs, list) else [recs]


# ── golden cases ──────────────────────────────────────────────
# count: 期望条数；speakers: 期望 speaker 列表（顺序，允许 ''）；
# topic_sub: 每场 topic 应包含的片段（可选，用于强校验关键页）。
# fixture: tests/fixtures/html/ 下的固化 HTML 文件名。
CASES = [
    # 补丁4 表格解析：ctld4391 拆 2 期且字段正确（旧值 topic='0- 17' 垃圾）
    {'url': 'http://ctld.scnu.edu.cn/a/20241111/4391.html', 'fixture': 'ctld4391.html',
     'count': 2, 'speakers': ['穆肃', '黄卫祖'],
     'topic_sub': ['人工智能在教育技术中的应用', '基于人工智能的个性化']},
    # 补丁17 已回退页：应单讲座、host 归位、不误拆
    {'url': 'http://skc.scnu.edu.cn/a/20231116/786.html', 'fixture': 'skc786.html',
     'count': 1, 'speakers': ['陈钊'], 'topic_sub': ['中国关键核心技术测度']},
    {'url': 'http://skc.scnu.edu.cn/a/20230323/691.html', 'fixture': 'skc691.html',
     'count': 1, 'speakers': ['王文斌'], 'topic_sub': ['国际中文教育']},
    # 纯文本多讲座：psy899 跨讲者 abstract/bio 不泄漏（蔡清/库逸轩 各独立）
    {'url': 'http://psy.scnu.edu.cn/a/20151201/899.html', 'fixture': 'psy899.html',
     'count': 2, 'speakers': ['蔡清', '库逸轩'],
     'topic_sub': ['What is atypical', 'Neural mechanisms']},
    # 裸专题并列：ctld4299 拆 2 期
    {'url': 'http://ctld.scnu.edu.cn/a/20240408/4299.html', 'fixture': 'ctld4299.html',
     'count': 2, 'speakers': ['谢幼如', '王颖'],
     'topic_sub': ['数智赋能课程教学创新', '课程思政的教学设计与教学实践']},
    # CTLD 通识课「（第N场）：」结构：4408/4410 拆 2 期、location 无泄漏
    {'url': 'http://ctld.scnu.edu.cn/a/20250303/4408.html', 'fixture': 'ctld4408.html',
     'count': 2, 'speakers': ['汤庸', '穆肃'],
     'topic_sub': ['DeepSeek与人工智能技术前沿', '中小学校如何开好人工智能课']},
    {'url': 'http://ctld.scnu.edu.cn/a/20250317/4410.html', 'fixture': 'ctld4410.html',
     'count': 2, 'speakers': ['胡国胜', '王红'],
     'topic_sub': ['价值引领的人工智能教育应用', '人工智能助力教师队伍新发展']},
    # xz 跨日期同主题系列：单页拆多期不误合。
    # 注意：本页为行知书院讲座海报（VLM 提取，响应已录制于 tests/fixtures/vlm_cache.json），
    # 实际含 5 场不同主讲的研究报告，应拆 5 条。旧基线 count=1 是错误值——
    # 当时 publishTime=None 导致 OCR 日期缺年份上下文、5 场被错误合并；
    # 发布时间提取修复后正确拆 5 场。topic 当前未提取到，故 topic_sub 不强校验。
    {'url': 'http://xz.scnu.edu.cn/a/20221026/65.html', 'fixture': 'xz65.html',
     'count': 5,
     'speakers': ['张曦', '黄佩瑶', '陈嘉仪', '林逸鑫', '龚雅云、黄嘉正'], 'topic_sub': [],
     'requires_vlm': True},
    # cs 多报告（同页 2 场不同主讲）
    {'url': 'http://cs.scnu.edu.cn/a/20240516/5708.html', 'fixture': 'cs5708.html',
     'count': 2, 'speakers': ['罗富财', '林富春'],
     'topic_sub': ['Generic Construction', 'More Efficient Zero-Knowledge']},
    # ggy5666 同人 4 场（徐湘林 x4）——校验不误拆且 bio 不互串
    {'url': 'http://ggy.scnu.edu.cn/a/20211116/5666.html', 'fixture': 'ggy5666.html',
     'count': 4,
     'speakers': ['徐湘林', '徐湘林', '徐湘林', '徐湘林'], 'topic_sub': []},
    # physics807 三场：嘉宾在主题之前（嘉宾：X → 主题：Y → 嘉宾简介：…教授），
    # 块末"嘉宾：下一场"指向下一场；核验前置 speaker 正确落位（刘玉鑫/吴小山/刘玉斌）
    {'url': 'https://physics.scnu.edu.cn/a/20191118/807.html', 'fixture': 'physics807.html',
     'count': 3, 'speakers': ['刘玉鑫', '吴小山', '刘玉斌'], 'topic_sub': []},
    # 2026-09 主讲人提取修复回归：原规则把题目/单位当主讲人、并列多主讲漏识别。
    # 对应 parsers.py 三处修复（报告人后跟题目则跳过取下一标签 / 并列多主讲兼容半角括号单位 /
    # 单位提取前截掉粘连的日期时间地点元数据）。以下 6 例锁定修复后正确主讲人。
    {'url': 'http://skc.scnu.edu.cn/a/20191202/512.html', 'fixture': 'skc512.html',
     'count': 1, 'speakers': ['白凯']},
    {'url': 'http://lswh.scnu.edu.cn/a/20181022/13.html', 'fixture': 'lswh13.html',
     'count': 1, 'speakers': ['黄国信、温春来']},
    {'url': 'https://physics.scnu.edu.cn/a/20250303/12933.html', 'fixture': 'physics12933.html',
     'count': 1, 'speakers': ['温永立']},
    {'url': 'https://physics.scnu.edu.cn/a/20221011/12119.html', 'fixture': 'physics12119.html',
     'count': 1, 'speakers': ['罗洪刚']},
    {'url': 'https://physics.scnu.edu.cn/a/20211117/11773.html', 'fixture': 'physics11773.html',
     'count': 1, 'speakers': ['李海欧']},
    {'url': 'https://physics.scnu.edu.cn/a/20221018/12127.html', 'fixture': 'physics12127.html',
     'count': 1, 'speakers': ['陈理想']},
    # 2026-09-10 round-16 多场串页修复回归（psy「讲座一/二」结构，源页已实证）。
    # 当前 parser 仅拆分达标；字段级缺陷（bio/abs 串页、location 串场）由 KnownDefectTest 锁定。
    # psy940 两场连排（无「讲座一/二」标签，仅两块 题目/时间/地点）：同讲者刘贤臣两场
    {'url': 'http://psy.scnu.edu.cn/a/20151228/940.html', 'fixture': 'psy940.html',
     'count': 2, 'speakers': ['刘贤臣', '刘贤臣'], 'field_garbage': False},
    # psy1323 讲座一/二（各带 简介+讲座内容）：杨启正 / 杨立行 同校两场
    {'url': 'http://psy.scnu.edu.cn/a/20170626/1323.html', 'fixture': 'psy1323.html',
     'count': 2, 'speakers': ['杨启正', '杨立行'],
     'topic_sub': ['Post-Concussion Syndrome', 'Individual differences'],
     'field_garbage': False},
    # psy127 无摘要多场页（源页仅 时间/地点/题目/主讲人）：不得产出「学术讲座一」栏目标题垃圾摘要
    {'url': 'http://psy.scnu.edu.cn/a/20130301/127.html', 'fixture': 'psy127.html',
     'count': 2, 'speakers': ['乐国安', '沈模卫'],
     'topic_sub': ['基于网络平台的社会心理与行为研究', '论心理学原理与应用'],
     'field_garbage': True},
    # 直播已下线(404)且无本地副本：唯一保留 skip 语义的 case（文档化例外）。
    {'url': 'http://ctld.scnu.edu.cn/a/20250310/4409.html', 'fixture': 'ctld4409.html',
     'count': 2, 'speakers': ['卢晓中', '赵淦森'], 'topic_sub': [], 'may_404': True},
]


# 字段尾部垃圾词（历次修复实录）：多场页「讲座一/二」串场尾巴、psy 早期沙龙页
# 「心理学学术沙龙」栏目词、psy127「学术讲座一」栏目标题、页脚版权/导航词。
_TAIL_GARBAGE_RE = re.compile(
    r'(讲座一|讲座二|学术讲座一|心理学学术沙龙|版权所有|Copyright|'
    r'关于华南师范大学|上一篇|下一篇)\s*$')


def _check_no_bio_leak(self, url, recs):
    """跨讲者 bio 泄漏：A 的 speakerBio 不应包含 B 的 speaker 姓名（B≠A）。"""
    for a in recs:
        sa, ba = a.get('speaker'), a.get('speakerBio')
        if not (sa and ba):
            continue
        for b in recs:
            sb = b.get('speaker')
            if sb and sb != sa and sb in ba:
                self.fail(f'{url} bio 泄漏：{sa} 的 bio 含有 {sb} 的姓名')


def _make_test(case):
    def test(self):
        if case.get('may_404') and not os.path.exists(os.path.join(FIXDIR, case['fixture'])):
            self.skipTest('该页已下线(404)且无本地 fixture——文档化的唯一 skip 例外；'
                          '若能取得该页 HTML，放入 tests/fixtures/html/ 即可恢复校验')
        # 密封化后：解析失败直接红（fixture 在仓库内，不存在网络抖动借口）
        recs = parse(case)
        # 条数
        self.assertEqual(len(recs), case['count'],
                         f"条数期望 {case['count']}，实际 {len(recs)}")
        # speaker 列表（无序比较：多主讲页顺序受解析细节影响，非语义关键）
        got_spk = [r.get('speaker') or '' for r in recs]
        self.assertCountEqual(got_spk, case['speakers'],
                              f"speaker 期望 {case['speakers']}，实际 {got_spk}")
        # topic 强校验 + 垃圾值拦截（无序：每个子串出现在任一 topic 即可，
        # 避免多讲座 topic 顺序差异导致假失败；同时容忍个别页的字符误读被锁定）
        topics = [(r.get('topic') or '') for r in recs]
        for t in topics:
            for bad in _FORBIDDEN_TOPIC:
                self.assertNotIn(bad, t, f"topic 含垃圾值 {bad!r}: {t!r}")
        for sub in (case.get('topic_sub') or []):
            if sub and not any(sub in t for t in topics):
                self.fail(f"无 topic 含 {sub!r}；topics={topics}")
        # 跨讲者 bio 泄漏
        _check_no_bio_leak(self, case['url'], recs)
        # 字段尾部垃圾（仅 field_garbage=True 的 case 启用；False=该页已知垃圾、由 KnownDefect 锁）
        if case.get('field_garbage'):
            for r in recs:
                for fld in ('abstract', 'speakerBio', 'location'):
                    v = (r.get(fld) or '').strip()
                    if v:
                        m = _TAIL_GARBAGE_RE.search(v)
                        if m:
                            self.fail(
                                f"{case['url']} {fld} 尾部含栏目/页脚垃圾 "
                                f"{m.group(0)!r}: ...{v[-40:]!r}")
    return test


class GoldenTest(unittest.TestCase):
    def test_00_ctld432_inverted_speaker(self):
        """idx2635 职务倒装「主讲人：网络中心主任 林南晖」→ speaker=林南晖 + speakerTitle=网络中心主任。

        极端场景兜底要求：模型 A/B 全不可用时，纯规则也能识别这种
        「职务在前、姓名在后」的反常写法（真实页 ctld432 的正文排版）。
        affiliation 因无法自动补校名，最终人工落库为「网络中心」，此处不做规则断言。"""
        html = load_html({'fixture': 'ctld432.html',
                          'url': 'http://ctld.scnu.edu.cn/a/20170922/432.html'})
        recs = P.parse_detail(html, 'http://ctld.scnu.edu.cn/a/20170922/432.html',
                              '', '', default_year=None)
        recs = recs if isinstance(recs, list) else [recs]
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r.get('speaker'), '林南晖')
        self.assertEqual(r.get('speakerTitle'), '网络中心主任')
        # 2026-09-09 规则增强：倒装职务推导机构名（无法补校名，取职务机构）
        self.assertEqual(r.get('speakerAffiliation'), '网络中心')


for _i, _c in enumerate(CASES):
    _slug = _c['url'].rstrip('/').split('/')[-1].replace('.html', '')
    setattr(GoldenTest, f'test_{_i:02d}_{_slug}', _make_test(_c))


class KnownDefectTest(unittest.TestCase):
    """2026-09-10 round-16 已知解析缺陷清单（源页锚点均已实证）——期望行为用 @expectedFailure 锁定。

    这些页面的库内数据是人工修复的，但 parser 重抓会再次产出污染。
    每个用例锁定「正确行为」：当前失败（xfail）= 已知缺陷；
    若未来修复使某个用例意外通过（unexpected success），unittest 会报红提醒摘除标记并更新基线。
    期望值证据：tmp/_r14/_probe_r16*.py 对源页的逐段探测。
    """

    @staticmethod
    def _parse(fixture, url):
        html = load_html({'fixture': fixture, 'url': url})
        recs = P.parse_detail(html, url, college='', campus='', default_year=None)
        return recs if isinstance(recs, list) else [recs]

    @unittest.expectedFailure
    def test_psy1305_should_split_two(self):
        """psy1305 郑东萍/林安迪讲座一/二应拆 2 条，location 不串讲者名。当前未拆(1 条)。"""
        recs = self._parse('psy1305.html', 'http://psy.scnu.edu.cn/a/20170612/1305.html')
        self.assertEqual(len(recs), 2)
        by = {r.get('speaker'): r for r in recs}
        self.assertEqual(set(by), {'郑东萍', '林安迪'})
        self.assertEqual(by['郑东萍'].get('location'), '心理学院201会议室')
        self.assertIn('Hawaii', by['郑东萍'].get('speakerAffiliation') or '')
        self.assertIn('Impartiality', by['林安迪'].get('topic') or '')
        self.assertIn('ecological', by['郑东萍'].get('abstract') or '')
        self.assertIn('friendship and morality', by['林安迪'].get('abstract') or '')

    @unittest.expectedFailure
    def test_psy961_should_split_two_speaker_not_garbage(self):
        """psy961 张喜淋/张洳源应拆 2 条；speaker 不得取成 'Cognitive Sciences' 垃圾值。当前未拆。"""
        recs = self._parse('psy961.html', 'http://psy.scnu.edu.cn/a/20160113/961.html')
        self.assertEqual(len(recs), 2)
        spk = [r.get('speaker') for r in recs]
        self.assertCountEqual(spk, ['张喜淋', '张洳源'])
        for r in recs:
            self.assertNotIn('Cognitive Sciences', r.get('speaker') or '')
            self.assertNotIn('讲座一', r.get('location') or '')

    @unittest.expectedFailure
    def test_psy940_fields_clean(self):
        """psy940 拆分已达标，但 r1 location 串第二场描述、abstract 为日期行垃圾、bio 尾串日期行。"""
        recs = self._parse('psy940.html', 'http://psy.scnu.edu.cn/a/20151228/940.html')
        self.assertEqual(len(recs), 2)
        r1 = recs[0]
        self.assertEqual(r1.get('location'), '心理学院5楼学术报告厅')
        self.assertEqual((r1.get('abstract') or '').strip(), '')   # 源页无讲座一摘要
        self.assertFalse(_TAIL_GARBAGE_RE.search(r1.get('speakerBio') or ''))

    @unittest.expectedFailure
    def test_psy1385_fields_pairing(self):
        """psy1385 拆分已达标，但两场 location 串「讲座二」、胡玉正 bio 尾串耿凤基简介标签、aff 错同。"""
        recs = self._parse('psy1385.html', 'http://psy.scnu.edu.cn/a/20171117/1385.html')
        self.assertEqual(len(recs), 2)
        by = {r.get('speaker'): r for r in recs}
        hu, geng = by['胡玉正'], by['耿凤基']
        self.assertNotIn('讲座二', hu.get('location') or '')
        self.assertIn('马里兰', geng.get('speakerAffiliation') or '')
        self.assertNotIn('耿凤基', hu.get('speakerBio') or '')
        self.assertFalse(_TAIL_GARBAGE_RE.search(geng.get('speakerBio') or ''))
        self.assertTrue((hu.get('abstract') or '').startswith('Substance use disorder'))

    @unittest.expectedFailure
    def test_psy1323_abstract_attribution(self):
        """psy1323 拆分已达标，但两场 abstract 错位（讲座一内容给了杨立行且尾串「讲座二」）。"""
        recs = self._parse('psy1323.html', 'http://psy.scnu.edu.cn/a/20170626/1323.html')
        self.assertEqual(len(recs), 2)
        by = {r.get('speaker'): r for r in recs}
        self.assertTrue((by['杨启正'].get('abstract') or '').startswith('轻度头外伤'))
        self.assertTrue((by['杨立行'].get('abstract') or '').startswith('Conaway'))
        for r in recs:
            self.assertFalse(_TAIL_GARBAGE_RE.search(r.get('speakerBio') or ''))
            self.assertNotIn('杨立行', by['杨启正'].get('speakerBio') or '')

    @unittest.expectedFailure
    def test_psy1179_bio_pairing_and_affiliation(self):
        """psy1179 拆分已达标，但刘勋 bio 复制了周晓林简介（互串）、aff 均错取中科院、location 串「讲座二」。"""
        recs = self._parse('psy1179.html', 'http://psy.scnu.edu.cn/a/20161130/1179.html')
        self.assertEqual(len(recs), 2)
        by = {r.get('speaker'): r for r in recs}
        zhou, liu = by['周晓林'], by['刘勋']
        self.assertIn('北京大学', zhou.get('speakerAffiliation') or '')
        self.assertIn('百人计划', liu.get('speakerBio') or '')
        self.assertNotIn('1400', zhou.get('speakerBio') or '')   # 1400次被引是刘勋简介特征
        self.assertEqual(zhou.get('location'), '心理学院201')

    @unittest.expectedFailure
    def test_psy127_speaker_time_pairing(self):
        """psy127 拆分已达标，但讲者与开始时间错位（乐国安=讲座一 9:30，沈模卫=讲座二 15:30）。"""
        recs = self._parse('psy127.html', 'http://psy.scnu.edu.cn/a/20130301/127.html')
        self.assertEqual(len(recs), 2)
        by = {r.get('speaker'): r for r in recs}
        self.assertEqual((by['乐国安'].get('lectureStart') or '')[11:16], '09:30')
        self.assertEqual((by['沈模卫'].get('lectureStart') or '')[11:16], '15:30')
        self.assertEqual(by['乐国安'].get('lectureIndex'), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
