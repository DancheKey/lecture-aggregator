#!/usr/bin/env python3
# 生成 Supabase 迁移 SQL：安全约束 + RPC 增量函数 + 存量数据导入。
# 用法：python scripts/gen_supabase_sql.py > supabase-migration.sql
# 然后在 Supabase 控制台 SQL Editor 粘贴执行（一次即可，幂等）。
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS_PATH = os.path.join(ROOT, 'data', 'lecture_stats.json')

# 值域上限：单讲座计数天花板（防刷兜底；显示层另有 capDisplay 300+ 封顶）
CAP_VISITS = 1_000_000
CAP_LIKES = 100_000
CAP_WANTS = 100_000

HEADER = """-- ============================================================
-- 讲座聚合站 · Supabase 迁移 SQL（2026-10-17 生成，可重复执行）
-- 执行位置：Supabase 控制台 → SQL Editor → 粘贴整段运行
-- 前置：已建表 lecture_stats(url text PK, visits int8, likes int8, wants int8) 且已开 RLS
-- ============================================================

-- ① 值域约束（粗粒度防刷兜底，脚本最多刷到这里就被数据库拦住）
alter table lecture_stats drop constraint if exists lecture_stats_visits_range;
alter table lecture_stats drop constraint if exists lecture_stats_likes_range;
alter table lecture_stats drop constraint if exists lecture_stats_wants_range;
alter table lecture_stats
  add constraint lecture_stats_visits_range check (visits between 0 and {cap_v}),
  add constraint lecture_stats_likes_range check (likes between 0 and {cap_l}),
  add constraint lecture_stats_wants_range check (wants between 0 and {cap_w});

-- ② RPC 增量函数：前端只能 +/-1，写不了任意值（security definer = 以所有者权限跑，
--    anon 无需表级 INSERT/UPDATE 权限；表级写权限保持关闭，只走函数这一道闸门）
create or replace function increment_like(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats;
begin
  insert into lecture_stats (url, likes) values (p_url, greatest(p_delta, 0))
  on conflict (url) do update set likes = greatest(lecture_stats.likes + p_delta, 0)
  returning * into v;
  return to_jsonb(v) - 'url';
end $$;

create or replace function increment_want(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats;
begin
  insert into lecture_stats (url, wants) values (p_url, greatest(p_delta, 0))
  on conflict (url) do update set wants = greatest(lecture_stats.wants + p_delta, 0)
  returning * into v;
  return to_jsonb(v) - 'url';
end $$;

create or replace function increment_visit(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats;
begin
  insert into lecture_stats (url, visits) values (p_url, greatest(p_delta, 0))
  on conflict (url) do update set visits = least(greatest(lecture_stats.visits + p_delta, 0), {cap_v})
  returning * into v;
  return to_jsonb(v) - 'url';
end $$;

-- ③ RLS：anon 只读 + 可执行三个函数；表级写权限一律不授（写入只走 RPC）
drop policy if exists "anon read" on lecture_stats;
create policy "anon read" on lecture_stats for select using (true);
grant execute on function increment_like(text, int) to anon;
grant execute on function increment_want(text, int) to anon;
grant execute on function increment_visit(text, int) to anon;

-- ④ 存量导入（本地 data/lecture_stats.json，幂等 upsert）
"""


def sql_str(s):
    return "'" + str(s).replace("'", "''") + "'"


def main():
    if not os.path.exists(STATS_PATH):
        sys.exit('找不到 ' + STATS_PATH)
    with open(STATS_PATH, encoding='utf-8') as f:
        raw = json.load(f)
    # 兼容两种形态：裸 dict（url→统计）或 {stats: {...}} 包装
    items = raw.get('stats', raw) if isinstance(raw, dict) else {}

    print(HEADER.format(cap_v=CAP_VISITS, cap_l=CAP_LIKES, cap_w=CAP_WANTS))
    rows = 0
    for url, st in sorted(items.items()):
        v = int(st.get('visits', 0) or 0)
        l = int(st.get('likes', 0) or 0)
        w = int(st.get('wants', 0) or 0)
        print('insert into lecture_stats (url, visits, likes, wants) values '
              '({}, {}, {}, {}) on conflict (url) do update set '
              'visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;'
              .format(sql_str(url), v, l, w))
        rows += 1
    if rows == 0:
        print('-- （本地无存量，跳过导入）')
    print('-- 完成：{} 条存量。'.format(rows), file=sys.stderr)


if __name__ == '__main__':
    main()
