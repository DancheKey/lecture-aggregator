# -*- coding: utf-8 -*-
"""B8：列表页取不回内容时的失败记账守卫。

背景（2026-09-30 第三方评审 B8）：`_process_source` 的列表页循环**无失败守卫**。
`fetch()` 返回 None 时，`html` 为 None → `collect_links(None, ...)` 返回 [] →
内层循环一次都不进 → 函数末尾 `return local, None`（= 成功）→ 本源水位照常推进，
而该时段发布的讲座**永久漏抓**，且 `failed_sources` 里没有任何痕迹。

实测口径（2026-09-30 探测 53 源 / 60 个列表页）：`fetch` 全部成功（0 个返回 None），
但有 17 个页面「取回成功、0 条讲座条目」（如法学院：页面上 77 个 `<a>` 全是导航，
讲座正文为空——栏目真空/改版）。故修复必须**三态分层**：

  ① 列表页取回失败（网络/HTTP/robots）→ 判本源失败，水位不推进 ← 真正的修复
  ② 取回成功但 0 条条目→ **不**判失败，只记[LIST-EMPTY] 日志
  ③ 取回成功且有条目 → 正常

⚠ ②③ 必须与 ① 区分：`failed_sources` 在 main() 里是**全局**水位闸门
（任一源失败即整轮不推进 last_scrape，见 scraper.py:1840 附近）。若把「栏目真空」
也报成失败，一个永久改版的栏目会把整个增量管线**永久冻结**，退回每轮全量重抓
（历史实测 3.9h）。本套件锁死这个分层，防止后续"顺手扩大检查范围"再次把闸门焊死。

本套件桩掉 fetch / parse_detail / sleep，**零网络、零磁盘写入**。
"""
import importlib.util
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_scraper():
    # 主模块在 scraper/scraper.py（scraper/ 无 __init__.py，是命名空间包），
    # 直接 `import scraper` 会命中命名空间包而非主模块，故从文件显式加载。
    path = os.path.join(_ROOT, 'scraper', 'scraper.py')
    spec = importlib.util.spec_from_file_location('scraper.scraper', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _load_scraper()

# 一条「像详情页」的链接 + 一个通过的讲座标题（is_lecture 需命中）
GOOD_PAGE = '<html><body><a href="/a/202609/123.html">学术讲座通知</a></body></html>'
# 取回成功但没有任何可入参链接（导航-only，模拟栏目真空/改版）
EMPTY_PAGE = ('<html><body><a href="/xueyuangaikuang/">学院概况</a>'
              '<a href="/javascript.html">首页</a></body></html>')


class _SourceRun:
    """跑一次 _process_source，桩掉 fetch/parse_detail/sleep，返回 (err, 记录数)。"""

    def __init__(self, mod, pages, existing_urls=None, is_incremental=False):
        self.mod = mod
        self.pages = pages          # {url: html 或 None}；详情页不登记 → 视为取回失败
        self.fetched = []
        self.parsed = []

    def run(self, list_urls, **kw):
        m = self.mod
        orig = (m.fetch, m.parse_detail, m.time.sleep, m._can_fetch)
        m._can_fetch = lambda url: True
        m.time.sleep = lambda *_a, **_k: None

        def fake_fetch(url, _retries=3, allowed_domains=None):
            self.fetched.append(url)
            if url in self.pages:
                return self.pages[url]
            # 详情页（路径形如 /a/YYYYMM/N.html）：不在 pages 里就按「取回成功、
            # 正文为标题」处理，否则本用例关注不到解析环节（会误以为 local 为空
            # 是守卫造成的）。推导出的翻页页仍返回 None。
            if re.search(r'/a/\d{6}/\d+\.html?$', url):
                return f'<html><body>{url}</body></html>'
            return None

        def fake_parse_detail(d, href, name, campus, year, list_title='', **kwargs):
            self.parsed.append(href)
            return {'sourceUrl': href, 'listTitle': list_title, 'title': list_title}

        m.fetch = fake_fetch
        m.parse_detail = fake_parse_detail
        try:
            src = {'name': '测试源', 'campus': '石牌', 'base': 'http://t.scnu.edu.cn',
                   'list_urls': list_urls}
            local, err = m._process_source(src, 2026, kw.pop('existing_urls', set()),
                                           kw.pop('is_incremental', False), **kw)
            return err, local
        finally:
            m.fetch, m.parse_detail, m.time.sleep, m._can_fetch = orig


def _lu(u, mode=None):
    return {'url': u, 'collect_mode': mode} if mode else u


class ListPageFailureAccountingTest(unittest.TestCase):
    def setUp(self):
        self.u_ok = 'http://t.scnu.edu.cn/list/'
        self.u_bad = 'http://t.scnu.edu.cn/broken/'
        self.u_empty = 'http://t.scnu.edu.cn/empty/'

    def test_00_前置_有内容时必须成功(self):
        """基线：正常源不得被新守卫误判为失败（否则全局水位会无谓冻结）。"""
        r = _SourceRun(P, {self.u_ok: GOOD_PAGE})
        err, local = r.run([_lu(self.u_ok)])
        self.assertIsNone(err, f'正常源不该判失败：{err}')
        self.assertEqual(len(local), 1, '应解析出 1 条')

    def test_01_列表页取回失败_判本源失败(self):
        """B8 核心：fetch 返回 None 此前 return None（=成功）→ 水位推进 → 永久漏抓。"""
        r = _SourceRun(P, {self.u_bad: None})
        err, local = r.run([_lu(self.u_bad)])
        self.assertIsNotNone(err, '列表页取回失败必须判本源失败（水位不得推进）')
        self.assertIn('列表页取回失败', err)
        self.assertIn('broken', err, '报错须含失败 URL，便于定位')
        self.assertEqual(len(local), 0)

    def test_02_取回成功但0条_不判失败(self):
        """栏目真空/改版不是故障。若判失败会永久冻结全局水位闸门。"""
        r = _SourceRun(P, {self.u_empty: EMPTY_PAGE})
        err, local = r.run([_lu(self.u_empty)])
        self.assertIsNone(err,
                          f'0 条但取回成功不应判失败（会冻结全局水位）：{err}')

    def test_03_部分页失败仍判失败(self):
        """一个 list_url 挂了、另一个正常 → 仍须判失败（那一页的讲座会漏抓）。"""
        r = _SourceRun(P, {self.u_ok: GOOD_PAGE, self.u_bad: None})
        err, local = r.run([_lu(self.u_ok), _lu(self.u_bad)])
        self.assertIsNotNone(err, '部分列表页取回失败仍须判本源失败')
        # 已抓到的部分结果不浪费（严重-3 的既有约定）
        self.assertEqual(len(local), 1, '已成功页的解析结果应保留')

    def test_04_多页全失败_报错含计数(self):
        r = _SourceRun(P, {self.u_bad: None, 'http://t.scnu.edu.cn/b2/': None})
        err, local = r.run([_lu(self.u_bad), _lu('http://t.scnu.edu.cn/b2/')])
        self.assertIsNotNone(err)
        self.assertIn('2/2', err, '报错须给「失败/总页数」计数')

    def test_04b_配置页失败与推导页失败分别记账(self):
        """计数分母只数**配置里的 list_url**，推导页的 404 不进分子也不进分母。

        场景：配置 2 页（p1 正常、p2 本身 404），p1 的推导页 2.html 也404。
        期望：1/2（1 个配置页坏/ 共 2 个配置页）——推导页既不算坏、也不进分母。
        若分母误含推导页会得到 1/3，报错会让人误以为「有三页、坏了两个」。
        """
        u1, u2 = 'http://t.scnu.edu.cn/p1/', 'http://t.scnu.edu.cn/p2/'
        r = _SourceRun(P, {u1: GOOD_PAGE, u2: None})
        err, local = r.run([_lu(u1), _lu(u2)])
        self.assertIsNotNone(err, '配置页 p2 取回失败须判本源失败')
        self.assertIn('1/2', err, f'分母应只数配置页（推导页404 不计入）：{err}')

    def test_05_失败源不入库(self):
        """失败源的已抓结果照常返回给调用方合并，但水位由 main() 侧拦住。"""
        r = _SourceRun(P, {self.u_ok: GOOD_PAGE, self.u_bad: None})
        err, local = r.run([_lu(self.u_ok), _lu(self.u_bad)])
        self.assertIsNotNone(err)
        self.assertEqual(len(local), 1, '失败源仍应返回已抓到的部分结果')

    def test_06_推导出的翻页页失败不判本源失败(self):
        """第 2 页（由 _sequential_candidate **猜**出来的 /list/2.html）取回失败属正常。

        源站本来就没有第 2 页时它必然 404。若把它算作失败，几乎所有源都会被判失败
        → 全局水位永久冻结。test_00已锁「正常源不被误判」，这里锁反向：
        正常源判成功后，日志里应当出现过 [PAGE-404]（证明确实尝试过翻页）。
        """
        r = _SourceRun(P, {self.u_ok: GOOD_PAGE})
        err, local = r.run([_lu(self.u_ok)])
        self.assertIsNone(err, f'仅翻页页取回失败不得判本源失败：{err}')
        self.assertEqual(len(local), 1, '首页内容应正常入库')
        # 确实尝试过猜出的下一页（否则本用例就没测到东西）
        self.assertIn('http://t.scnu.edu.cn/list/2.html', r.fetched,
                      '未尝试推导翻页页——桩的行为变了，本用例失去意义')

    def test_07_失败不触发无限重试_pagination(self):
        """失败页不得被 _next_page_url/_sequential_candidate 反复重试。"""
        r = _SourceRun(P, {self.u_bad: None})
        err, _ = r.run([_lu(self.u_bad)])
        # 破页break 出双层循环，u_bad 只应被取一次
        self.assertEqual(r.fetched.count(self.u_bad), 1,
                         f'失败页被重复取回{r.fetched.count(self.u_bad)} 次（会拖慢每轮抓取）')


class GlobalWatermarkGuardTest(unittest.TestCase):
    """锁死「failed_sources 是全局闸门」这一前提——它决定 ② 为何不能判失败。

    若将来有人把闸门改成按源独立水位，本套件提醒重新评估分层的必要性。
    """

    def test_08_main_中失败源确实拦住全局水位(self):
        src = open(os.path.join(_ROOT, 'scraper', 'scraper.py'), encoding='utf-8').read()
        self.assertIn('if failed_sources:', src,
                      'main() 里已无 failed_sources 闸门——分层前提需重新评估')
        # 水位推进只在 else 分支（无失败）发生
        tail = src[src.rindex('if failed_sources:'):]
        self.assertIn("payload['last_scrape'] = since", tail,
                      '失败时不再沿用旧水位，水位冻结语义已变')
        self.assertIn("'last_scrape': now_iso", tail,
                      '成功时才推进水位；失败分支必须不写 last_scrape')

    def test_09_水位读取失败必须告警而非静默退化(self):
        """水位文件读坏 → 全量抓取（实测 3.9h），必须留痕。

        2026-10-02（Q1 拍板加）。此前这里是裸`except: since = None`，
        退化后 CI 日志零痕迹，只表现为「今天跑得特别久」。

        定位说明：server.py 也有同样的读取，但按其 `_warn` docstring 属
        「构造命令参数」应保持静默，且 scraper 会再兜底读一次同一文件——
        **本行才是决定「要不要全量重抓」的那处**，故只锁这里。
        """
        src = open(os.path.join(_ROOT, 'scraper', 'scraper.py'), encoding='utf-8').read()
        seg = src[src.index("since = json.load(open(last_scrape_path"):]
        seg = seg[:seg.index('is_incremental =')]
        self.assertIn('except Exception as e:', seg,
                      '水位读取的 except 必须绑定异常对象，否则无法告警')
        self.assertIn('[WARN]', seg,
                      '水位读取失败未告警——本轮会静默退化为全量抓取（实测 3.9h）')
        self.assertIn('全量抓取', seg,
                      '告警文案须点明「退化为全量」与耗时量级，否则读日志的人不会当回事')


class ListDateSwitchTest(unittest.TestCase):
    """B7：SCNU_LISTDATE_SKIP 曾是**假开关**（只管日志，真实过滤无条件执行）。

    危害不在于过滤本身，而在于「回退对照」做不到：设 SCNU_LISTDATE_SKIP=0
    想关掉过滤重放一遍，日志却打「过滤已开启」，排查者据此得出完全相反的结论。
    修法：开关值由 main() 下传为 _process_source 的 listdate_skip_enabled，
    真实跳过判据（_should_skip_by_item_date）与之联动。
    """

    U = 'http://t.scnu.edu.cn/list/'
    # 条目日期 2020-01-01，远早于水位 2026-01-01 → 开启过滤时应被跳过
    OLD_ITEM_PAGE = ('<html><body><li><a href="/a/202001/9.html">学术讲座通知</a>'
                     '<span>2020-01-01</span></li></body></html>')

    def _run(self, skip_enabled):
        r = _SourceRun(P, {self.U: self.OLD_ITEM_PAGE})
        err, local = r.run([_lu(self.U)], is_incremental=True,
                           cutoff_date_str='2026-01-01',
                           listdate_skip_enabled=skip_enabled)
        self.assertIsNone(err, f'本用例不测失败路径：{err}')
        return r, local

    def test_10_开关开启_旧条目被跳过(self):
        r, local = self._run(True)
        self.assertEqual(len(local), 0, '条目日期早于水位，开启过滤时不应解析')
        self.assertEqual(r.parsed, [], f'不该抓到详情页：{r.parsed}')

    def test_11_开关关闭_旧条目照抓(self):
        """这是 B7 的核心断言：关闭必须真的关掉（旧版无论如何都照跳）。"""
        r, local = self._run(False)
        self.assertEqual(len(local), 1,
                         'SCNU_LISTDATE_SKIP=0 必须让条目级过滤真的失效（回退对照）')
        self.assertEqual(r.parsed, ['http://t.scnu.edu.cn/a/202001/9.html'],
                         f'关闭过滤时详情页应被抓取并解析：{r.parsed}')

    def test_12_无水位日期时开关不影响抓取(self):
        """cutoff_date_str 为空（首轮/全量）本就不过滤，行为不得因开关而变。"""
        for flag in (True, False):
            r = _SourceRun(P, {self.U: self.OLD_ITEM_PAGE})
            err, local = r.run([_lu(self.U)], is_incremental=True,
                               cutoff_date_str=None,
                               listdate_skip_enabled=flag)
            self.assertIsNone(err)
            self.assertEqual(len(local), 1, f'开关={flag} 时无水位也应照抓')

    def test_13_main_确实把开关下传(self):
        """防止将来又退回「只打日志」：main 必须读一次 env 并传给 _process_source。"""
        src = open(os.path.join(_ROOT, 'scraper', 'scraper.py'), encoding='utf-8').read()
        self.assertIn('listdate_skip_on = _listdate_skip_enabled()', src,
                      'main() 未读 env 开关')
        self.assertIn("src_latest_date.get(src.get('name', ''), ''), listdate_skip_on)",
                      src, '_process_source 的提交未带上开关值')

    def test_14_默认参数为开启(self):
        """直接调 _process_source（不传该参数）必须保持历史行为（过滤开启）。"""
        import inspect
        sig = inspect.signature(P._process_source)
        self.assertIs(sig.parameters['listdate_skip_enabled'].default, True,
                      '默认值必须是 True，否则所有既有调用方的过滤行为会静默改变')


if __name__ == '__main__':
    unittest.main(verbosity=2)
