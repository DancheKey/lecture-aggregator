# -*- coding: utf-8 -*-
"""env 开关真值判定守卫（scraper/env_flags.py + 4 处调用点）。

背景：审计发现全仓 4 处 env 开关各写了一套关闭值集合，同一串输入在不同开关上
效果相反——用户写「想关掉」却把功能留着（'no' 在 LLM_TEXT 上开着、'False' 在
LISTDATE/LEDGER/JUDGE_SE 上开着），且这类失效是静默的：日志正常、数据正常，
只是行为与意图相反，事后极难归因。

统一后的关闭值集合 = {'0','false','no','off'}，大小写不敏感；空串/未设置走默认值。

调用点（改动前后的差异即本测试要锁的东西）：
    scraper/scraper.py:918    _listdate_skip_enabled()  SCNU_LISTDATE_SKIP
    scraper/scraper.py:950    _ledger_enabled()         SCNU_LEDGER_SKIP
    scraper/cited_judge.py:362 _se_enabled()             SCNU_JUDGE_SE
    scraper/parsers.py:1542    _text_llm_flag()          SCNU_LLM_TEXT / SCNU_LLM_RICH

注意：主模块位于 scraper/ 目录（无 __init__.py），且各模块用扁平 import
（from env_flags import ...），故需先把 scraper/ 加进 sys.path 再显式加载。
"""
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRAPER = os.path.join(_ROOT, 'scraper')
if _SCRAPER not in sys.path:
    sys.path.insert(0, _SCRAPER)

import env_flags  # noqa: E402


def _load(name, filename):
    path = os.path.join(_SCRAPER, filename)
    spec = importlib.util.spec_from_file_location('scraper.t_env.' + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _grep_lines(filename, needle):
    """返回包含 needle 的所有行（去重、保序），用于把失败信息限制在相关行。

    教训：断言直接对整份源码用 in，失败时 pytest 会把全文打进报告（parsers.py
    近 30 万字符，一条失败会淹没整个测试报告）。断言必须自带信息量边界。
    """
    path = os.path.join(_SCRAPER, filename)
    hits = []
    with open(path, encoding='utf-8') as f:
        for i, line in enumerate(f.read().splitlines(), 1):
            if needle in line:
                hits.append(f'{filename}:{i}: {line.strip()}')
    return '\n'.join(hits)


# 值 -> 期望真值。'1'/'true'/未设置/空串 为真；其余关。
_CLOSING = ('0', 'false', 'False', 'FALSE', 'no', 'No', 'NO', 'off', 'Off', 'OFF')
_OPENING = ('1', 'true', 'True', 'yes', 'on', 'enabled')


class EnvFlagsCoreTest(unittest.TestCase):
    """env_flags.flag / is_true 的值语义矩阵。"""

    def test_01_closing_values_all_false(self):
        for v in _CLOSING:
            self.assertFalse(env_flags.is_true(v), f'is_true({v!r}) 应为假')
            self.assertFalse(
                env_flags.flag('SCNU_X', env={ 'SCNU_X': v }),
                f'env={v!r} 应判定为关')

    def test_02_opening_values_all_true(self):
        for v in _OPENING:
            self.assertTrue(env_flags.is_true(v), f'is_true({v!r}) 应为真')
            self.assertTrue(
                env_flags.flag('SCNU_X', env={'SCNU_X': v}),
                f'env={v!r} 应判定为开')

    def test_03_unset_and_blank_fall_back_to_default(self):
        for default, want in (('1', True), ('0', False)):
            # 未设置
            self.assertEqual(env_flags.flag('SCNU_MISSING', default=default, env={}), want)
            # 空串 / 纯空白：视同未设置，不得因 '' 落在关闭值里而变成「关」
            for blank in ('', '   ', '\t'):
                self.assertEqual(
                    env_flags.flag('SCNU_X', default=default, env={'SCNU_X': blank}),
                    want, f'空值 {blank!r} 应回落默认 {default!r}')

    def test_04_surrounding_whitespace_tolerated(self):
        self.assertFalse(env_flags.flag('SCNU_X', env={'SCNU_X': ' 0 '}))
        self.assertFalse(env_flags.flag('SCNU_X', env={'SCNU_X': ' FALSE '}))
        self.assertTrue(env_flags.flag('SCNU_X', env={'SCNU_X': ' 1 '}))

    def test_05_default_flag_true_when_absent(self):
        self.assertTrue(env_flags.flag('SCNU_ABSENT', env={}))

    def test_06_env_none_reads_os_environ(self):
        key = 'SCNU_TEST_FLAG_TMP'
        os.environ.pop(key, None)
        try:
            self.assertTrue(env_flags.flag(key, default='1'))
            os.environ[key] = '0'
            self.assertFalse(env_flags.flag(key, default='1'))
        finally:
            os.environ.pop(key, None)

    def test_07_non_str_values_coerced(self):
        # 环境变量经 os.environ 必为 str，但 _load_dotenv() 走 JSON 解析时可能是
        # int/bool/None —— 不做 str() 会在 0/False 上崩或判错。
        for v, want in ((0, False), (1, True), (False, False), (True, True), (None, True)):
            self.assertEqual(env_flags.is_true(v), want, f'is_true({v!r})')


class CallSiteParityTest(unittest.TestCase):
    """4 处调用点对同一组输入必须给出一致判定（改动前正是这里分叉）。"""

    @classmethod
    def setUpClass(cls):
        cls.S = _load('scraper', 'scraper.py')
        cls.J = _load('cited_judge', 'cited_judge.py')
        cls.P = _load('parsers', 'parsers.py')

    def _all(self, value):
        """给定环境变量值，返回 4 处调用点的判定结果。"""
        saved = {k: os.environ.get(k)
                 for k in ('SCNU_LISTDATE_SKIP', 'SCNU_LEDGER_SKIP',
                           'SCNU_JUDGE_SE', 'SCNU_LLM_TEXT')}
        keys = saved.keys()
        try:
            for k in keys:
                os.environ[k] = value
            parsers_val = self.P._text_llm_flag('SCNU_LLM_TEXT', '1')
            return (self.S._listdate_skip_enabled(),
                    self.S._ledger_enabled(),
                    self.J._se_enabled(),
                    parsers_val)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_10_closing_values_disabled_everywhere(self):
        for v in _CLOSING:
            got = self._all(v)
            self.assertEqual(got, (False,) * 4, f'{v!r} 应在 4 处开关上全部关闭，实得 {got}')

    def test_11_opening_values_enabled_everywhere(self):
        for v in _OPENING:
            got = self._all(v)
            self.assertEqual(got, (True,) * 4, f'{v!r} 应在 4 处开关上全部开启，实得 {got}')

    def test_12_blank_keeps_default_enabled(self):
        for blank in ('', '  '):
            got = self._all(blank)
            self.assertEqual(got, (True,) * 4, f'空值 {blank!r} 应全保持默认开启')

    def test_13_regression_zero_still_closes(self):
        # 生产与 CI 现有全部调用点只用 '0'/'1'，统一后 '0' 语义必须零变化。
        self.assertEqual(self._all('0'), (False,) * 4)
        self.assertEqual(self._all('1'), (True,) * 4)


class NoDuplicateJudgementTest(unittest.TestCase):
    """防回退：调用点不得再各自写一套关闭值集合。"""

    FILES = ('scraper.py', 'cited_judge.py', 'parsers.py')

    def test_20_no_adhoc_false_set_remains(self):
        offenders = []
        for fn in self.FILES:
            path = os.path.join(_SCRAPER, fn)
            with open(path, encoding='utf-8') as f:
                lines = f.read().splitlines()
            for i, line in enumerate(lines, 1):
                if "not in ('0'" in line or 'not in ("0"' in line:
                    offenders.append(f'{fn}:{i}: {line.strip()}')
        self.assertEqual(offenders, [], '开关判定退回到各自内联关闭值集合：\n' +
                         '\n'.join(offenders))

    def test_21_every_callsite_delegates_to_env_flags(self):
        for fn in self.FILES:
            path = os.path.join(_SCRAPER, fn)
            with open(path, encoding='utf-8') as f:
                src = f.read()
            self.assertIn('env_flags', src, f'{fn} 未引用 env_flags')

    def test_22_parsers_keeps_its_own_dotenv_lookup(self):
        # 统一的是「值怎么算真」，不是「配置从哪读」。parsers 的 .env 回退是
        # 刻意设计（真实 env > 项目根 .env > 默认），不得被整体替换掉。
        src = open(os.path.join(_SCRAPER, 'parsers.py'), encoding='utf-8').read()
        self.assertIn('_load_dotenv().get(name)', src)
        # 只看 _text_llm_flag 函数体那一行所在行，避免全文 in 命中别处
        self.assertIn('env_flags.is_true', _grep_lines('parsers.py', 'env_flags.is_true'))

    def test_23_parsers_llm_module_constants_not_poisoned(self):
        # 导入 parsers 会按当时的 env 求值 _USE_LLM_TEXT/RICH；这里只要求它们是 bool，
        # 不断言具体值（值依赖 CI 是否注入 key 与 .env 内容）。
        p = _load('parsers_only', 'parsers.py')
        for name in ('_USE_LLM_TEXT', '_USE_LLM_RICH'):
            self.assertIsInstance(getattr(p, name), bool, name)


if __name__ == '__main__':
    unittest.main()
