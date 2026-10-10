#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心模块**可编译性**门禁（零外部依赖、确定性、无副作用）。

## 为什么需要它

2026-10-02 一次批量编辑把 `_stats_dirty = {...}` 误写成 `def _stats_dirty = {...}`
（赋值语句前多了 `def`），server.py 直接 SyntaxError。
这类错误的传播路径极短：server.py 是**本地演示服务器**，语法错误不会触发任何
既有测试——

  · tests/test_server.py 靠 `importlib` 加载 server.py，import 就抛，
    但它把异常当「模块加载失败」向上传播，测试仍以 error 形式结束，
    信息淹没在 traceback 里；
  · CI 的 gate 只在 **push 到 main 后**才跑，而 server.py 是本地脚本，
    公网部署根本不 import 它 → 语法错误可以静默存活数周，
    直到维护者下次本地起服务才发现。

`py_compile` 是纯语法层检查：不做导入、不执行顶层代码、不产生 __pycache__
（用 `cfile=os.devnull`），因此可以在任何阶段安全运行。

## 判据

对仓库内全部**入库的核心模块**逐个 `compile()`：
语法错误即红，报出文件名与行号。覆盖范围刻意包含 CI 不 import、只有本地用的
server.py——那正是最容易悄悄坏掉的一类。
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 全部入库核心模块。scripts/ 下被 .gitignore 刻意忽略的一次性脚本
# （backfill_*/fix_*/probe_* 等）不入库、CI 根本没有，不能列入清单；
# 它们语法坏掉只会让人本地扑空，但那是本地维护责任，非流水线范畴。
CORE_MODULES = [
    'server.py',
    'scraper/scraper.py',
    'scraper/parsers.py',
    'scraper/timeparse.py',
    'scraper/field_vocab.py',
    'scraper/hybrid.py',
    'scraper/cited_judge.py',
    'scraper/llm_provider.py',
    'scraper/llm_cache.py',
    'scraper/env_flags.py',
    'scripts/generate_frontend_data.py',
    'scripts/frontend_fields.py',
    'scripts/excluded_urls.py',
    'scripts/test_invariants.py',
    # 2026-10-04 入库（移出 .gitignore）：CI 守卫 test_time_fixes_20261004 按路径
    # 加载它做体检新档的断言，文件必须随仓库走，语法坏掉也要在编译门禁先红。
    'scripts/audit_data_quality.py',
]


class CompileGateTest(unittest.TestCase):
    def _iter_targets(self):
        for rel in CORE_MODULES:
            yield rel
        # tests/ 下的全部用例（语法坏掉的测试会以 error 形式炸出，掩盖真实失败）
        tdir = os.path.join(ROOT, 'tests')
        for f in sorted(os.listdir(tdir)):
            if f.endswith('.py'):
                yield 'tests/' + f

    def test_all_core_modules_compile(self):
        bad = []
        for rel in self._iter_targets():
            path = os.path.join(ROOT, rel.replace('/', os.sep))
            if not os.path.exists(path):
                bad.append((rel, '文件不存在（清单需同步）'))
                continue
            with open(path, encoding='utf-8') as f:
                src = f.read()
            try:
                # cfile=os.devnull：只做语法分析，不落 __pycache__（保持工作区干净）
                compile(src, rel, 'exec')
            except SyntaxError as e:
                bad.append((rel, f'L{e.lineno}: {e.msg}'))
            except ValueError as e:
                # 例如源码含 NUL 字节
                bad.append((rel, str(e)))
        self.assertEqual(
            [], bad,
            '核心模块存在语法错误（语法坏掉不会被任何功能测试捕获，'
            'server.py 尤其如此——CI 不 import 它）：\n'
            + '\n'.join(f'  {r}: {m}' for r, m in bad))

    def test_server_imports_cleanly(self):
        """server.py 额外做一次真实 import（顶层只读磁盘、不起服务）。

        覆盖 py_compile 抓不到的错误：模块级 NameError、循环 import、
        顶层代码抛异常等。__main__ 守卫保证不会真的监听端口。
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location('_srv_compile_gate',
                                                      os.path.join(ROOT, 'server.py'))
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:                       # noqa: BLE001 - 任何异常都要报出来
            self.fail(f'server.py 导入失败（本地起服务会直接不可用）：'
                      f'{type(e).__name__}: {e}')
        # 关键运行时符号必须存在（改动时误删/误改名的兜底）
        for name in ('Handler', '_stat_lock', '_flush_stats', '_mark_dirty',
                     '_stats_dirty', 'main'):
            self.assertTrue(hasattr(mod, name), f'server.py 缺少符号 {name}')


class RobotsBeforeAllowlistTest(unittest.TestCase):
    """robots 查询必须排在域名白名单**之后**（2026-10-02 修复）。

    原实现先查 robots 再验白名单，于是列表页里出现的任意外站/内网链接
    （含 http://169.254.169.254/ 这类云元数据地址）都会先触发一次
    `GET {scheme}://{host}/robots.txt` 才被白名单拒掉——构成盲 SSRF 探测
    （响应不回传故读不到数据，但请求确实发出，且打扰被注入的第三方站点）。

    本用例直接断言**不发生任何网络请求**，而不是断言调用顺序——
    顺序改一改就会失效，行为断言才稳。
    """

    def _fetch_with_recorder(self, url, allowed):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            '_scr_' + str(abs(hash(url)) % 100000),
            os.path.join(ROOT, 'scraper', 'scraper.py'))
        S = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(S)
        calls = []

        def fake_get(u, **kw):
            calls.append(u)
            raise RuntimeError('network')
        S.requests.get = fake_get
        S._ROBOTS_CACHE.clear()
        try:
            S.fetch(url, _retries=1, allowed_domains=allowed)
        except Exception:
            pass
        return calls

    def test_60_白名单外不发起任何请求(self):
        for url in ('http://evil.example.org/x.html',
                    'http://169.254.169.254/latest/meta-data/',
                    'https://internal.corp/secret'):
            calls = self._fetch_with_recorder(url, ['scnu.edu.cn'])
            self.assertEqual(calls, [],
                             f'{url} 在白名单外却发起了 {len(calls)} 次请求：{calls}')

    def test_61_白名单内仍正常查robots(self):
        """白名单内不得被短路——robots 合规是本分存在的目的。"""
        calls = self._fetch_with_recorder(
            'http://psy.scnu.edu.cn/a/b.html', ['scnu.edu.cn'])
        self.assertTrue(any('robots.txt' in c for c in calls),
                        '白名单内 URL 应照常查询 robots.txt（合规检查被误短路了）')

    def test_62_未传白名单时保持原行为(self):
        """allowed_domains 为 None 的调用（若有）不得被新参数影响。"""
        calls = self._fetch_with_recorder('http://x.scnu.edu.cn/a.html', None)
        self.assertTrue(any('robots.txt' in c for c in calls),
                        '未传白名单时应照旧查 robots')


class ScriptEntrypointTest(unittest.TestCase):
    """脚本入口必须把返回值交给退出码（2026-10-02）。"""

    @staticmethod
    def _read(rel):
        with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
            return f.read()

    def test_44_脚本入口须传递退出码(self):
        """静态锁：`sys.exit(main())` 而不是裸 `main()`。

        audit_fields.py 此前是 `main()`——Python 会丢弃 return 值，函数里
        `return 1` 对进程退出码毫无影响，使 --fail-on 的判定形同虚设
        （2026-10-02 修复时踩到）。
        """
        import ast
        tree = ast.parse(self._read('scripts/audit_fields.py'))
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            for sub in ast.walk(node):
                if not (isinstance(sub, ast.Expr) and isinstance(sub.value, ast.Call)):
                    continue
                fn = sub.value.func
                name = getattr(fn, 'id', '') or getattr(fn, 'attr', '')
                if name == 'main':
                    bad.append(sub.lineno)
        self.assertEqual(bad, [],
                         f'audit_fields.py:{bad} 裸调用 main() 会丢弃退出码，'
                         f'须写成 sys.exit(main())')

    def test_45_audit_fields支持fail_on(self):
        """audit_fields.py 必须提供 --fail-on（否则 CI 无从阻断）。"""
        src = self._read('scripts/audit_fields.py')
        self.assertIn('--fail-on', src, '缺少 --fail-on 参数')
        self.assertIn('::error::', src,
                      'fail-on 触发时应发 GitHub Actions 错误注解')

    def test_46_体检基线文件存在且合法(self):
        """daily.yml 的基线对比依赖 data/audit_baseline.json。

        缺失时 daily.yml 会把「本次全部高危问题」当成新增而误阻断一次
        （首次运行必然如此），故基线须随仓库入库。
        2026-10-10：version 从 1 升为 2——旧 key 归一化只按全角「（」切，
        ASCII 括号/冒号/空格分隔的实例值切不动，导致实例级键（讲者名/日期
        直接进键）双向出错（误报阻断部署 + 漏报静默放行）。v2 改为在
        「冒号/全角括号/空格-半角括号」最左出现处截断，只保留模板部分。
        """
        p = os.path.join(ROOT, 'data', 'audit_baseline.json')
        if not os.path.exists(p):
            self.skipTest('基线文件尚未生成（首次运行后会自动写入）')
        with open(p, encoding='utf-8') as f:
            payload = json.load(f)
        self.assertIn('high', payload, '基线须含 high 字段')
        self.assertIsInstance(payload['high'], dict)
        self.assertEqual(payload.get('version'), 2,
                         '基线 version 应为 2（key 归一化修正，见 daily.yml）')

    def test_47_测试不得在导入期改写全局缓存(self):
        """防「夹具被测试写脏」（2026-10-02 实踩）。

        本机.env 有 B 模型 key 时，golden/snapshot 会真实调模型；若某个缓存写入
        路径没被 setUp 里的桩覆盖，结果会落进 **git 跟踪的夹具**
        `tests/fixtures/vlm_cache.json`。实测该文件从 1 条涨到 24 条——污染后每次
        跑用例的缓存命中集合都不同，且内容随模型输出漂移，排查成本极高。

        正确做法是 setUp/tearDown 成对打桩（用完还原）；模块导入阶段赋值则对全
        进程常驻，既污染夹具、也会让同进程其他测试（如 test_llm_cache 的并发写）
        全部落进空桩。本用例把「只能在 setUp/tearDown 里打桩」变成硬约束。
        """
        import ast
        bad = []
        for rel in ('tests/test_parser_golden.py', 'tests/test_parser_snapshot.py'):
            path = os.path.join(ROOT, rel)
            with open(path, encoding='utf-8') as f:
                tree = ast.parse(f.read())
            for node in tree.body:          # 模块级语句（不在函数/类体内）
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AugAssign):
                    targets = [node.target]
                for t in targets:
                    if isinstance(t, ast.Attribute) \
                            and isinstance(t.value, ast.Name) and t.value.id == '_LC' \
                            and t.attr in ('cache_path', 'cache_set'):
                        bad.append((rel, node.lineno, t.attr))
        self.assertEqual(
            bad, [],
            '这些模块在**导入阶段**改写了 llm_cache 全局状态：\n'
            + '\n'.join(f'  {r}:{ln} _LC.{a} = ...' for r, ln, a in bad)
            + '\n后果：① 本机有模型 key 时会把结果写进 git 跟踪的'
              ' tests/fixtures/vlm_cache.json（夹具污染）；'
              '② 同进程其他测试的缓存桩被永久改写。'
              '\n修法：移到该测试类的 setUp/tearDown 里成对打桩/还原。')

    def test_48_夹具不得被测试写脏(self):
        """git 跟踪的 VLM 夹具内容必须与 HEAD 一致（防污染静默扩散）。

        上一条锁「不得在导入期改写全局状态」，本条兜住漏网：直接比对夹具内容与
        仓库基线——即使污染发生在别的路径（如某个脚本误写），也会在此变红。
        """
        import subprocess
        rel = 'tests/fixtures/vlm_cache.json'
        cur = os.path.join(ROOT, rel)
        if not os.path.exists(cur):
            self.skipTest('夹具不存在')
        head = subprocess.run(['git', 'show', 'HEAD:' + rel], cwd=ROOT,
                              capture_output=True)
        if head.returncode != 0:
            self.skipTest('该夹具尚未入库（首次提交前无法比对）')
        try:
            a = json.loads(head.stdout.decode('utf-8'))
            b = json.loads(self._read(rel))
        except ValueError:
            self.skipTest('夹具非合法 JSON，交由其它用例报错')
        self.assertEqual(set(b.keys()), set(a.keys()),
                         f'{rel} 的键集与 HEAD 不一致——'
                         f'新增 {sorted(set(b) - set(a))[:3]}、'
                         f'缺失 {sorted(set(a) - set(b))[:3]}。'
                         f'该文件是 git 跟踪的测试夹具，'
                         f'本机有模型 key 时测试会把真实调用结果写进来。'
                         f'修法：git checkout -- {rel}，并检查缓存写入路径是否被打桩。')

    def test_49_daily依赖完整性(self):
        """daily.yml 装的依赖必须覆盖它调用的脚本的 import 需求（防将来分裂）。

        背景（2026-10-02）：daily.yml 只 `pip install -r scraper/requirements.txt`，
        而根 requirements.txt（含 ruamel）**不被 daily 安装**。当前不炸，因为
        daily 调用的 6 个脚本都不 import server.py。但这是**潜伏风险**：哪天有人
        在 scripts/ 里 import server（或 scraper 侧新增 ruamel 用法），
        每日 cron 会在无人值守时 ImportError 红掉，且只在 Actions 日志里可见。

        本用例不依赖「谁 import 谁」的静态猜测，而是直接比对：daily 声明安装的
        依赖集，是否覆盖 daily 调用的脚本**实际 import**的第三方包。
        """
        import re
        import subprocess as sp
        wf = os.path.join(ROOT, '.github', 'workflows', 'daily.yml')
        text = self._read(os.path.relpath(wf, ROOT).replace('\\', '/')) \
            if os.path.exists(wf) else ''
        if not text:
            self.skipTest('daily.yml 不存在')
        m = re.search(r'pip install -r ([\w./-]+\.txt)', text)
        self.assertIsNotNone(m, 'daily.yml 未找到 pip install -r 语句')
        req_files = re.findall(r'-r ([\w./-]+\.txt)', text)
        installed = set()
        for rf in req_files:
            p = os.path.join(ROOT, rf)
            self.assertTrue(os.path.exists(p), f'daily.yml 引用了不存在的 {rf}')
            with open(p, encoding='utf-8') as f:
                for line in f:
                    line = line.split('#')[0].strip()
                    if line:
                        installed.add(re.split(r'[=<>!]', line)[0].strip().lower())

        # daily 调用的脚本 → 其第三方 import
        scripts = set(re.findall(r'python ([\w./-]+\.py)', text))
        stdlib_ok = set(getattr(sys, 'stdlib_module_names', None)
                        or sys.builtin_module_names)
        # 本仓库内的模块（含 scraper/ 与 scripts/ 下的同目录文件）
        local_mods = {'scraper', 'scripts', 'server', 'tests'}
        for sub in ('scraper', 'scripts'):
            d = os.path.join(ROOT, sub)
            if os.path.isdir(d):
                local_mods |= {os.path.splitext(f)[0] for f in os.listdir(d)
                               if f.endswith('.py')}
        # import 名与 PyPI 包名不一致的映射（import X 实为安装 Y）
        alias = {'bs4': 'beautifulsoup4', 'yaml': 'pyyaml',
                 'ruamel': 'ruamel.yaml', 'PIL': 'pillow',
                 'cv2': 'opencv-python-headless', 'sklearn': 'scikit-learn',
                 'dotenv': 'python-dotenv'}
        # requirements 包名里 `-` 与 `_` 互通（PEP 503 规范化），
        # 故比对时统一把两种符号都换成下划线。
        def norm(pkg):
            return pkg.lower().replace('-', '_')

        installed_norm = {norm(i) for i in installed}
        missing = {}
        for s in sorted(scripts):
            p = os.path.join(ROOT, s)
            if not os.path.exists(p):
                continue
            with open(p, encoding='utf-8') as f:
                src = f.read()
            for mod in re.findall(r'^\s*(?:import|from)\s+([a-zA-Z_][\w.]*)',
                                  src, re.M):
                top = mod.split('.')[0].lower()
                if top in stdlib_ok or top in local_mods:
                    continue
                pkg = alias.get(top, top)
                if norm(pkg) in installed_norm or norm(top) in installed_norm:
                    continue
                missing.setdefault(s, set()).add(pkg)
        self.assertEqual(
            {k: sorted(v) for k, v in missing.items()}, {},
            'daily.yml 调用的脚本 import 了未在 daily 安装的第三方包：\n'
            + '\n'.join(f'  {k}: {sorted(v)}' for k, v in missing.items())
            + '\ndaily.yml 只装了 ' + ', '.join(req_files)
            + '；缺包会在无人值守的 cron 里 ImportError。'
              '修法：把该包加进 scraper/requirements.txt，'
              '或让 daily.yml 额外 `-r requirements.txt`。')

    def test_50_模型波动容错条件不得过严(self):
        """防「容错条件写错 → 形同虚设」（2026-10-02 实踩）。

        test_parser_golden 有一个针对 B 模型输出波动的 skip 容错：断言失败且消息
        含「讲座一」时改为 skip。但首版条件写成 `'讲座一' in msg and 'location' in msg`，
        而 `assertNotIn('讲座一', value)` 的失败消息**只有**：

            '讲座一' unexpectedly found in '<实际值>'

        根本不含字段名 'location' → 条件永不成立，容错形同虚设（该轮仍判红）。
        这类错误不会抛异常、不会让任何用例变红，只能靠人发现，故固化为断言。
        """
        src_path = os.path.join(ROOT, 'tests', 'test_parser_golden.py')
        with open(src_path, encoding='utf-8') as f:
            src = f.read()
        m = re.search(r"except AssertionError as e:(.*?)\n\s+raise", src, re.S)
        self.assertIsNotNone(m, '未找到 psy961 的波动容错分支')
        branch = m.group(1)
        if '讲座一' not in branch:
            self.skipTest('该容错分支已被移除或改名，无需检查')
        # 条件里不得再要求出现字段名——assertNotIn 的消息不含它
        self.assertNotIn("'location' in msg", branch,
                         "容错条件要求消息含 'location'，但 assertNotIn 的失败消息"
                         '不含字段名 → 条件永不成立，容错形同虚设')
        self.assertNotIn('"location" in msg', branch,
                         "容错条件要求消息含 'location'，但 assertNotIn 的失败消息"
                         '不含字段名 → 条件永不成立，容错形同虚设')


if __name__ == '__main__':
    unittest.main(verbosity=2)