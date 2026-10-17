#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Supabase 迁移 SQL 的结构守卫（2026-10-17 深夜漏洞复盘，含 CI 接线）。

背景：anon key 公开是 Supabase 的设计定位（同 reCAPTCHA site key），安全闸门
全在数据库侧。上线当晚实测发现 increment_like / increment_want 的 update 与
insert 两条路径只有 greatest(...,0) 下钳、没有 least 上钳——持 key 者灌一个
p_delta=99999 即可把 likes/wants 刷爆（表级 RLS 挡得住直接 INSERT/UPDATE，
挡不住函数内部逻辑漏洞；check 约束上限 10 万也过松）。

本套件锁死四类红线：
  ① 三个增量函数齐全；
  ② 每个函数的 insert 与 on conflict 两条路径都必须 least(greatest(...)) 双钳
     （当晚漏洞正是 like/want 两函数缺 least，update 路径最先失守）；
  ③ check 约束存在且 likes/wants 上限 <= 10000（防刷量级，非真实量级）；
  ④ RLS 只读策略 + 三个函数对 anon 授权在位；
  ⑤ 入库的 supabase-migration.sql 与 scripts/gen_supabase_sql.py 的当前产出
     逐字一致——单一事实源可验证（当晚用户两次「跑了个寂寞」的根因之一就是
     编辑器旧内容/手改 SQL，生成器必须能被测试校准）。

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
        """insert 与 on conflict 都必须 least(greatest(...))——当晚漏洞即缺此。"""
        for n in FUNCS:
            body = _func_body(self.sql, n)
            n_least = body.count('least(greatest(')
            self.assertEqual(
                n_least, 2,
                f'{n} 的 least(greatest 应恰好 2 处（insert 行 + on conflict 行），'
                f'实得 {n_least}——缺 least 上钳即可被灌大 delta 刷爆计数')
            self.assertGreaterEqual(
                body.count('greatest('), 2, f'{n} 丢失 greatest 下钳（负数 delta 可把计数压成负）')

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
