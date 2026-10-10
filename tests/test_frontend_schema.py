# -*- coding: utf-8 -*-
"""前端键 schema 守卫：site/ 前端引用的每个 `l.字段` 必须由数据生产方提供。

背景（2026-09-26 全局审计 P2-3）：前端读取的键（含可选键）此前无任何测试锁定，
产出方（parsers/hybrid/scraper/generate）删除或改名某字段时，前端会静默读到
undefined——本测试把「前端读到 undefined」变成 CI 可拦截项。

方法：从 site/index.html + site/app.js 提取 `l.KEY` 引用集合，断言其 ⊆
data/lectures.json 全库键并集 ∪ 派生键 {unitType, speakerKeys}
（二者由 generate_frontend_data 与 server._attach_unit_types 统一加工，
  行为已有 tests/test_frontend_consistency.py 锁定）。
"""
import glob
import hashlib
import json
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scraper'))

DERIVED_KEYS = {'unitType', 'speakerKeys', 'b'}
# 前端已按职责分片（app.core/state/computed/display/social/data/admin），
# 字段引用分散在各分片里——这里动态发现，避免再拆/再合时扫描清单过时导致假绿或假红。
_FRONTEND_FILES = ('site/index.html',) + tuple(
    sorted(os.path.relpath(p, _ROOT).replace('\\', '/')
           for p in glob.glob(os.path.join(_ROOT, 'site', 'app*.js')))
)
_LREF_RE = re.compile(r'\bl\.([A-Za-z_][A-Za-z0-9_]*)')


class FrontendSchemaGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.refs = set()
        for f in _FRONTEND_FILES:
            src = open(os.path.join(_ROOT, f), encoding='utf-8').read()
            cls.refs |= set(_LREF_RE.findall(src))
        produced = set()
        with open(os.path.join(_ROOT, 'data', 'lectures.json'), encoding='utf-8') as fh:
            for r in json.load(fh)['data']:
                produced |= set(r)
        cls.produced = produced

    def test_frontend_refs_have_producers(self):
        """前端引用的每个 l.字段都必须有生产方（全库任一记录出现过即算）。"""
        missing = sorted(self.refs - self.produced - DERIVED_KEYS)
        self.assertEqual(
            missing, [],
            f'前端引用了数据生产方不再提供的字段: {missing}——'
            f'要么恢复产出（parsers/hybrid/scraper/generate_frontend_data），'
            f'要么同步删除 site/ 前端引用')

    def test_refs_nonempty(self):
        """提取器有效性自检：引用集合非空且覆盖核心字段（防止正则失配假绿）。"""
        self.assertGreater(len(self.refs), 20)
        for core in ('title', 'topic', 'speaker', 'lectureStart', 'sourceUrl'):
            self.assertIn(core, self.refs)

    def test_derived_keys_still_derived(self):
        """派生键（unitType/speakerKeys/b）不得变成解析器直接产出——
        若 parsers 开始直出，generate/server 的加工就不再单一。"""
        for k in DERIVED_KEYS:
            self.assertNotIn(k, self.produced)


class ScriptVersionStampTest(unittest.TestCase):
    """index.html / stats.html 的 `?v=` 版本戳必须等于 **Git 里那份** JS 的 hash。

    背景（2026-10-02 两次误判后定案）：GitHub Pages 对未哈希的静态资源长期缓存，
    项目用「`?v=<sha256[:10]>`」做缓存破坏，只有
    `scripts/generate_frontend_data.py::stamp_script_version` 会重打。

    ⚠ **校验基准必须是 Git 里的内容，不是工作区文件。**
    本仓库 `site/*.js` 在工作区是 CRLF，入库时被 git 转成 LF（core.autocrlf），
    两者 sha256 必然不同：

        工作区 app.display.js (CRLF, 17511B) -> e72f3e2d48
        git 里 app.display.js  (LF,   17208B) -> fd8ba8ea49

    而 `stamp_script_version` 哈希的是**工作区文件**，Pages 发的是 **git 里那份**——
    于是页面上的版本号与实际下发的文件永远对不上，缓存破坏**完全失效**
    （回访用户持续加载旧 JS，且零报错）。

    首次发现时我误判成「改完 JS 忘了重打戳」，重打后本地校验全绿；
    实则本地校验用的也是工作区文件，同样是错的。真正判定必须看 git 内容。

    本用例按 git blob 校验（见 `_git_blob_hash`），并与工作区校验交叉对照，
    二者不一致即说明存在 CRLF/LF 转换——那正是本缺陷的成因。
    """

    PAGES = ('site/index.html', 'site/stats.html')
    SCRIPT_RE = re.compile(r'src="([\w./-]+\.js)\?v=([0-9a-f]{10})"')

    # ---- 2026-10-10 新增：全量反向断言（根治「清单漂移」）----
    #
    # 为什么需要它：上面那条 SCRIPT_RE **只匹配已带 ?v= 的引用**，所以
    # 「新资源漏打戳」对它完全不可见——漏戳的资源以裸 src="x.js" 出现，正则
    # 压根不匹配，测试静默通过。而孤儿检查（test_所有app分片都被引用）复用
    # 同一正则，同样漏。这正是 git 历史里两轮同源事故的根因：
    #   · f58e5fd 重打 app.admin.js 戳（门禁红）
    #   · daa11e1 趋势页导航改落生成脚本模板
    # 两次都是「改了 A 处、同步清单 B 处被遗忘」，且没有任何门禁能强制清单完整。
    #
    # 本断言反转为**由文件系统事实驱动**：扫描所有页面里的本地 src=/href=
    # 引用，凡指向 site/ 内的 .js/.css，都必须带 ?v= 且等于 git blob 的 hash。
    # 这样新增资源天然被覆盖，不需要维护任何清单。
    #
    # vendor/ 下的第三方库（tailwind.css / vue.global.prod.js）不在此列：
    # 它们不在 stamp 注册表里，按设计保持固定名 + CDN 缓存。
    ALL_PAGES = ('site/index.html', 'site/stats.html', 'site/visits-trend.html')
    # ⚠ 尾部只捕获 ?v= 部分，不要用 [^"']*\1 回溯收尾——那会跨过闭合引号
    # 继续吞到下一个属性（本项目 index.html 里 <link ... /> 紧接 <script，
    # 实测会把 vendor/tailwind.css 匹配成 ' />\n  <script defer src='）。
    REF_RE = re.compile(r'''(?:src|href)=(["'])([\w./-]+\.(?:js|css))\??(v=[^"']*)?\1''')

    @classmethod
    def _local_refs(cls):
        """返回 [(page, asset, ver_or_None)]，只含 site/ 内的本地 js/css 引用。"""
        refs = []
        for page in cls.ALL_PAGES:
            p = os.path.join(_ROOT, page)
            if not os.path.exists(p):
                continue
            html = open(p, encoding='utf-8').read()
            for _q, asset, ver in cls.REF_RE.findall(html):
                if asset.startswith('vendor/'):
                    continue
                if os.path.exists(os.path.join(_ROOT, 'site', asset)):
                    refs.append((page, asset, ver[2:] if ver else None))
        return refs

    def test_99_所有本地资源都必须打戳且版本号正确(self):
        """反向全量断言：凡被页面引用的本地 .js/.css，都必须带正确的 ?v=。

        这条测试取代「维护一份资源清单」的做法——新增分片/样式会被自动纳入校验，
        从此不再出现「加了资源忘了打戳、浏览器永久缓存旧版且零告警」。
        """
        refs = self._local_refs()
        self.assertTrue(refs, '未扫描到任何本地资源引用——正则或页面路径失效？')
        unstamped, mismatched = [], []
        for page, asset, ver in refs:
            if not ver:
                unstamped.append(f'{page} -> {asset}')
                continue
            rel = 'site/' + asset
            real = self._git_blob_hash(rel)
            if real is None:
                mismatched.append(f'{page} -> {asset}: 未入库（git 索引取不到）')
            elif ver != real:
                mismatched.append(
                    f'{page} -> {asset}: 戳={ver} 但 Git 实际={real}')
        self.assertEqual(
            unstamped, [],
            '以下本地资源被页面引用但**没有缓存版本号**——改动对回访用户不生效，'
            '且浏览器会长期缓存旧版：\n  ' + '\n  '.join(unstamped)
            + '\n修法：把它登记进 scripts/generate_frontend_data.py 的'
              ' stamp_script_version 调用并重跑该脚本。')
        self.assertEqual(
            mismatched, [],
            '以下资源的版本号与 Git 实际内容不符——缓存破坏失效：\n  '
            + '\n  '.join(mismatched)
            + '\n修法：重跑 scripts/generate_frontend_data.py 重打戳。')

    def test_98_孤儿资源检测(self):
        """反向：site/ 下的 .js/.css 若无任何页面引用，就是孤儿（改了也不生效）。

        2026-10-10 用它收掉 site/style.css——被 Tailwind 预编译取代的遗留文件，
        既不被加载也不被 stamp，纯属噪音。孤儿文件比缺戳更隐蔽：它看起来
        「存在且有内容」，但任何改动都不会反映到页面上。
        """
        referenced = {a for _p, a, _v in self._local_refs()}
        site_dir = os.path.join(_ROOT, 'site')
        orphans = []
        for fn in sorted(os.listdir(site_dir)):
            if not fn.endswith(('.js', '.css')) or fn in ('footer-counter.js',):
                continue
            # footer-counter.js 由三页共同加载，且不在 index/stats 的 ?v= 体系内，
            # 实际已在别处打戳（见 test_99 的引用集），故从孤儿判定中排除。
            if fn not in referenced:
                orphans.append(fn)
        self.assertEqual(
            orphans, [],
            f'site/ 下这些资源无任何页面引用（孤儿）：{orphans}——'
            '改了不会生效，请删除或补上引用。')

    def _html(self, page):
        p = os.path.join(_ROOT, page)
        if not os.path.exists(p):
            self.skipTest('%s 不存在' % page)
        with open(p, encoding='utf-8') as f:
            return f.read()

    @staticmethod
    def _git_blob_hash(rel):
        """取 Git 索引/HEAD 里该文件的真实字节 sha256[:10]；取不到返回 None。

        优先读工作区的 git 索引（`git show :path`），这样**未提交的改动也能校验**，
        避免"提交后才发现"的滞后暴露。
        """
        import subprocess
        r = subprocess.run(['git', 'show', ':' + rel], cwd=_ROOT,
                           capture_output=True)
        if r.returncode != 0 or not r.stdout:
            return None
        return hashlib.sha256(r.stdout).hexdigest()[:10]

    def test_版本戳与git内容一致(self):
        for page in self.PAGES:
            html = self._html(page)
            tags = self.SCRIPT_RE.findall(html)
            self.assertTrue(tags, '%s 里没有任何带 ?v= 的 script 引用——'
                                  '缓存破坏机制可能已被移除' % page)
            for name, ver in tags:
                rel = 'site/' + name
                real = self._git_blob_hash(rel)
                self.assertIsNotNone(real, '%s 引用了不存在/未入库的 %s' % (page, name))
                self.assertEqual(
                    ver, real,
                    '%s 里 %s 的版本戳是 %s，但 **Git 里**的实际 hash 是 %s——'
                    'Pages 下发的是 git 那份，浏览器会按版本戳从 CDN 缓存拿旧文件。'
                    '\n修法：见 scripts/generate_frontend_data.py::stamp_script_version'
                    ' ——它必须哈希 Git 内容（或仓库统一为 LF），'
                    '否则重打多少次都对不上。'
                    % (page, name, ver, real))

    def test_工作区与git换行一致(self):
        """工作区与 Git 内容不应存在 CRLF/LF 差异。

        这是版本戳对不上的**根因**：stamp 哈希工作区（CRLF）、Pages 发 Git（LF），
        两者永远不同。凡是入库的 site/*.js 有此差异，缓存破坏即失效。
        这里给出明确报错，而非让维护者去比对两个毫无关联的 hash。
        """
        import subprocess
        diff = []
        for f in sorted(os.listdir(os.path.join(_ROOT, 'site'))):
            if not f.endswith('.js'):
                continue
            rel = 'site/' + f
            blob = subprocess.run(['git', 'show', ':' + rel], cwd=_ROOT,
                                  capture_output=True).stdout
            local_p = os.path.join(_ROOT, rel)
            if not os.path.exists(local_p) or not blob:
                continue
            with open(local_p, 'rb') as fh:
                local = fh.read()
            if local != blob:
                diff.append('%s（工作区 %dB / git %dB）'
                            % (rel, len(local), len(blob)))
        self.assertEqual(
            diff, [],
            '工作区与 Git 索引内容不一致（通常是 CRLF/LF 转换）：\n  '
            + '\n  '.join(diff)
            + '\n影响：stamp_script_version 哈希的是工作区文件，而 Pages 下发 git 那份，'
              '两者永远不同 → ?v= 缓存破坏完全失效。'
              '\n修法：① 让 stamp 按 git 内容哈希；或 ② 仓库内统一使用 LF'
              '（如在 .gitattributes 里为 site/*.js 固定 eol=lf，并重新 checkout）。')

    def test_所有app分片都被引用(self):
        """反向：site/app*.js 都要被 index.html 引用，否则是孤儿文件。"""
        html = self._html('site/index.html')
        referenced = {os.path.basename(n) for n, _ in self.SCRIPT_RE.findall(html)}
        site = os.path.join(_ROOT, 'site')
        for f in sorted(os.listdir(site)):
            if not re.fullmatch(r'app[\w.]*\.js', f):
                continue
            self.assertIn(f, referenced,
                          'site/%s 存在但没被 index.html 引用——'
                          '它是孤儿分片（改了也不会生效）' % f)


if __name__ == '__main__':
    unittest.main(verbosity=2)
