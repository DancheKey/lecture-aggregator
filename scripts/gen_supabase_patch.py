#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「当前唯一应跑」的 Supabase 云端补丁：说明头 + 同源 SQL 正文。

为什么需要这个脚本（P3-8，2026-10-18 复查）：
  此前补丁是手写说明头再粘一份 SQL（docs/supabase-patch-guard-<日期>.sql）。
  手拼版没有任何门禁锁同源——gen_supabase_sql.py 一改，补丁就静默过期，
  用户照旧文件跑一遍 = 修了个寂寞（上线当晚已踩过两次）。本脚本让补丁
  由单一事实源产出，tests/test_supabase_migration.py::test_09 锁死同源。

产出固定名 supabase-patch-current.sql（仓库根，与 supabase-migration.sql 并列）：
  只保留一份「当前应跑」的补丁，历史补丁（docs/supabase-patch-*.sql）
  是当时的操作记录，不再更新，也不该再跑。
  ⚠ 不放 docs/：该目录整体在 .gitignore 中，入库后 CI 的 test_09
  同源门禁会因文件缺失而 skip，等于没有门禁。

用法：
  python scripts/gen_supabase_patch.py            # 写 supabase-patch-current.sql
  python scripts/gen_supabase_patch.py --stdout   # 只打印不写（测试用，无副作用）
  python scripts/gen_supabase_patch.py --check    # 只比对不写，不一致返回 1
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_SQL = os.path.join(ROOT, 'scripts', 'gen_supabase_sql.py')
OUT_PATH = os.path.join(ROOT, 'supabase-patch-current.sql')

# --stdout 模式输出的正文含 ⚠ 等非 GBK 字符：Windows 默认 cp936 下 write 崩溃
# （与 gen_supabase_sql.py 同款问题，2026-10-18 修复）。
try:
    sys.stdout.reconfigure(newline='\n', encoding='utf-8')
except Exception:
    pass

# 说明头：静态字符串（不含时间戳等易变量，保证同源比对确定性）
HEADER = """-- ============================================================
-- 讲座聚合站 · Supabase 云端补丁（当前唯一应跑的一份）
-- 本文件由 scripts/gen_supabase_patch.py 生成，请勿手改——
--   改了会被 tests/test_supabase_migration.py::test_09 抓出来。
-- 与入库的 supabase-migration.sql 正文完全同源，区别只在本说明头。
--
-- 执行位置：Supabase 控制台 → SQL Editor → 新建 query → 粘贴整段运行
-- 幂等：create or replace / drop if exists / set default 均可重复执行，
--   不会覆盖云端已产生的真实增量，可放心重跑。
--
-- 2026-10-19 版包含（均有实测依据）：
--   ① 列兜底：默认值 0 + not null（缺默认值的表插新 URL 会 23502）
--   ② 值域 check 约束：visits<=100万 / likes<=1000 / wants<=1000
--   ③ 三个增量 RPC：insert 与 on conflict 共用同一个钳过的 d，杜绝两路漂移；
--      单次步长统一钳到 1（visit 原 100；like/want 此前误用累计上限 1000 当步长）。
--      like/want 允许 -1：取消点赞必须真的减（原 greatest(p_delta,0) 把 -1 钳成 0，
--      「取消」是空操作，且本地已删键 → 再点又 +1，可反复刷高）。
--      已知取舍：匿名者可对他人计数 -1，彻底方案是设备级点赞表（独立迭代）。
--   ④ p_url 双守卫：长度 1..300 + 前缀必须 http(s):// 或 __（保留键族）
--      ——原零校验，anon 可建空键/超长键行，且只限长度挡不住批量造行
--   ⑤ RLS：先 enable row level security 再建只读 policy
--      ——原 SQL 只有 create policy，照它重建库会得到「RLS 关着 + anon 全表
--         ALL 权限」，匿名可绕过 RPC 直写整表
--
-- 跑完复测（5 条，预期见括号）：
--   curl -s -X POST "$URL/rest/v1/rpc/increment_visit" -H "apikey: $KEY" \\
--     -H "Content-Type: application/json" -d '{"p_url":"","p_delta":1}'   → 400（空键拒绝）
--   curl -s -X POST "$URL/rest/v1/rpc/increment_visit" -H "apikey: $KEY" \\
--     -H "Content-Type: application/json" -d "{\\"p_url\\":\\"$(python -c 'print("x"*400)')\\",\\"p_delta\\":1}"  → 400（超长键拒绝）
--   curl -s -X POST "$URL/rest/v1/rpc/increment_visit" -H "apikey: $KEY" \\
--     -H "Content-Type: application/json" -d '{"p_url":"abc","p_delta":1}' → 400（非法前缀拒绝）
--   curl -s -X POST "$URL/rest/v1/rpc/increment_visit" -H "apikey: $KEY" \\
--     -H "Content-Type: application/json" -d '{"p_url":"__site__","p_delta":1}'  → 200 且 visits +1（正常放行）
--   curl -s -X POST "$URL/rest/v1/rpc/increment_like" -H "apikey: $KEY" \\
--     -H "Content-Type: application/json" -d '{"p_url":"__site__","p_delta":-1}' → 200 且 likes -1（取消生效）
-- ============================================================

"""


def build():
    """返回补丁全文（说明头 + 同源 SQL）。"""
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
        # 不写盘：测试/CI 用它做同源比对，避免产生工作区副作用
        sys.stdout.write(text)
        return 0
    if mode == '--check':
        if not os.path.exists(OUT_PATH):
            print('缺少 ' + os.path.relpath(OUT_PATH, ROOT) + '——请先运行本脚本生成')
            return 1
        cur = open(OUT_PATH, encoding='utf-8', newline='').read()
        cur = cur.replace('\r\n', '\n')
        if cur != text:
            print('补丁已过期：与 scripts/gen_supabase_sql.py 产出不一致，请重新生成')
            return 1
        print('补丁与生成器同源：' + os.path.relpath(OUT_PATH, ROOT))
        return 0
    # 默认写盘。CRLF 会让 git 产生行尾伪差异，强制 LF。
    with open(OUT_PATH, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print('已生成 ' + os.path.relpath(OUT_PATH, ROOT))
    return 0


if __name__ == '__main__':
    sys.exit(main())
