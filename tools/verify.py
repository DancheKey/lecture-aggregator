"""校验新信息源栏目 URL 的连通性与内容（是否含讲座类链接）。"""
import os
import sys
import json
import time
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import charset_normalizer

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SCNULectureAggregator/0.2)'}
TIMEOUT = 15
LECTURE_RE = __import__('re').compile(r'学术讲座|讲座|学术报告|报告会|学术沙龙|讲坛|论坛|预告')


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        best = charset_normalizer.from_bytes(r.content).best()
        html = str(best) if best else r.content.decode('utf-8', 'replace')
        return html, r.status_code
    except Exception as e:
        return None, None


def lecture_link_count(html, base):
    if not html:
        return 0, []
    soup = BeautifulSoup(html, 'html.parser')
    n = 0
    samples = []
    for a in soup.find_all('a'):
        t = a.get_text(strip=True)
        if LECTURE_RE.search(t):
            n += 1
            if len(samples) < 3:
                samples.append(t[:40])
    return n, samples


def main():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'new_sources.json')
    with open(path, encoding='utf-8') as f:
        news = json.load(f)
    for s in news:
        name = s['name']
        base = s['base']
        print('=' * 70)
        print(f"【{name}】 {s['campus']}  base={base}")
        urls = s.get('list_urls', [])
        if urls == ['__PROBE__']:
            # 探查候选路径
            for p in s.get('probe_paths', []):
                u = base.rstrip('/') + p
                html, code = fetch(u)
                n, samp = lecture_link_count(html, base)
                print(f"  PROBE code={code} size={len(html) if html else 0} lectureLinks={n}  {u}")
                time.sleep(0.4)
            continue
        for u in urls:
            html, code = fetch(u)
            n, samp = lecture_link_count(html, base)
            print(f"  code={code} size={len(html) if html else 0} lectureLinks={n}  {u}")
            for sm in samp:
                print(f"      · {sm}")
            time.sleep(0.4)


if __name__ == '__main__':
    main()
