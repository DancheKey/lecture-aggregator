#!/usr/bin/env python3
"""重新解析 data/lectures.json 中所有已有 URL，用最新 parser 提取字段后写回。

用途：parser 逻辑升级后，不需要重新抓取列表页，只把已有详情页重新下载并解析，
即可让旧数据享受到更完整的字段提取（如 题目/地点/无标签简介）。
"""
import json
import os
import re
import sys
import time
import requests
import charset_normalizer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SCNULectureAggregator/0.1)'}
TIMEOUT = 15


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        best = charset_normalizer.from_bytes(r.content).best()
        if best:
            return str(best)
        return r.content.decode('utf-8', errors='replace')
    except Exception as e:
        print(f'[WARN] fetch failed {url}: {e}', file=sys.stderr)
        return None


def main():
    sys.path.insert(0, os.path.join(ROOT, 'scraper'))
    from parsers import parse_detail  # noqa: E402
    # 重新解析时跳过图片 OCR：避免 torch/easyocr 在部分机器上崩溃，同时大幅提升速度；
    # 结构化字段（题目/地点/主讲人/简介/摘要）通常已在正文中。
    import parsers as _parsers
    _parsers._img_to_text = lambda img_url_or_bytes: ''

    with open(DATA_PATH, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    arr = raw.get('data', []) if isinstance(raw, dict) else raw

    updated = 0
    unchanged = 0
    failed = 0
    new_arr = []
    for rec in arr:
        url = rec.get('sourceUrl')
        if not url:
            new_arr.append(rec)
            continue
        html = fetch(url)
        if not html:
            print(f'[FAIL] {url}')
            failed += 1
            new_arr.append(rec)
            continue
        try:
            new_rec = parse_detail(
                html, url,
                rec.get('college', ''),
                rec.get('campus', ''),
                default_year=2026,
                list_title=rec.get('listTitle', '')
            )
            # 保留 parser 未覆盖的元字段
            new_rec['listTitle'] = rec.get('listTitle', '')
            # 判断是否有字段变化
            relevant = ['topic', 'location', 'speaker', 'speakerAffiliation',
                        'speakerBio', 'lectureStart', 'lectureEnd', 'abstract', 'organizer']
            changed = any(new_rec.get(k) != rec.get(k) for k in relevant)
            if changed:
                print(f'[UPD] {rec.get("college")} | {rec.get("title")[:40]}')
                updated += 1
            else:
                unchanged += 1
            new_arr.append(new_rec)
        except Exception as e:
            print(f'[ERR] {url}: {e}', file=sys.stderr)
            new_arr.append(rec)
            failed += 1
        time.sleep(0.2)

    new_arr.sort(key=lambda x: x.get('lectureStart') or '', reverse=True)
    with open(DATA_PATH, 'w', encoding='utf-8') as f:
        json.dump({'updatedAt': raw.get('updatedAt', ''), 'data': new_arr},
                  f, ensure_ascii=False, indent=2)
    print(f'\n[SUMMARY] updated={updated}, unchanged={unchanged}, failed={failed}, total={len(new_arr)}')


if __name__ == '__main__':
    main()
