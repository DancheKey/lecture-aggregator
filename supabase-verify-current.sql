-- ============================================================
-- 讲座聚合站 · Supabase 只读核查 SQL（2026-11-28）
-- 执行位置：Supabase 控制台 → SQL Editor → 新建 query → 粘贴整段运行
-- 本文件只有 SELECT / DO 查询，不调用任何增量 RPC，不修改数据或权限。
--
-- 预期：
--   1. 四个 increment_* 函数都是 prosecdef=true，定义含 lecture_stat_key_allowed、
--      p_delta is null，以及 SET search_path TO pg_catalog, public。
--   2. increment_* 与白名单探测函数 ACL 不给 PUBLIC（proacl 不含 =X）；
--      anon/authenticated/service_role 有 X；sync_whitelist 只给 anon/service_role。
--   3. lecture_stats、lecture_allowed_keys、lecture_whitelist_sync 的 RLS 都为 true。
--   4. anon/authenticated 对 lecture_stats 只有 SELECT，对两张私有表没有任何权限。
--   5. lecture_allowed_keys 存在且行数约等于当前讲座数 + 1（__site__）。
--   6. sync_whitelist 有固定 search_path；令牌状态只存在哈希，不打印哈希或令牌。
--   7. 历史非白名单行可以存在，但不应再被 RPC 写入；核查脚本不会自动删除它们。
-- ============================================================

-- ① 线上真实函数定义（最权威；不要只看仓库 SQL）
select p.proname,
       pg_get_functiondef(p.oid) as definition
from pg_catalog.pg_proc p
join pg_catalog.pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in (
    'increment_like', 'increment_want', 'increment_visit', 'increment_cal',
    'lecture_stat_key_allowed', 'sync_whitelist'
  )
order by p.proname;

-- ② security definer、搜索路径与 EXECUTE ACL
select p.proname,
       p.prosecdef,
       p.proconfig,
       p.proacl
from pg_catalog.pg_proc p
join pg_catalog.pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in (
    'increment_like', 'increment_want', 'increment_visit', 'increment_cal',
    'lecture_stat_key_allowed', 'sync_whitelist'
  )
order by p.proname;

-- ③ 两张表的 RLS 开关与表级 ACL
select c.relname,
       c.relrowsecurity,
       c.relforcerowsecurity,
       c.relacl
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relname in ('lecture_stats', 'lecture_allowed_keys', 'lecture_whitelist_sync')
order by c.relname;

-- ④ 现有 RLS 策略（ lecture_stats 应只有公开 SELECT；白名单表不应有 anon 策略 ）
select schemaname,
       tablename,
       policyname,
       permissive,
       roles,
       cmd,
       qual,
       with_check
from pg_catalog.pg_policies
where schemaname = 'public'
  and tablename in ('lecture_stats', 'lecture_allowed_keys', 'lecture_whitelist_sync')
order by tablename, policyname;

-- ⑤ anon / authenticated / service_role 的表权限
select table_name,
       grantee,
       string_agg(privilege_type, ', ' order by privilege_type) as privileges
from information_schema.role_table_grants
where table_schema = 'public'
  and table_name in ('lecture_stats', 'lecture_allowed_keys')
  and grantee in ('anon', 'authenticated', 'service_role')
group by table_name, grantee
order by table_name, grantee;

-- ⑥ 白名单与同步令牌状态：只输出行数和布尔值，绝不输出 token_hash 原值。
select to_regclass('public.lecture_allowed_keys') as whitelist_table,
       case
         when to_regclass('public.lecture_allowed_keys') is null then null
         else pg_stat_get_live_tuples(to_regclass('public.lecture_allowed_keys')::oid)
       end as approximate_rows;

do $verify$
declare
  row_count bigint;
  site_actions text;
begin
  if to_regclass('public.lecture_allowed_keys') is null then
    raise notice 'WHITELIST_MISSING: public.lecture_allowed_keys 不存在，需执行当前补丁';
    return;
  end if;

  execute 'select count(*) from public.lecture_allowed_keys' into row_count;
  execute $q$
    select coalesce(string_agg(allowed_actions::text, ''), '<missing>')
    from public.lecture_allowed_keys
    where url = '__site__'
  $q$ into site_actions;
  raise notice 'WHITELIST_ROWS=% SITE_ACTIONS=%', row_count, site_actions;
end
$verify$;

do $verify$
declare
  row_count bigint;
  invalid_hashes bigint;
  previous_enabled boolean;
begin
  if to_regclass('public.lecture_whitelist_sync') is null then
    raise notice 'SYNC_STATE_MISSING: lecture_whitelist_sync 不存在，需执行当前补丁';
    return;
  end if;

  execute 'select count(*) from public.lecture_whitelist_sync' into row_count;
  execute $q$
    select count(*)
    from public.lecture_whitelist_sync
    where token_hash !~ '^[0-9a-f]{64}$'
       or (previous_token_hash is not null
           and previous_token_hash !~ '^[0-9a-f]{64}$')
  $q$ into invalid_hashes;
  execute $q$
    select coalesce(bool_or(previous_token_hash is not null), false)
    from public.lecture_whitelist_sync
  $q$ into previous_enabled;
  raise notice 'SYNC_STATE_ROWS=% HASH_SHAPE_OK=% PREVIOUS_HASH_ENABLED=%',
            row_count, invalid_hashes = 0 and row_count = 1, previous_enabled;
end
$verify$;

-- ⑦ 历史孤儿统计行：只统计、只打印前 10 个样本，不删除。
--    若 WHITELINE_MISSING，上面已明确返回，不会访问不存在的表。
do $verify$
declare
  row_count bigint;
  sample text;
begin
  if to_regclass('public.lecture_allowed_keys') is null then
    return;
  end if;

  execute $q$
    select count(*)
    from public.lecture_stats s
    left join public.lecture_allowed_keys k on k.url = s.url
    where k.url is null
  $q$ into row_count;

  execute $q$
    select coalesce(string_agg(q.url, E'\n' order by q.url), '<none>')
    from (
      select s.url
      from public.lecture_stats s
      left join public.lecture_allowed_keys k on k.url = s.url
      where k.url is null
      order by s.url
      limit 10
    ) q
  $q$ into sample;

  raise notice 'NON_WHITELISTED_ROWS=% SAMPLE=%', row_count, sample;
end
$verify$;
