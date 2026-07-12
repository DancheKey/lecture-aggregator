"""华南师范大学讲座采集器：列表页 -> 详情页 -> 去重 -> data/lectures.json。"""
import os
import re
import sys
import json
import time
import yaml
import requests
import charset_normalizer
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parsers import parse_detail, is_lecture  # noqa: E402

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SCNULectureAggregator/0.1)'}
TIMEOUT = 15
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        # 华师部分站点为 GBK 编码，需按字节自动识别，避免中文乱码
        best = charset_normalizer.from_bytes(r.content).best()
        if best:
            return str(best)
        return r.content.decode('utf-8', errors='replace')
    except Exception as e:
        print(f'[WARN] fetch failed {url}: {e}', file=sys.stderr)
        return None


NAV_KW = ['首页', '主页', '上一页', '下一页', '尾页', '返回', '更多', '>>',
          'home', 'about', 'contact', 'rss', 'sitemap']
# 常见 CMS 内容页 URL 特征：/a/20260616/348.html 或 /xueshujiangzuo/2026/0628/74.html
_CONTENT_URL_RE = re.compile(r'/((a/\d{8}/\d+\.html)|(\d{4}/\d{4}/\d+\.html)|(\d{4}/\d{2}/\d{2}/.*\.html))', re.I)


def _abs_url(href, base):
    if href.startswith('http'):
        return href
    if href.startswith('/'):
        return base.rstrip('/') + href
    return base.rstrip('/') + '/' + href


def collect_links(html, base, list_url=None, collect_mode='auto'):
    """从列表页提取详情页链接。

    collect_mode:
      - auto: 用 is_lecture 标题关键词过滤（默认）。
      - all_items: 不过滤标题关键词，直接抓取列表中看起来像内容项的链接，
                   用于「列表页本身就是讲座列表」的栏目（如行知书院讲座预约）。
    """
    if not html:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    list_url_norm = list_url.rstrip('/') if list_url else None
    for a in soup.find_all('a'):
        txt = a.get_text(strip=True)
        href = a.get('href')
        if not href or not txt:
            continue
        # 跳过 javascript、锚点、当前列表页自身
        if href.startswith('javascript') or href == '#' or href.startswith('#'):
            continue
        url = _abs_url(href, base)
        if list_url_norm and url.rstrip('/') == list_url_norm:
            continue
        if collect_mode == 'all_items':
            # 排除明显导航词
            tlow = txt.lower()
            if any(k in tlow for k in NAV_KW):
                continue
            # 排除过短文本（导航常见）
            if len(txt) < 4:
                continue
            # all_items 用于「列表页即讲座列表」的栏目，只保留看起来像内容页的链接
            if not _CONTENT_URL_RE.search(url):
                continue
            links.append((url, txt))
        else:
            if not is_lecture(txt):
                continue
            links.append((url, txt))
    return links


def _normalize_title(title):
    """标题归一化：去空白、去常见前后缀，用于同源去重比对。"""
    s = re.sub(r'\s+', '', title.strip())
    # 去掉末尾常见的来源标注
    s = re.sub(r'[（(][^）)]*[）)]$', '', s)
    return s


def dedup(records):
    """同一学院内，标题高度相似的讲座只保留一条（保留字段更完整的）。"""
    groups = {}
    for rec in records:
        key = (rec.get('college', ''), _normalize_title(rec.get('title', '')))
        if key not in groups:
            groups[key] = []
        groups[key].append(rec)

    kept = []
    dup_count = 0
    for key, group in groups.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        # 按字段完整度排序：非空字段多的优先
        group.sort(
            key=lambda r: sum(1 for v in [
                r.get('lectureStart'), r.get('location'), r.get('speaker'),
                r.get('speakerAffiliation'), r.get('topic'), r.get('speakerBio')
            ] if v),
            reverse=True,
        )
        kept.append(group[0])
        dup_count += len(group) - 1
        names = set(r['sourceUrl'] for r in group[1:])
        print(f'[DEDUP] {key[0]} | {group[0]["title"][:40]} → 保留1条, 去重{len(group)-1}条')
    if dup_count:
        print(f'[DEDUP] 总共去除 {dup_count} 条重复记录')
    return kept


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--since', help='仅抓取该 ISO 时间之后的新信息（增量模式）')
    parser.add_argument('--full', action='store_true', help='全量抓取，忽略增量')
    args = parser.parse_args()

    cfg_path = os.path.join(ROOT, 'scraper', 'sources.yaml')
    with open(cfg_path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    year = time.localtime().tm_year

    # 决定抓取模式：有 --since 且非 --full → 增量；否则全量
    last_scrape_path = os.path.join(ROOT, 'data', 'last_scrape.json')
    since = args.since
    if not since and not args.full and os.path.exists(last_scrape_path):
        try:
            since = json.load(open(last_scrape_path, encoding='utf-8')).get('last_scrape')
        except Exception:
            since = None
    is_incremental = bool(since) and not args.full

    # 读取现有记录：增量模式作为基底（合并写回）+ 已抓 URL 集合（跳过解析/OCR）
    data_path = os.path.join(ROOT, 'data', 'lectures.json')
    existing = []
    if os.path.exists(data_path):
        try:
            existing = json.load(open(data_path, encoding='utf-8'))
        except Exception:
            existing = []
    existing_urls = {str(r.get('sourceUrl', '')).rstrip('/') for r in existing if r.get('sourceUrl')}

    lectures = {}
    if is_incremental:
        # 增量：以已有记录为基底，只补充新 URL（不重新解析旧条目）
        for r in existing:
            u = r.get('sourceUrl')
            if u:
                lectures[u] = r

    for src in cfg['sources']:
        try:
            name = src['name']
            campus = src.get('campus', '')
            base = src['base']
            # 本源所有列表页 URL（归一化），用于跳过「栏目入口」类链接
            src_list_norm = set()
            for lu in src.get('list_urls', []):
                u = lu['url'] if isinstance(lu, dict) else lu
                src_list_norm.add(str(u).rstrip('/'))
            seen = set()
            for lu in src.get('list_urls', []):
                if isinstance(lu, dict):
                    list_url = lu['url']
                    collect_mode = lu.get('collect_mode', 'auto')
                else:
                    list_url = lu
                    collect_mode = 'auto'
                html = fetch(list_url)
                for href, txt in collect_links(html, base, list_url=list_url, collect_mode=collect_mode):
                    href_norm = href.rstrip('/')
                    if href_norm in seen:
                        continue
                    seen.add(href_norm)
                    # 增量模式：已抓取过的 URL 直接跳过（不下载详情、不解析、不做 OCR）
                    if is_incremental and href_norm in existing_urls:
                        continue
                    # 跳过指向本源其他列表页的链接（栏目入口，不是讲座详情）
                    if href_norm in src_list_norm:
                        continue
                    d = fetch(href)
                    if not d:
                        continue
                    try:
                        rec = parse_detail(d, href, name, campus, year, list_title=txt)
                    except Exception as e:
                        print(f'[WARN] parse failed {href}: {e}', file=sys.stderr)
                        continue
                    rec['listTitle'] = txt
                    lectures[href] = rec
                    print(f'[OK] {name} | {rec.get("lectureStart")} | {txt}')
            time.sleep(1)
        except Exception as e:
            # 单源异常不应拖垮整次抓取：记录后继续下一个源，已采数据照常写入
            print(f'[ERROR] 信息源「{src.get("name")}」抓取失败：{e}', file=sys.stderr)
            continue

    # 同源去重：同一学院标题相似的只保留一条
    raw = list(lectures.values())
    out = dedup(raw)
    out.sort(key=lambda x: x.get('lectureStart') or '', reverse=True)
    data_dir = os.path.join(ROOT, 'data')
    os.makedirs(data_dir, exist_ok=True)
    import datetime
    # 用北京时间（Asia/Shanghai）记录更新时间，避免 GitHub Runner 默认 UTC 导致日期差一天
    try:
        from zoneinfo import ZoneInfo
        now_iso = datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    except Exception:
        # 回退：UTC+8 小时
        now_iso = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).isoformat(timespec='seconds')
    # 写入带更新时间戳的包裹格式：{updatedAt, data}；前端与后端均兼容旧版纯数组。
    with open(os.path.join(data_dir, 'lectures.json'), 'w', encoding='utf-8') as f:
        json.dump({'updatedAt': now_iso, 'data': out}, f, ensure_ascii=False, indent=2)
    with open(last_scrape_path, 'w', encoding='utf-8') as f:
        json.dump({'last_scrape': now_iso, 'mode': 'incremental' if is_incremental else 'full'},
                  f, ensure_ascii=False, indent=2)
    print(f'[DONE] total {len(out)} lectures -> data/lectures.json  '
          f'(mode={"incremental" if is_incremental else "full"}, since={since})')


if __name__ == '__main__':
    main()
