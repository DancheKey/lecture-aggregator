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
import re
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
    # 实际举办校区推导（2026-10-13 用户拍板）：校级+location 命中 → actualCampus
    {'sourceUrl': 'http://e.scnu.edu.cn/v/5.html', 'lectureStart': '2026-09-05 15:00:00',
     'campus': '校级', 'location': '大学城校园文1栋模拟法庭', 'title': '校级大学城场'},
    # 南海校区归入佛山（2026-10-13 用户裁定）
    {'sourceUrl': 'http://f.scnu.edu.cn/v/6.html', 'lectureStart': '2026-09-06 15:00:00',
     'campus': '校级', 'location': '南海校区多功能会议厅', 'title': '校级南海场'},
    # 校级但 location 无校区关键词 → 不推导（举办地未知，仍归校级）
    {'sourceUrl': 'http://g.scnu.edu.cn/v/7.html', 'lectureStart': '2026-09-07 15:00:00',
     'campus': '校级', 'location': '待公布', 'title': '校级无地点场'},
    # 非校级（归口即校区级单位）→ 不推导，哪怕 location 含校区词
    {'sourceUrl': 'http://h.scnu.edu.cn/v/8.html', 'lectureStart': '2026-09-08 15:00:00',
     'campus': '石牌', 'location': '石牌校园教育科学学院125室', 'title': '石牌学院场'},
    # 建筑/单位名推导（2026-10-13 用户裁定三条：桃李园→石牌、心理学院→石牌、文二栋→大学城）
    {'sourceUrl': 'http://i.scnu.edu.cn/v/9.html', 'lectureStart': '2026-09-09 15:00:00',
     'campus': '校级', 'location': '桃李园三楼高明厅', 'title': '校级桃李园场'},
    {'sourceUrl': 'http://j.scnu.edu.cn/v/10.html', 'lectureStart': '2026-09-10 15:00:00',
     'campus': '校级', 'location': '心理学院520报告厅', 'title': '校级心理学院场'},
    {'sourceUrl': 'http://k.scnu.edu.cn/v/11.html', 'lectureStart': '2026-09-11 15:00:00',
     'campus': '校级', 'location': '文二栋536', 'title': '校级文二栋场'},
    # 优先级：显式校区词先于建筑名（「大学城校园心理学院…」归大学城，不归石牌）
    {'sourceUrl': 'http://l.scnu.edu.cn/v/12.html', 'lectureStart': '2026-09-12 15:00:00',
     'campus': '校级', 'location': '大学城校园心理学院301', 'title': '校级显式优先场'},
    # 建筑/单位名扩展（2026-10-13 用户追加裁定：田家炳/会议厅/课室大楼/教学楼/
    # 文科楼/国际文化学院/天河校区→石牌，先进光电子/政管/体科院→大学城）
    {'sourceUrl': 'http://m.scnu.edu.cn/v/13.html', 'lectureStart': '2026-09-13 15:00:00',
     'campus': '校级', 'location': '田家炳教育书院301', 'title': '校级田家炳场'},
    {'sourceUrl': 'http://n.scnu.edu.cn/v/14.html', 'lectureStart': '2026-09-14 15:00:00',
     'campus': '校级', 'location': '体育科学学院报告厅', 'title': '校级体科战场'},
    {'sourceUrl': 'http://o.scnu.edu.cn/v/15.html', 'lectureStart': '2026-09-15 15:00:00',
     'campus': '校级', 'location': '天河校区科学楼', 'title': '校级天河场'},
    # 共现强证据词（2026-10-13 用户原则「学院按所在校区」+ 共现统计：地理科学学院
    # →石牌 75 次 100%）
    {'sourceUrl': 'http://p.scnu.edu.cn/v/16.html', 'lectureStart': '2026-09-16 15:00:00',
     'campus': '校级', 'location': '地理科学学院101阶梯教室', 'title': '校级地科场'},
    # 教师发展中心→石牌（用户 2026-10-13 二次裁定）先于括号内「网络教育学院」：
    # 建筑名表按序扫描；网络教育学院亦归石牌（用户三次裁定，推翻早前共现→佛山推定）
    {'sourceUrl': 'http://q.scnu.edu.cn/v/17.html', 'lectureStart': '2026-09-17 15:00:00',
     'campus': '校级', 'location': '教师发展中心206课室(网络教育学院二楼)', 'title': '校级网教场'},
    # 2026-10-13 用户裁定补充：文学院→大学城、国际商学院→佛山（学院按所在校区）
    {'sourceUrl': 'http://r.scnu.edu.cn/v/18.html', 'lectureStart': '2026-09-18 15:00:00',
     'campus': '校级', 'location': '文学院报告厅', 'title': '校级文学院场'},
    {'sourceUrl': 'http://s.scnu.edu.cn/v/19.html', 'lectureStart': '2026-09-19 15:00:00',
     'campus': '校级', 'location': '国际商学院学术报告厅', 'title': '校级国商场'},
    {'sourceUrl': 'http://t.scnu.edu.cn/v/20.html', 'lectureStart': '2026-09-20 15:00:00',
     'campus': '校级', 'location': '校史文博馆一楼展厅', 'title': '校级文博馆场'},
    # 线上举办（2026-10-13 用户要求加「线上」标签）：location 写参会方式即 isOnline
    {'sourceUrl': 'http://u.scnu.edu.cn/v/21.html', 'lectureStart': '2026-09-21 15:00:00',
     'campus': '大学城', 'location': '腾讯会议 295-390-567', 'title': '线上腾讯会议场'},
    {'sourceUrl': 'http://v.scnu.edu.cn/v/22.html', 'lectureStart': '2026-09-22 15:00:00',
     'campus': '石牌', 'location': '线上', 'title': '线上裸词场'},
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
        # 实际举办校区推导（2026-10-13 用户拍板）：显式锁语义，防「两边一起错」
        self.assertEqual(got_gen[6].get('actualCampus'), '大学城')
        self.assertEqual(got_gen[7].get('actualCampus'), '佛山')  # 南海归佛山
        self.assertNotIn('actualCampus', got_gen[8])              # 无关键词不推导
        self.assertNotIn('actualCampus', got_gen[9])              # 非校级不推导
        # 两条路径必须同产该字段（单边接入即漂移，全量回归兜底之外再显式锁一对）
        self.assertEqual(got_gen[6].get('actualCampus'), got_srv[6].get('actualCampus'))
        self.assertEqual(got_gen[7].get('actualCampus'), got_srv[7].get('actualCampus'))
        # 建筑/单位名推导（2026-10-13 用户裁定三条）
        self.assertEqual(got_gen[10].get('actualCampus'), '石牌')   # 桃李园
        self.assertEqual(got_gen[11].get('actualCampus'), '石牌')   # 心理学院
        self.assertEqual(got_gen[12].get('actualCampus'), '大学城')  # 文二栋
        # 显式校区词优先于建筑名（防优先级反转）
        self.assertEqual(got_gen[13].get('actualCampus'), '大学城')
        # 建筑/单位名扩展（2026-10-13 用户追加裁定）
        self.assertEqual(got_gen[14].get('actualCampus'), '石牌')    # 田家炳教育书院
        self.assertEqual(got_gen[15].get('actualCampus'), '大学城')  # 体育科学学院
        self.assertEqual(got_gen[16].get('actualCampus'), '石牌')    # 天河校区归石牌
        self.assertEqual(got_gen[17].get('actualCampus'), '石牌')    # 地理科学学院（共现强证据）
        # 教师发展中心（石牌）优先于括号内「网络教育学院」；后者亦归石牌（用户三次裁定）
        self.assertEqual(got_gen[18].get('actualCampus'), '石牌')
        self.assertEqual(got_gen[19].get('actualCampus'), '大学城')  # 文学院（用户裁定）
        self.assertEqual(got_gen[20].get('actualCampus'), '佛山')    # 国际商学院（用户裁定）
        self.assertEqual(got_gen[21].get('actualCampus'), '石牌')    # 校史文博馆（用户裁定）
        # 线上举办：location 为参会方式即 True；实体地点不写字段（缺省即否）
        self.assertTrue(got_gen[22].get('isOnline'))                 # 腾讯会议
        self.assertTrue(got_gen[23].get('isOnline'))                 # 裸「线上」
        self.assertNotIn('isOnline', got_gen[21])                    # 实体地点不写
        self.assertEqual(got_gen[22].get('isOnline'), got_srv[22].get('isOnline'))
        self.assertEqual(got_gen[11].get('actualCampus'), got_srv[11].get('actualCampus'))
        self.assertEqual(got_gen[12].get('actualCampus'), got_srv[12].get('actualCampus'))
        self.assertEqual(got_gen[15].get('actualCampus'), got_srv[15].get('actualCampus'))

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


class TestFrameGuardCoverage(unittest.TestCase):
    """三页防点击劫持三件套必须在位（2026-10-14 P1-1 一修 + 二补 trend 页）。

    背景：GitHub Pages 不能下发 X-Frame-Options / frame-ancestors 响应头
    （meta 里的 frame-ancestors 会被浏览器忽略），三个 HTML 页用同一套
    「预隐藏 class + 顶层放行」兜底。三处 IIFE 是同源拷贝（app.core.js /
    stats.js / gen_visits_trend.py 的 JS 常量），单边漏改 = 该页对点击
    劫持裸奔，且**没有任何报错**——故固化为断言。

    注意 trend 页落点与两页不同：该页 CSP 禁 'unsafe-inline'，CSS 规则与
    IIFE 分别落**外链** visits-trend.css / visits-trend.js（由生成器产物），
    与 index/stats 的 head 内联 <style> 等效（link 渲染阻塞，先于首帧）。
    本类同时锁模板与产物，防「只改模板忘跑生成器」与「只改产物被 CI 覆盖」。
    """
    SITE = os.path.join(ROOT, 'site')

    def _read(self, *parts):
        with open(os.path.join(self.SITE, *parts), encoding='utf-8') as f:
            return f.read()

    def test_60_三页html均带预隐藏class(self):
        for page in ('index.html', 'stats.html', 'visits-trend.html'):
            html = self._read(page)
            m = re.search(r'<html[^>]*>', html)
            self.assertTrue(m, f'{page} 无 <html> 标签')
            self.assertIn('pre-hidden', m.group(0),
                          f'{page} 的 <html> 缺 pre-hidden class——'
                          f'被第三方 iframe 嵌入时页面第一帧就可见、可点')

    def test_61_三页均有display_none规则(self):
        # index/stats 的规则在 head 内联 <style>（其 CSP 放行 unsafe-inline）；
        # trend 的规则在外链 visits-trend.css（其 CSP 只有 'self'）。
        for page in ('index.html', 'stats.html'):
            html = self._read(page)
            self.assertRegex(html, r'\.pre-hidden\s*\{\s*display:\s*none',
                             f'{page} 无 .pre-hidden{{display:none}} 规则——'
                             f'预隐藏 class 是空操作，iframe 里页面照常显示')
        css = self._read('visits-trend.css')
        self.assertRegex(css, r'\.pre-hidden\s*\{\s*display:\s*none',
                         'visits-trend.css 无 .pre-hidden{display:none} 规则')

    def test_62_三处js均有顶层放行逻辑(self):
        for js in ('app.core.js', 'stats.js', 'visits-trend.js'):
            text = self._read(js)
            self.assertIn('window.top === window.self', text,
                          f'{js} 缺顶层判定——预隐藏后无人还原，顶层访客看到白屏')
            self.assertIn("classList.remove('pre-hidden')", text,
                          f'{js} 顶层分支未移除 pre-hidden class')
            self.assertIn('window.top.location.replace', text,
                          f'{js} 被嵌入分支未尝试顶层跳转')

    def test_63_trend页三件套来自生成器模板(self):
        """产物必须与模板同步：直接改产物会被次日 CI 的生成器覆盖（10-08 踩坑）。"""
        with open(os.path.join(ROOT, 'scripts', 'gen_visits_trend.py'),
                  encoding='utf-8') as f:
            tpl = f.read()
        self.assertIn('class="pre-hidden"', tpl,
                      'gen_visits_trend.py 的 html 模板缺 pre-hidden class')
        self.assertIn('.pre-hidden{display:none}', tpl,
                      'gen_visits_trend.py 的 CSS 常量缺 .pre-hidden 规则')
        self.assertIn("classList.remove('pre-hidden')", tpl,
                      'gen_visits_trend.py 的 JS 常量缺顶层放行 IIFE')


class StatsCampusDualSetTest(unittest.TestCase):
    """统计页校区筛选必须是「归口 ∪ 实际举办」双集合口径（2026-10-14 P2-2）。

    首页 filtered() 按 `campus ∪ sources ∪ actualCampus` 记录级筛选；统计页此前
    只看 campusMap（学院→归口校区），校级讲座实际在石牌/大学城办的 232 条在具体
    校区筛选中完全看不到——两页口径分叉，且统计页无任何门禁守护。
    本类锁三处：生成器产出 campusActualMap、stats.js 消费它、产物里带该键。
    """

    def test_60_生成器产出实际举办校区映射(self):
        with open(os.path.join(ROOT, 'scripts', 'generate_frontend_data.py'),
                  encoding='utf-8') as f:
            src = f.read()
        self.assertIn('campusActualMap', src,
                      '生成器未产出 campusActualMap（统计页双集合口径失据）')
        self.assertIn("_fv.actual_campus(item)", src,
                      '生成器未用 _fv.actual_campus 推导实际举办校区')

    def test_61_统计页消费双集合(self):
        with open(os.path.join(ROOT, 'site', 'stats.js'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('campusActualMap', src,
                      'stats.js 未消费 campusActualMap（统计页仍是单归口口径）')
        # 双集合都必须从 summary 里取（缺键时不得抛）
        self.assertIn("this.summary.campusActualMap", src,
                      'campusActualMap 未按 this.summary 防御性读取')
        # 必须真的用它过滤（只引用不过滤=假消费：删掉 forEach 块断言仍绿的教训）。
        # 2026-10-14 方案 A：matrix()/yearTotals() 的讲座级命中过滤统一用 cf 变量。
        self.assertIn('al.includes(cf)', src,
                      'campusActualMap 未参与 campusFilter 过滤（假消费）')
        # 行集合不得再按首见语义（campusMap 单归口）过滤矩阵行：
        # campusMatrix 选中校区时已是该校区全量口径，任何额外行过滤都会
        # 丢联合发布的讲座-单位对（曾致石牌合计 1758≠1760）。
        self.assertNotIn('campusColleges', src,
                         'stats.js 不得再消费 campusColleges（首见语义过滤，会丢讲座-单位对）')

    def test_62_产物含该键(self):
        p = os.path.join(ROOT, 'site', 'lectures', 'stats.json')
        if not os.path.exists(p):
            self.skipTest('stats.json 未生成（本地未跑生成器）')
        with open(p, encoding='utf-8') as f:
            s = json.load(f)
        self.assertIn('campusActualMap', s, '产物 stats.json 缺 campusActualMap')


class BusuanziCspScopeTest(unittest.TestCase):
    """CSP 对 busuanzi 的 script-src 必须收敛到具体文件路径（2026-10-14 P2-3）。

    原 CSP 放行整个 busuanzi.aspark.cc 域：镜像站任一路径被挂马即可在页面上下文
    执行任意 JS。已收敛为仅 /js 一个文件（SRI 不可行：对方无 CORS 头，加了反而
    加载失败，详见 footer-counter.js 注释）。connect-src 保留整域（计数 API 路径
    随版本可能变，不能收敛）。
    """

    def test_63_script_src不含裸域放行(self):
        with open(os.path.join(ROOT, 'site', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        m = re.search(r'content="([^"]*Content-Security-Policy[^"]*)"', html)
        if not m:
            m = re.search(r'<meta http-equiv="Content-Security-Policy"\s+content="([^"]*)"', html)
        self.assertIsNotNone(m, '未找到 CSP meta')
        csp = m.group(1)
        # script-src 段内不得出现裸域（必须以 /js 结尾）
        ss = re.search(r'script-src ([^;]+)', csp)
        self.assertIsNotNone(ss, 'CSP 缺 script-src')
        seg = ss.group(1)
        self.assertNotRegex(seg, r'busuanzi\.aspark\.cc(?!/js)\b',
                            'script-src 仍是裸域放行（应收敛到 /js 具体路径）')
        self.assertIn('https://busuanzi.aspark.cc/js', seg,
                      'script-src 缺少 busuanzi /js 具体路径')
        # connect-src 保留整域（计数 API）不得被误收敛
        cs = re.search(r'connect-src ([^;]+)', csp)
        self.assertIsNotNone(cs, 'CSP 缺 connect-src')
        self.assertIn('https://busuanzi.aspark.cc', cs.group(1),
                      'connect-src 丢了 busuanzi 计数 API 域名（计数会失效）')


class DocNumbersSmokeTest(unittest.TestCase):
    """文档/注释陈旧数字防回退冒烟（2026-10-14 P2-10 配套）。

    策略：条数/片数等**日变数字**不硬锁（每天增量更新，写死即天天过期）；
    锁的是「旧值表述不得回退」+「与实测一致的稳定锚」（chunkSize）。

    ⚠ `docs/` 整目录在 .gitignore（本地文档、不入库）→ CI 检出里不存在，
    故读 docs 的用例必须 skipTest（同 test_ci_gate_coverage.py 的既有范式）。
    2026-10-14 教训：本类首版直接 open → 本地全绿、CI 3 错误把门禁打红，
    连带 deploy 被跳过。**新增「读本地未入库文件」的测试必须模拟 CI 条件
    （临时移走该目录）验证一次。**
    """

    def _read(self, rel):
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            self.skipTest(f'{rel} 不存在（docs/ 与 .gitignore 同步，CI 上必然缺失）'
                          '—— 该守卫仅本地生效')
        with open(p, encoding='utf-8') as f:
            return f.read()

    def test_64_deploy分片条数与实测一致(self):
        deploy = self._read(os.path.join('docs', 'deploy.md'))
        m = re.search(r'chunk_0001\.\.N\.json\s+每片\s+(\d+)\s+条', deploy)
        self.assertIsNotNone(m, 'deploy.md 分片条数描述行缺失或格式变更')
        # 实测锚：chunks.json 的 chunkSize（生成器唯一事实源）
        p = os.path.join(ROOT, 'site', 'lectures', 'chunks.json')
        if not os.path.exists(p):
            self.skipTest('chunks.json 未生成（本地未跑生成器）')
        with open(p, encoding='utf-8') as f:
            real = json.load(f).get('chunkSize')
        self.assertEqual(int(m.group(1)), real,
                         f'deploy.md 每片 {m.group(1)} 条 ≠ 实测 chunkSize={real}')

    def test_65_架构文档无单文件旧表述(self):
        arch = self._read(os.path.join('docs', 'ARCHITECTURE.md'))
        self.assertNotIn('单文件组件', arch,
                         'ARCHITECTURE.md 仍称前端为单文件组件（实际 8 个 JS）')

    def test_66_appdata无旧体积与截断表述(self):
        src = self._read(os.path.join('site', 'app.data.js'))
        for stale in ('8×334KB', '70KB 首屏', '7.4MB', '拉 8 分片', '截断预览'):
            self.assertNotIn(stale, src,
                             f'app.data.js 仍含陈旧表述「{stale}」（P2-10 已更新）')

    def test_67_deploy起始年份非2020(self):
        deploy = self._read(os.path.join('docs', 'deploy.md'))
        self.assertNotIn('2020 年至今', deploy,
                         'deploy.md 起始年份仍写 2020（实测最早 2009）')


class CampusMatrixStatsTest(unittest.TestCase):
    """统计页校区矩阵（方案 A，2026-10-14）：选中校区后数字必须与首页同口径。

    背景：此前统计页选中校区只过滤「显示哪些行」，行内数字仍是单位全部校区
    总数（社科处 116 vs 汕尾实际 1）——用户报告后确认方案 A：生成器预计算
    「校区 × 学院 × 年份」矩阵（campusMatrix），stats.js 选中校区时改读它。
    """

    def test_68_生成器产出校区矩阵(self):
        with open(os.path.join(ROOT, 'scripts', 'generate_frontend_data.py'),
                  encoding='utf-8') as f:
            src = f.read()
        self.assertIn("'campusMatrix': campus_matrix", src,
                      '生成器未产出 campusMatrix')
        self.assertIn('matched_campuses', src,
                      '生成器未按「归口 ∪ 实际举办」命中集合构建矩阵')

    def test_69_统计页消费校区矩阵(self):
        with open(os.path.join(ROOT, 'site', 'stats.js'), encoding='utf-8') as f:
            src = f.read()
        self.assertIn('this.summary.campusMatrix', src,
                      'stats.js 未消费 campusMatrix')
        # 真分支断言：选中校区时必须真的改读校区矩阵（只引用不分支 = 假消费）
        self.assertRegex(
            src, r'this\.campusFilter\s*\?\s*\(cm\[this\.campusFilter\]',
            'stats.js 未按 campusFilter 分支切换矩阵来源（假消费）')
        # 访问/点赞叠加段必须也有讲座级校区命中过滤（否则 count 与 visits 口径分叉）
        self.assertIn('let hit = false', src,
                      'stats.js 访问/点赞叠加段缺讲座级校区命中过滤')

    def test_70_校区矩阵与首页口径一致(self):
        """对账：campusMatrix 每格 == 按首页 filtered() 口径直接算出的讲座-单位对数。"""
        sp = os.path.join(ROOT, 'site', 'lectures', 'stats.json')
        lp = os.path.join(ROOT, 'site', 'lectures.json')
        if not (os.path.exists(sp) and os.path.exists(lp)):
            self.skipTest('stats.json / lectures.json 未生成（本地未跑生成器）')
        with open(sp, encoding='utf-8') as f:
            cm = (json.load(f) or {}).get('campusMatrix') or {}
        with open(lp, encoding='utf-8') as f:
            data = (json.load(f) or {}).get('data') or []
        agg = {}
        for l in data:
            units, seen = [], set()
            for c in [l.get('college')] + [s.get('college') for s in l.get('sources') or []]:
                if c and c not in seen:
                    seen.add(c)
                    units.append(c)
            matched = {l.get('campus')} | {s.get('campus') for s in l.get('sources') or []}
            matched.discard(None)
            matched.discard('')
            if l.get('actualCampus'):
                matched.add(l['actualCampus'])
            for campus in matched:
                for u in units:
                    agg[(campus, u)] = agg.get((campus, u), 0) + 1
        got = {}
        for campus, colleges in cm.items():
            for college, years in colleges.items():
                got[(campus, college)] = sum(years.values())
        diff = [k for k in set(got) | set(agg) if got.get(k) != agg.get(k)]
        self.assertEqual(
            got, agg,
            f'校区矩阵与首页口径不一致（{len(diff)} 处，样例：{diff[:5]}）')


if __name__ == '__main__':
    unittest.main(verbosity=2)
