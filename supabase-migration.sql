-- ============================================================
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
  add constraint lecture_stats_visits_range check (visits between 0 and 1000000),
  add constraint lecture_stats_likes_range check (likes between 0 and 100000),
  add constraint lecture_stats_wants_range check (wants between 0 and 100000);

-- ③ RPC 增量函数：前端只能 +/-1，写不了任意值（security definer = 以所有者权限跑，
--    anon 无需表级 INSERT/UPDATE 权限；表级写权限保持关闭，只走函数这一道闸门）。
--    insert 必须显式写全三列——只填目标列会依赖列默认值，缺默认值的表直接 23502。
create or replace function increment_like(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats;
begin
  insert into lecture_stats (url, visits, likes, wants) values (p_url, 0, greatest(p_delta, 0), 0)
  on conflict (url) do update set likes = greatest(lecture_stats.likes + p_delta, 0)
  returning * into v;
  return to_jsonb(v) - 'url';
end $$;

create or replace function increment_want(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats;
begin
  insert into lecture_stats (url, visits, likes, wants) values (p_url, 0, 0, greatest(p_delta, 0))
  on conflict (url) do update set wants = greatest(lecture_stats.wants + p_delta, 0)
  returning * into v;
  return to_jsonb(v) - 'url';
end $$;

create or replace function increment_visit(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats;
begin
  insert into lecture_stats (url, visits, likes, wants) values (p_url, greatest(p_delta, 0), 0, 0)
  on conflict (url) do update set visits = least(greatest(lecture_stats.visits + p_delta, 0), 1000000)
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

-- ⑤ 存量导入：未启用（默认）。如需导入本地历史计数，
--    运行 python scripts/gen_supabase_sql.py --with-existing > supabase-migration.sql
