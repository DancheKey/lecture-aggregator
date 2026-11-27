#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Supabase 迁移 SQL：白名单、列兜底、值域约束、RPC 与 RLS。

用法：
  python scripts/gen_supabase_sql.py                  # 完整补丁（含当前讲座白名单）
  python scripts/gen_supabase_sql.py --whitelist-only # 仅同步后续新增的讲座键
  python scripts/gen_supabase_sql.py --with-existing  # 额外导入本地 data/lecture_stats.json
"""
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(newline='\n', encoding='utf-8')
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS_PATH = os.path.join(ROOT, 'data', 'lecture_stats.json')
LECTURES_PATH = os.path.join(ROOT, 'site', 'lectures.json')

CAP_VISITS = 1_000_000
CAP_LIKES = 1_000
CAP_WANTS = 1_000
CAP_CALS = 1_000
CAP_STEP_VISITS = 1
CAP_STEP_LIKES = 1
CAP_STEP_WANTS = 1
CAP_STEP_CALS = 1
CAP_STEP_NEG = -1
MAX_URL_LEN = 300
SITE_KEY = '__site__'
LECTURE_ACTIONS = ('visit', 'like', 'want', 'cal')

HEADER = """-- ============================================================
-- 讲座聚合站 · Supabase 迁移 SQL（2026-11-26 修订，可重复执行）
-- 执行位置：Supabase 控制台 → SQL Editor → 粘贴整段运行
-- 前置：已建表 public.lecture_stats(url text PK, visits int8, likes int8, wants int8)
-- 安全边界：anon key 公开，所有写操作只走白名单 RPC；表级写权限显式关闭。
-- ============================================================

-- ① 列兜底：默认值 0 + not null；存量 null 先归零，避免插入新键时 23502。
update public.lecture_stats set visits = 0 where visits is null;
update public.lecture_stats set likes = 0 where likes is null;
update public.lecture_stats set wants = 0 where wants is null;
alter table public.lecture_stats add column if not exists cal_downloads int8;
update public.lecture_stats set cal_downloads = 0 where cal_downloads is null;
alter table public.lecture_stats
  alter column visits set default 0,
  alter column likes set default 0,
  alter column wants set default 0,
  alter column cal_downloads set default 0,
  alter column visits set not null,
  alter column likes set not null,
  alter column wants set not null,
  alter column cal_downloads set not null;

-- ② 值域约束：单条讲座的累计天花板，防止异常增量击穿计数。
alter table public.lecture_stats drop constraint if exists lecture_stats_visits_range;
alter table public.lecture_stats drop constraint if exists lecture_stats_likes_range;
alter table public.lecture_stats drop constraint if exists lecture_stats_wants_range;
alter table public.lecture_stats drop constraint if exists lecture_stats_cals_range;
alter table public.lecture_stats
  add constraint lecture_stats_visits_range check (visits between 0 and {cap_v}),
  add constraint lecture_stats_likes_range check (likes between 0 and {cap_l}),
  add constraint lecture_stats_wants_range check (wants between 0 and {cap_w}),
  add constraint lecture_stats_cals_range check (cal_downloads between 0 and {cap_c});

-- ③ 统计键白名单：前端 url=in. 只是查询过滤，不能作为安全边界。
--    每个讲座键允许四种动作；__site__ 只允许 visit。表不开放给 anon/authenticated，
--    后续由受信任的 SQL Editor 或 service_role 同步，匿名用户不能自行扩表。
create table if not exists public.lecture_allowed_keys (
  url text primary key,
  allowed_actions text[] not null,
  constraint lecture_allowed_keys_url_shape
    check (length(url) between 1 and {max_len} and url ~* '^(https?://|__)'),
  constraint lecture_allowed_keys_actions
    check (cardinality(allowed_actions) > 0
           and allowed_actions <@ array['visit','like','want','cal']::text[])
);
alter table public.lecture_allowed_keys enable row level security;
revoke all on table public.lecture_allowed_keys from public, anon, authenticated;
grant all on table public.lecture_allowed_keys to service_role;
"""

BODY = """
-- ④ 白名单探测函数：只供 security definer RPC 内部使用，撤销 PUBLIC/anon/authenticated 执行权。
create or replace function public.lecture_stat_key_allowed(p_url text, p_action text)
returns boolean language sql stable security definer
set search_path = pg_catalog, public
as $$
  select exists (
    select 1
    from public.lecture_allowed_keys k
    where k.url = p_url
      and p_action = any(k.allowed_actions)
  );
$$;
revoke all on function public.lecture_stat_key_allowed(text, text) from public, anon, authenticated;

-- ⑤ 增量 RPC：所有关系名显式指定 public，搜索路径固定；null 增量直接拒绝。
--    like/want 保留 -1 取消语义；visit/cal 为单向动作，负增量仍钳成 0。
create or replace function public.increment_like(p_url text, p_delta int)
returns json language plpgsql security definer
set search_path = pg_catalog, public
as $$
declare v public.lecture_stats%rowtype; d int;
begin
  if p_url is null or length(p_url) = 0 or length(p_url) > {max_len} then
    raise exception 'p_url must be 1..{max_len} chars' using errcode = 22023;
  end if;
  if p_url !~* '^(https?://|__)' then
    raise exception 'p_url must be an http(s) URL or __-prefixed key' using errcode = 22023;
  end if;
  if p_delta is null then
    raise exception 'p_delta must not be null' using errcode = 22023;
  end if;
  if not public.lecture_stat_key_allowed(p_url, 'like') then
    raise exception 'stat key is not allowed for like' using errcode = 22023;
  end if;
  d := least(greatest(p_delta, {step_neg}), {step_l});
  insert into public.lecture_stats (url, visits, likes, wants)
    values (p_url, 0, greatest(d, 0), 0)
  on conflict (url) do update set likes = least(greatest(lecture_stats.likes + d, 0), {cap_l})
  returning * into v;
  return pg_catalog.to_jsonb(v) - 'url';
end $$;

create or replace function public.increment_want(p_url text, p_delta int)
returns json language plpgsql security definer
set search_path = pg_catalog, public
as $$
declare v public.lecture_stats%rowtype; d int;
begin
  if p_url is null or length(p_url) = 0 or length(p_url) > {max_len} then
    raise exception 'p_url must be 1..{max_len} chars' using errcode = 22023;
  end if;
  if p_url !~* '^(https?://|__)' then
    raise exception 'p_url must be an http(s) URL or __-prefixed key' using errcode = 22023;
  end if;
  if p_delta is null then
    raise exception 'p_delta must not be null' using errcode = 22023;
  end if;
  if not public.lecture_stat_key_allowed(p_url, 'want') then
    raise exception 'stat key is not allowed for want' using errcode = 22023;
  end if;
  d := least(greatest(p_delta, {step_neg}), {step_w});
  insert into public.lecture_stats (url, visits, likes, wants)
    values (p_url, 0, 0, greatest(d, 0))
  on conflict (url) do update set wants = least(greatest(lecture_stats.wants + d, 0), {cap_w})
  returning * into v;
  return pg_catalog.to_jsonb(v) - 'url';
end $$;

create or replace function public.increment_visit(p_url text, p_delta int)
returns json language plpgsql security definer
set search_path = pg_catalog, public
as $$
declare v public.lecture_stats%rowtype; d int;
begin
  if p_url is null or length(p_url) = 0 or length(p_url) > {max_len} then
    raise exception 'p_url must be 1..{max_len} chars' using errcode = 22023;
  end if;
  if p_url !~* '^(https?://|__)' then
    raise exception 'p_url must be an http(s) URL or __-prefixed key' using errcode = 22023;
  end if;
  if p_delta is null then
    raise exception 'p_delta must not be null' using errcode = 22023;
  end if;
  if not public.lecture_stat_key_allowed(p_url, 'visit') then
    raise exception 'stat key is not allowed for visit' using errcode = 22023;
  end if;
  d := least(greatest(p_delta, 0), {step_v});
  insert into public.lecture_stats (url, visits, likes, wants)
    values (p_url, d, 0, 0)
  on conflict (url) do update set visits = least(greatest(lecture_stats.visits + d, 0), {cap_v})
  returning * into v;
  return pg_catalog.to_jsonb(v) - 'url';
end $$;

create or replace function public.increment_cal(p_url text, p_delta int)
returns json language plpgsql security definer
set search_path = pg_catalog, public
as $$
declare v public.lecture_stats%rowtype; d int;
begin
  if p_url is null or length(p_url) = 0 or length(p_url) > {max_len} then
    raise exception 'p_url must be 1..{max_len} chars' using errcode = 22023;
  end if;
  if p_url !~* '^(https?://|__)' then
    raise exception 'p_url must be an http(s) URL or __-prefixed key' using errcode = 22023;
  end if;
  if p_delta is null then
    raise exception 'p_delta must not be null' using errcode = 22023;
  end if;
  if not public.lecture_stat_key_allowed(p_url, 'cal') then
    raise exception 'stat key is not allowed for cal' using errcode = 22023;
  end if;
  d := least(greatest(p_delta, 0), {step_c});
  insert into public.lecture_stats (url, visits, likes, wants, cal_downloads)
    values (p_url, 0, 0, 0, greatest(d, 0))
  on conflict (url) do update set cal_downloads = least(greatest(lecture_stats.cal_downloads + d, 0), {cap_c})
  returning * into v;
  return pg_catalog.to_jsonb(v) - 'url';
end $$;

-- ⑥ RLS 与最小权限：即使将来误开写策略，anon/authenticated 也没有表级写权限。
alter table public.lecture_stats enable row level security;
drop policy if exists "anon read" on public.lecture_stats;
create policy "anon read" on public.lecture_stats for select using (true);
revoke all on table public.lecture_stats from public, anon, authenticated;
grant select on table public.lecture_stats to anon, authenticated;
grant all on table public.lecture_stats to service_role;

revoke all on function public.increment_like(text, int) from public;
revoke all on function public.increment_want(text, int) from public;
revoke all on function public.increment_visit(text, int) from public;
revoke all on function public.increment_cal(text, int) from public;
grant execute on function public.increment_like(text, int) to anon, authenticated, service_role;
grant execute on function public.increment_want(text, int) to anon, authenticated, service_role;
grant execute on function public.increment_visit(text, int) to anon, authenticated, service_role;
grant execute on function public.increment_cal(text, int) to anon, authenticated, service_role;

-- ⑦ 存量导入默认关闭；启用后只补本地历史计数，不覆盖云端真实增量。
--    运行：python scripts/gen_supabase_sql.py --with-existing > supabase-migration.sql
--    新讲座发布后同步白名单：python scripts/gen_supabase_sql.py --whitelist-only
--    生成的 SQL 在 SQL Editor 执行；自动同步需另行配置受信任的 service_role secret。
"""


def sql_str(value):
    return "'" + str(value).replace("'", "''") + "'"


def load_allowed_keys():
    if not os.path.exists(LECTURES_PATH):
        sys.exit('找不到 ' + LECTURES_PATH)
    with open(LECTURES_PATH, encoding='utf-8') as f:
        raw = json.load(f)
    items = raw.get('data', []) if isinstance(raw, dict) else []
    keys = {SITE_KEY}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get('ck')
        if not key:
            continue
        if not isinstance(key, str) or len(key) > MAX_URL_LEN or not re.match(r'^(https?://|__)', key, re.I):
            sys.exit('非法讲座统计键：' + repr(key))
        keys.add(key)
    return sorted(keys)


def print_whitelist():
    print('-- 当前前端讲座键白名单（由 site/lectures.json 确定性生成，按 URL 排序）。')
    print('insert into public.lecture_allowed_keys (url, allowed_actions) values')
    rows = []
    for key in load_allowed_keys():
        actions = ('visit',) if key == SITE_KEY else LECTURE_ACTIONS
        values = ','.join(sql_str(action) for action in actions)
        rows.append(f'  ({sql_str(key)}, array[{values}]::text[])')
    print(',\n'.join(rows))
    print('on conflict (url) do update set allowed_actions = excluded.allowed_actions;')


def main():
    args = set(sys.argv[1:])
    unknown = args - {'--with-existing', '--whitelist-only'}
    if unknown:
        sys.exit('未知参数：' + ' '.join(sorted(unknown)))
    if '--whitelist-only' in args and '--with-existing' in args:
        sys.exit('--whitelist-only 不能与 --with-existing 同用')
    if '--whitelist-only' in args:
        print_whitelist()
        return
    sys.stdout.write(HEADER.format(cap_v=CAP_VISITS, cap_l=CAP_LIKES, cap_w=CAP_WANTS,
                                   cap_c=CAP_CALS, max_len=MAX_URL_LEN))
    print_whitelist()
    sys.stdout.write(BODY.format(cap_v=CAP_VISITS, cap_l=CAP_LIKES, cap_w=CAP_WANTS,
                                 cap_c=CAP_CALS, max_len=MAX_URL_LEN,
                                 step_v=CAP_STEP_VISITS, step_l=CAP_STEP_LIKES,
                                 step_w=CAP_STEP_WANTS, step_c=CAP_STEP_CALS,
                                 step_neg=CAP_STEP_NEG))
    if '--with-existing' not in sys.argv:
        return
    if not os.path.exists(STATS_PATH):
        sys.exit('找不到 ' + STATS_PATH)
    with open(STATS_PATH, encoding='utf-8') as f:
        raw_stats = json.load(f)
    items = raw_stats.get('stats', raw_stats) if isinstance(raw_stats, dict) else {}
    rows = 0
    for url, stats in sorted(items.items()):
        visits = int(stats.get('visits', 0) or 0)
        likes = int(stats.get('likes', 0) or 0)
        wants = int(stats.get('wants', 0) or 0)
        print('insert into public.lecture_stats (url, visits, likes, wants) values '
              f'({sql_str(url)}, {visits}, {likes}, {wants}) on conflict (url) do nothing;')
        rows += 1
    print('-- 完成：{} 条存量。'.format(rows), file=sys.stderr)


if __name__ == '__main__':
    main()
