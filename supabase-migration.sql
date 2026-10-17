-- ============================================================
-- 讲座聚合站 · Supabase 迁移 SQL（2026-10-17 生成，可重复执行）
-- 执行位置：Supabase 控制台 → SQL Editor → 粘贴整段运行
-- 前置：已建表 lecture_stats(url text PK, visits int8, likes int8, wants int8) 且已开 RLS
-- ============================================================

-- ① 值域约束（粗粒度防刷兜底，脚本最多刷到这里就被数据库拦住）
alter table lecture_stats drop constraint if exists lecture_stats_visits_range;
alter table lecture_stats drop constraint if exists lecture_stats_likes_range;
alter table lecture_stats drop constraint if exists lecture_stats_wants_range;
alter table lecture_stats
  add constraint lecture_stats_visits_range check (visits between 0 and 1000000),
  add constraint lecture_stats_likes_range check (likes between 0 and 100000),
  add constraint lecture_stats_wants_range check (wants between 0 and 100000);

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
  on conflict (url) do update set visits = least(greatest(lecture_stats.visits + p_delta, 0), 1000000)
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

insert into lecture_stats (url, visits, likes, wants) values ('http://abdn.scnu.edu.cn/a/20230504/362.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://cs.scnu.edu.cn/a/20160613/2386.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://cs.scnu.edu.cn/a/20261008/6008.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://ctld.scnu.edu.cn/a/20190920/1031.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://ctld.scnu.edu.cn/a/20201222/1280.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://ctld.scnu.edu.cn/a/20211202/1453.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://ctld.scnu.edu.cn/a/20221212/4108.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://ctld.scnu.edu.cn/a/20230602/4267.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://ctld.scnu.edu.cn/a/20231120/4268.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20140504/3716.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20140508/3717.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20160108/4169.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20161008/5100.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20171102/5891.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20171202/5983.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20190112/6769.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20190509/6973.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20200108/7635.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20200109/7636.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20230920/9717.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20260924/11175.html', 2, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20261015/11187.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20261019/11186.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://em.scnu.edu.cn/a/20261019/11209.html', 2, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://ibc.scnu.edu.cn/a/20250107/2779.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://iqm.scnu.edu.cn/a/20211122/189.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://iqm.scnu.edu.cn/a/20230329/282.html', 2, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://iqm.scnu.edu.cn/a/20250410/442.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://iqm.scnu.edu.cn/a/20250526/449.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://iqm.scnu.edu.cn/a/20260921/557.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://jky.scnu.edu.cn/a/20241029/5920.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://life.scnu.edu.cn/a/20260626/6781.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://life.scnu.edu.cn/a/20260626/6782.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://life.scnu.edu.cn/a/20260725/6792.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://life.scnu.edu.cn/a/20261012/6827.html', 2, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://lswh.scnu.edu.cn/a/20141124/5.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://lswh.scnu.edu.cn/a/20250320/51.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://maths.scnu.edu.cn/a/20240611/8036.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://maths.scnu.edu.cn/a/20241109/8182.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://maths.scnu.edu.cn/a/20250112/8260.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://maths.scnu.edu.cn/a/20260407/8681.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://maths.scnu.edu.cn/a/20260925/8850.html', 2, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://maths.scnu.edu.cn/a/20261006/8855.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://maths.scnu.edu.cn/a/20261013/8854.html', 1, 2, 1) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://maths.scnu.edu.cn/a/20261014/8856.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://maths.scnu.edu.cn/a/20261015/8866.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://maths.scnu.edu.cn/a/20261016/8867.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://psy.scnu.edu.cn/a/20130701/192.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://psy.scnu.edu.cn/a/20160113/961.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://psy.scnu.edu.cn/a/20170222/1226.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://psy.scnu.edu.cn/a/20170612/1305.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://psy.scnu.edu.cn/a/20170624/1322.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://psy.scnu.edu.cn/a/20190311/1703.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://psy.scnu.edu.cn/a/20191105/1840.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://psy.scnu.edu.cn/a/20260324/2940.html', 2, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://psy.scnu.edu.cn/a/20260707/2990.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://skc.scnu.edu.cn/a/20150923/196.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://swc.scnu.edu.cn/collaborative/2021/0705/34.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://swc.scnu.edu.cn/collaborative/2021/0707/3.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://xz.scnu.edu.cn/a/20240116/127.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('http://xz.scnu.edu.cn/a/20251114/301.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://module.scnu.edu.cn/article-3685-10806-1.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://module.scnu.edu.cn/article-3685-10815-1.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://module.scnu.edu.cn/article-3685-10862-1.html', 2, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://module.scnu.edu.cn/article-3685-10878-1.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://module.scnu.edu.cn/article-3685-11064-1.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://module.scnu.edu.cn/article-3685-11157-1.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://module.scnu.edu.cn/article-3685-11158-1.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://module.scnu.edu.cn/article-3685-3762-1.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://module.scnu.edu.cn/article-3685-7542-1.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://physics.scnu.edu.cn/a/20191118/806.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://physics.scnu.edu.cn/a/20220513/11930.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://physics.scnu.edu.cn/a/20230920/12437.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://physics.scnu.edu.cn/a/20240112/12574.html', 1, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://physics.scnu.edu.cn/a/20260602/13312.html', 2, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://physics.scnu.edu.cn/a/20261012/13403.html', 3, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
insert into lecture_stats (url, visits, likes, wants) values ('https://yjsy.scnu.edu.cn/a/20261012/10265.html', 2, 0, 0) on conflict (url) do update set visits = excluded.visits, likes = excluded.likes, wants = excluded.wants;
