#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Supabase 迁移 SQL 的结构守卫（2026-10-17 深夜漏洞复盘，含 CI 接线）。

背景：anon key 公开是 Supabase 的设计定位（同 reCAPTCHA site key），安全闸门
全在数据库侧。上线当晚实测发现 increment_like / increment_want 的 update 与
insert 两条路径只有 greatest(...,0) 下钳、没有 least 上钳——持 key 者灌一个
p_delta=99999 即可把 likes/wants 刷爆（表级 RLS 挡得住直接 INSERT/UPDATE，
挡不住函数内部逻辑漏洞；check 约束上限 10 万也过松）。

本套件锁死九类红线：
  ① 三个增量函数齐全；
  ② 每个函数的 insert 与 on conflict 两条路径都必须 least(greatest(...)) 双钳
     （当晚漏洞正是 like/want 两函数缺 least，update 路径最先失守）；
  ③ check 约束存在且 likes/wants 上限 <= 10000（防刷量级，非真实量级）；
  ④ RLS 只读策略 + 三个函数对 anon 授权在位；
  ⑤ 入库的 supabase-migration.sql 与 scripts/gen_supabase_sql.py 的当前产出
     逐字一致——单一事实源可验证（当晚用户两次「跑了个寂寞」的根因之一就是
     编辑器旧内容/手改 SQL，生成器必须能被测试校准）；
  ⑥ 2026-10-18 两项实测修复：p_url 长度守卫（空键/8000 字符行可被匿名建出，
     拖慢 fetchAll 并让行数可被无限灌高）与 visit 单次上钳 100（原 100 万一次
     curl 即可把无 capDisplay 的页脚 PV 刷成门面事故）；
  ⑦ 2026-10-18 复查补：RLS 必须显式 enable（原 SQL 只有 create policy——
     Supabase 默认 anon 有全表 ALL 权限，RLS 关着时 policy 形同虚设）；
  ⑧ 2026-10-18 复查补：p_url 前缀守卫（只限长度挡不住 'a'/'b'… 批量造行），
     并用 site/lectures.json 全量 ck 实测该规则零误伤；
  ⑨ 2026-10-18 复查补：docs/supabase-patch-current.sql 必须由
     scripts/gen_supabase_patch.py 同源产出（手拼补丁会静默过期）。

只用标准库；读仓库内文件，无网络、无 key、无 Supabase 依赖。
"""
import json
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_PATH = os.path.join(ROOT, 'supabase-migration.sql')
GEN_PATH = os.path.join(ROOT, 'scripts', 'gen_supabase_sql.py')
PATCH_PATH = os.path.join(ROOT, 'supabase-patch-current.sql')
PATCH_GEN = os.path.join(ROOT, 'scripts', 'gen_supabase_patch.py')
LECTURES_JSON = os.path.join(ROOT, 'site', 'lectures.json')

FUNCS = ('increment_like', 'increment_want', 'increment_visit')


def _func_body(sql, name):
    """截取 create or replace function <name>(...) ... $$; $$ 之间的函数体。"""
    m = re.search(r'create or replace function ' + name + r'\(.*?\$\$(.*?)\$\$;',
                  sql, re.S)
    assert m, 'SQL 中找不到函数 ' + name
    return m.group(1)


class TestSupabaseMigrationSql(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(SQL_PATH):
            raise unittest.SkipTest('supabase-migration.sql 不存在（云端计数未启用）')
        with open(SQL_PATH, encoding='utf-8') as f:
            cls.sql = f.read()
        if os.path.exists(PATCH_PATH):
            with open(PATCH_PATH, encoding='utf-8') as f:
                cls.patch = f.read()
        else:
            cls.patch = None

    def test_00_三函数齐全(self):
        for n in FUNCS:
            self.assertIn('function ' + n + '(', self.sql, '缺增量函数 ' + n)

    def test_01_两路径双钳(self):
        """insert 与 on conflict 都必须 least(greatest(...))——当晚漏洞即缺此。

        2026-10-18 起单次钳收敛到 d 变量（`d := least(greatest(p_delta, 0), cap)`
        一行，insert 的 values 与 on conflict 的 `+ d` 共用同一个钳过的值），
        故每个函数体仍是恰好 2 处 least(greatest(：d 定义一处 + update 外层
        总量钳一处。改成 1 处 = 某条路径没了上钳；改成 3 处 = 又回到了两路
        各写一份钳值、会漂移的旧形态。
        """
        for n in FUNCS:
            body = _func_body(self.sql, n)
            n_least = body.count('least(greatest(')
            self.assertEqual(
                n_least, 2,
                f'{n} 的 least(greatest 应恰好 2 处（d 定义 + update 总量钳），'
                f'实得 {n_least}——缺 least 上钳即可被灌大 delta 刷爆计数')
            self.assertGreaterEqual(
                body.count('greatest('), 2, f'{n} 丢失 greatest 下钳（负数 delta 可把计数压成负）')

    def test_05_p_url长度守卫(self):
        """S1（2026-10-18 实测）：三个函数都必须校验 p_url，越界抛 22023。

        实测背景：此前零校验，anon 持 key 可建空键行与 8000 字符行——
        危害是污染表 + fetchAll 全表拉取被拖慢 + 行数可被无限灌高。
        （注意：空串键并不会「混进前端统计 map」——app.cloud.js 的
        row.url 为空串是 falsy，已被 if (row && row.url) 拦掉；那条
        说法是 2026-10-18 首版注释的误判，已修正。）
        """
        for n in FUNCS:
            body = _func_body(self.sql, n)
            self.assertIn('p_url is null', body, f'{n} 缺 p_url null 守卫')
            self.assertIn('length(p_url) = 0', body, f'{n} 缺空键守卫')
            # 带 then 结尾作边界：'300' 是 '3000' 的前缀，无边界断言会把
            # 上限被改松（如 3000）的破坏误判为通过。
            self.assertIn('length(p_url) > 300 then', body, f'{n} 缺超长键守卫（上限 300）')
            self.assertIn('errcode = 22023', body,
                          f'{n} 守卫未用 22023（invalid_parameter_value）——'
                          f'PostgREST 需 4xx 才能让前端 catch 后静默降级')
            # 守卫必须位于函数体入口（任何建行语句之前），否则先建行后拒绝
            guard_at = body.index('length(p_url) = 0')
            insert_at = body.index('insert into lecture_stats')
            self.assertLess(guard_at, insert_at,
                            f'{n} 的 p_url 守卫在 insert 之后——空/超长键仍会先建行')

    def test_06_单次步长与取消(self):
        """B1/S1/S5（2026-10-19）：单次步长统一钳 1，like/want 允许 -1 取消。

        S5：visit 单次 100 → 1（一次请求 = 一次真实访问）。
        S1：like/want 此前没有独立步长，直接复用累计上限 1000 当单次上钳——
            一次 curl 即可把某条讲座顶到 1000。
        B1：取消必须真的减。greatest(p_delta, 0) 会把 -1 钳成 0 → 「取消」是
            空操作（数字不降，toast 却报已取消），且本地已删键，再点又 +1，
            同一用户可反复刷高。
        分工不变：d 钳**单次 delta**，update 外层钳**累计总量**。
        """
        body = _func_body(self.sql, 'increment_visit')
        # 尾部分号作边界：'1' 是 '1000000' 的前缀，无边界断言会把单次钳
        # 被改回 100 万的破坏误判为通过。
        self.assertIn('d := least(greatest(p_delta, 0), 1);', body,
                      'increment_visit 的单次增量应钳到 1')
        self.assertIn(
            'set visits = least(greatest(lecture_stats.visits + d, 0), 1000000)',
            body, 'increment_visit 的累计总量上限应为 100 万（PV 量级，勿误改）')
        # like/want：单次 ±1（允许 -1 取消），累计上限仍 1000
        for n, col in (('increment_like', 'likes'), ('increment_want', 'wants')):
            b = _func_body(self.sql, n)
            self.assertIn('d := least(greatest(p_delta, -1), 1);', b,
                          f'{n} 的单次步长应为 ±1 且允许 -1（取消必须真的减）')
            self.assertIn(
                f'set {col} = least(greatest(lecture_stats.{col} + d, 0), 1000)',
                b, f'{n} 的累计上限应保持 1000')

    def test_07_rls必须显式开启(self):
        """P1-2（2026-10-18 复查）：SQL 要 enable row level security，不能只建 policy。

        原 SQL 只有 create policy。Supabase 对新表默认授 anon 全表 ALL 权限，
        RLS 未开启时 policy 形同虚设——照本 SQL 重建库 = 匿名可绕过 RPC 直写
        整表，所有钳制全部失效。本库实测 RLS 已开，此句是幂等兜底 + 防重建库漏开。
        """
        self.assertIn(
            'alter table lecture_stats enable row level security;',
            self.sql, '缺 enable row level security——policy 在但 RLS 关着 = 完全裸奔')
        # 语义顺序：先开 RLS 再建策略（反过来也能跑，但先开后建才是正意）
        enable_at = self.sql.index('enable row level security')
        policy_at = self.sql.index('create policy "anon read"')
        self.assertLess(enable_at, policy_at, 'enable RLS 应早于 create policy')

    def test_08_p_url前缀守卫(self):
        """P1-3（2026-10-18 复查）：只限长度挡不住批量造行，必须校验前缀。

        p_url='a'、'b'… 每个都满足 1..300 字符，anon 可无限建行；行数灌到
        触发 Supabase Max Rows（默认 1000）截断后，部分讲座计数会静默读不到。
        真实键 100% 是 http(s):// 地址或 __ 保留族（与 app.cloud.js 的
        /^__/ 过滤同口径）。
        """
        for n in FUNCS:
            body = _func_body(self.sql, n)
            self.assertIn("p_url !~* '^(https?://|__)'", body,
                          f'{n} 缺 p_url 前缀守卫——只限长度挡不住批量造行')
            guard_at = body.index("p_url !~* '^(https?://|__)'")
            insert_at = body.index('insert into lecture_stats')
            self.assertLess(guard_at, insert_at,
                            f'{n} 的前缀守卫在 insert 之后——非法键仍会先建行')

    def test_08b_前缀守卫零误伤真实键(self):
        """用 site/lectures.json 全量 ck 实测**SQL 里的**守卫规则：改规则前先量化影响面。

        关键：正则必须从 self.sql 里抽出来，不能在测试里另写一份。否则把生成器
        的规则改严（如 https-only）时本测试照旧绿——2026-10-18 破坏验证 D3
        就是这么漏的（测的是自己的正则，不是被测对象的规则）。
        """
        if not os.path.exists(LECTURES_JSON):
            self.skipTest('site/lectures.json 不存在')
        with open(LECTURES_JSON, encoding='utf-8') as f:
            items = json.load(f).get('data', [])
        cks = [x.get('ck') for x in items if isinstance(x, dict) and x.get('ck')]
        self.assertTrue(cks, 'lectures.json 里读不到任何 ck')
        # 从 SQL 抽守卫正则（三处应一致，任一处不同即视为漂移）
        pats = set(re.findall(r"p_url !~\* '([^']+)'", self.sql))
        self.assertTrue(pats, 'SQL 里抽不到 p_url 前缀守卫正则')
        self.assertEqual(len(pats), 1,
                         f'三个函数的前缀守卫正则不一致：{pats}——应统一由单一常量派生')
        pat = pats.pop()
        # 防「改松」：.* 等于没拦；必须真的限制协议
        self.assertNotIn('.*', pat, f'守卫正则 {pat!r} 含 .*，等于没拦')
        self.assertIn('http', pat, f'守卫正则 {pat!r} 未限制协议，挡不住任意短键批量造行')
        bad = [c for c in cks if not re.match(pat, c, re.IGNORECASE)]
        self.assertEqual(bad, [],
                         f'{len(bad)}/{len(cks)} 条真实 ck 不满足守卫规则 {pat!r}，'
                         f'放行后会 400 静默降级：{bad[:3]}')
        longest = max(len(c) for c in cks)
        self.assertLessEqual(
            longest, 300,
            f'最长真实 ck 达 {longest} 字符，超过 MAX_URL_LEN=300——放行后该讲座点赞会静默失败')

    def test_09_云端补丁与生成器同源(self):
        """P3-8（2026-10-18 复查）：补丁必须由 gen_supabase_patch.py 产出。

        手拼补丁（说明头 + 粘一份 SQL）没有任何机制阻止它过期——生成器一改，
        补丁静默漂移，用户照旧文件跑 = 修了个寂寞。本测试用 --stdout 重新
        产出并与入库文件逐字比对。
        """
        if self.patch is None:
            self.skipTest('supabase-patch-current.sql 不存在（仓库根；云端计数未启用）')
        r = subprocess.run([sys.executable, PATCH_GEN, '--stdout'],
                           capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, '补丁生成器运行失败: ' + r.stderr[-300:])
        norm = lambda s: s.replace('\r\n', '\n')
        self.assertEqual(
            norm(r.stdout), norm(self.patch),
            'docs/supabase-patch-current.sql 与 gen_supabase_patch.py 产出不一致——'
            '请重新生成（python scripts/gen_supabase_patch.py）而非手改补丁')

    def test_02_check约束存在且上限合理(self):
        caps = dict(re.findall(r'check \((\w+) between 0 and (\d+)\)', self.sql))
        for col in ('visits', 'likes', 'wants'):
            self.assertIn(col, caps, f'缺 {col} 的 check 值域约束')
        self.assertLessEqual(int(caps['likes']), 10000, 'likes 上限过松（防刷量级应 <= 10000）')
        self.assertLessEqual(int(caps['wants']), 10000, 'wants 上限过松（防刷量级应 <= 10000）')
        self.assertGreater(int(caps['visits']), int(caps['likes']),
                           'visits 上限应远大于 likes（PV 量级与点赞不同）')

    def test_03_列兜底与安全授权(self):
        # 列兜底：默认值 0 + not null（缺默认值的表插新 URL 会 23502）
        for col in ('visits', 'likes', 'wants'):
            self.assertIn(f'alter column {col} set default 0', self.sql)
            self.assertIn(f'alter column {col} set not null', self.sql)
        # RLS 只读 + 函数授权；表级写权限一律不授（写入只走 RPC）
        self.assertIn('create policy "anon read" on lecture_stats for select using (true)',
                      self.sql, 'RLS 只读策略缺失')
        for n in FUNCS:
            self.assertIn(f'grant execute on function {n}(text, int) to anon;', self.sql,
                          f'{n} 未授 anon 执行权')

    def test_04_入库SQL与生成器一致(self):
        """单一事实源：手改 SQL 会被本测试抓住（生成器重新产出比对）。"""
        r = subprocess.run([sys.executable, GEN_PATH], capture_output=True,
                           text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, '生成器运行失败: ' + r.stderr[-300:])
        norm = lambda s: s.replace('\r\n', '\n')
        self.assertEqual(
            norm(r.stdout), norm(self.sql),
            'supabase-migration.sql 与 gen_supabase_sql.py 产出不一致——'
            '请重新生成（python scripts/gen_supabase_sql.py > supabase-migration.sql）'
            '而非手改 SQL')


if __name__ == '__main__':
    unittest.main()
