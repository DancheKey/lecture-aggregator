#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「当前唯一应跑」的 Supabase 云端补丁：说明头 + 同源 SQL 正文。

产出固定名 supabase-patch-current.sql。正文由 scripts/gen_supabase_sql.py
单一事实源产出，tests/test_supabase_migration.py 锁死同源。

用法：
  python scripts/gen_supabase_patch.py            # 写 supabase-patch-current.sql
  python scripts/gen_supabase_patch.py --stdout   # 只打印不写
  python scripts/gen_supabase_patch.py --check    # 只比对不写，不一致返回 1
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_SQL = os.path.join(ROOT, 'scripts', 'gen_supabase_sql.py')
OUT_PATH = os.path.join(ROOT, 'supabase-patch-current.sql')

try:
    sys.stdout.reconfigure(newline='\n', encoding='utf-8')
except Exception:
    pass

HEADER = """-- ============================================================
-- 讲座聚合站 · Supabase 云端补丁（当前唯一应跑的一份）
-- 本文件由 scripts/gen_supabase_patch.py 生成，请勿手改。
-- 执行位置：Supabase 控制台 → SQL Editor → 新建 query → 粘贴整段运行。
-- 幂等：结构语句均可重复执行；白名单按当前 site/lectures.json upsert，
-- 不覆盖 lecture_stats 中已经积累的真实计数。
--
-- 2026-11-28 版：
--   ① lecture_allowed_keys：只有当前讲座键可写统计；__site__ 仅允许 visit。
--   ② 四个 RPC 写入前校验白名单动作，并拒绝 p_delta=null。
--   ③ 所有 RPC 使用 SECURITY DEFINER + 固定 search_path，撤销 PUBLIC 默认执行权。
--   ④ lecture_stats / lecture_allowed_keys 显式开启 RLS；匿名只有统计表 SELECT，
--      没有两表的 INSERT/UPDATE/DELETE 权限。
--   ⑤ 新增 sync_whitelist：日常流水线用共享令牌同步，数据库只存令牌哈希；
--      只 upsert、不删除，限制输入与总行数；轮换时短暂接受上一条哈希。
--   ⑥ 保留既有单次增量钳制、取消点赞 -1、累计上限与正常前端接口。
--
-- 上线前后核查：运行仓库根 supabase-verify-current.sql（只读，不调用增量 RPC）。
-- 上线后：把同步令牌加入 GitHub Secret SUPABASE_WHITELIST_SYNC_TOKEN，
-- 每日生成前端数据后由 .github/workflows/daily.yml 自动同步，无需手工加白名单。
-- 令牌轮换：运行 python scripts/gen_supabase_sql.py --rotate-sync-token，
-- 按输出顺序更新 Secret 与数据库哈希，最后关闭 previous_token_hash 接受窗口。
--
-- 边界：本补丁解决「任意 URL 建行」和「null 增量」。同一合法讲座键的匿名重复投票、
-- 设备级幂等与时间窗限流仍是后续独立批次，不因本补丁而视为已经解决。
-- ============================================================

"""


def build():
    r = subprocess.run([sys.executable, GEN_SQL], capture_output=True,
                       text=True, encoding='utf-8')
    if r.returncode != 0:
        sys.exit('gen_supabase_sql.py 运行失败: ' + r.stderr[-400:])
    sql = r.stdout.replace('\r\n', '\n')
    if not sql.endswith('\n'):
        sql += '\n'
    return HEADER + sql


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    text = build()
    if mode == '--stdout':
        sys.stdout.write(text)
        return 0
    if mode == '--check':
        if not os.path.exists(OUT_PATH):
            print('缺少 ' + os.path.relpath(OUT_PATH, ROOT) + '——请先运行本脚本生成')
            return 1
        with open(OUT_PATH, encoding='utf-8', newline='') as f:
            cur = f.read().replace('\r\n', '\n')
        if cur != text:
            print('补丁已过期：与 scripts/gen_supabase_sql.py 产出不一致，请重新生成')
            return 1
        print('补丁与生成器同源：' + os.path.relpath(OUT_PATH, ROOT))
        return 0
    with open(OUT_PATH, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print('已生成 ' + os.path.relpath(OUT_PATH, ROOT))
    return 0


if __name__ == '__main__':
    sys.exit(main())
