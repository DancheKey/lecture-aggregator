-- ============================================================
-- 讲座聚合站 · Supabase 迁移 SQL（2026-10-18 修订，可重复执行）
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
  add constraint lecture_stats_likes_range check (likes between 0 and 1000),
  add constraint lecture_stats_wants_range check (wants between 0 and 1000);

-- ③ RPC 增量函数：前端只能 +/-1，写不了任意值（security definer = 以所有者权限跑，
--    anon 无需表级 INSERT/UPDATE 权限；表级写权限保持关闭，只走函数这一道闸门）。
--    insert 必须显式写全三列——只填目标列会依赖列默认值，缺默认值的表直接 23502。
--    ⚠ 2026-10-17 深夜修复：update 与 insert 两条路径都必须 least 上钳——此前
--    like/want 只有 greatest(...,0) 下钳，实测灌 p_delta=99999 可直接刷爆计数。
--    ⚠ 2026-10-18 修订（两项，均有实测依据）：
--       S1 p_url 守卫：此前对 p_url 零校验，anon 实测可建空键行与 8000 字符行
--          （拖慢 fetchAll 全表拉取，并让表行数可被无限灌高）。现入口两道
--          校验：长度 1..300，且前缀必须 http(s):// 或 __（保留键族，与前端
--          fetchAll 的 /^__/ 过滤同口径）——只限长度挡不住「p_url='a'/'b'…」
--          批量造行，行数灌到触发分页截断后部分讲座计数会静默读不到。
--          越界一律抛 22023（PostgREST 400，前端 catch 后静默降级，对正常
--          调用零影响——真实键最长 71 字符，且 100% 是 http(s) 地址）。
--       S2 visit 单次上钳 100 万→100：原值下一次 curl 就能把页脚 PV（无 capDisplay）
--          刷成门面事故。d 变量统一承载「钳过的单次增量」，insert 与 on conflict
--          两条路径共用同一个 d，杜绝两路上钳值漂移；累计总量仍由 1000000 兜底。
--    ⚠ 2026-10-19 修订（B1/S1/S5，三项缺陷同源）：
--       B1 取消无效：like/want 原为 greatest(p_delta, 0)，前端取消传 -1 被钳成 0，
--          「取消点赞」实际不减——界面 toast 报已取消、数字却被云端权威值弹回原值；
--          且本地 likedUrls 已删键，再点一次又 +1，同一用户可反复刷高（上限 1000）。
--          现改为 greatest(p_delta, -1)：允许 -1，仍拒绝 -2 及更小的批量减。
--          已知取舍：匿名者可对他人计数 -1；彻底方案是设备级点赞表（独立迭代）。
--       S1 单次步长：like/want 此前直接用累计上限 1000 当单次上钳，一次 curl 即可
--          顶满。现独立为 1/1，与累计上限 1000/1000 分离。
--       S5 visit 步长 100 → 1：一次请求 = 一次真实访问。
create or replace function increment_like(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats; d int;
begin
  if p_url is null or length(p_url) = 0 or length(p_url) > 300 then
    raise exception 'p_url must be 1..300 chars' using errcode = 22023;
  end if;
  if p_url !~* '^(https?://|__)' then
    raise exception 'p_url must be an http(s) URL or __-prefixed key' using errcode = 22023;
  end if;
  d := least(greatest(p_delta, -1), 1);
  insert into lecture_stats (url, visits, likes, wants)
    -- greatest(d, 0)：取消一个云端还不存在的行（本地 likedUrls 有残留、云端被清过）
    -- 时 d = -1，直接插会违反 check (likes >= 0) → 400；插 0 则是无害的幂等空操作。
    values (p_url, 0, greatest(d, 0), 0)
  on conflict (url) do update set likes = least(greatest(lecture_stats.likes + d, 0), 1000)
  returning * into v;
  return to_jsonb(v) - 'url';
end $$;

create or replace function increment_want(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats; d int;
begin
  if p_url is null or length(p_url) = 0 or length(p_url) > 300 then
    raise exception 'p_url must be 1..300 chars' using errcode = 22023;
  end if;
  if p_url !~* '^(https?://|__)' then
    raise exception 'p_url must be an http(s) URL or __-prefixed key' using errcode = 22023;
  end if;
  d := least(greatest(p_delta, -1), 1);
  insert into lecture_stats (url, visits, likes, wants)
    values (p_url, 0, 0, greatest(d, 0))
  on conflict (url) do update set wants = least(greatest(lecture_stats.wants + d, 0), 1000)
  returning * into v;
  return to_jsonb(v) - 'url';
end $$;

create or replace function increment_visit(p_url text, p_delta int)
returns json language plpgsql security definer as $$
declare v lecture_stats; d int;
begin
  if p_url is null or length(p_url) = 0 or length(p_url) > 300 then
    raise exception 'p_url must be 1..300 chars' using errcode = 22023;
  end if;
  if p_url !~* '^(https?://|__)' then
    raise exception 'p_url must be an http(s) URL or __-prefixed key' using errcode = 22023;
  end if;
  d := least(greatest(p_delta, 0), 1);
  insert into lecture_stats (url, visits, likes, wants)
    values (p_url, d, 0, 0)
  on conflict (url) do update set visits = least(greatest(lecture_stats.visits + d, 0), 1000000)
  returning * into v;
  return to_jsonb(v) - 'url';
end $$;

-- ④ RLS：先确保行级安全真的开着——policy 存在但 RLS 关闭 = 完全裸奔。
--    ⚠ 2026-10-18 复查补：原 SQL 只有 create policy，漏了 enable。Supabase 对
--    新表默认给 anon 全表 ALL 权限，RLS 未开启时 policy 形同虚设，匿名可绕过
--    RPC 直接 INSERT/UPDATE 任意值（本库实测 RLS 已开，此句是幂等兜底，
--    防止照本 SQL 重建库时漏开）。enable 重复执行无副作用。
alter table lecture_stats enable row level security;
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
