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
        """排除名单读取：两条实现必须返回相同集合。"""
        self.assertEqual(gen.load_excluded(), srv._load_excluded())

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
