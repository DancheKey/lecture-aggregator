#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「按地点分流」折叠闸门回归（2026-11-11 用户拍板）。

背景
----
旧规则只有一条：`同源同日 >= FORUM_MIN(4)` 就折成一张 forum 合并卡，而合并卡
模板（`site/index.html`）**只渲染 head.location**（首场地点）。对真正的论坛 /
大会（同一场地、不同人轮番讲）这是对的；但 `yjsy/10291`「勷勤论坛一周活动预告」
只是把一周 10 场独立活动列在一页，11-11 / 11-12 各 4 场且时间、地点、校区
全不同 —— 折起来后另外 3 个地点被藏进展开清单，访客反而看不清在哪办。

用户拍板四条：
  ① 同源同日 >= 4 场仍是折叠候选；
  ② 组内「归一后不同有效地点数 >= 2」→ 整组逐场展开，不折叠；
  ③ 地点为空 /「待定」不提供信息，不参与制造差异（即默认折叠）；
  ④ 历史多地点组一并展开，不做时间特判；统计侧同步按同口径逐场计数。

本套件守护的真正风险
--------------------
`FORUM_MIN` 口径散在 5 处：`app.computed.js` 的 units() 与 filtered()、
`app.social.js::migrateCountKeys()`、生成器 `forum_stat_groups()` 与页面级虚拟
索引。2026-10-27 阈值 3→4 时漏改 app.social.js，造出 13 组「伪造已赞」。
故本轮把「多地点组黑名单」收敛为 **Python 侧唯一计算**，以记录级 `vf: 0`
下发前端；前端三处只读标记、不复算地点，统计侧复用同一份结果。

这里既测判据本身（归一 / 阈值 / 占位 / 分组），也测两条下发路径与统计侧的
同步，并用静态源码守卫把「前端又自己写了一套地点比较」变成红灯。

运行：python tests/test_venue_fold.py（纯离线）
"""
import importlib.util
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
SITE_LECTURES_PATH = os.path.join(ROOT, 'site', 'lectures.json')


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _load_module('gen_venue', os.path.join(ROOT, 'scripts', 'generate_frontend_data.py'))
srv = _load_module('srv_venue', os.path.join(ROOT, 'server.py'))
ff = _load_module('ff_venue', os.path.join(ROOT, 'scripts', 'frontend_fields.py'))

U = 'http://x.scnu.edu.cn/a/20261111/1.html'
V = 'http://x.scnu.edu.cn/a/20261111/2.html'
DAY1 = '2026-11-11 09:00:00'
DAY2 = '2026-11-12 09:00:00'


def rec(url=U, start=DAY1, venue='', idx=None, college='学院A'):
    """最小可跑通 build_stats / with_unit 的记录。

    idx 不为空才带 lectureIndex —— count_key 会产出 `url~idx`，build_stats
    才会把该页判为 split（有页面级虚拟索引入口），这是 test_2x 的前提。
    """
    r = {'sourceUrl': url, 'lectureStart': start, 'location': venue,
         'college': college, 'campus': '石牌', 'title': 't', 'topic': 't'}
    if idx is not None:
        r['lectureIndex'] = idx
    return r


def four_sessions(venues, url=U, start=DAY1):
    return [rec(url, start, v, idx=i + 1) for i, v in enumerate(venues)]


def url_dates_of(rows):
    out = {}
    for r in rows:
        u = r.get('sourceUrl') or ''
        d = (r.get('lectureStart') or '')[:10]
        out.setdefault(u, set())
        if d:
            out[u].add(d)
    return out


class VenueNormalizeTest(unittest.TestCase):
    """地点归一：轻到足以吃掉解析噪声，又不把真实不同场地并成一个。"""

    def test_01_空与占位地点统一返回空串(self):
        for v in ('', '   ', '待定', ' 待定 ', '暂定', '另行通知', '待通知',
                  '待公布', '暂无', '无', '未定', 'TBD', 'tba', '?', '？', '-', '—'):
            self.assertEqual(gen.normalize_venue(v), '', f'{v!r} 应视为无信息地点')

    def test_02_全角数字与中文括号归一(self):
        self.assertEqual(gen.normalize_venue('石牌校园教育科学学院１２５室'),
                         '石牌校园教育科学学院125室')
        self.assertEqual(gen.normalize_venue('（大学城）'), '大学城')

    def test_03_嘉宾介绍等解析噪声尾巴剥离(self):
        # yjsy/10291 实测：地点串进了下一节的「嘉宾介绍」标签
        dirty = '石牌校园计算机学院学术报告厅嘉宾介绍'
        clean = '石牌校园计算机学院学术报告厅'
        self.assertEqual(gen.normalize_venue(dirty), gen.normalize_venue(clean))
        for noise in ('主讲人简介', '报告人简介', '主讲人', '报告人', '活动地点', '活动时间'):
            self.assertEqual(gen.normalize_venue(clean + noise),
                             gen.normalize_venue(clean), noise)

    def test_04_大小写与空白不敏感(self):
        self.assertEqual(gen.normalize_venue(' Room 101 '), gen.normalize_venue('room101'))
        self.assertEqual(gen.normalize_venue('A栋\n101室'), gen.normalize_venue('A栋101室'))

    def test_05_不同楼栋不同室号仍是两个地点(self):
        pairs = [('A栋101室', 'A栋102室'), ('石牌校园', '大学城校园'),
                 ('文科楼二楼会议室', '文科楼三楼会议室'),
                 ('计算机学院学术报告厅', '教育科学学院125室')]
        for a, b in pairs:
            self.assertNotEqual(gen.normalize_venue(a), gen.normalize_venue(b),
                                f'{a} / {b} 被过度归一成同一场地，会把多地点组误折')


class VenueBlockedGroupsTest(unittest.TestCase):
    """黑名单判据：阈值 + 有效地点数 + 分组边界。"""

    def test_10_未达阈值即使多地点也不拦(self):
        rows = four_sessions(['A', 'B', 'C', 'D'])[:3]
        self.assertEqual(gen.venue_blocked_groups(rows), set())

    def test_11_四场同地点不拦(self):
        self.assertEqual(gen.venue_blocked_groups(four_sessions(['A'] * 4)), set(),
                         '同一场地轮番讲正是该折叠的论坛 / 大会')

    def test_12_四场两个不同地点整组被拦(self):
        self.assertEqual(
            gen.venue_blocked_groups(four_sessions(['A', 'B', 'A', 'B'])),
            {(U, DAY1[:10])})

    def test_13_空与待定地点不制造差异(self):
        self.assertEqual(
            gen.venue_blocked_groups(four_sessions(['A', '', '待定', '  '])), set(),
            '占位地点不提供信息，按用户拍板默认折叠')
        self.assertEqual(
            gen.venue_blocked_groups(four_sessions(['', '待定', None, '暂定'])), set())

    def test_14_两个有效地点且混入占位仍被拦(self):
        self.assertEqual(
            gen.venue_blocked_groups(four_sessions(['A', 'B', '', '待定'])),
            {(U, DAY1[:10])})

    def test_15_跨天不串组(self):
        # 同一 URL 两天各 2 场，地点各不相同；合起来看像「多地点 4 场」，
        # 但按 sourceUrl|日期分组后两天都未达阈值 → 一组都不该拦。
        rows = [rec(U, DAY1, 'A', 1), rec(U, DAY1, 'B', 2),
                rec(U, DAY2, 'C', 3), rec(U, DAY2, 'D', 4)]
        self.assertEqual(gen.venue_blocked_groups(rows), set())

    def test_16_不同源页不串组(self):
        rows = four_sessions(['A', 'A', 'A', 'A'], U) + \
               four_sessions(['B', 'B', 'B', 'B'], V)
        self.assertEqual(gen.venue_blocked_groups(rows), set())

    def test_17_分组键与前端mergedDayKey同源(self):
        r = rec(U, DAY1, 'A', 1)
        self.assertEqual(gen.forum_group_key(r), (U, '2026-11-11'))
        self.assertEqual(gen.forum_group_key({'sourceUrl': U}), (U, ''))
        self.assertEqual(gen.forum_group_key({}), ('', ''))


class VenueStatSyncTest(unittest.TestCase):
    """展示层逐场展开后，统计矩阵 / 页面级虚拟索引必须同步。"""

    def test_20_被拦组统计逐场计数(self):
        rows = four_sessions(['A', 'B', 'C', 'D'])
        st = gen.build_stats(rows, '2026-11-11T00:00:00+08:00')
        self.assertEqual(st['matrix']['学院A']['2026'], 4,
                         '首页 4 张独立卡、统计表却只算 1 场 = 口径分叉')
        self.assertEqual(sum(st['yearTotals'].values()), 4)
        real = [l for l in st['lectures'] if not l.get('p')]
        self.assertEqual(len(real), 4, '最小索引始终逐场全量')

    def test_21_可折叠组仍按一场活动归并(self):
        rows = four_sessions(['A'] * 4)
        st = gen.build_stats(rows, '2026-11-11T00:00:00+08:00')
        self.assertEqual(st['matrix']['学院A']['2026'], 1)
        skip, overrides = gen.forum_stat_groups(rows)
        self.assertEqual(len(skip), 3)
        self.assertEqual(len(overrides), 1)

    def test_22_被拦组不补页面级虚拟索引(self):
        rows = four_sessions(['A', 'B', 'C', 'D'])
        st = gen.build_stats(rows, '2026-11-11T00:00:00+08:00')
        virtual = [l for l in st['lectures'] if l.get('p')]
        self.assertEqual(virtual, [],
                         '逐场单卡没有「整页赞」入口，补虚拟索引会多出无对应按钮的记录')

    def test_23_可折叠组仍补页面级虚拟索引(self):
        rows = four_sessions(['A'] * 4)
        st = gen.build_stats(rows, '2026-11-11T00:00:00+08:00')
        virtual = [l for l in st['lectures'] if l.get('p')]
        self.assertEqual(len(virtual), 1)
        self.assertEqual(virtual[0]['k'], U)

    def test_24_同一页一天可折一天多地点时按天分流(self):
        rows = four_sessions(['A'] * 4, U, DAY1) + \
               four_sessions(['B', 'C', 'D', 'E'], U, DAY2)
        st = gen.build_stats(rows, '2026-11-11T00:00:00+08:00')
        self.assertEqual(st['matrix']['学院A']['2026'], 5,
                         '11-11 折成 1 场活动 + 11-12 逐场 4 场')
        virtual = [l for l in st['lectures'] if l.get('p')]
        self.assertEqual(len(virtual), 1, '页面级键按 URL 去重，只补 1 条')


class VenueFlagDeliveryTest(unittest.TestCase):
    """`vf: 0` 的两条下发路径、白名单与真实数据落地。"""

    def test_30_前端白名单必须含vf(self):
        self.assertIn('vf', ff.FRONTEND_KEEP,
                      '白名单漏了 vf 会被 strip_frontend_fields 裁掉，前端恒读到 undefined')

    def test_31_只给被拦组写0其余不带该键(self):
        rows = four_sessions(['A', 'B', 'C', 'D'], U) + \
               four_sessions(['Z'] * 4, V)
        blocked = gen.venue_blocked_groups(rows)
        out = [gen.with_unit(r, url_dates_of(rows), blocked) for r in rows]
        self.assertEqual([r.get('vf') for r in out[:4]], [0, 0, 0, 0])
        self.assertTrue(all('vf' not in r for r in out[4:]),
                        '缺省即允许折叠，非被拦组不应多出一个键（旧缓存也兼容）')

    def test_32_未传黑名单时行为与改造前一致(self):
        out = [gen.with_unit(r, url_dates_of([r])) for r in four_sessions(['A', 'B'])]
        self.assertTrue(all('vf' not in r for r in out))

    def test_33_本地下发与生成器给同一组打同样的标记(self):
        rows = four_sessions(['A', 'B', 'C', 'D']) + four_sessions(['Z'] * 4, V)
        blocked = gen.venue_blocked_groups(rows)
        from_gen = [gen.with_unit(r, url_dates_of(rows), blocked) for r in rows]
        from_srv = srv._attach_unit_types(rows)
        self.assertEqual([r.get('vf') for r in from_gen],
                         [r.get('vf') for r in from_srv],
                         '本地 /api/lectures 与公网静态切片的 vf 标记必须一致')

    def test_34_真实数据黑名单含研究生院预告的两天(self):
        with open(DATA_PATH, encoding='utf-8') as f:
            data = (json.load(f) or {}).get('data') or []
        self.assertTrue(data, '缺少 data/lectures.json')
        blocked = gen.venue_blocked_groups(data)
        yjsy = 'https://yjsy.scnu.edu.cn/a/20261110/10291.html'
        self.assertIn((yjsy, '2026-11-11'), blocked)
        self.assertIn((yjsy, '2026-11-12'), blocked,
                      '用户报告的页面两天都应逐场展开')
        self.assertGreaterEqual(len(blocked), 5,
                                '历史 3 组多地点页也应一并展开（用户拍板不做时间特判）')

    def test_35_线上切片标记与黑名单对得上(self):
        """端到端：生成器产物里 vf=0 的记录必须落在被拦组内，且每组齐全。"""
        with open(DATA_PATH, encoding='utf-8') as f:
            data = (json.load(f) or {}).get('data') or []
        with open(SITE_LECTURES_PATH, encoding='utf-8') as f:
            site = (json.load(f) or {}).get('data') or []
        self.assertTrue(site, '缺少 site/lectures.json —— 改完数据/生成器后必须重跑')
        blocked = gen.venue_blocked_groups(data)
        flagged = [r for r in site if r.get('vf') == 0]
        self.assertTrue(flagged, '产物里没有任何 vf=0，折叠闸门未下发')
        for r in flagged:
            self.assertIn(gen.forum_group_key(r), blocked,
                          'vf=0 打在了不该展开的组上')
        seen = {}
        for r in flagged:
            k = gen.forum_group_key(r)
            seen[k] = seen.get(k, 0) + 1
        for k in blocked:
            self.assertGreaterEqual(seen.get(k, 0), gen.FORUM_MIN,
                                    f'被拦组 {k} 的产物里 vf 标记不齐，会有几场仍被折走')


class VenueFrontendGuardTest(unittest.TestCase):
    """静态守卫：口径散在 5 处，任一处漏改都要能被拦住。"""

    @classmethod
    def setUpClass(cls):
        def read(rel):
            with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
                return f.read()
        cls.computed = read('site/app.computed.js')
        cls.social = read('site/app.social.js')
        cls.calendar = read('site/app.calendar.js')
        cls.generator = read('scripts/generate_frontend_data.py')

    def test_40_三处消费点都读vf标记(self):
        self.assertGreaterEqual(self.computed.count('vf === 0'), 2,
                                'units() 与 filtered() 两处都必须读 vf')
        self.assertIn('vf === 0', self.social,
                      'migrateCountKeys 漏读 vf 会展开出伪造已赞键')
        self.assertIn('blockedDays.has(gk)', self.social)

    def test_41_前端不得另写一套地点比较逻辑(self):
        self.assertNotIn('normalize_venue', self.computed + self.social,
                         '前端复算地点会与 Python 归一实现分叉，必须只读 vf')
        self.assertIn('blockedDays.has(k)', self.computed)
        self.assertIn('!blocked.has(k)', self.computed)

    def test_42_阈值仍为4且页面级索引复用同一份黑名单(self):
        self.assertIn('const FORUM_MIN = 4;', self.computed)
        self.assertIn('FORUM_MIN = 4', self.generator)
        self.assertIn('(_u, day) not in blocked', self.generator,
                      '页面级虚拟索引必须按同一份黑名单过滤')
        self.assertIn('venue_blocked = venue_blocked_groups(data)', self.generator)

    def test_43_日历侧注释同步且合并卡说明不误导(self):
        self.assertIn('vf', self.calendar,
                      '日历侧注释仍写「同页同日 ≥4 场就整组加」，会误导后续维护者')


if __name__ == '__main__':
    unittest.main(verbosity=2)
