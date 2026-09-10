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


if __name__ == '__main__':
    unittest.main(verbosity=2)
