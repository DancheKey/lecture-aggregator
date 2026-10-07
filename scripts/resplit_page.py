#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用单页多场拆分落地：用当前解析器重解析指定 URL，把存量单条记录替换为多场。

先例与守卫承自 scripts/fix_conf_splits.py 的 AUTO 模式（2026-09-26）：

  - fetch（缓存于 tmp/，可离线重跑）
  - 纯规则重解析（关双轨，拆分与时间是规则能力，不掺模型）
  - 断言拆出 ≥2 场，否则中止（不硬拆）
  - 年份体检：各场 lectureStart 年份须与原记录 publishTime 年份一致
  - 静态字段（college/campus/title/publishTime 等）从原记录继承
  - 原 URL 的记录须连续，整体原位替换；改前备份

⚠ 教训（psy 脏值清理 2026-10-07）：改 data/lectures.json 后必须跑
  scripts/generate_frontend_data.py 同步前端切片并一并提交。

## 用法

    python scripts/resplit_page.py <URL>            # dry-run
    python scripts/resplit_page.py <URL> --apply    # 写盘（备份 + 推进 updatedAt）

## 已落地

    2026-10-07  psy283（三场工作坊，7月2-4日）     —— 见 fix_psy283_split.py（含地点修正）
    2026-10-07  psy221（迎校庆工作坊，12月1日三场）—— 候选1 重复标签可自动拆
"""
import datetime
import json
import re
import os
import shutil
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers  # noqa: E402

DATA = os.path.join(ROOT, 'data', 'lectures.json')
BACKUP_DIR = os.path.join(ROOT, 'tmp')
APPLY = '--apply' in sys.argv
URL = next((a for a in sys.argv[1:] if a.startswith('http')), None)

# 拆分各场共享、从原记录继承的静态字段（fix_conf_splits 同款口径）
_STATIC_KEYS = ('sourceUrl', 'images', 'college', 'campus', 'title', 'listTitle',
                'publishTime', 'publishTimeSource', 'organizer', 'newsFilterBypass')


def _fetch(url):
    dst = os.path.join(BACKUP_DIR, 'resplit_' +
                       re.sub(r'[^0-9A-Za-z]', '_', url) + '.html')
    if os.path.exists(dst) and os.path.getsize(dst) > 500:
        return open(dst, 'rb').read().decode('utf-8', 'ignore')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=30).read()
    open(dst, 'wb').write(raw)
    return raw.decode('utf-8', 'ignore')


def main():
    if not URL:
        print('用法: python scripts/resplit_page.py <URL> [--apply]')
        return 1
    with open(DATA, encoding='utf-8') as f:
        doc = json.load(f)
    rows = doc['data'] if isinstance(doc, dict) else doc
    hits = [i for i, r in enumerate(rows) if r.get('sourceUrl') == URL]
    if not hits:
        print('[ABORT] 库中找不到 %s' % URL)
        return 1
    if hits[-1] - hits[0] + 1 != len(hits):
        print('[ABORT] 该 URL 的记录不连续（%s）——人工检查' % hits)
        return 1
    base = rows[hits[0]]
    base_year = (base.get('publishTime') or '')[:4]

    html = _fetch(URL)
    parsed = parsers.parse_detail(html, URL, base.get('college'), base.get('campus'),
                                  skip_news_filter=True)
    plist = parsed if isinstance(parsed, list) else [parsed]
    print('重解析产出 %d 条（原 %d 条）' % (len(plist), len(hits)))
    if len(plist) < 2:
        print('[ABORT] 当前解析器拆不出 ≥2 场——先诊断拆分候选为何不触发，勿硬拆。')
        return 1

    merged = []
    for pr in plist:
        rec = dict(pr)
        for k in _STATIC_KEYS:
            if not rec.get(k):
                rec[k] = base.get(k)
        merged.append(rec)
    bad = [r for r in merged
           if base_year and not str(r.get('lectureStart') or '').startswith(base_year)]
    if bad:
        print('[ABORT] 存在年份异常场次（期望 %s）：%s'
              % (base_year, [(r.get('lectureStart'), r.get('lectureEnd')) for r in bad]))
        return 1
    for r in merged:
        print('  第%s场 %s ~ %s @ %s | speaker=%r | topic=%r'
              % (r.get('lectureIndex'), r.get('lectureStart'), r.get('lectureEnd'),
                 r.get('location'), r.get('speaker'), (r.get('topic') or '')[:40]))

    if not APPLY:
        print('\n[dry-run] 未写盘。加 --apply 执行。')
        return 0

    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    bak = os.path.join(BACKUP_DIR, 'lectures.json.bak-%s-before-resplit' % ts)
    shutil.copy2(DATA, bak)
    print('\n备份 -> %s' % os.path.relpath(bak, ROOT))

    rows[hits[0]:hits[-1] + 1] = merged
    payload = doc if isinstance(doc, dict) else {'data': rows}
    payload['data'] = rows
    payload['updatedAt'] = datetime.datetime.now().astimezone().isoformat(
        timespec='seconds')
    tmp = DATA + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA)
    print('已写入 %s（%d 条 → %d 场，updatedAt 已推进）'
          % (os.path.relpath(DATA, ROOT), len(hits), len(merged)))
    print('⚠ 记得跑 python scripts/generate_frontend_data.py 同步前端切片')
    return 0


if __name__ == '__main__':
    sys.exit(main())
