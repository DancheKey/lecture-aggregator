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
import json
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scraper'))

DERIVED_KEYS = {'unitType', 'speakerKeys'}
_FRONTEND_FILES = ('site/index.html', 'site/app.js')
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
        """派生键（unitType/speakerKeys）不得变成解析器直接产出——
        若 parsers 开始直出，generate/server 的加工就不再单一。"""
        self.assertNotIn('unitType', self.produced)
        self.assertNotIn('speakerKeys', self.produced)


if __name__ == '__main__':
    unittest.main(verbosity=2)
