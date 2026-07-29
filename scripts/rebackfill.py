#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebackfill.py —— 「parser 改动 → 受影响 URL 回填 → 再生切片」标准动作工具（Q2）。

背景（铁律）：任何 scraper/parsers.py 的字段抽取/拆分逻辑改动，都必须配套
「重解析受影响 URL → 合并新字段 → 再生 4 个前端切片」，否则线上 data 会停留在
旧逻辑的结果（4299 教训）。本工具把这套手动流程固化、复用，避免每轮手搓新脚本。

设计原则（与既有回填脚本一致，遵守"已清洗数据禁止全量重解析替换"）：
- 仅覆盖「抽取字段」(title/topic/speaker/host/location/abstract/speakerBio/
  lectureStart/speakerAffiliation/splitMode)；其余字段(listTitle/海报元数据/
  publishTime/__idx/updatedAt…)原样保留。
- 仅当「新值非空 且 与新值≠旧值」时覆盖，避免把正确旧值清成空或倒退。
- 按 (sourceUrl, lectureIndex) 匹配；若 parser 重跑后记录条数与库中不同
  （结构性变化，如 1→2 拆分），默认**跳过并告警**，交由定向脚本处理（安全护栏）。
- 写回 indent=2，保持最小 diff。

用法：
  # 指定若干 URL（空格分隔）
  python tools/rebackfill.py "http://skc.scnu.edu.cn/a/20230323/691.html" ...
  # 按源域名/学院名批量（sourceUrl 含该子串的全部记录）
  python tools/rebackfill.py --source skc.scnu.edu.cn
  python tools/rebackfill.py --source 国际文化学院
  # 从文件读 URL（每行一个）
  python tools/rebackfill.py --file urls.txt
  # 预演（不写盘）
  python tools/rebackfill.py --source skc.scnu.edu.cn --dry
  # 启用真实 VLM（默认打桩，零 API 成本；海报页如需重跑 VLM 才加）
  python tools/rebackfill.py --source swc.scnu.edu.cn --vlm
"""
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
DATA = os.path.join(ROOT, 'data', 'lectures.json')
CACHE = os.path.join(ROOT, 'tmp_diag')

# 仅覆盖这些「抽取字段」，保护所有元数据
EXTRACTED_FIELDS = [
    'title', 'topic', 'speaker', 'host', 'location', 'abstract',
    'speakerBio', 'lectureStart', 'speakerAffiliation', 'splitMode',
]

import parsers as P


def load_html(url):
    base = url.rstrip('/').split('/')[-1]
    cached = os.path.join(CACHE, base)
    if os.path.exists(cached):
        return open(cached, encoding='utf-8', errors='replace').read()
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=20).read()
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('gb18030', errors='replace')


def parse_one(url):
    html = load_html(url)
    recs = P.parse_detail(html, url, college='', campus='', default_year=None)
    return recs if isinstance(recs, list) else [recs]


def collect_urls(args):
    urls = []
    for u in args.positional:
        if u.startswith('http'):
            urls.append(u)
    if args.source:
        raw = json.load(open(DATA, encoding='utf-8'))
        seen = set()
        for r in raw['data']:
            su = r.get('sourceUrl', '')
            if args.source in su or args.source in (r.get('college') or ''):
                if su not in seen:
                    seen.add(su)
                    urls.append(su)
    if args.file:
        with open(args.file, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and line.startswith('http'):
                    urls.append(line)
    return urls


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('positional', nargs='*', help='目标 URL（http 开头）')
    ap.add_argument('--source', help='按 sourceUrl 子串或 college 名批量选取')
    ap.add_argument('--file', help='从文件读 URL（每行一个）')
    ap.add_argument('--dry', action='store_true', help='预演，不写盘')
    ap.add_argument('--vlm', action='store_true', help='启用真实 VLM（默认打桩）')
    ap.add_argument('--force', action='store_true',
                    help='记录条数变化时仍强制按位置合并（危险，慎用）')
    args = ap.parse_args()

    if not args.positional and not args.source and not args.file:
        ap.error('必须提供 URL / --source / --file 之一')

    # VLM：默认打桩，零 API 成本（仅当 --vlm 才走真实模型）
    if not args.vlm:
        P._load_vlm_config = lambda: None

    urls = collect_urls(args)
    if not urls:
        print('[rebackfill] 未定位到任何 URL。')
        return

    raw = json.load(open(DATA, encoding='utf-8'))
    data = raw['data']
    by_url = {}
    for i, r in enumerate(data):
        by_url.setdefault(r['sourceUrl'], []).append(i)

    changes = []          # (url, field, old, new, lectureIndex)  安全覆盖
    skipped = []          # (url, reason)
    review = []           # (url, field, old, new, lectureIndex)  疑似退化，需人工确认

    for url in urls:
        try:
            new_recs = parse_one(url)
        except Exception as e:
            skipped.append((url, f'parse error: {type(e).__name__} {e}'))
            continue
        if not new_recs:
            skipped.append((url, 'parser returned empty'))
            continue
        idxs = by_url.get(url, [])
        if not idxs:
            skipped.append((url, 'not in data/lectures.json'))
            continue
        if len(new_recs) != len(idxs) and not args.force:
            skipped.append((url, f'count mismatch new={len(new_recs)} old={len(idxs)} '
                                  f'(structural change; use --force or a targeted script)'))
            continue
        # VLM 护栏：记录若由 VLM 富化（海报页），默认打桩重跑会丢失 VLM 字段；
        # 除非显式 --vlm，整 URL 跳过，避免破坏海报解析结果。
        if not args.vlm:
            if any((data[j].get('imageParseMethod') == 'vlm') or data[j].get('hasPosterImage')
                   for j in idxs):
                skipped.append((url, 'VLM-enriched record; pass --vlm to re-run VLM'))
                continue
        # 按 lectureIndex 匹配；无则按位置
        for i, new in enumerate(new_recs):
            rec = data[idxs[min(i, len(idxs) - 1)]]
            new_li = new.get('lectureIndex')
            if new_li is not None:
                hit = None
                for j in idxs:
                    if data[j].get('lectureIndex') == new_li:
                        hit = data[j]
                        break
                if hit is not None:
                    rec = hit
            for fld in EXTRACTED_FIELDS:
                old_v = rec.get(fld)
                new_v = new.get(fld)
                if new_v in (None, '', []):
                    continue
                if new_v == old_v:
                    continue
                # 退化护栏：新值显著短于旧值（旧值已充实）且非合理清洗 → 疑似丢内容，
                # 列入 review 不自动覆盖（如 bio 被截断）。不误伤补丁6 这类 location 合理缩短
                # （旧~40字→新~30字，比例 0.75 不触发）。
                if (isinstance(old_v, str) and isinstance(new_v, str)
                        and len(old_v) > 120 and len(new_v) < 0.5 * len(old_v)):
                    review.append((url, fld, old_v, new_v, rec.get('lectureIndex')))
                    continue
                # 仅当新值非空且不同于旧值才覆盖（防清空/倒退）
                changes.append((url, fld, old_v, new_v, rec.get('lectureIndex')))
                rec[fld] = new_v

    # 报告
    print(f'[rebackfill] 处理 {len(urls)} 个 URL；安全覆盖 {len(changes)} 处；'
          f'待复核 {len(review)} 处；跳过 {len(skipped)} 个。')
    for c in changes[:40]:
        url, fld, old_v, new_v, li = c
        tag = f'[{li}]' if li is not None else ''
        print(f'  {fld}{tag}: {repr(old_v)[:34]} -> {repr(new_v)[:34]}'
              f'  ({url.split("/")[-1]})')
    if len(changes) > 40:
        print(f'  …（其余 {len(changes) - 40} 处省略）')
    for c in review:
        url, fld, old_v, new_v, li = c
        tag = f'[{li}]' if li is not None else ''
        print(f'  REVIEW {fld}{tag}: 新值显著短于旧值 '
              f'({len(new_v)}<{len(old_v)}字) -> {repr(new_v)[:30]}'
              f'  ({url.split("/")[-1]})')
    for s in skipped:
        print(f'  SKIP {s[0].split("/")[-1]}: {s[1]}')

    if args.dry:
        print('[DRY-RUN] 未写入。')
        return
    if not changes:
        print('无安全变更，未写盘（若有 REVIEW 项需人工确认后单独处理）。')
        return

    raw['data'] = data
    with open(DATA, 'w', encoding='utf-8') as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f'\n已写入 {DATA}（安全覆盖 {len(changes)} 处；'
          f'{len(review)} 处待复核未动）。')

    # 再生 4 个前端切片
    gen = os.path.join(ROOT, 'scripts', 'generate_frontend_data.py')
    if os.path.exists(gen):
        print('--- 再生前端切片 ---')
        os.system(f'"{sys.executable}" "{gen}"')
    else:
        print('[warn] 未找到 scripts/generate_frontend_data.py，跳过切片再生。')


if __name__ == '__main__':
    main()
