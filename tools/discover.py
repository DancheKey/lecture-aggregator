"""讲座栏目发现脚本：逐个访问各单位主页，定位"讲座/学术/通知公告"栏目URL。

用法：python discover.py
输出：
  - tools/discover_report.txt   人类可读报告（每个单位候选栏目）
  - tools/draft_sources.yaml    自动挑选后的草稿信息源（供人工复核）
"""
import os
import sys
import json
import time
import yaml
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
import charset_normalizer

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SCNULectureAggregator/0.2)'}
TIMEOUT = 15

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNITS_PATH = os.path.join(ROOT, 'tools', 'units.json')
REPORT_PATH = os.path.join(ROOT, 'tools', 'discover_report.txt')
DRAFT_PATH = os.path.join(ROOT, 'tools', 'draft_sources.yaml')

# 链接文本关键词权重
TEXT_W = {
    '讲座': 10, '学术报告': 10, '报告会': 10, '学术讲座': 10, '专家讲座': 10,
    '学术活动': 8, '学术动态': 8, '学术交流': 8, '讲座预告': 8, '学术预告': 8,
    '讲座信息': 8, '讲座通知': 8, '学术论坛': 8, '论坛': 6,
    '通知公告': 6, '通知': 5, '公告': 5, '通告': 5,
    '新闻': 2, '资讯': 2, '动态': 2, '信息': 1, '快讯': 1,
}
# URL路径关键词权重
URL_W = {'lecture': 4, 'xueshu': 3, 'academic': 3, 'huodong': 3, 'activity': 3,
         'tongzhi': 3, 'tzgg': 3, 'notice': 3, 'gonggao': 3, 'yugao': 3, 'announcement': 3}
# 惩罚关键词（行政/事务类，通常不是讲座发布栏）
PENALTY = ['招生', '招聘', '财务', '后勤', '党建', '工会', '人事', '采购', '招标',
           '资产', '离退休', '基建', '审计', '保卫', '医疗', '医院', '团委', '校友',
           '团', '就业', '资助', '学工']

COMMON_PATHS = [
    '/tongzhigonggao/', '/xinwenzixun/tongzhigonggao/', '/xinwen/tongzhigonggao/',
    '/tzgg/', '/notice/', '/xueshuhuodong/', '/keyanxinxi/xueshuhuodong/',
    '/xueshuhuodongyugao/', '/xueshuyugao/', '/tz/', '/list/',
]


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        best = charset_normalizer.from_bytes(r.content).best()
        html = str(best) if best else r.content.decode('utf-8', 'replace')
        return html, r.status_code
    except Exception as e:
        return None, None


def score(text, url):
    s = 0
    for k, w in TEXT_W.items():
        if k in text:
            s += w
    low = url.lower()
    for k, w in URL_W.items():
        if k in low:
            s += w
    for p in PENALTY:
        if p in text:
            s -= 6
    return s


def discover_links(base):
    html, code = fetch(base)
    if not html:
        return [], code
    soup = BeautifulSoup(html, 'html.parser')
    bd = urlparse(base).netloc
    cands = []
    seen = set()
    for a in soup.find_all('a'):
        t = a.get_text(strip=True)
        h = a.get('href')
        if not h or not t or h.lower().startswith('javascript'):
            continue
        absurl = urljoin(base, h)
        if urlparse(absurl).netloc != bd:
            continue
        if absurl in seen:
            continue
        seen.add(absurl)
        sc = score(t, absurl)
        if sc > 0:
            cands.append((sc, t, absurl))
    cands.sort(reverse=True)
    return cands, code


def probe_paths(base):
    res = []
    for p in COMMON_PATHS:
        url = base.rstrip('/') + p
        html, code = fetch(url)
        if code == 200 and html:
            res.append((url, len(html)))
    return res


def pscore_url(u):
    s = 0
    low = u.lower()
    for k in ['tongzhi', 'tzgg', 'gonggao', 'xueshu', 'huodong', 'yugao', 'lecture', 'notice', 'xueshu']:
        if k in low:
            s += 1
    return s


def autopick(cands, probes):
    good = [c for c in cands if c[0] >= 5]
    if good:
        return good[0][2], 'link:' + good[0][1]
    if probes:
        probes_sorted = sorted(probes, key=lambda x: pscore_url(x[0]), reverse=True)
        return probes_sorted[0][0], 'probe'
    return None, 'NONE'


def main():
    with open(UNITS_PATH, encoding='utf-8') as f:
        units = json.load(f)
    report_lines = []
    draft = {'sources': []}
    for u in units:
        name = u['name']
        base = u['base']
        campus = u['campus']
        report_lines.append('=' * 70)
        report_lines.append(f"【{name}】 {campus}  base={base}")
        cands, code = discover_links(base)
        report_lines.append(f"  主页抓取: code={code}, 候选链接 {len(cands)} 条")
        for sc, t, url in cands[:6]:
            report_lines.append(f"    [score={sc:>3}] {t}  ->  {url}")
        probes = probe_paths(base)
        if probes:
            report_lines.append(f"  常见路径探测命中 {len(probes)} 条:")
            for url, sz in probes:
                report_lines.append(f"    [size={sz}] {url}")
        pick, why = autopick(cands, probes)
        report_lines.append(f"  >>> 自动选择: {pick}  ({why})")
        if pick:
            draft['sources'].append({
                'name': name, 'campus': campus, 'base': base,
                'list_urls': [pick], '_pick': why,
            })
        else:
            draft['sources'].append({
                'name': name, 'campus': campus, 'base': base,
                'list_urls': [], '_pick': 'MANUAL',
            })
        time.sleep(0.6)

    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))
    # 写入草稿（去掉辅助字段）
    clean = {'sources': [{k: v for k, v in s.items() if not k.startswith('_')} for s in draft['sources']]}
    with open(DRAFT_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(clean, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f'完成：{len(units)} 个单位，{len(draft["sources"])} 条草稿。报告={REPORT_PATH}')


if __name__ == '__main__':
    main()
