# -*- coding: utf-8 -*-
"""存量跨源去重写回（一次性）：对 data/lectures.json 全量跑 cross_source_dedup。

背景：cross_source_dedup 只在 scraper 全量抓取路径调用；incremental_merge 只对
new_records 内部去重（scraper.py:943），存量跨源重复不会被合并。本脚本补跑一遍。

用法：
  python scripts/dedup_existing.py            # dry-run：只打印将合并的对
  python scripts/dedup_existing.py --apply    # 备份后写回
"""
import os
import sys
import json
import shutil
import argparse
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
os.chdir(ROOT)

from scraper import cross_source_dedup  # noqa: E402

PATH = os.path.join(ROOT, 'data', 'lectures.json')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='实际写回（默认 dry-run）')
    args = ap.parse_args()

    with open(PATH, encoding='utf-8') as f:
        doc = json.load(f)
    before = doc['data']
    out = cross_source_dedup([dict(r) for r in before])

    # 找出被合并（merged）与消失的记录，打印审计
    def _key(r):
        return ((r.get('sourceUrl') or '').rstrip('/'), r.get('lectureIndex'))

    before_keys = {}
    for r in before:
        before_keys.setdefault(_key(r), []).append(r)
    merged = [r for r in out if r.get('merged')]
    print(f'[STAT] {len(before)} -> {len(out)}（减少 {len(before) - len(out)}）；'
          f'merged 标记 {len(merged)} 条')
    for r in merged:
        srcs = r.get('sources') or []
        print(f'  [MERGED] {r.get("speaker")} | {r.get("lectureStart")} | '
              f'{(r.get("title") or "")[:50]!r}')
        print(f'           primary={r.get("sourceUrl")}')
        for s in srcs:
            print(f'           absorbed={s.get("sourceUrl")} ({s.get("college")})')

    # 消失的 URL（被吸收进 merged.sources 的整条）
    out_urls = {((r.get('sourceUrl') or '').rstrip('/'), r.get('lectureIndex'))
                for r in out}
    gone = [r for r in before
            if (_key(r) not in out_urls)
            and not any((_key(r)[0] == (sr.get('sourceUrl') or '').rstrip('/')
                         and r.get('lectureIndex') is None)
                        for mr in merged for sr in (mr.get('sources') or []))]
    for r in gone:
        print(f'  [GONE?] {r.get("sourceUrl")} idx={r.get("lectureIndex")} '
              f'speaker={r.get("speaker")!r} start={r.get("lectureStart")}')
    if not args.apply:
        print('[DRY] 未写回。加 --apply 执行。')
        return

    # 排序与 scraper 全量路径一致（lectureStart desc）
    out.sort(key=lambda x: x.get('lectureStart') or '', reverse=True)
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    except Exception:
        now = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).isoformat(
            timespec='seconds')
    ts = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    bak = PATH + f'.bak-{ts}-dedup'
    shutil.copy2(PATH, bak)
    tmp = PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'updatedAt': now, 'data': out}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)
    print(f'[DONE] 写回 {len(out)} 条；备份 {bak}')


if __name__ == '__main__':
    main()
