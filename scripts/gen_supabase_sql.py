#!/usr/bin/env python3
# 生成 Supabase 迁移 SQL：列兜底 + 值域约束 + RPC 增量函数 + RLS。
# 用法：
#   python scripts/gen_supabase_sql.py                 # 仅结构（默认，不含存量导入）
#   python scripts/gen_supabase_sql.py --with-existing # 额外导入本地 data/lecture_stats.json
# 然后在 Supabase 控制台 SQL Editor 粘贴执行（一次即可，幂等）。
# 说明：存量导入用 on conflict do nothing，绝不覆盖云端已产生的真实增量；
# 2026-10-17 用户决策：云端从零积累，默认不带存量。
import json
import os
import sys

# Windows 上 stdout 文本模式会把 \n 翻译成 \r\n，重定向出的 SQL 带 CRLF；
# git 入库虽会 normalize，但消除写盘时的 CRLF 可免去每次 commit 的行尾警告。
try:
    sys.stdout.reconfigure(newline='\n')
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS_PATH = os.path.join(ROOT, 'data', 'lecture_stats.json')

# 值域上限：单讲座计数天花板（防刷兜底；显示层另有 capDisplay 300+ 封顶）
CAP_VISITS = 1_000_000
# 2026-10-17 深夜修复：原值 100_000 太松——anon key 公开下，直接灌 p_delta=99999
# 实测可把 likes/wants 刷到 99999（函数只做了 greatest 下钳）。降至 1000：仍远超
# 校园讲座真实量级，又与显示层 300+ 封顶留足余量，刷的成本收益比骤降。
CAP_LIKES = 1_000
CAP_WANTS = 1_000

HEADER = """-- ============================================================
-- 讲座聚合站 · Supabase 迁移 SQL（2026-10-17 生成，可重复执行）
-- 执行位置：Supabase 控制台 → SQL Editor → 粘贴整段运行
-- 前置：已建表 lecture_stats(url text PK, visits int8, likes int8, wants int8) 且已开 RLS
-- ============================================================

-- ① 列兜底：2026-10-17 实测踩坑——建表时未设默认值的列会让 RPC 插入新 URL 时
--   违反 not-null（报 23502）。此处统一补默认值 0、清空存量 null、补 not-null；
--   已正确的库重复执行无副作用。
update lecture_stats set visits = 0 where visits is null;
update lecture_stats set likes = 0 where likes is null;
update lecture_stats set wants = 0 where wants is null;
alter table lecture_stats
  alter column visits set default 0,
  alter column likes set default 0,
  alter column wants set default 0,
  alter column visits set not null,
  alter column likes set not null,
  alter column wants set not null;

-- ② 值域约束（粗粒度防刷兜底，脚本最多刷到这里就被数据库拦住）
alter table lecture_stats drop constraint if exists lecture_stats_visits_range;
alter table lecture_stats drop constraint if exists lecture_stats_likes_range;
alter table lecture_stats drop constraint if exists lecture_stats_wants_range;
alter table lecture_stats
  add constraint lecture_stats_visits_range check (visits between 0 and {cap_v}),
  add constraint lecture_stats_likes_range check (likes between 0 and {cap_l}),
  add constraint lecture_stats_wants_range check (wants between 0 and {cap_w});

-- ③ RPC 增量函数：前端只能 +/-1，写不了任意值（security definer = 以所有者权限跑，
--    anon 无需表级 INSERT/UPDATE 权限；表级写权限保持关闭，只走函数这一道闸门）。
--    insert 必须显式写全三列——只填目标列会依赖列默认值，缺默认值的表直接 23502。
--    ⚠ 2026-10-17 深夜修复：update 与 insert 两条路径都必须 least 上钳——此前
--    like/want 只有 greatest(...,0) 下钳，实测灌 p_delta=99999 可直接刷爆计数。
create or replace function increment_like(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats;
begin
  insert into lecture_stats (url, visits, likes, wants)
    values (p_url, 0, least(greatest(p_delta, 0), {cap_l}), 0)
  on conflict (url) do update set likes = least(greatest(lecture_stats.likes + p_delta, 0), {cap_l})
  returning * into v;
  return to_jsonb(v) - 'url';
end $$;

create or replace function increment_want(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats;
begin
  insert into lecture_stats (url, visits, likes, wants)
    values (p_url, 0, 0, least(greatest(p_delta, 0), {cap_w}))
  on conflict (url) do update set wants = least(greatest(lecture_stats.wants + p_delta, 0), {cap_w})
  returning * into v;
  return to_jsonb(v) - 'url';
end $$;

create or replace function increment_visit(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats;
begin
  insert into lecture_stats (url, visits, likes, wants)
    values (p_url, least(greatest(p_delta, 0), {cap_v}), 0, 0)
  on conflict (url) do update set visits = least(greatest(lecture_stats.visits + p_delta, 0), {cap_v})
  returning * into v;
  return to_jsonb(v) - 'url';
end $$;

-- ④ RLS：anon 只读 + 可执行三个函数；表级写权限一律不授（写入只走 RPC）
drop policy if exists "anon read" on lecture_stats;
create policy "anon read" on lecture_stats for select using (true);
grant execute on function increment_like(text, int) to anon;
grant execute on function increment_want(text, int) to anon;
grant execute on function increment_visit(text, int) to anon;

-- ⑤ 存量导入：默认不生成（2026-10-17 决策：云端从零积累）。
--   启用方式：python scripts/gen_supabase_sql.py --with-existing > supabase-migration.sql
--   即使用户启用，也用 on conflict do nothing——重复执行不得把云端已产生的
--   真实增量覆盖回本地旧值；需要校准请人工核对后单独处理。
"""


def sql_str(s):
    return "'" + str(s).replace("'", "''") + "'"


def main():
    with_existing = '--with-existing' in sys.argv
    if with_existing and not os.path.exists(STATS_PATH):
        sys.exit('找不到 ' + STATS_PATH)
    print(HEADER.format(cap_v=CAP_VISITS, cap_l=CAP_LIKES, cap_w=CAP_WANTS))
    if not with_existing:
        print('-- ⑤ 存量导入：未启用（默认）。如需导入本地历史计数，')
        print('--    运行 python scripts/gen_supabase_sql.py --with-existing > supabase-migration.sql')
        return
    with open(STATS_PATH, encoding='utf-8') as f:
        raw = json.load(f)
    # 兼容两种形态：裸 dict（url→统计）或 {stats: {...}} 包装
    items = raw.get('stats', raw) if isinstance(raw, dict) else {}
    rows = 0
    for url, st in sorted(items.items()):
        v = int(st.get('visits', 0) or 0)
        l = int(st.get('likes', 0) or 0)
        w = int(st.get('wants', 0) or 0)
        print('insert into lecture_stats (url, visits, likes, wants) values '
              '({}, {}, {}, {}) on conflict (url) do nothing;'
              .format(sql_str(url), v, l, w))
        rows += 1
    if rows == 0:
        print('-- （本地无存量，跳过导入）')
    print('-- 完成：{} 条存量。'.format(rows), file=sys.stderr)


if __name__ == '__main__':
    main()
