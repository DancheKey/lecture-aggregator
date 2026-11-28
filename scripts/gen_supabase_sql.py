#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Supabase 迁移 SQL：白名单、列兜底、值域约束、RPC 与 RLS。

用法：
  python scripts/gen_supabase_sql.py                  # 完整补丁（含当前讲座白名单）
  python scripts/gen_supabase_sql.py --whitelist-only # 仅同步后续新增的讲座键
  python scripts/gen_supabase_sql.py --with-existing  # 额外导入本地 data/lecture_stats.json
"""
import hashlib
import json
import os
import re
import secrets
import sys

try:
    sys.stdout.reconfigure(newline='\n', encoding='utf-8')
    sys.stderr.reconfigure(newline='\n', encoding='utf-8')
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
MAX_WHITELIST_KEYS = 10_000
SITE_KEY = '__site__'
LECTURE_ACTIONS = ('visit', 'like', 'want', 'cal')
SYNC_TOKEN_HASH = 'de821c43aa7742513d36008090e2f997b7225bb31659a200cac770dd835713a0'

HEADER = """-- ============================================================
-- 讲座聚合站 · Supabase 迁移 SQL（2026-11-28 修订，可重复执行）
-- 执行位置：Supabase 控制台 → SQL Editor → 粘贴整段运行
-- 前置：已建表 public.lecture_stats(url text PK, visits int8, likes int8, wants int8)
-- 安全边界：anon key 公开，所有写操作只走白名单 RPC；表级写权限显式关闭。
-- 白名单由 GitHub Actions 通过 sync_whitelist(jsonb) 自动同步；
-- 同步令牌只存哈希，函数只 upsert、不删除，并限制白名单总行数。
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

-- 同步令牌只保存 SHA-256 哈希；previous_token_hash 用于无缝轮换。
create table if not exists public.lecture_whitelist_sync (
  id boolean primary key default true check (id),
  token_hash text not null check (token_hash ~ '^[0-9a-f]{{64}}$'),
  previous_token_hash text check (previous_token_hash ~ '^[0-9a-f]{{64}}$'),
  updated_at timestamptz not null default now()
);
alter table public.lecture_whitelist_sync enable row level security;
revoke all on table public.lecture_whitelist_sync from public, anon, authenticated;
grant all on table public.lecture_whitelist_sync to service_role;
insert into public.lecture_whitelist_sync (id, token_hash, previous_token_hash)
values (true, '{sync_hash}', null)
on conflict (id) do nothing;
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

-- GitHub Actions 专用：只 upsert 当前讲座键，不删除历史键。
-- 令牌通过请求头传入，数据库只保存哈希；轮换期间同时接受上一条哈希。
create or replace function public.sync_whitelist(p_keys jsonb)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, public
as $$
declare
  v_header jsonb;
  v_token text;
  v_hash text;
  v_state public.lecture_whitelist_sync%rowtype;
  v_received bigint;
  v_unique bigint;
  v_existing bigint;
  v_new bigint;
  v_upserted bigint;
  v_total bigint;
begin
  if p_keys is null or jsonb_typeof(p_keys) <> 'array' then
    raise exception 'p_keys must be a jsonb array' using errcode = '22023';
  end if;
  if exists (
    select 1 from jsonb_array_elements(p_keys) e
    where jsonb_typeof(e.value) <> 'string'
  ) then
    raise exception 'p_keys must contain only strings' using errcode = '22023';
  end if;

  select count(*), count(distinct value)
    into v_received, v_unique
  from jsonb_array_elements_text(p_keys);
  if v_received < 1 or v_received > {max_keys} then
    raise exception 'p_keys count must be 1..{max_keys}' using errcode = '22023';
  end if;
  if v_received <> v_unique then
    raise exception 'p_keys must not contain duplicates' using errcode = '22023';
  end if;
  if not exists (
    select 1 from jsonb_array_elements_text(p_keys) where value = '__site__'
  ) then
    raise exception 'p_keys must contain __site__' using errcode = '22023';
  end if;
  if exists (
    select 1
    from jsonb_array_elements_text(p_keys)
    where value <> '__site__'
      and (length(value) > {max_len} or value !~* '^https?://[^/?#[:space:]]+')
  ) then
    raise exception 'p_keys contains an invalid lecture key' using errcode = '22023';
  end if;

  v_header := nullif(current_setting('request.headers', true), '')::jsonb;
  v_token := nullif(v_header ->> 'x-sync-token', '');
  if v_token is null or length(v_token) < 32 then
    raise exception 'whitelist sync is unauthorized' using errcode = '42501';
  end if;
  v_hash := encode(sha256(convert_to(v_token, 'UTF8')), 'hex');
  select * into v_state from public.lecture_whitelist_sync where id = true;
  if not found
     or (v_hash <> v_state.token_hash
         and v_hash is distinct from v_state.previous_token_hash) then
    raise exception 'whitelist sync is unauthorized' using errcode = '42501';
  end if;

  -- 串行化持令牌的同步调用，避免并发都在锁前通过行数检查后共同插入。
  lock table public.lecture_allowed_keys in share row exclusive mode;

  select count(*) into v_existing from public.lecture_allowed_keys;
  select count(*) into v_new
  from (
    select distinct value from jsonb_array_elements_text(p_keys)
  ) q
  left join public.lecture_allowed_keys k on k.url = q.value
  where k.url is null;
  if v_existing + v_new > {max_keys} then
    raise exception 'whitelist row cap exceeded' using errcode = '22023';
  end if;

  insert into public.lecture_allowed_keys (url, allowed_actions)
  select value,
         case when value = '__site__'
              then array['visit']::text[]
              else array['visit','like','want','cal']::text[]
         end
  from jsonb_array_elements_text(p_keys)
  on conflict (url) do update set allowed_actions = excluded.allowed_actions;
  get diagnostics v_upserted = row_count;

  select count(*) into v_total from public.lecture_allowed_keys;
  return jsonb_build_object(
    'received', v_received,
    'upserted', v_upserted,
    'total', v_total
  );
end $$;

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

revoke all on function public.sync_whitelist(jsonb) from public;
grant execute on function public.sync_whitelist(jsonb) to anon, service_role;

-- ⑦ 存量导入默认关闭；启用后只补本地历史计数，不覆盖云端真实增量。
--    运行：python scripts/gen_supabase_sql.py --with-existing > supabase-migration.sql
--    日常白名单由 GitHub Actions 调 sync_whitelist 自动同步；SQL Editor 手动同步仅作兜底。
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
        if key != SITE_KEY and not re.match(r'^https?://[^/?#\s]+', key, re.I):
            sys.exit('非法讲座统计键：' + repr(key))
        keys.add(key)
    return sorted(keys)


def print_whitelist_json():
    print(json.dumps(load_allowed_keys(), ensure_ascii=False, separators=(',', ':')))


def rotate_sync_token():
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    token_path = os.path.join(ROOT, 'tmp', 'supabase_whitelist_sync_token.txt')
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
        f.write(token + '\n')
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass
    print('新令牌已写入（被 gitignore 忽略）：' + token_path)
    print('把文件内容添加到 GitHub Secrets: SUPABASE_WHITELIST_SYNC_TOKEN，然后执行：')
    print('update public.lecture_whitelist_sync')
    print('set previous_token_hash = token_hash,')
    print(f"    token_hash = '{token_hash}',")
    print('    updated_at = now()')
    print('where id = true;')
    print('GitHub Secret 更新成功后，可执行以下语句关闭旧令牌接受窗口：')
    print('update public.lecture_whitelist_sync set previous_token_hash = null where id = true;')


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
    known = {'--with-existing', '--whitelist-only', '--whitelist-json', '--rotate-sync-token'}
    unknown = args - known
    if unknown:
        sys.exit('未知参数：' + ' '.join(sorted(unknown)))
    sync_modes = args & {'--whitelist-only', '--whitelist-json', '--rotate-sync-token'}
    if len(sync_modes) > 1 or (sync_modes and '--with-existing' in args):
        sys.exit('--whitelist-only / --whitelist-json / --rotate-sync-token 不能与其它模式组合')
    if '--rotate-sync-token' in args:
        rotate_sync_token()
        return
    if '--whitelist-json' in args:
        print_whitelist_json()
        return
    if '--whitelist-only' in args:
        print_whitelist()
        return
    sys.stdout.write(HEADER.format(cap_v=CAP_VISITS, cap_l=CAP_LIKES, cap_w=CAP_WANTS,
                                   cap_c=CAP_CALS, max_len=MAX_URL_LEN,
                                   sync_hash=SYNC_TOKEN_HASH))
    print_whitelist()
    sys.stdout.write(BODY.format(cap_v=CAP_VISITS, cap_l=CAP_LIKES, cap_w=CAP_WANTS,
                                 cap_c=CAP_CALS, max_len=MAX_URL_LEN,
                                 max_keys=MAX_WHITELIST_KEYS,
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
