"""按年份把历史讲座记录用 Agnes 文本 LLM 重新增强（定向补丁，不整体替换）。

只对该年记录的 sourceUrl 重新抓取正文，调用 parsers 里已验证的
_llm_extract_text_fields + _apply_llm_text_to_result，把 LLM 提取的字段
补丁式回填进现有记录（仅填 topic/speaker/location/abstract/speakerBio 与
时间；不动 sourceUrl/college/title/lectureIndex 等身份与去重键，不覆盖已有
非空好值）。符合「禁整体替换、只做定向补丁」铁律。

用法：
  python scripts/backfill_llm_by_year.py --year 2026
  python scripts/backfill_llm_by_year.py --year 2026 --limit 5     # 冒烟
  python scripts/backfill_llm_by_year.py --year 2026 --dry-run     # 不写盘

说明：
- 每个 URL 只抓一次；LLM 调用走 parsers 内全局限速锁（文本 3s/次）。
- 写盘前自动备份 data/lectures.json，再用临时文件 + os.replace 原子替换。
- 抓不到的页（404/超时）保持原记录不变。
"""
import os
import sys
import json
import time
import re
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402
import charset_normalizer  # noqa: E402
import parsers  # noqa: E402

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SCNULectureAggregator/0.1)'}


def _decode_html(raw):
    """鲁棒解码 HTML：优先 <meta charset> 声明，其次 UTF-8 严格，再次 GB18030 兜底。"""
    try:
        head = raw[:2048].decode('latin-1', errors='ignore')
        m = re.search(r'charset\s*=\s*[\'\"]?\s*([a-z0-9\-_]+)', head, re.I)
        if m:
            enc = m.group(1).strip().lower()
            if enc in ('gb2312', 'gbk', 'gb18030', 'gbk2312'):
                enc = 'gb18030'
            elif enc in ('big5', 'big5hkscs'):
                enc = 'big5'
            if enc not in ('utf-8', 'utf8', 'us-ascii', 'ascii', 'iso-8859-1'):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    pass
    except Exception:
        pass
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode('gb18030')
    except UnicodeDecodeError:
        pass
    best = charset_normalizer.from_bytes(raw).best()
    if best:
        return str(best)
    return raw.decode('utf-8', errors='replace')


# 复刻 parsers.parse_detail 对正文容器的选取（稳定小片段，避免耦合其内部实现）
_CONTENT_DIV_CLASSES = (
    'wp_articlecontent', 'wp_entry', 'article-content', 'container-left',
    'article', 'content', 'news-details-all', 'news-details-middle',
    'news-text', 'entry-content',
)


def _year_of(s):
    if not s:
        return None
    m = str(s)[:4]
    return int(m) if m.isdigit() else None


def extract_body_text(html, url):
    """从 HTML 提取讲座正文文本（尽量与 parse_detail 一致）。"""
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(' ')
    meta_parts = []
    for meta in (soup.find('meta', attrs={'name': 'description'}),
                 soup.find('meta', property='og:description'),
                 soup.find('meta', attrs={'name': 'og:description'})):
        if meta and meta.get('content') and len(meta.get('content').strip()) > 3:
            meta_parts.append(meta.get('content').strip())
    if meta_parts:
        text = text + ' ' + ' '.join(meta_parts)
    text = parsers._n1_normalize(parsers._normalize_label_text(re.sub(r'\s+', ' ', text).strip()))
    text = parsers._strip_footer(text)
    content_div = None
    for cls in _CONTENT_DIV_CLASSES:
        content_div = soup.find('div', class_=cls)
        if content_div:
            break
    if not content_div:
        content_div = soup.find('article')
    body = content_div.get_text(' ') if content_div else text
    body = parsers._n1_normalize(parsers._normalize_label_text(re.sub(r'\s+', ' ', body).strip()))
    body = parsers._strip_footer(body)
    # JS 渲染站点（maths/physics 等）正文容器只含导航骨架，但 meta description 中保存了
    # 完整讲座摘要。把 meta 追加进 body，保证 LLM 能读到主讲人/时间/地点。
    if meta_parts:
        body = body + ' ' + ' '.join(meta_parts)
        body = parsers._n1_normalize(parsers._normalize_label_text(re.sub(r'\s+', ' ', body).strip()))
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, required=True)
    ap.add_argument('--limit', type=int, default=0, help='最多处理 N 个 URL（冒烟用）')
    ap.add_argument('--dry-run', action='store_true', help='不写盘，仅打印统计')
    ap.add_argument('--force', action='store_true',
                    help='重跑全部该年 URL（默认跳过已 llmTextEnhanced 的记录）')
    args = ap.parse_args()

    data_path = os.path.join(ROOT, 'data', 'lectures.json')
    raw = json.load(open(data_path, encoding='utf-8'))
    recs = raw['data'] if isinstance(raw, dict) else raw

    # 筛选该年记录
    year_recs = [r for r in recs if _year_of(r.get('lectureStart')) == args.year
                 or (_year_of(r.get('lectureStart')) is None and _year_of(r.get('publishTime')) == args.year)]
    # 按 sourceUrl 聚合（2026 实测 1:1，仍通用处理）
    by_url = {}
    for r in year_recs:
        u = str(r.get('sourceUrl', '')).rstrip('/')
        if not u.startswith('http'):
            continue
        by_url.setdefault(u, []).append(r)

    urls = list(by_url.keys())
    # 默认跳过已全部增强的 URL（省额度 + 提速）；--force 才全跑
    if not args.force:
        urls = [u for u in urls
                if not all(r.get('llmTextEnhanced') for r in by_url[u])]
    if args.limit:
        urls = urls[:args.limit]
    print(f'[INFO] 年份 {args.year}：该年记录 {len(year_recs)} 条，唯一 URL {len(by_url)} 个，'
          f'本次处理 {len(urls)} 个' + ('（dry-run，不写盘）' if args.dry_run else ''))

    stat = {'ok': 0, 'fail': 0, 'enhanced': 0, 'no_llm': 0}
    sess = requests.Session()

    for i, url in enumerate(urls, 1):
        try:
            # 显式禁用代理：scnu.edu.cn / agnes-ai.cn 均为国内域名须直连。
            # 注意 proxies={'http':None,'https':None}（空字典才阻止 requests 回退读环境变量），
            # 单个 None 反而会让 requests 回退去读 HTTP(S)_PROXY 环境变量。
            r = sess.get(url, headers=HEADERS, timeout=30,
                         proxies={'http': None, 'https': None})
            if r.status_code != 200:
                stat['fail'] += 1
                print(f'  [{i}/{len(urls)}] SKIP {r.status_code} {url}')
                continue
        except Exception as e:
            stat['fail'] += 1
            print(f'  [{i}/{len(urls)}] ERR {url} -> {e}')
            continue

        # 用 bytes + 鲁棒解码（与 scraper.py fetch 一致），避免 requests.text 按 ISO-8859-1
        # 错误解码华师 UTF-8 页面导致中文乱码、LLM 认错人名。
        html = _decode_html(r.content)
        body = extract_body_text(html, url)
        if len(body) < 30:
            stat['no_llm'] += 1
            print(f'  [{i}/{len(urls)}] NO_LLM body_too_short(len={len(body)}) {url}')
            continue
        lf = parsers._llm_extract_text_fields(body, url)
        if not lf:
            stat['no_llm'] += 1
            print(f'  [{i}/{len(urls)}] NO_LLM lf_empty {url}')
            continue

        url_year = parsers._year_from_url(url)
        default_year = args.year
        for rec in by_url[url]:
            try:
                parsers._apply_llm_text_to_result(
                    rec, lf, default_year,
                    rec.get('publishTime'), None, url_year)
                if rec.get('llmTextEnhanced'):
                    stat['enhanced'] += 1
            except Exception as e:
                print(f'  [{i}/{len(urls)}] MERGE-ERR {url} -> {e}')
        stat['ok'] += 1
        print(f'  [{i}/{len(urls)}] OK {url}  speaker={by_url[url][0].get("speaker")}  '
              f'abstract={"有" if by_url[url][0].get("abstract") else "无"}')

    print(f'\n[SUMMARY] 抓取成功 {stat["ok"]} / 失败 {stat["fail"]} / 无LLM结果 {stat["no_llm"]}；'
          f'被增强记录 {stat["enhanced"]} 条')

    if args.dry_run:
        print('[DRY-RUN] 未写盘。')
        return

    # 原子写盘（先备份）
    if stat['enhanced'] == 0:
        print('[INFO] 无记录被增强，跳过写盘。')
        return
    bak = data_path + '.bak-' + time.strftime('%Y%m%d%H%M%S')
    import shutil
    shutil.copy2(data_path, bak)
    out = {'data': recs} if isinstance(raw, dict) else recs
    tmp = data_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, data_path)
    print(f'[DONE] 已写盘 {data_path}；备份 {bak}')


if __name__ == '__main__':
    main()
