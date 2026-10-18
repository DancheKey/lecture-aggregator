"""分层验证映射完整性门禁（2026-10-18 甲方案）。

背景：门禁涨到 677 pytest + 21 node 后，每次改动跑全套 + 纯净检出复跑，
单次验证执行成本 5-6 分钟。scripts/select_tests.py 把验证分三层
（改动中只跑相关组 / 推送前跑全套 / 纯净检出仅限触及 tests/.github/）。

本套件锁映射表本身不腐化——映射漏一项的后果是「该跑的没跑」，正是分层
方案唯一的新型风险，必须有门禁反漂移：

  ① 真实仓库视角：跑 check_mapping() 必须零问题（pytest 全覆盖 +
     路径全匹配 + 登记文件存在 + FULL_ONLY 不分裂 + node 套件依赖可提取）。
  ② 合成输入视角（不依赖 git，确定性）：未知文件 → ALL 安全默认；
     新增未映射源码/测试文件 → check 报问题。
  ③ 行为视角：典型改动的两层选择（PYTESTS + NODE）正确。

运行：python tests/test_layer_mapping.py（纯离线，无网络无密钥）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'scripts'))
import select_tests  # noqa: E402


class MappingIntegrityTest(unittest.TestCase):
    """① 真实仓库视角：跑 select_tests.check_mapping() 必须零问题。"""

    def test_01_真实仓库映射完整(self):
        problems = select_tests.check_mapping()
        self.assertEqual(problems, [],
                         '映射完整性问题（新增测试/源码忘了归组？）:\n  '
                         + '\n  '.join(problems))

    def test_02_每个测试文件都被覆盖(self):
        """静态复算 ① 的覆盖面，给出更直接的失败信息。"""
        test_files = sorted(
            'tests/' + f for f in os.listdir(os.path.join(select_tests.ROOT, 'tests'))
            if f.startswith('test_') and f.endswith('.py'))
        self.assertTrue(test_files, 'tests/ 里读不到任何测试文件')
        covered = set(select_tests.FULL_ONLY)
        for g in select_tests.GROUPS:
            covered.update(g['tests'])
        missing = [t for t in test_files if t not in covered]
        self.assertEqual(missing, [],
                         '测试文件未被任何组收录: %s' % missing)

    def test_03_FULL_ONLY不被子集组收录(self):
        subset = {t for g in select_tests.GROUPS if g['name'] != 'full'
                  for t in g['tests']}
        clash = [t for t in select_tests.FULL_ONLY if t in subset]
        self.assertEqual(clash, [],
                         'FULL_ONLY 又被子集组收录（元测试会被漏跑）: %s' % clash)

    def test_03b_每个node套件都提得出依赖(self):
        """node 映射零维护的代价：提不出依赖的套件必须显式登记 NODE_ALWAYS。

        当前真实仓库所有套件都能提取到依赖（NODE_ALWAYS 为空）——这条锁
        「现状」：将来谁写出读取模式逃逸现有规则的套件，这里先红，逼他
        改进 suite_deps 或 conscious 登记，而不是静默降级成永远全跑。
        """
        node_dir = os.path.join(select_tests.ROOT, select_tests.NODE_DIR)
        suites = sorted(f for f in os.listdir(node_dir) if f.endswith('.js'))
        self.assertTrue(suites, 'tests/js/ 里读不到任何套件')
        nodep = [s for s in suites if not select_tests.suite_deps(s)]
        self.assertEqual(nodep, [],
                         'node 套件提不出依赖（改进 suite_deps 或登记 NODE_ALWAYS）: %s'
                         % nodep)


class SyntheticAntiRotTest(unittest.TestCase):
    """② 合成输入视角：不依赖 git，确定性锁「漏映射必红 + 未知必全套」。"""

    def _real_test_files(self):
        return sorted(
            'tests/' + f for f in os.listdir(os.path.join(select_tests.ROOT, 'tests'))
            if f.startswith('test_') and f.endswith('.py'))

    def test_04_未知文件升级全套(self):
        py, groups = select_tests.select_pytests(['scraper/brand_new_module.py'])
        self.assertEqual(py, 'ALL',
                         '未知源码文件必须升级全套（安全默认），实得 %r 组=%r'
                         % (py, groups))
        self.assertEqual(select_tests.select_node(['scraper/brand_new_module.py']), 'ALL')

    def test_05_未映射的新测试文件报问题(self):
        problems = select_tests.check_mapping(
            tracked=['site/index.html'],
            test_files=self._real_test_files() + ['tests/test_brand_new.py'])
        self.assertTrue(any('test_brand_new' in p for p in problems),
                        '新增测试文件未归组时 check 必须报问题，实得 %r' % problems)

    def test_06_未映射的新源码文件报问题(self):
        problems = select_tests.check_mapping(
            tracked=['scraper/brand_new_module.py', 'site/index.html'],
            test_files=self._real_test_files())
        self.assertTrue(any('brand_new_module' in p for p in problems),
                        '新增源码文件未归组时 check 必须报问题，实得 %r' % problems)

    def test_07_干净输入零问题(self):
        problems = select_tests.check_mapping(
            tracked=['site/app.cloud.js', 'scraper/parsers.py', 'README.md'],
            test_files=self._real_test_files())
        self.assertEqual(problems, [],
                         '已映射的输入不应报问题（防 check 自身假红）: %r' % problems)


class PytestBehaviorSpotCheckTest(unittest.TestCase):
    """③a pytest 行为视角：典型改动的分组结果（防映射表被静默改松）。"""

    def test_08_云端改动命中cloud组(self):
        py, groups = select_tests.select_pytests(['site/app.cloud.js'])
        self.assertIn('cloud', groups)
        self.assertIn('tests/test_supabase_migration.py', py)
        # site/ 前缀同属 frontend 组——并集语义，两者都该在（app.cloud.js 改动
        # 会改版本戳，schema 门禁必须跑）
        self.assertIn('frontend', groups)
        self.assertIn('tests/test_frontend_schema.py', py)

    def test_09_解析器改动命中parser组(self):
        py, groups = select_tests.select_pytests(['scraper/parsers.py'])
        self.assertIn('parser', groups)
        self.assertIn('tests/test_parser_golden.py', py)
        self.assertIn('tests/test_speaker_normalize.py', py)
        # parser 组不该把云端/前端/server 测试拖进来（分层瘦身的收益点）
        self.assertNotIn('tests/test_supabase_migration.py', py)
        self.assertNotIn('tests/test_server.py', py)
        self.assertNotIn('tests/test_frontend_schema.py', py)

    def test_10_元改动一律全套(self):
        for f in ('tests/js/cloud_stats.js', 'tests/test_layer_mapping.py',
                  '.github/workflows/test.yml', 'scripts/select_tests.py',
                  'requirements.txt'):
            py, _ = select_tests.select_pytests([f])
            self.assertEqual(py, 'ALL', '%s 改动应触发全套，实得 %r' % (f, py))
            self.assertEqual(select_tests.select_node([f]), 'ALL')

    def test_11_纯文档无测试(self):
        for f in ('README.md', 'AGENTS.md', '.gitignore', '.zcodeignore'):
            py, _ = select_tests.select_pytests([f])
            self.assertEqual(py, 'NONE', '%s 是纯文档，应无测试，实得 %r' % (f, py))
            self.assertEqual(select_tests.select_node([f]), 'NONE')

    def test_12_运维工具无测试(self):
        py, groups = select_tests.select_pytests(['scripts/fix_psy_speaker_restore.py'])
        self.assertIn('tools', groups)
        self.assertEqual(py, 'NONE')
        self.assertEqual(select_tests.select_node(['scripts/fix_psy_speaker_restore.py']), 'NONE')

    def test_13_数据改动命中data组(self):
        py, groups = select_tests.select_pytests(['data/lectures.json'])
        self.assertIn('data', groups)
        self.assertIn('tests/test_data_reconciliation.py', py)

    def test_14_server改动不拖前端重装备(self):
        """frontend/server 拆组的收益点：site/*.js 改动不跑 29s 的 test_server。"""
        py, groups = select_tests.select_pytests(['site/app.social.js'])
        self.assertIn('frontend', groups)
        self.assertNotIn('server', groups)
        self.assertNotIn('tests/test_server.py', py)
        py2, groups2 = select_tests.select_pytests(['server.py'])
        self.assertIn('server', groups2)
        self.assertIn('tests/test_server.py', py2)

    def test_15_多组并集去重(self):
        py, groups = select_tests.select_pytests(['scraper/parsers.py', 'scraper/scraper.py'])
        self.assertIn('parser', groups)
        self.assertIn('crawl', groups)
        listed = py.split()
        self.assertEqual(len(listed), len(set(listed)), '并集后仍有重复测试文件')

    def test_16_多组并集包含双方测试(self):
        py, _ = select_tests.select_pytests(['scraper/parsers.py', 'data/lectures.json'])
        self.assertIn('tests/test_parser_golden.py', py)
        self.assertIn('tests/test_data_quality.py', py)


class NodeBehaviorSpotCheckTest(unittest.TestCase):
    """③b node 行为视角：机械依赖提取的选择结果。"""

    def test_17_云端改动只跑云端套件加站点套件(self):
        node = select_tests.select_node(['site/app.cloud.js'])
        self.assertIn('tests/js/cloud_stats.js', node)
        self.assertIn('tests/js/cloud_pagination.js', node)
        # app.cloud.js 在 site/ 下 → 依赖整站的套件也该跑（bundle 会执行它）
        self.assertIn('tests/js/app_bundle_smoke.js', node)

    def test_18_趋势页只跑趋势套件(self):
        node = select_tests.select_node(['scripts/gen_visits_trend.py'])
        self.assertEqual(node, 'tests/js/visits_trend_render.js')

    def test_19_解析器改动不跑node(self):
        """没有 node 套件依赖 parsers.py——前端 JS 行为与解析器正交。"""
        self.assertEqual(select_tests.select_node(['scraper/parsers.py']), 'NONE')

    def test_20_server改动不跑node(self):
        self.assertEqual(select_tests.select_node(['server.py']), 'NONE')

    def test_21_数据改动跑跨语言一致性套件(self):
        node = select_tests.select_node(['data/lectures.json'])
        self.assertIn('tests/js/app_time_placeholder.js', node)
        self.assertIn('tests/js/app_year_consistency.js', node)
        # 不该把纯前端套件拖进来
        self.assertNotIn('tests/js/cloud_stats.js', node)

    def test_22_依赖提取机械正确(self):
        """抽查提取器本身：套件自己声明的依赖必须被提出来。"""
        deps = select_tests.suite_deps('cloud_stats.js')
        self.assertIn('site/app.cloud.js', deps)
        deps = select_tests.suite_deps('visits_trend_render.js')
        self.assertIn('scripts/gen_visits_trend.py', deps,
                      'path.join(SITE, "..", "scripts", ...) 的 .. 必须归一化')
        deps = select_tests.suite_deps('app_ltkey_consistency.js')
        self.assertIn('scripts/frontend_fields.py', deps,
                      '内联 python 探针的 import 必须提成依赖')


if __name__ == '__main__':
    unittest.main(verbosity=2)
