# -*- coding: utf-8 -*-
"""抓取站点访问量快照（busuanzi），追加/更新到台账 data/visits_history.json。

为什么需要它：
    公网是纯静态托管（GitHub Pages），全站访问量唯一来源是 busuanzi.aspark.cc。
    该服务一旦失效，累计访问量会永久丢失——台账就是为这件事准备的备份：
    CI 每天取一次快照落盘入库，成为 Git 历史的一部分，永远不会丢。

关键实现约束（2026-09-10 实测，勿改）：
    - 必须用 **GET**：该端点 GET = 纯读不计数，POST = 计数 + 1。
      若改用 POST，每天会给自家 PV 灌水（CI 出口 IP 变动还会污染 UV）。
    - 请求头 x-bsz-referer 决定统计分桶，必须填站点首页 URL，否则读到别的桶。
    - 返回 {"success":true,"data":{site_pv,site_uv,page_pv,page_uv}}。

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

API_URL = 'https://busuanzi.aspark.cc/api'
SITE_URL = 'https://danchekey.github.io/lecture-aggregator/'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
TIMEOUT = 25
CST = datetime.timezone(datetime.timedelta(hours=8))


def _warn(msg):
    print('::warning::' + msg)


def fetch_snapshot():
    """GET 一次 busuanzi API（不计数），返回四个计数值。"""
    req = urllib.request.Request(API_URL, method='GET')
    req.add_header('x-bsz-referer', SITE_URL)
    req.add_header('User-Agent', UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        payload = json.load(r)
    if not payload.get('success'):
        raise RuntimeError('busuanzi 返回 success=false：%s' % payload)
    d = payload['data']
    keys = ('site_pv', 'site_uv', 'page_pv', 'page_uv')
    missing = [k for k in keys if k not in d]
    if missing:
        raise RuntimeError('返回缺少字段：%s' % ', '.join(missing))
    return dict((k, int(d[k])) for k in keys)


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
    with open(tmp, 'w', encoding='utf-8') as f:
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
        return 0

    now = datetime.datetime.now(CST)
    today = now.strftime('%Y-%m-%d')
    rec = {
        'date': today,
        'capturedAt': now.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'site_pv': snap['site_pv'],
        'site_uv': snap['site_uv'],
        'page_pv': snap['page_pv'],
        'page_uv': snap['page_uv'],
        'uv_delta': 0,
        'pv_delta': 0,
        'note': '',
    }

    hist = load_history() or {'source': 'busuanzi.aspark.cc',
                              'siteUrl': SITE_URL,
                              'snapshots': []}
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
        duv = snap['site_uv'] - int(prev.get('site_uv', 0))
        dpv = snap['site_pv'] - int(prev.get('site_pv', 0))
        rec['uv_delta'] = duv if duv > 0 else 0
        rec['pv_delta'] = dpv if dpv > 0 else 0
        if duv < 0 or dpv < 0:
            rec['note'] = ('计数器疑似重置/回落，日增记 0'
                           '（实测 uv %d / pv %d）' % (duv, dpv))

    for i, s in enumerate(snaps):
        if s.get('date') == today:
            snaps[i] = rec
            break
    else:
        snaps.append(rec)
    snaps.sort(key=lambda s: s.get('date', ''))

    print('快照 %s：site_pv=%d site_uv=%d page_pv=%d page_uv=%d'
          '（日增 uv=%s pv=%s）'
          % (today, snap['site_pv'], snap['site_uv'],
             snap['page_pv'], snap['page_uv'],
             rec['uv_delta'], rec['pv_delta']))
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
