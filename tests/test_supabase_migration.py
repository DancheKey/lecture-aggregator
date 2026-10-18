#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Supabase 迁移 SQL 的结构守卫（2026-10-17 深夜漏洞复盘，含 CI 接线）。

背景：anon key 公开是 Supabase 的设计定位（同 reCAPTCHA site key），安全闸门
全在数据库侧。上线当晚实测发现 increment_like / increment_want 的 update 与
insert 两条路径只有 greatest(...,0) 下钳、没有 least 上钳——持 key 者灌一个
p_delta=99999 即可把 likes/wants 刷爆（表级 RLS 挡得住直接 INSERT/UPDATE，
挡不住函数内部逻辑漏洞；check 约束上限 10 万也过松）。

本套件锁死六类红线：
  ① 三个增量函数齐全；
  ② 每个函数的 insert 与 on conflict 两条路径都必须 least(greatest(...)) 双钳
     （当晚漏洞正是 like/want 两函数缺 least，update 路径最先失守）；
  ③ check 约束存在且 likes/wants 上限 <= 10000（防刷量级，非真实量级）；
  ④ RLS 只读策略 + 三个函数对 anon 授权在位；
  ⑤ 入库的 supabase-migration.sql 与 scripts/gen_supabase_sql.py 的当前产出
     逐字一致——单一事实源可验证（当晚用户两次「跑了个寂寞」的根因之一就是
     编辑器旧内容/手改 SQL，生成器必须能被测试校准）；
  ⑥ 2026-10-18 两项实测修复：p_url 长度守卫（空键/8000 字符行可被匿名建出，
     拖慢 fetchAll 并混入统计 map）与 visit 单次上钳 100（原 100 万一次 curl
     即可把无 capDisplay 的页脚 PV 刷成门面事故）。

只用标准库；读仓库内文件，无网络、无 key、无 Supabase 依赖。
"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_PATH = os.path.join(ROOT, 'supabase-migration.sql')
GEN_PATH = os.path.join(ROOT, 'scripts', 'gen_supabase_sql.py')

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
        既拖慢 fetchAll 全表拉取，空键行还绕过前端 /^__/ 过滤混进统计 map。
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

    def test_06_visit单次上钳100(self):
        """S2（2026-10-18）：visit 单次增量钳 100，累计总量钳 100 万不变。

        原单次上钳 = 总量上限 = 100 万，一次 curl 就能把页脚 PV（无 capDisplay）
        刷成门面事故。分工：d := 行钳**单次 delta**（100），update 外层钳
        **累计总量**（1000000）——两者不可互相替代。
        """
        body = _func_body(self.sql, 'increment_visit')
        # 尾部分号作边界：'100' 是 '1000000' 的前缀，无边界断言会把单次钳
        # 被改回 100 万的破坏误判为通过（这正是 S2 修掉的原始形态）。
        self.assertIn('d := least(greatest(p_delta, 0), 100);', body,
                      'increment_visit 的单次增量未钳到 100')
        self.assertIn(
            'set visits = least(greatest(lecture_stats.visits + d, 0), 1000000)',
            body, 'increment_visit 的累计总量上限应为 100 万（PV 量级，勿误改）')
        # like/want 的单次钳保持 1000（与显示层 capDisplay 300+ 匹配）
        for n in ('increment_like', 'increment_want'):
            b = _func_body(self.sql, n)
            self.assertIn('d := least(greatest(p_delta, 0), 1000);', b,
                          f'{n} 的单次增量应钳 1000（本次只降 visit，勿顺手动它）')

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
        r = subprocess.run([sys.executable, GEN_PATH], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, '生成器运行失败: ' + r.stderr[-300:])
        norm = lambda s: s.replace('\r\n', '\n')
        self.assertEqual(
            norm(r.stdout), norm(self.sql),
            'supabase-migration.sql 与 gen_supabase_sql.py 产出不一致——'
            '请重新生成（python scripts/gen_supabase_sql.py > supabase-migration.sql）'
            '而非手改 SQL')


if __name__ == '__main__':
    unittest.main()
