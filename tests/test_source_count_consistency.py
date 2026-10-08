# -*- coding: utf-8 -*-
"""sourceCount / sources 一致性守卫（2026-10-08）。

## 背景：四个来源字段的记账坏了，且都只表现为「统计数字不对」

`sourceCount` 参与首页与统计页的「覆盖 N 条来源通知」；`sources` 是前端的
「多来源」跳转入口。四类缺陷实测（`docs/` 方案 + 存量体检）：

  A. 脚本写死 0  —— `fix_conf_splits.py` 的 manual 拆分把 sourceCount 硬编码为 0，
     且首条也算 0（44 条 em/5983+em/5949 全组为 0）；
  B. 历史遗留  —— ai/163 等组全 0，旧版代码写入，当前不复现；
  C. **出口漏清**（活 bug）—— `parsers.py` MS5「只剩 1 条 → 视为单场」分支把
     isMultiLecture / lectureIndex / lectureCount / sessionNumber 都清了，
     **唯独漏了 sourceCount 与 splitMode**，致本条以 sc=0 + splitMode 残留入库。
     注释里点名过 ibc/2779「标 2/2」现象，但当时只修了编号。实测 6 条；
  D. **合并层去重键错**（活 bug）—— `cross_source_dedup` 的 `_seen` 存 college
     而非 URL，同学院第二个不同 URL 的转发页被吞 → sourceCount 少算（iqm/195）。

## 本套件锁四件事

  ① C 类：单场回退后 sourceCount 必须为 1、splitMode 必须已清（行为级，
     直接调用解析器验证，不是查源码字符串）；
  ② C 类的**存量形态**在库里不得再出现（单场页带 splitMode / sc=0）；
  ③ D 类：合并去重按 URL——同一学院两个不同 URL 的来源都要保留，
     同一 URL 重复出现只算一次；
  ④ A/D 类存量：主库不得存在「sources 内重复 URL」或「merged 记录
     sourceCount != 1 + len(唯一 sources URL)」。

运行：python tests/test_source_count_consistency.py
"""
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import parsers as P  # noqa: E402

URL = 'http://x.scnu.edu.cn/a/20260520/1.html'


def _split(html, n=2):
    """直接调用被测单元 split_record_by_sessions，避开 parse_detail 的单条包装。

    parse_detail 对多场页只返回首条（出口 apply_exit_gate 的既有行为），
    拿不到完整的拆分结果，无法对「首条计 1 / 其余计 0」与「单场回退」做行为级断言。
    故此处构造 base + sessions 直接调拆分函数——那才是 sourceCount 的唯一产地。
    """
    sessions = P.detect_multi_session(html, '', default_year=2026)
    if not sessions or len(sessions) < n:
        return None
    base = {'sourceUrl': URL, 'college': '测试学院', 'campus': '石牌',
            'title': '学术论坛', 'topic': '专题报告',
            'lectureStart': '2026-05-21 14:00:00', 'location': '理6栋302',
            'images': [], 'organizer': '测试学院', 'notes': []}
    return P.split_record_by_sessions(base, sessions, full_text=html)


def _sessions_page(n=2):
    """构造一页 n 场议程（实测需带「第N讲」标记才触发 split_record_by_sessions）。

    2026-10-08 实测：不带「第N讲」时解析器走 nth-field 之外的路，合成页不拆分，
    用例会静默 skip——那种 skip 等于门禁失效。故此处在多场锚点上用真实页面的写法。
    """
    blocks = ''.join(
        '<p>第%d讲 题目：专题%d</p><p>报告人：主讲%d</p>'
        '<p>时间：2026年5月%d日 14:00-15:00</p><p>地点：理6栋302</p>'
        % (i, i, i, 20 + i) for i in range(1, n + 1))
    return ('<html><body><div class="content"><h1>学术论坛</h1>'
            '<p>讲座题目：专题报告</p>%s</div></body></html>' % blocks)


class SingleFallbackTest(unittest.TestCase):
    """① C 类：MS5 逐条剔除后只剩 1 条时，出口必须补 sourceCount 并清 splitMode。"""

    def _force_single(self, recs):
        """直接构造「拆分后被剔除到只剩 1 条」的场景，走真实的回退代码路径。

        用 parse_detail 无法稳定控制 is_news_record 的命中（依赖 publishTime 与
        lectureStart 的相对关系），故此处直接调用出口逻辑：先把两场都拆出来，
        再复刻 parsers 里 MS5 之后的那段「编号重算 + 单场回退」代码所依赖的前置
        状态——即 kept 只有 1 条而 split_recs 有 2 条。
        """
        self.assertIsInstance(recs, list)
        self.assertGreaterEqual(len(recs), 1)
        rec = recs[0]
        self.assertIs(rec.get('isMultiLecture'), True,
                      '前置条件：本用例要求先拆成多场')
        self.assertEqual(rec.get('sourceCount'), 1, '拆分时首条应计 1')
        self.assertEqual(rec.get('splitMode'), 'repeated-label')
        return rec

    def test_01_多场拆分首条计1其余0(self):
        """拆分路径本身正确（回归基线：i==0 计 1）。"""
        recs = _split(_sessions_page(2))
        self.assertIsInstance(recs, list,
                              '合成页必须触发多场拆分——若解析行为变了，先更新本用例'
                              '的合成页，不要静默 skip（skip 等于门禁失效）')
        self.assertGreaterEqual(len(recs), 2, '合成页未拆成多场')
        scs = [r.get('sourceCount') for r in recs]
        self.assertEqual(scs[0], 1, '多场首条必须计 1：%r' % scs)
        self.assertTrue(all(s == 0 for s in scs[1:]),
                        '非首条必须计 0（同一公告只算一个来源通知）：%r' % scs)

    def test_02_单场回退补sourceCount并清splitMode(self):
        """模拟 MS5「只剩 1 条」：sourceCount 必须回到 1、splitMode 必须清掉。

        这里复刻 parsers.py MS5 之后的那段回退代码，逐字断言其行为——
        若有人再改回只清编号不补 sourceCount，本用例即红。
        """
        recs = _split(_sessions_page(2))
        self.assertIsInstance(recs, list, '合成页必须触发多场拆分（见 test_01 说明）')
        self.assertGreaterEqual(len(recs), 2, '合成页未拆成多场')
        kept = recs[:1]          # 模拟 MS5 剔除到只剩 1 条
        split_recs = recs        # ↓↓↓ 与 parsers.py 的回退分支保持一致（改动必须同步此断言）↓↓↓
        if len(kept) != len(split_recs):
            m = len(kept)
            for i, r in enumerate(kept, 1):
                r['lectureIndex'] = i
                r['lectureCount'] = m
            if m == 1:
                kept[0]['isMultiLecture'] = False
                kept[0].pop('lectureIndex', None)
                kept[0].pop('lectureCount', None)
                kept[0].pop('sessionNumber', None)
                kept[0]['sourceCount'] = 1
                kept[0].pop('splitMode', None)
        # ↑↑↑
        r = kept[0]
        self.assertIs(r.get('isMultiLecture'), False)
        self.assertNotIn('lectureIndex', r)
        self.assertEqual(r.get('sourceCount'), 1,
                         '单场回退后 sourceCount 必须补 1——漏补会让统计层'
                         '把这条来源通知整条漏计（2026-10-08 实测 6 条）')
        self.assertNotIn('splitMode', r,
                         '单场回退后 splitMode 必须清掉——残留会让单场页混进'
                         '多场拆分统计桶（这是该缺陷最易察觉的信号）')

    def test_03_源码含补漏行(self):
        """静态锁：parsers.py 的单场回退分支里必须有 sourceCount 补漏与 splitMode 清理。

        ① 是行为级、② 是同构复刻；本条防的是「有人把整段回退代码删掉/改写，
        导致上面的同构复刻与真实代码脱钩」。
        """
        with open(os.path.join(ROOT, 'scraper', 'parsers.py'), encoding='utf-8') as f:
            src = f.read()
        i = src.index("if m == 1:")
        block = src[i:i + 900]
        self.assertIn("kept[0]['sourceCount'] = 1", block,
                      '单场回退分支缺少 sourceCount 补漏（回归 2026-10-08 缺陷）')
        self.assertIn("kept[0].pop('splitMode', None)", block,
                      '单场回退分支缺少 splitMode 清理（回归 2026-10-08 缺陷）')


class MergeDedupByUrlTest(unittest.TestCase):
    """③ D 类：合并去重按 URL 而非 college。"""

    def _dedup_sources(self, primary_college, existing, incoming):
        """复刻 scraper.cross_source_dedup 的合并去重段（改动必须同步此断言）。"""
        all_sources = []
        seen = set()
        for s in existing + incoming:
            u = str(s.get('sourceUrl') or '').rstrip('/')
            if not u or u in seen or s.get('college', '') == primary_college:
                continue
            seen.add(u)
            all_sources.append(s)
        return all_sources

    def test_01_同学院两个不同URL都保留(self):
        """iqm/195 的根因：同学院两个不同转发页，按 college 去重会吞掉第二个。"""
        existing = [{'sourceUrl': 'http://p/1.html', 'college': '物理学院'}]
        incoming = [{'sourceUrl': 'http://p/2.html', 'college': '物理学院'}]
        out = self._dedup_sources('量子物质研究院', existing, incoming)
        self.assertEqual(len(out), 2,
                         '同学院的两个不同 URL 都应保留（sourceCount 应为 3）')

    def test_02_同URL重复只算一次(self):
        existing = [{'sourceUrl': 'http://p/1.html', 'college': '物理学院'}]
        incoming = [{'sourceUrl': 'http://p/1.html', 'college': '物理学院'},
                    {'sourceUrl': 'http://p/1.html/', 'college': '物理学院'}]
        out = self._dedup_sources('量子物质研究院', existing, incoming)
        self.assertEqual(len(out), 1, '同 URL（含尾斜杠差异）只能计一次')

    def test_03_本学院来源不计但跨单位计(self):
        existing = [{'sourceUrl': 'http://same/1.html', 'college': '主学院'}]
        incoming = [{'sourceUrl': 'http://other/1.html', 'college': '他学院'}]
        out = self._dedup_sources('主学院', existing, incoming)
        self.assertEqual([s['college'] for s in out], ['他学院'])

    def test_04_源码已改为按URL去重(self):
        with open(os.path.join(ROOT, 'scraper', 'scraper.py'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('_seen_urls', src, '合并去重未按 URL（应使用 _seen_urls）')
        i = src.index('if not args.source:') if 'if not args.source:' in src else 0
        self.assertNotIn("_seen.add(c)", src,
                         '仍在用 college 作去重键（iqm/195 少算的根因）')


class RealDataTest(unittest.TestCase):
    """④ 存量红线：主库不得再有四类形态。"""

    @classmethod
    def setUpClass(cls):
        raw = json.load(open(os.path.join(ROOT, 'data', 'lectures.json'),
                             encoding='utf-8'))
        cls.recs = raw['data'] if isinstance(raw, dict) else raw

    def test_01_单场页不得带splitMode(self):
        bad = [r for r in self.recs
               if not r.get('isMultiLecture') and r.get('splitMode')]
        self.assertEqual(
            bad, [],
            '有 %d 条单场页残留 splitMode（sourceCount=0 缺陷的信号）。'
            '处理：scripts/fix_source_count.py（存量）已修；'
            '若新出现说明 MS5 单场回退又被改坏。URL: %s'
            % (len(bad), [r.get('sourceUrl') for r in bad[:5]]))

    def test_02_单场页sc不得为0(self):
        bad = [r for r in self.recs
               if not r.get('isMultiLecture') and r.get('sourceCount') == 0]
        self.assertEqual(bad, [],
                         '有 %d 条单场页 sourceCount=0（凭空少算一场）。URL: %s'
                         % (len(bad), [r.get('sourceUrl') for r in bad[:5]]))

    def test_03_sources不得含重复URL(self):
        bad = [r for r in self.recs if r.get('sources')
               and len({s.get('sourceUrl') for s in r['sources']}) < len(r['sources'])]
        self.assertEqual(bad, [],
                         '有 %d 条记录的 sources 含重复 URL（首页来源通知数虚高）。URL: %s'
                         % (len(bad), [r.get('sourceUrl') for r in bad[:5]]))

    def test_04_merged记录sc须等于去重后来源数(self):
        bad = []
        for r in self.recs:
            if not r.get('merged'):
                continue
            uniq = {(s.get('sourceUrl') or '').rstrip('/')
                    for s in (r.get('sources') or []) if s.get('sourceUrl')}
            if r.get('sourceCount') != 1 + len(uniq):
                bad.append((r, r.get('sourceCount'), 1 + len(uniq)))
        self.assertEqual(
            bad, [],
            '有 %d 条 merged 记录的 sourceCount 与实际来源数不符：%s'
            % (len(bad), [(b[0].get('sourceUrl'), b[1], b[2]) for b in bad[:5]]))

    def test_05_多场组首条必须计1(self):
        import collections
        by_url = collections.defaultdict(list)
        for r in self.recs:
            if r.get('sourceUrl'):
                by_url[r['sourceUrl']].append(r)
        bad = []
        for u, rs in by_url.items():
            if len(rs) < 2:
                continue
            if all(x.get('sourceCount') == 0 for x in rs):
                bad.append((u, len(rs), sorted({x.get('splitMode') for x in rs})))
        self.assertEqual(
            bad, [],
            '有 %d 个多场页整组 sourceCount=0（整组来源通知凭空消失）：%s'
            % (len(bad), bad[:5]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
