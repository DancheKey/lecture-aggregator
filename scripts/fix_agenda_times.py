# -*- coding: utf-8 -*-
"""议程时刻继承落地：用「环节窗继承」修复后的解析器重解析 label-speakers 页。

背景（2026-09-26）：表格型议程无每场独立时刻，多人异名兜底曾给全场统一标
页眉时间（em/4169 24场全标发布钟点 11:33）。解析器已改为继承节次/环节窗
（表格替换保留节次行 + 全文环节锚点就近取值），本脚本把两页存量重解析落库：
  - em/4169 香樟经济学论坛：24 场 → 09:00/10:30/14:00/15:30 四个环节窗
  - em/3771 新年学术研讨会：11 场 → 09:20/10:30/13:30/15:10 各节窗口
用法：python scripts/fix_agenda_times.py [--apply]
"""
import os
import re
import sys
import json
import shutil
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from parsers import parse_detail  # noqa: E402

DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
TARGETS = [
    'http://em.scnu.edu.cn/a/20160108/4169.html',
    'http://em.scnu.edu.cn/a/20150110/3771.html',
]
_STATIC_KEYS = ('sourceUrl', 'images', 'college', 'campus', 'title', 'listTitle',
                'publishTime', 'publishTimeSource', 'organizer', 'newsFilterBypass')

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})


def fetch(url):
    dst = os.path.join(ROOT, 'tmp', 'conf_split_html',
                       re.sub(r'[^0-9A-Za-z]', '_', url) + '.html')
    if os.path.exists(dst) and os.path.getsize(dst) > 500:
        return open(dst, 'rb').read()
    try:
        resp = SESSION.get(url, timeout=20)
    except requests.exceptions.SSLError:
        import urllib.parse
        h = urllib.parse.urlparse(url).hostname or ''
        assert h == 'scnu.edu.cn' or h.endswith('.scnu.edu.cn')
        resp = SESSION.get(url, timeout=20, verify=False)
    open(dst, 'wb').write(resp.content)
    return resp.content


def main():
    apply_mode = '--apply' in sys.argv
    data = json.load(open(DATA_PATH, encoding='utf-8'))
    recs = data['data']
    repl = {}
    for url in TARGETS:
        base = next(r for r in recs if r.get('sourceUrl') == url)
        html = fetch(url)
        parsed = parse_detail(html, url, base.get('college'), base.get('campus'),
                              skip_news_filter=True)
        plist = parsed if isinstance(parsed, list) else [parsed]
        assert len(plist) >= 2, f'{url} 重解析未拆出多场'
        merged = []
        for pr in plist:
            rec = dict(pr)
            for k in _STATIC_KEYS:
                if k not in rec or rec.get(k) in (None, ''):
                    rec[k] = base.get(k)
            merged.append(rec)
        repl[url] = merged
        from collections import Counter
        dist = dict(Counter((r.get('lectureStart') or '')[11:16] for r in merged))
        print(f'{url.rsplit("/", 1)[-1]}: {len(merged)} 场 时刻分布 {dist}')

    if not apply_mode:
        print('演练结束（未落库）。加 --apply 落库。')
        return

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = f'{DATA_PATH}.bak-{ts}-agendatime'
    shutil.copyfile(DATA_PATH, bak)
    print(f'已备份 → {bak}')
    out = []
    seen = set()
    for r in recs:
        u = r.get('sourceUrl')
        if u in repl and u not in seen:
            out.extend(repl[u])
            seen.add(u)
        elif u not in repl:
            out.append(r)
    data['data'] = out
    data['updatedAt'] = datetime.datetime.now().isoformat(timespec='seconds')
    with open(DATA_PATH, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'落库完成，共 {len(out)} 条')


if __name__ == '__main__':
    main()
