# -*- coding: utf-8 -*-
"""「隐形记录」守卫（2026-10-04，方案 B）。

排除名单的维护流程是「本地删记录 + URL 进名单」，但跨源合并的中间态会绕过它：
合并发生时来源 URL 尚未入名单，之后该 URL 被删/拉黑，已挂上它的存量记录就变成
「前端永不上站、主库却占条数」的隐形记录——实测 ctld1436 工作坊两场
（合并来源 gxb/124 在名单），前端 lectureCount 3808 与主库 3810 长期对不上。

本套件锁三件事，任何一件被破坏都会红：

  ① audit_data_quality 的「排除」档：任一关联 URL 命中名单的记录必须被报出，
     且 fixability 给出「删除或复议名单」的人工建议（防清完无守卫）。
  ② 实库回归：data/lectures.json 与真实排除名单跑 scan 必须为 0 条命中——
     以后再积累隐形记录（合并中间态 / 只拉黑不删记录），CI 在这里红。
  ③ 主库条数 == 前端 lectureCount：两个数字必须同源同值，防止再次出现
     「3810 vs 3808」这种静默分歧。

运行：python tests/test_audit_excluded_records.py
"""
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))


def _load(name, relpath):
    """按文件路径显式加载（scraper/ scripts/ 均无 __init__.py）。"""
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audit = _load('audit_under_test', os.path.join('scripts', 'audit_data_quality.py'))
excluded_urls = _load('excluded_under_test', os.path.join('scripts', 'excluded_urls.py'))

# 合成名单：不含真实 URL，避免实库/名单变化让单元用例失真。
# 注意条目须与 load_excluded() 的产出同口径（rstrip('/') 归一后无尾斜杠）。
SYN_LIST = {'http://x.example.com/a/1.html', 'http://y.example.com/b'}


def _rec(url='http://ok.example.com/a/9.html', sources=None, **kw):
    r = {'sourceUrl': url, 'title': '讲座', 'speaker': '张三',
         'location': '理6栋302', 'lectureStart': '2026-04-20 14:30:00'}
    if sources is not None:
        r['sources'] = [{'sourceUrl': s} for s in sources]
    r.update(kw)
    return r


class ExcludedFlagTest(unittest.TestCase):
    """① 排除档判定：主 URL 或任一合并来源命中名单 → 报「隐形记录」。"""

    def _descs(self, recs, excluded=SYN_LIST):
        return [i[3] for i in audit.scan(recs, excluded=excluded)]

    def test_01_主URL命中被报出(self):
        descs = self._descs([_rec('http://x.example.com/a/1.html')])
        self.assertTrue(any('隐形记录' in d for d in descs),
                        '主 URL 命中名单的记录必须被报出')

    def test_02_合并来源命中被报出(self):
        descs = self._descs([_rec(sources=['http://y.example.com/b/'])])
        self.assertTrue(any('隐形记录' in d for d in descs),
                        '合并来源命中名单的记录必须被报出（ctld1436 即此形态）')

    def test_03_干净记录不误报(self):
        descs = self._descs([_rec(), _rec(sources=['http://ok.example.com/a/2.html'])])
        self.assertFalse(any('隐形记录' in d for d in descs))

    def test_04_修复建议指向删除或复议名单(self):
        recs = [_rec(sources=['http://x.example.com/a/1.html'])]
        issues = audit.scan(recs, excluded=SYN_LIST)
        it = next(i for i in issues if i[3].startswith('隐形记录'))
        self.assertEqual(it[0], '排除')
        self.assertEqual(it[2], '低')
        kind, adv = audit.fixability(it[0], it[3], it[4])
        self.assertEqual(kind, 'manual')
        self.assertIn('从主库删除', adv)

    def test_05_名单为空时检查静默跳过(self):
        descs = self._descs([_rec('http://x.example.com/a/1.html')], excluded={})
        self.assertFalse(any('隐形记录' in d for d in descs),
                         '名单缺失/为空时不得误报（环境缺文件也须安全）')


class VsbCanonNormalizationTest(unittest.TestCase):
    """VSB 站点组归一化（2026-10-14，防御性加固）。

    背景：_canon_url_key 已下沉至 excluded_urls.py，is_excluded 用同一套归一。
    此前 is_excluded 只 rstrip('/')，名单写 em 形态、记录是 module 形态时排除
    静默失效。当前名单 322 条 0 条 vsb/module，属预防性加固。
    """

    def test_01_em形态命中module记录(self):
        """名单写 em.scnu.edu.cn，记录是 module.scnu.edu.cn → 必须命中。"""
        # 直接构造合成名单（不依赖真实名单内容）
        excluded = {excluded_urls.canon_url_key('http://em.scnu.edu.cn/a/20260924/11175.html')}
        self.assertTrue(
            excluded_urls.is_excluded('https://module.scnu.edu.cn/article-3685-11175-1.html', excluded),
            'module 形态必须命中 em 形态的名单条目（VSB 站点组折叠）')

    def test_02_module形态命中em记录(self):
        """反向：名单写 module 形态，记录是 em 形态 → 也必须命中。"""
        excluded = {excluded_urls.canon_url_key('https://module.scnu.edu.cn/article-3685-11175-1.html')}
        self.assertTrue(
            excluded_urls.is_excluded('http://em.scnu.edu.cn/a/20260924/11175.html', excluded),
            'em 形态必须命中 module 形态的名单条目')

    def test_03_跨站同ID不误合并(self):
        """VSB 文章 ID 每站独立编号，跨站同 ID 不同文章 → 不得误命中。"""
        excluded = {excluded_urls.canon_url_key('http://em.scnu.edu.cn/a/20260924/22.html')}
        self.assertFalse(
            excluded_urls.is_excluded('http://lswh.scnu.edu.cn/a/20260101/22.html', excluded),
            '跨站同 ID 不同文章，不得误合并')

    def test_04_非VSB形态仍rstrip归一(self):
        """普通 URL 仍走 rstrip('/') 归一（尾斜杠差异不致排除遗漏）。"""
        excluded = {'http://x.example.com/a/1.html'}
        self.assertTrue(
            excluded_urls.is_excluded('http://x.example.com/a/1.html/', excluded))
        self.assertFalse(
            excluded_urls.is_excluded('http://x.example.com/a/2.html', excluded))


class RealDataRegressionTest(unittest.TestCase):
    """②③ 实库回归：CI 每次推送都对真实数据跑，再积累即红。"""

    @classmethod
    def setUpClass(cls):
        raw = json.load(open(os.path.join(ROOT, 'data', 'lectures.json'),
                             encoding='utf-8'))
        cls.recs = raw['data'] if isinstance(raw, dict) else raw
        cls.excluded = excluded_urls.load_excluded()

    def test_01_实库零隐形记录(self):
        hits = [r for r in self.recs
                if any(excluded_urls.is_excluded(u, self.excluded)
                       for u in excluded_urls.record_urls(r))]
        self.assertEqual(hits, [],
                         f'主库积压 {len(hits)} 条隐形记录（前端不上站但占条数）。'
                         f'处理：确认后从主库删除（scripts/fix_purge_excluded_records.py '
                         f'可复查清单），或复议排除名单。命中 URL：'
                         f'{sorted({u for r in hits for u in excluded_urls.record_urls(r) if excluded_urls.is_excluded(u, self.excluded)})[:5]}')

    def test_02_主库条数等于前端lectureCount(self):
        stats = json.load(open(os.path.join(ROOT, 'site', 'lectures', 'stats.json'),
                               encoding='utf-8'))
        self.assertEqual(len(self.recs), stats.get('lectureCount'),
                         '主库与前端 lectureCount 不一致——数据改动后忘记重跑 '
                         'generate_frontend_data.py，或又出现隐形记录')


if __name__ == '__main__':
    unittest.main()
