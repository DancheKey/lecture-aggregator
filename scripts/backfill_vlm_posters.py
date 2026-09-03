#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按年份/月份把海报页讲座记录用 VLM 重新抽取（仅填空），应用当前 VLM 规则。

对已入库海报记录（hasPosterImage / imageParseMethod）：重抓 poster 图 →
parsers._vlm_extract_fields（当前 VLM 提示词与「仅填空」全局规则）→ 仅填空回填
abstract/speakerBio/speaker/location/speakerAffiliation/speakerTitle/lectureStart/lectureEnd，
绝不覆盖非空值。

为何清空 VLM 缓存：原流程缓存键 = md5(筛选后海报图集合)，与库内 images 全集不同，
无法逐条匹配旧键；且「新规则」可能改了 VLM 提示词，旧结果命中会阻止新规则生效。
故写盘前整文件清空 data/.vlm_cache.json（先备份），保证本次重抽走当前 VLM 配置。
写盘前另自动备份 data/lectures.json，临时文件 + os.replace 原子替换。

用法：
  python scripts/backfill_vlm_posters.py --year 2024 --month-start 1 --month-end 6
  python scripts/backfill_vlm_posters.py --year 2024 --month-start 1 --month-end 6 --limit 3 --dry-run
说明：
- --dry-run 只打印待重跑目标及其空字段，不调 VLM、不写盘、不动缓存。
- 国内域名（智谱 open.bigmodel.cn）直连约定：运行前清除代理环境变量。
"""
import os
import re
import sys
import json
import time
import argparse
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

# 国内域名直连约定（智谱 VLM 不绕代理）
for _k in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy'):
    os.environ.pop(_k, None)

import parsers  # noqa: E402

# 与 parsers._is_chrome_img 的 URL bad 元组保持一致（过滤 logo/横幅/二维码/公众号等装饰图）
_BAD = ('logo', 'banner', 'icon', 'avatar', 'arrow', 'btn', 'nav', 'share', 'close',
        'header', 'slide', 'weixin', 'wechat', 'qr', 'qrcode', 'qr-code', 'scan',
        'saoma', 'carousel', 'flash', 'pixel', 'spacer', '二维码', '关注', '公众号',
        '扫码', '订阅')

# 仅填空回填的目标字段
_FILL = ('abstract', 'speakerBio', 'speaker', 'location',
         'speakerAffiliation', 'speakerTitle', 'lectureStart', 'lectureEnd')


def _is_chrome(src):
    s = (src or '').lower()
    s2 = unquote(s)
    return any(k in s or k in s2 for k in _BAD)


def _empty(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _year_of(s):
    s = str(s or '')[:4]
    return int(s) if s.isdigit() else None


def _month_of(s):
    s = str(s or '')[5:7]
    return int(s) if s.isdigit() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--month-start', type=int, default=None, help='月份起点(含)')
    ap.add_argument('--month-end', type=int, default=None, help='月份终点(含)')
    ap.add_argument('--limit', type=int, default=0, help='最多处理 N 条（冒烟用）')
    ap.add_argument('--dry-run', action='store_true', help='只打印目标与空字段，不调VLM/不写盘')
    ap.add_argument('--force', action='store_true',
                    help='重跑全部海报记录（默认跳过无空字段的）')
    args = ap.parse_args()

    data_path = os.path.join(ROOT, 'data', 'lectures.json')
    raw = json.load(open(data_path, encoding='utf-8'))
    recs = raw['data'] if isinstance(raw, dict) else raw

    # 筛选海报 + 年份
    post = [r for r in recs
            if (r.get('hasPosterImage') or r.get('imageParseMethod'))
            and (r.get('sourceUrl') or '').startswith('http')]
    post = [r for r in post
            if _year_of(r.get('lectureStart')) == args.year
            or (_year_of(r.get('lectureStart')) is None
                and _year_of(r.get('publishTime')) == args.year)]
    # 月份范围
    if args.month_start or args.month_end:
        ms, me = (args.month_start or 1), (args.month_end or 12)
        def _in_month(r):
            mo = _month_of(r.get('lectureStart')) or _month_of(r.get('publishTime'))
            return mo is not None and ms <= mo <= me
        post = [r for r in post if _in_month(r)]
        print(f'[INFO] 月份范围 {ms}-{me} 过滤后：{len(post)} 条海报记录')
    # 默认跳过无空字段的（--force 才全跑）
    if not args.force:
        post = [r for r in post if any(_empty(r.get(f)) for f in _FILL)]
    if args.limit:
        post = post[:args.limit]
    print(f'[INFO] 年份 {args.year} 海报待重跑：{len(post)} 条'
          + ('（dry-run，不调 VLM/不写盘）' if args.dry_run else ''))

    if args.dry_run:
        for i, r in enumerate(post, 1):
            empt = [f for f in _FILL if _empty(r.get(f))]
            print(f'  [{i}] 空字段 {empt} {r.get("sourceUrl")}')
        print(f'[DRY-RUN] 共 {len(post)} 条目标，未调 VLM、未写盘。')
        return

    cfgs = parsers._load_vlm_configs()
    if not cfgs:
        print('[ERR] 无 VLM 配置（检查 .env 的 ZHIPU_API_KEY）')
        return

    # 精准 bypass：monkey-patch _vlm_cache_get 强制返回 None，仅本次处理的图集
    # 绕过旧缓存、走当前 VLM 规则重抽；_vlm_cache_set 只刷新这 54 条对应键，
    # 其他年份的 VLM 缓存键原样保留（读全文件→改 1 键→写回），不浪费历史额度。
    parsers._vlm_cache_get = lambda key: None
    print('[INFO] 已精准 bypass VLM 缓存（仅本批图集重抽，其他年份缓存保留）')

    stat = {'ok': 0, 'filled': 0, 'skip': 0, 'err': 0, 'nosession': 0}
    for i, r in enumerate(post, 1):
        imgs = [u for u in (r.get('images') or []) if isinstance(u, str) and u.startswith('http')]
        fimgs = [u for u in imgs if not _is_chrome(u)]
        if not fimgs:
            fimgs = imgs  # 兜底：装饰过滤后为空则退用全集
        vlm = parsers._vlm_extract_fields(fimgs[:2] if fimgs else fimgs, cfgs)
        if vlm is None:
            stat['err'] += 1
            print(f'  [{i}] VLM无结果 {r.get("sourceUrl")}')
            continue
        if isinstance(vlm, list):
            stat['nosession'] += 1
            print(f'  [{i}] 多讲座拆分，跳过 {r.get("sourceUrl")}')
            continue
        filled = []
        for f in _FILL:
            if _empty(r.get(f)) and not _empty(vlm.get(f)):
                r[f] = vlm[f].strip() if isinstance(vlm[f], str) else vlm[f]
                filled.append(f)
        if filled:
            r['vlmBackfilled'] = True
            stat['ok'] += 1
            stat['filled'] += len(filled)
            print(f'  [{i}] FILL[{".".join(filled)}] {r.get("sourceUrl")}')
        else:
            stat['skip'] += 1
            print(f'  [{i}] no-op {r.get("sourceUrl")}')

    print(f'\n[SUMMARY] 回填 {stat["ok"]} 条 / 无变化 {stat["skip"]} / '
          f'VLM无结果 {stat["err"]} / 多讲座跳过 {stat["nosession"]} / 共填 {stat["filled"]} 字段')
    if stat['ok'] == 0:
        print('[INFO] 无记录被回填，跳过写盘。')
        return
    import shutil
    bak = data_path + '.bak-' + time.strftime('%Y%m%d%H%M%S')
    shutil.copy2(data_path, bak)
    out = {'data': recs} if isinstance(raw, dict) else recs
    tmp = data_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, data_path)
    print(f'[DONE] 已写盘 {data_path}；备份 {bak}')


if __name__ == '__main__':
    main()
