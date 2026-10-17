# -*- coding: utf-8 -*-
"""抓取站点访问量快照（Supabase），追加/更新到台账 data/visits_history.json。

为什么需要它：
    公网是纯静态托管（GitHub Pages），全站访问量数据源是自建 Supabase
    （lecture_stats 表专用行 __site__，页脚经 increment_visit RPC 增量）。
    CI 每天取一次快照落盘入库，成为 Git 历史的一部分，永远不会丢。

口径迁移（2026-10-18，勿混淆）：
    - 2026-09-10 ~ 2026-10-17：busuanzi.aspark.cc（GET /api 纯读），
      台账含 site_pv / site_uv / page_pv / page_uv 四值。
    - 现行：Supabase 只提供 PV（页面打开次数）。UV（独立访客）依赖
      busuanzi 服务端按 IP 去重，自建无法复现 → **UV 停更**，新快照的
      site_uv / page_pv / page_uv / uv_delta 记 null（JSON null，趋势页
      显示「—」并截止曲线），site_pv 继续累积。种子 1724 = busuanzi
      末次台账值（2026-10-17），两段口径同为「页面打开次数」，无缝衔接。

关键实现约束：
    - 只用 **GET**：REST 查询是纯读；绝不调用 increment_* RPC（那是页脚
      计数入口，CI 取数走 RPC 会每天给自家 PV 灌水 +1）。
    - 配置来源：env SUPABASE_URL / SUPABASE_ANON_KEY 优先（CI 从 GitHub
      Secrets 注入；key 本为公开 anon key，进 Secrets 仅为传参方便），
      回落读 site/supabase-config.json（本地开发用）。

台账设计：
    - 日期用北京时间（UTC+8）。同一天重复运行**更新**当日记录，不重复追加。
    - 日增 = 本次 − 上一条「非当日」记录；**负值一律记 0 并写 note**
      （负增量意味着对方计数器重置或异常，绝不产出负数）。
    - JSON 用 indent=2 多行：每日追加只在文件末尾增加行，git 能自动合并，
      避免与本地推送争抢同一行（单行 JSON 曾出过此类事故）。

失败处理：
    网络/解析失败时打印 ::warning:: 并**退出 0**——不阻断 CI 的抓取与部署，
    仅在 Actions 日志里留黄色告警。

用法：
    python scripts/fetch_visits_snapshot.py
    python scripts/fetch_visits_snapshot.py --dry-run   # 只打印，不写盘
"""
import os
import sys
import json
import argparse
import datetime
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(ROOT, 'data', 'visits_history.json')

SITE_URL = 'https://danchekey.github.io/lecture-aggregator/'
SITE_ROW_KEY = '__site__'   # lecture_stats.url 主键：全站 PV 专用行
TIMEOUT = 25
CST = datetime.timezone(datetime.timedelta(hours=8))
# 台账最新快照距今超过该天数仍取不到数 → 升级为 ::error::（2026-10-10 P3）。
# 单日失败只告警不报错（外部服务抖动不该打断数据发布）；但持续多日取不到
# 意味着数据源失效或 schema 变更，台账与趋势页会持续冻结且此前**零信号**
# ——这是「公网访问量的唯一本地备份」，失明必须可见。
STALE_DAYS = 3


def _warn(msg):
    print('::warning::' + msg)


def _stale_error():
    """台账是否已连续 STALE_DAYS 天无新快照；是则发 ::error:: 并返回 True。"""
    try:
        hist = load_history() or {}
        snaps = hist.get('snapshots') or []
        last_date = max((str(s.get('date', '')) for s in snaps), default='')
        if not last_date:
            return True   # 台账空且本次也失败：同样算失明
        gap = (datetime.datetime.now(CST).date()
               - datetime.date.fromisoformat(last_date)).days
        if gap >= STALE_DAYS:
            print('::error::访问量台账已 %d 天无新快照（Supabase 持续不可用或'
                  '配置错误），公网访问量与趋势页将持续冻结，请检查 '
                  'SUPABASE_URL/SUPABASE_ANON_KEY 配置' % gap)
            return True
    except Exception:
        pass
    return False


def _load_config():
    """取 Supabase 配置：env 优先，回落 site/supabase-config.json。"""
    url = os.environ.get('SUPABASE_URL', '').strip().rstrip('/')
    key = os.environ.get('SUPABASE_ANON_KEY', '').strip()
    if url and key:
        return url, key
    cfg_path = os.path.join(ROOT, 'site', 'supabase-config.json')
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding='utf-8') as f:
                j = json.load(f)
            url = url or str(j.get('url', '')).strip().rstrip('/')
            key = key or str(j.get('anonKey', '')).strip()
            if url and key:
                return url, key
        except (ValueError, OSError) as e:
            _warn('supabase-config.json 读取失败：%s：%s' % (type(e).__name__, e))
    return None


def fetch_snapshot():
    """GET 一次 Supabase（纯读不计数），返回 {'site_pv': int}。"""
    cfg = _load_config()
    if not cfg:
        raise RuntimeError('缺少 Supabase 配置（env SUPABASE_URL/SUPABASE_ANON_KEY '
                           '与 site/supabase-config.json 均不可用）')
    url, key = cfg
    # SITE_ROW_KEY 为固定 ASCII 常量，直接拼 URL 即可（无需编码）
    api = url + '/rest/v1/lecture_stats?select=visits&url=eq.' + SITE_ROW_KEY
    req = urllib.request.Request(api, method='GET')
    req.add_header('apikey', key)
    req.add_header('Authorization', 'Bearer ' + key)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        rows = json.load(r)
    if not isinstance(rows, list) or not rows:
        raise RuntimeError('查询 %s 行为空——云端补丁未执行（建行种子 SQL 见 '
                           'docs/ 下 supabase-patch-sitepv 文件）' % SITE_ROW_KEY)
    visits = rows[0].get('visits')
    if visits is None:
        raise RuntimeError('%s 行 visits 为 null' % SITE_ROW_KEY)
    return {'site_pv': int(visits)}


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return None
    try:
        with open(HISTORY_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (ValueError, OSError) as e:
        _warn('台账读取失败，将重建（%s：%s）' % (type(e).__name__, e))
        return None


def save_history(data):
    tmp = HISTORY_PATH + '.tmp'
    # newline='\n'：本台账承诺「逐行追加便于自动合并」（见模块 docstring），
    # Windows 文本模式会把 \n 写成 \r\n，破坏该前提并产生 git 换行噪声。
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, HISTORY_PATH)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只打印结果，不写盘')
    args = ap.parse_args()

    try:
        snap = fetch_snapshot()
    except Exception as e:
        _warn('访问量快照获取失败，本次不写入台账（%s：%s）' % (type(e).__name__, e))
        # 2026-10-10 P3：单日失败保持告警；台账已连续 STALE_DAYS 天失明则升级
        # ::error::（红色注解在 Actions 摘要可见）。仍退 0——数据源抖动
        # 不得阻断讲座数据的部署（本步失败会令 deploy job 被跳过）。
        _stale_error()
        return 0

    now = datetime.datetime.now(CST)
    today = now.strftime('%Y-%m-%d')
    # 2026-10-18 起 UV 停更：site_uv/page_pv/page_uv/uv_delta 记 null
    #（不是 0——0 会被当成真实值画进曲线/表格，null 才显示「—」并断线）。
    rec = {
        'date': today,
        'capturedAt': now.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'site_pv': snap['site_pv'],
        'site_uv': None,
        'page_pv': None,
        'page_uv': None,
        'uv_delta': None,
        'pv_delta': 0,
        'note': '',
    }

    hist = load_history() or {'source': 'supabase',
                              'siteUrl': SITE_URL,
                              'snapshots': []}
    hist['source'] = 'supabase'
    snaps = hist.setdefault('snapshots', [])

    # 差分基准取「最后一条非当日」记录：同日重复运行时基准保持不变
    prev = None
    for s in reversed(snaps):
        if s.get('date') != today:
            prev = s
            break

    if prev is None:
        rec['note'] = '基线（无前序记录，日增不可用）'
    else:
        dpv = snap['site_pv'] - int(prev.get('site_pv') or 0)
        rec['pv_delta'] = dpv if dpv > 0 else 0
        if dpv < 0:
            rec['note'] = '计数器疑似重置/回落，日增记 0（实测 pv %d）' % dpv
        if not rec['note'] and prev.get('site_uv') is not None:
            # UV→Supabase 迁移后的首条快照：留痕，解释曲线断点
            rec['note'] = '计数源切换为自建 Supabase（2026-10-18），UV 停更'

    for i, s in enumerate(snaps):
        if s.get('date') == today:
            snaps[i] = rec
            break
    else:
        snaps.append(rec)
    snaps.sort(key=lambda s: s.get('date', ''))

    print('快照 %s：site_pv=%d（日增 pv=%s；UV 已停更）'
          % (today, snap['site_pv'], rec['pv_delta']))
    if rec['note']:
        print('  注：%s' % rec['note'])

    if args.dry_run:
        print('--dry-run：未写盘')
        return 0

    save_history(hist)
    print('已写入 %s（共 %d 条）'
          % (os.path.relpath(HISTORY_PATH, ROOT), len(snaps)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
