# -*- coding: utf-8 -*-
"""重解析「职称残片」脏数据的源页并回写字段（2026-10-01，方案 A 配套）。

⚠ 铁律：字段值必须取自源页原文 —— 故**不手工改值**，而是用当前解析器重新解析
源页，把产出的字段回写。默认只回写 speaker（本轮目标就是姓名残片），
speakerTitle / speakerAffiliation 需显式加 --fields 才写（避免连带改动未核实的值）。

目标清单来自 scripts/scan_title_fragment_residues.py 的产出
（reports/title-fragments.json，verdict=residue 且回源页有佐证者）。

用法：
  python scripts/apply_title_fragment_fix.py                  # dry-run
  python scripts/apply_title_fragment_fix.py --apply          # 只写 speaker
  python scripts/apply_title_fragment_fix.py --apply --fields speaker,speakerTitle
"""
import hashlib
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402

DATA = os.path.join(ROOT, 'data', 'lectures.json')
CACHE = os.path.join(ROOT, 'tmp', 'reparse_sweep')
SCAN = os.path.join(ROOT, 'reports', 'title-fragments.json')


def decode(raw):
    for enc in ('utf-8', 'gb18030', 'gbk', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'ignore')


def load_page(url):
    """优先用 reparse 扫描的磁盘缓存（md5(url).html），缺则联网。"""
    p = os.path.join(CACHE, hashlib.md5(url.encode('utf-8')).hexdigest() + '.html')
    if os.path.exists(p) and os.path.getsize(p) > 1500:
        return decode(open(p, 'rb').read())
    try:
        import requests
        s = requests.Session()
        s.verify = False
        s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        r = s.get(url, timeout=20)
        if r.status_code == 200 and len(r.content) > 1500:
            return decode(r.content)
    except Exception:
        pass
    return None


def pick(outs, want, old):
    """多场拆分时挑最合适的一条：优先 speaker 等于期望值，其次等于旧值，再取首条。"""
    if not isinstance(outs, list):
        return outs or {}
    for o in outs:
        if str(o.get('speaker') or '') == want:
            return o
    for o in outs:
        if str(o.get('speaker') or '') == old:
            return o
    return outs[0] if outs else {}


def main():
    apply = '--apply' in sys.argv
    fields = ('speaker',)
    for a in sys.argv[1:]:
        if a.startswith('--fields='):
            fields = tuple(f.strip() for f in a.split('=', 1)[1].split(',') if f.strip())

    doc = json.loads(io.open(DATA, encoding='utf-8').read())
    rows = doc['data']
    by_url = {}
    for x in rows:
        by_url.setdefault(str(x.get('sourceUrl') or ''), []).append(x)

    scan = json.load(io.open(SCAN, encoding='utf-8'))['items']
    targets = [c for c in scan if c['verdict'] == 'residue']

    changed = 0
    for c in targets:
        url, old, want = c['url'], c['speaker'], c['stem']
        html = load_page(url)
        if not html:
            print('[SKIP] 源页不可用: %s' % url)
            continue
        hits = by_url.get(url) or []
        if not hits:
            print('[SKIP] 库内无该 sourceUrl: %s' % url)
            continue
        rec = hits[0]
        yr = str(rec.get('lectureStart') or '')[:4]
        year = int(yr) if yr.isdigit() else 2020
        out = P.parse_detail(html, url, rec.get('college') or '',
                             rec.get('campus') or '石牌', year,
                             skip_news_filter=True)
        new = pick(out, want, old)
        print('源页: %s' % url)
        print('  证据: %s' % (c.get('evidence') or ''))
        for f in fields:
            oldv, newv = rec.get(f), new.get(f)
            if oldv == newv:
                print('     %-20s %r  (不变)' % (f, oldv))
                continue
            print('  →  %-20s %r -> %r' % (f, oldv, newv))
            rec[f] = newv
            changed += 1
        print('')

    if not changed:
        print('无字段变化，未写盘')
        return
    if not apply:
        print('[dry-run] 共 %d 个字段待改，加 --apply 落库' % changed)
        return

    tmp = DATA + '.tmp'
    io.open(tmp, 'w', encoding='utf-8', newline='\n').write(
        json.dumps(doc, ensure_ascii=False, indent=2))
    os.replace(tmp, DATA)
    print('[apply] 已写回 %s，改动 %d 个字段' % (DATA, changed))


if __name__ == '__main__':
    main()
