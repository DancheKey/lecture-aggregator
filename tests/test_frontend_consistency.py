#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
前端切片一致性守卫（2026-08-05 体检修复 中等-19/M5）。

背景：server.py 的 `_attach_unit_types` / `_load_excluded`（本地 /api/lectures 下发）
与 scripts/generate_frontend_data.py 的 `with_unit()` / `load_excluded()`（公网静态切片）
是两份「手工复制的实现」，代码注释声称二者必须严格一致，但此前没有任何测试守卫——
今天一致，下次单边修改就会静默漂移（本地下发与公网展示行为分叉）。

本测试用同一份数据分别跑两条路径，断言输出逐条相等：
  1) 合成小数据集（覆盖边界：同 URL 同天多场=session / 跨天=期 / 无日期 / 无 lectureIndex）；
  2) 真实 data/lectures.json（仓库内必有；全量回归）。

运行：python tests/test_frontend_consistency.py
"""
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load_module('gen_frontend', os.path.join(ROOT, 'scripts', 'generate_frontend_data.py'))
srv = _load_module('server_mod', os.path.join(ROOT, 'server.py'))
scr = _load_module('scraper_mod', os.path.join(ROOT, 'scraper', 'scraper.py'))


def _pipeline_gen(data, excluded):
    """generate_frontend_data 路径：过滤 excluded → 构 url_dates → with_unit。"""
    rows = [r for r in data if (r.get('sourceUrl') or '') not in excluded]
    url_dates = {}
    for item in rows:
        u = item.get('sourceUrl') or ''
        d = (item.get('lectureStart') or '')[:10]
        url_dates.setdefault(u, set())
        if d:
            url_dates[u].add(d)
    return [gen.with_unit(item, url_dates) for item in rows]


def _pipeline_srv(data, excluded):
    """server.py 路径：过滤 excluded → _attach_unit_types。"""
    rows = [r for r in data if (r.get('sourceUrl') or '') not in excluded]
    return srv._attach_unit_types(rows)


SYNTHETIC = [
    # 同 URL 同一天两场 → session（场）
    {'sourceUrl': 'http://a.scnu.edu.cn/x/1.html', 'lectureStart': '2026-09-01 15:00:00',
     'lectureIndex': 1, 'isMultiLecture': True, 'title': '同日第一场'},
    {'sourceUrl': 'http://a.scnu.edu.cn/x/1.html', 'lectureStart': '2026-09-01 19:00:00',
     'lectureIndex': 2, 'isMultiLecture': True, 'title': '同日第二场'},
    # 同 URL 跨天分期 → issue（期）
    {'sourceUrl': 'http://b.scnu.edu.cn/y/2.html', 'lectureStart': '2026-09-02 15:00:00',
     'lectureIndex': 1, 'isMultiLecture': True, 'title': '系列第一期'},
    {'sourceUrl': 'http://b.scnu.edu.cn/y/2.html', 'lectureStart': '2026-09-09 15:00:00',
     'lectureIndex': 2, 'isMultiLecture': True, 'title': '系列第二期'},
    # 无 lectureIndex：两条路径都必须原样透传（不附加 unitType）
    {'sourceUrl': 'http://c.scnu.edu.cn/z/3.html', 'lectureStart': '2026-09-03 10:00:00',
     'title': '普通单场讲座'},
    # 有 lectureIndex 但全组无日期：dates 为空集 → 两条路径都应判 issue
    {'sourceUrl': 'http://d.scnu.edu.cn/w/4.html', 'lectureIndex': 1,
     'isMultiLecture': True, 'title': '无日期分期'},
]


class ConsistencyTest(unittest.TestCase):

    def test_synthetic_pipelines_equal(self):
        """合成数据集：两条实现输出必须逐条相等（含 unitType 与透传行为）。"""
        excluded = set()
        got_gen = _pipeline_gen([dict(r) for r in SYNTHETIC], excluded)
        got_srv = _pipeline_srv([dict(r) for r in SYNTHETIC], excluded)
        self.assertEqual(len(got_gen), len(got_srv))
        for i, (e, a) in enumerate(zip(got_gen, got_srv)):
            self.assertEqual(e, a, f'合成用例第 {i} 条两条路径输出不一致：\ngen={e}\nsrv={a}')
        # 边界语义抽查（防两条实现「一起错」：显式锁定期望行为）
        self.assertEqual(got_gen[0].get('unitType'), 'session')   # 同天多场 → 场
        self.assertEqual(got_gen[2].get('unitType'), 'issue')     # 跨天分期 → 期
        self.assertNotIn('unitType', got_gen[4])                  # 无 lectureIndex → 不标注
        self.assertEqual(got_gen[5].get('unitType'), 'issue')     # 无日期 → 期（与现状一致）

    def test_excluded_filter_equal(self):
        """排除名单读取：三条路径必须返回相同集合（generate ↔ server ↔ scraper）。"""
        gen_ex = gen.load_excluded()
        srv_ex = srv.load_excluded()
        scr_ex = scr.load_excluded()
        self.assertEqual(gen_ex, srv_ex, 'generate ↔ server 排除名单不一致')
        self.assertEqual(gen_ex, scr_ex, 'generate ↔ scraper 排除名单不一致')

    def test_speaker_keys_semantics(self):
        """speakerKeys 语义显式断言（2026-09-10）。

        与上面几条的关系须知：test_synthetic_pipelines_equal 与
        test_real_data_pipelines_equal 的「逐条全字段相等」其实已经**隐式**覆盖了
        speakerKeys —— 只要两条实现都产出该字段，取值不同就会失败。那部分是有效的。
        但它只能证明「两边算得一样」，证明不了「算得对」：若某次把两侧同时改错
        （例如分隔符词表一起删掉、后缀表一起改坏），全字段比较会双双通过。
        本用例因此单独锁定取值语义，专门防这种「一起错」。

        期望语义（两条实现须完全一致）：
          单人      -> 1 键
          多人      -> 逐人各一键，且键中不含分隔符
          职称后缀  -> 剥除（'李明教授' -> '李明'）
          空 / None -> 空数组
        """
        cases = [
            ('张三', ['张三']),
            ('黄加耀, 刘轩奕', ['黄加耀', '刘轩奕']),
            ('魏文娅、傅承哲', ['魏文娅', '傅承哲']),
            ('张三、李四、王五', ['张三', '李四', '王五']),
            ('李明教授', ['李明']),
            ('', []),
            (None, []),
        ]
        for name, want in cases:
            got_gen = gen.speaker_keys(name)
            got_srv = srv._speaker_keys(name)
            self.assertEqual(got_gen, want,
                             f'generate.speaker_keys({name!r}) 期望 {want}，实际 {got_gen}')
            self.assertEqual(got_srv, want,
                             f'server._speaker_keys({name!r}) 期望 {want}，实际 {got_srv}')

    def test_speaker_keys_no_separator_leak(self):
        """真实数据回归：speakerKeys 的每一项都不得残留多人分隔符。

        防的是「拆了但没拆干净」——如 'A、B' 只剥掉首段分隔符却把 '、' 留在键里，
        会让前端讲者聚合视图多出脏键（同一讲者聚不到一起）。数据侧全量扫描。
        """
        if not os.path.exists(DATA_PATH):
            self.fail('缺少 data/lectures.json——本测试要求仓库内存在主数据')
        with open(DATA_PATH, encoding='utf-8') as f:
            raw = json.load(f)
        data = raw.get('data', []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
        self.assertTrue(data, 'data/lectures.json 为空，无法做 speakerKeys 回归')
        bad = []
        for r in data:
            for k in gen.speaker_keys(r.get('speaker')):
                if any(sep in k for sep in ('、', ',', '，', '/')):
                    bad.append((k, r.get('sourceUrl')))
        self.assertEqual(bad, [], f'speakerKeys 残留分隔符 {len(bad)} 例：{bad[:5]}')

    def test_real_data_pipelines_equal(self):
        """真实 data/lectures.json：两条实现输出必须逐条相等（全量回归）。"""
        if not os.path.exists(DATA_PATH):
            self.fail('缺少 data/lectures.json——本测试要求仓库内存在主数据')
        with open(DATA_PATH, encoding='utf-8') as f:
            raw = json.load(f)
        data = raw.get('data', []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
        self.assertTrue(data, 'data/lectures.json 为空，无法做一致性回归')
        excluded = gen.load_excluded()
        got_gen = _pipeline_gen([dict(r) for r in data], excluded)
        got_srv = _pipeline_srv([dict(r) for r in data], excluded)
        self.assertEqual(len(got_gen), len(got_srv),
                         '过滤 excluded 后条数不一致（两边排除名单语义分叉）')
        for i, (e, a) in enumerate(zip(got_gen, got_srv)):
            self.assertEqual(e, a, f'data/lectures.json 第 {i} 条两条路径输出不一致')

    def test_public_split_vs_local_inline(self):
        """刻意差异的锁定：公网切片剥离长文本并带桶号，本地 /api/lectures 保持内联。

        2026-09-28 长文本按需加载：公网首屏主分片剥离 speakerBio/abstract
        （这两项占前端 JSON 的 68%），改由 lectures/detail/detail_XX.json 按需取。
        server.py 是本地开发服务器、直连本机无带宽成本，**有意**保持内联
        （少一次请求、调试时能直接看到全文）。

        上面 test_real_data_pipelines_equal 比的是 with_unit / _attach_unit_types
        这一层，够不到 main() 里的长文本分离——若不另立本用例，两端在
        「长文本怎么下发」上分叉将完全无人察觉。故显式锁定：
        差异存在可以，差异漂移不行。
        """
        if not os.path.exists(DATA_PATH):
            self.fail('缺少 data/lectures.json——本测试要求仓库内存在主数据')
        with open(DATA_PATH, encoding='utf-8') as f:
            raw = json.load(f)
        data = raw.get('data', []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
        excluded = gen.load_excluded()
        rows = [r for r in data if (r.get('sourceUrl') or '') not in excluded]

        # 本地（server.py）路径：长文本内联，且不带桶号 b
        local = _pipeline_srv([dict(r) for r in rows], set())
        self.assertTrue(any(r.get('speakerBio') or r.get('abstract') for r in local),
                        '本地路径已无内联长文本——若是有意改为分离，请同步前端按需加载与本用例')
        self.assertFalse(any('b' in r for r in local), '本地路径不应带桶号 b（本地无 detail 分片可取）')

        # 公网（generate main）路径：长文本被剥离，桶号 b 可回指到 detail 桶
        public, buckets = gen.split_long_text(_pipeline_gen([dict(r) for r in rows], set()))
        with_text = [r for r in public if 'b' in r]
        self.assertTrue(with_text, '公网路径无任何条目带桶号 b——长文本分离疑似失效')
        for r in public:
            self.assertNotIn('speakerBio', r, '公网主分片仍内联 speakerBio，未剥离')
            self.assertNotIn('abstract', r, '公网主分片仍内联 abstract，未剥离')
        # 每条带 b 的记录，在其指向的桶里必须能按同一 key 取回原文（防键口径分叉）
        by_key = {}
        for b, bucket_rows in enumerate(buckets):
            for row in bucket_rows:
                by_key[row['key']] = (b, row)
        checked = 0
        for r in with_text:
            key = gen.lt_key(r)
            self.assertIn(key, by_key, f'桶号 b={r["b"]} 的条目在 detail 分片里查不到：{key}')
            b, row = by_key[key]
            self.assertEqual(b, r['b'], f'桶号不一致：条目标 {r["b"]}，实存 {b}（{key}）')
            checked += 1
        self.assertEqual(checked, len(by_key),
                         'detail 桶里有主分片不存在的孤儿条目（键口径与前端 _ltKey 分叉）')
        # 桶号须与前端 _ltKey 口径一致。
        # ⚠ 这里只断言 Python 侧；真正的跨语言比对在 tests/js/app_ltkey_consistency.js
        #   （执行真实的 site/app.data.js._ltKey 并与 lt_key 逐条比对）。前端实现在
        #   app.data.js 而非 app.js——随前端分片拆分迁移，旧注释写 app.js 会误导定位。
        self.assertEqual(gen.lt_key({'sourceUrl': 'u', 'lectureIndex': 3}), 'u#3')
        self.assertEqual(gen.lt_key({'sourceUrl': 'u'}), 'u#')
        # 跨语言门禁必须在两侧流水线里都跑（否则改 JS 不会被发现）
        ltkey_js = os.path.join(ROOT, 'tests', 'js', 'app_ltkey_consistency.js')
        self.assertTrue(os.path.exists(ltkey_js),
                        '缺少 tests/js/app_ltkey_consistency.js —— '
                        '_ltKey 与 lt_key 的跨语言一致性将无人守护')


class TestGeneratedArtifactsCovered(unittest.TestCase):
    """产物清单守卫：generate_frontend_data.py 产出的每类文件都必须被提交。

    背景（B10，2026-10-02 评审）：scripts/merge_remote.py 的 SITE_PATHS 是
    「本地手动合并」时要 git add 的产物清单，与 daily.yml 的目录级
    `git add -A data site` 语义等价但**手工维护**，天然会漂移。
    实测已漂移过：长文本分片（lectures/detail/*，2026-09-28 引入）与
    访问量快照（lectures/visits.json）都不在清单里 →
    提交后「新 chunks + 旧 detail」→ 公网展开简介/摘要读到上一轮内容，
    次日 daily.yml 才自愈，呈「偶发不一致」。

    本用例把「产物 ↔ 清单」的关系固化成断言：新增一类产物却忘了加清单即红。
    """
    MERGE_SCRIPT = os.path.join(ROOT, 'scripts', 'merge_remote.py')
    SITE = os.path.join(ROOT, 'site')

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(cls.MERGE_SCRIPT):
            raise unittest.SkipTest('merge_remote.py 是本地专用脚本（.gitignore），不存在')
        with open(cls.MERGE_SCRIPT, encoding='utf-8') as f:
            src = f.read()
        ns = {}
        # 只取 SITE_PATHS 常量，不执行整个模块（它 import git 相关依赖）
        start = src.index('SITE_PATHS = [')
        end = src.index(']', start) + 1
        exec(src[start:end], ns)
        cls.paths = ns['SITE_PATHS']

    def _covered(self, suffix):
        return any(p.endswith(suffix) or f'{suffix}*' in p or suffix in p
                   for p in self.paths)

    def test_40_五类主产物均在清单内(self):
        for name in ('lectures.json', 'latest.json', 'stats.json',
                     'chunks.json', 'chunk_*.json'):
            self.assertIn(name, ' '.join(self.paths),
                          f'{name} 未列入 SITE_PATHS')

    def test_41_长文本分片在清单内(self):
        """B10 核心：detail 桶与 manifest 必须能被 add（否则展开简介/摘要读到旧数据）。"""
        joined = ' '.join(self.paths)
        self.assertIn('lectures/detail/manifest.json', joined,
                      '长文本清单 manifest.json 未列入 SITE_PATHS')
        self.assertIn('lectures/detail/detail_', joined,
                      '长文本桶 detail_*.json 未列入 SITE_PATHS')

    def test_42_访问量快照在清单内(self):
        """B10 第二处漏项：页脚 busuanzi 降级数据源。"""
        self.assertIn('lectures/visits.json', ' '.join(self.paths),
                      'lectures/visits.json 未列入 SITE_PATHS')

    def test_43_清单里每类路径在磁盘上真实存在(self):
        """反向校验：清单不得残留已不存在的产物（如脚本被删/改名后留空壳）。"""
        import glob
        for p in self.paths:
            if '*' in p:
                base = os.path.join(ROOT, p)
                self.assertTrue(glob.glob(base), f'清单项 {p} 在磁盘上匹配不到任何文件')
            else:
                full = os.path.join(ROOT, p)
                # HTML 允许缺失（visits-trend 首次出片前）；JSON 产物必须存在
                if p.endswith('.html'):
                    continue
                self.assertTrue(os.path.exists(full), f'清单项 {p} 不存在（应删或改路径）')

    def test_44_实际产物目录被覆盖(self):
        """扫描 site/lectures/ 下的实际产物，确保每类都能被清单的通配符命中。

        本用例的价值已被证明：2026-10-02 首次运行时它当场扫出第三处漏项
        `last_run.json`（首页「更新于」的数据源），前两处（detail/、visits.json）
        则是肉眼复核时发现的。三次都是「清单漏项 → 静默不一致」，
        靠肉眼维护清单必然还会漏第四次，故固化为断言。
        """
        import glob
        import fnmatch
        d = os.path.join(self.SITE, 'lectures')
        if not os.path.isdir(d):
            self.skipTest('site/lectures/ 尚未生成')
        actual = []
        for f in os.listdir(d):
            full_rel = 'site/lectures/' + f
            if os.path.isdir(os.path.join(d, f)):
                # 目录本身不进清单（清单按文件粒度），只收其下的文件
                for g in os.listdir(os.path.join(d, f)):
                    actual.append(full_rel + '/' + g)
            else:
                actual.append(full_rel)
        uncovered = []
        for rel in actual:
            if not any(fnmatch.fnmatch(rel, p) for p in self.paths):
                uncovered.append(rel)
        self.assertEqual(uncovered, [],
                         f'以下实际产物未被 SITE_PATHS 覆盖：{uncovered}')

    def test_45_site根目录产物也被清单覆盖(self):
        """扫描 site/ 根目录的产物（lectures/ 之外的），确保都在 SITE_PATHS 内。

        test_44 只扫 lectures/ 子目录——2026-10-02 把访问量趋势拆成
        html/js/css/json 四个文件时就漏在了覆盖范围之外，git add 只会带上
        html，页面经 fetch 拿不到脚本而整页空白。故根目录也必须纳入。
        """
        import fnmatch
        actual = []
        for f in os.listdir(self.SITE):
            full = os.path.join(self.SITE, f)
            if os.path.isfile(full) and not f.startswith('.'):
                actual.append('site/' + f)
        # .nojekyll 等配置文件不需要被合并清单列出（不入库也不影响部署）
        skip = {'site/.nojekyll'}
        uncovered = [r for r in actual
                     if r not in skip
                     and not any(fnmatch.fnmatch(r, p) for p in self.paths)]
        # 只对「本项目生成的产物」断言：品牌图/样式等长期存在且已在清单或属静态资产
        # ⚠ 2026-10-10：site/style.css 已删除——它是被 Tailwind 预编译方案
        #   （vendor/tailwind.css）取代的遗留文件，全站无任何页面引用它，
        #   属「既不被加载、也不被 stamp」的孤儿资产。若日后重新引入，
        #   必须同时在 html 里引用它，否则本清单会重新把它列为待覆盖项。
        known_static = {
            'site/motto.png', 'site/motto.webp', 'site/scnu-emblem.png',
            'site/scnu-emblem.svg', 'site/site-title.png',
            'site/vendor/tailwind.css', 'site/vendor/vue.global.prod.js',
            'site/footer-counter.js', 'site/stats.js',
            'site/app.js', 'site/app.core.js', 'site/app.state.js',
            'site/app.computed.js', 'site/app.display.js', 'site/app.social.js',
            'site/app.data.js', 'site/app.admin.js',
        }
        missing = [u for u in uncovered if u not in known_static]
        self.assertEqual(missing, [],
                         f'site/ 根目录以下产物未被 SITE_PATHS 覆盖：{missing}')

    def test_46_访问量趋势四件套齐全(self):
        """拆分产物必须成套：只提交 html 会让页面 fetch 不到脚本而空白。"""
        for name in ('visits-trend.html', 'visits-trend.js',
                     'visits-trend.css', 'visits-trend-data.json'):
            p = os.path.join(self.SITE, name)
            self.assertTrue(os.path.exists(p), f'缺少产物 site/{name}')
            self.assertIn(name, ' '.join(self.paths),
                          f'site/{name} 未列入 SITE_PATHS（本地合并会漏提交）')


class TestExcludedUrlRecordScope(unittest.TestCase):
    """排除名单必须覆盖**合并来源**URL（2026-10-02）。

    背景：generate_frontend_data 与 server 此前都只比对 `r.get('sourceUrl')`，
    而跨源合并记录另有 `sources[].sourceUrl` 指向各来源页——主URL 未被排除、
    某个来源页在名单里时，整条记录照样上站，且前端「多来源」折叠里仍能跳到
    那个已被人工否决的页面，排除形同虚设。

    本类锁「任一关联 URL 命中即整条排除」这一语义，并锁定两个产出端
    （generate 与 server）都必须走记录级判定而非各自手写主URL比较。
    """

    EXCLUDED = {'http://a.com/x', 'http://b.com/y'}

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            '_excl', os.path.join(ROOT, 'scripts', 'excluded_urls.py'))
        self.ex = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ex)

    def test_50_纯主URL(self):
        self.assertTrue(self.ex.is_record_excluded(
            {'sourceUrl': 'http://a.com/x'}, self.EXCLUDED))
        self.assertFalse(self.ex.is_record_excluded(
            {'sourceUrl': 'http://c.com/z'}, self.EXCLUDED))

    def test_51_合并来源命中即排除(self):
        """主URL 未命中、来源命中 —— 修复前会漏放。"""
        rec = {'sourceUrl': 'http://c.com/z',
               'sources': [{'sourceUrl': 'http://a.com/x'}]}
        self.assertTrue(self.ex.is_record_excluded(rec, self.EXCLUDED),
                        '合并来源命中名单时整条应被排除')

    def test_52_合并来源都未命中则保留(self):
        rec = {'sourceUrl': 'http://c.com/z',
               'sources': [{'sourceUrl': 'http://d.com/w'}]}
        self.assertFalse(self.ex.is_record_excluded(rec, self.EXCLUDED))

    def test_53_边界_空值与尾斜杠(self):
        self.assertFalse(self.ex.is_record_excluded({}, self.EXCLUDED))
        self.assertFalse(self.ex.is_record_excluded({'sourceUrl': ''}, self.EXCLUDED))
        self.assertFalse(self.ex.is_record_excluded(
            {'sourceUrl': 'http://c.com/z', 'sources': None}, self.EXCLUDED))
        self.assertFalse(self.ex.is_record_excluded(
            {'sourceUrl': 'http://c.com/z', 'sources': [None, {}]}, self.EXCLUDED))
        self.assertTrue(self.ex.is_record_excluded(
            {'sourceUrl': 'http://a.com/x/'}, self.EXCLUDED), '尾斜杠应归一')

    def test_54_record_urls顺序稳定且去重(self):
        urls = self.ex.record_urls({
            'sourceUrl': 'http://c.com/z',
            'sources': [{'sourceUrl': 'http://a.com/x'}, {'sourceUrl': 'http://c.com/z'},
                        {'sourceUrl': 'http://a.com/x'}]})
        self.assertEqual(urls, ['http://c.com/z', 'http://a.com/x'],
                         '主 URL 在前、来源去重且保持原序')

    def test_55_两个产出端都用记录级判定(self):
        """generate_frontend_data 与 server 都不得再手写「只看主 URL」的比较。"""
        for rel in ('scripts/generate_frontend_data.py', 'server.py'):
            with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
                src = f.read()
            self.assertIn('is_record_excluded', src,
                          f'{rel} 未使用记录级排除判定（合并来源会漏放）')
            # 旧的写法必须消失
            code = '\n'.join(l for l in src.splitlines()
                             if not l.lstrip().startswith('#'))
            self.assertNotIn(
                "data = [r for r in data if (r.get('sourceUrl') or '').rstrip('/') not in excluded]",
                code,
                f'{rel} 仍在手写只比主 sourceUrl 的过滤')


if __name__ == '__main__':
    unittest.main(verbosity=2)
