#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把线上会议号（腾讯会议 / Zoom）统一并入 location 字段（2026-09-23）。

用户口径：线上讲座不再用独立「会议号」字段展示，一律并入「地点」，
格式「腾讯会议 xxx-xxx-xxx」或「Zoom <号码>」。本脚本处理三类存量：

  A. 含 meetingId / meetingPlatform 的记录 → 合并进 location（已有线下会场则括注追加），
     并删除这两个字段。
  B. 物理学院 location 为空的记录 → 重抓源页用 parse_detail 取地点（线上会议号）。
  C. location 为「腾讯会议 <9 位纯数字>」→ 归一为 xxx-xxx-xxx。

用法：
  python scripts/fix_meeting_locations.py            # dry-run，仅打印
  python scripts/fix_meeting_locations.py --apply    # 写回 data/lectures.json
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
os.environ.setdefault('SCNU_LLM_TEXT', '0')
os.environ.setdefault('SCNU_LLM_RICH', '0')

import requests  # noqa: E402
import parsers as P  # noqa: E402

DATA = os.path.join(ROOT, 'data', 'lectures.json')
PHYSICS = '物理学院'
_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}


def _pure_tc9(loc):
    m = re.fullmatch(r'\s*腾讯会议\s*(\d{9})\s*', loc or '')
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    with open(DATA, encoding='utf-8') as f:
        doc = json.load(f)
    data = doc['data']

    # --- A. meetingId / meetingPlatform 合并进 location ---
    a_cnt = 0
    for r in data:
        mid = r.get('meetingId')
        if not mid:
            continue
        plat = r.get('meetingPlatform') or ''
        merged = P.format_meeting_location(plat, mid)
        cur = (r.get('location') or '').strip()
        if not cur:
            new = merged
        elif merged and merged.split()[-1].replace('-', '') in cur.replace('-', '').replace(' ', ''):
            new = cur  # 已含该号码
        else:
            new = f'{cur}（{merged}）'
        print(f'[A] {r.get("sourceUrl","")[-24:]} :: {cur!r} + {plat} {mid} -> {new!r}')
        if args.apply:
            r['location'] = new
            r.pop('meetingId', None)
            r.pop('meetingPlatform', None)
        a_cnt += 1

    # --- B. 物理学院空 location 重抓源页 ---
    b_cnt = 0
    cache = {}
    for r in data:
        if r.get('college') != PHYSICS or (r.get('location') or '').strip():
            continue
        url = r.get('sourceUrl') or ''
        if not url:
            continue
        if url not in cache:
            try:
                resp = requests.get(url, headers=_UA, timeout=25)
                resp.encoding = 'utf-8'
                res = P.parse_detail(resp.text, url, PHYSICS, r.get('campus') or '大学城')
                cache[url] = res if isinstance(res, list) else [res]
            except Exception as e:  # noqa: BLE001
                cache[url] = []
                print(f'[B] 抓取失败 {url}: {e}')
        recs = cache[url]
        # 按 lectureStart 精确匹配，否则取首个非空
        loc = ''
        st = r.get('lectureStart') or ''
        for cand in recs:
            if st and cand.get('lectureStart') == st and cand.get('location'):
                loc = cand['location']
                break
        if not loc:
            for cand in recs:
                if cand.get('location'):
                    loc = cand['location']
                    break
        print(f'[B] {url[-24:]} start={st[:16]} -> {loc!r}')
        if args.apply and loc:
            r['location'] = loc
        if loc:
            b_cnt += 1

    # --- C. 腾讯会议 9 位纯数字归一 ---
    c_cnt = 0
    for r in data:
        d9 = _pure_tc9(r.get('location'))
        if d9:
            new = f'腾讯会议 {d9[:3]}-{d9[3:6]}-{d9[6:]}'
            print(f'[C] {r.get("sourceUrl","")[-24:]} :: {r.get("location")!r} -> {new!r}')
            if args.apply:
                r['location'] = new
            c_cnt += 1

    # --- D. 含「会议号：」措辞的纯线上值归一为「平台 号码」（混合值交换为「线下（线上）」）---
    d_cnt = 0
    _num = r'(\d{3}[\s-]?\d{3}[\s-]?\d{3}|\d{9,11})'
    _d_pure = re.compile(
        r'^腾讯会议\s*[（(]?\s*(?:会议)?号\s*[：:]?\s*' + _num + r'\s*[)）]?\s*$')
    _d_mix = re.compile(
        r'^腾讯会议\s*[（(]?\s*(?:会议)?号\s*[：:]?\s*' + _num + r'\s*[)）]\s*[；;]\s*(.+)$')
    for r in data:
        loc = (r.get('location') or '').strip()
        if not loc or '会议号' not in loc:
            continue
        mp = _d_pure.match(loc)
        mm = _d_mix.match(loc)
        if mp:
            new = P.format_meeting_location('腾讯会议', mp.group(1))
        elif mm:
            new = f'{mm.group(2).strip()}（{P.format_meeting_location("腾讯会议", mm.group(1))}）'
        else:
            print(f'[D] 跳过（无法安全归一）: {loc!r}')
            continue
        print(f'[D] {r.get("sourceUrl","")[-24:]} :: {loc!r} -> {new!r}')
        if args.apply:
            r['location'] = new
        d_cnt += 1

    print(f'\n统计：A(meetingId 合并)={a_cnt}  B(物理空地点补录)={b_cnt}  '
          f'C(腾讯会议归一)={c_cnt}  D(会议号措辞归一)={d_cnt}')
    if args.apply:
        with open(DATA, 'w', encoding='utf-8', newline='') as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        print(f'已写回 {DATA}')
    else:
        print('（dry-run，未写回；加 --apply 生效）')


if __name__ == '__main__':
    main()
