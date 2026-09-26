# -*- coding: utf-8 -*-
"""全库重解析差异盘点（dry-run，只读不落库）。

背景：解析器 2026-09-25/26 连续升级（候选9/10/11、质量闸门、表格真表头、
环节窗时刻继承），但按「增量陷阱」约定，存量 3804 条里只有十几页被定向
重解析过。本脚本对全部 3527 个源页用当前解析器重解析一遍，与库内存量对比，
产出差异报告：哪些页会新拆分 / 拆分数量变化 / 回退 / 逐场字段修正 / 失联。

口径与安全：
- parse_detail 规则层（SCNU_LLM_TEXT/RICH=0，VLM 配置置空——LLM 富化字段
  （abstract/speakerBio）不参与对比，属预期差异；OCR 保留）；
- skip_news_filter 用存量记录的 newsFilterBypass（与生产抓取口径一致）；
- list_title 用存量 listTitle（保证 title 可比）；
- 人工拆分页（splitMode=manual）照常对比但单独标注——**不作为落库建议**；
- HTML 落盘缓存（tmp/reparse_sweep/），中断后重跑自动跳过已抓页面；
- 只写 reports/ 与缓存目录，绝不触碰 data/lectures.json。

用法：python scripts/reparse_diff_report.py [--workers 4] [--limit N]
输出：reports/reparse-diff-20260926.json + reports/reparse-diff-20260926.md
"""
import os
import re
import sys
import json
import time
import hashlib
import datetime
import threading
import queue

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import parsers  # noqa: E402

# 盘点只要规则层：VLM 置空（海报页回落本地 OCR），避免 API 成本与不确定时延
parsers._load_vlm_configs = lambda: []

DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
CACHE_DIR = os.path.join(ROOT, 'tmp', 'reparse_sweep')
REPORT_BASE = os.path.join(ROOT, 'reports', 'reparse-diff-20260926')

COMPARE_FIELDS = ('topic', 'speaker', 'speakerAffiliation',
                  'lectureStart', 'lectureEnd', 'location')

_local = threading.local()


def get_session():
    if not hasattr(_local, 's'):
        s = requests.Session()
        s.verify = False
        s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        _local.s = s
    return _local.s


def fetch_cached(url):
    """HTML 磁盘缓存（断点续跑）；返回 bytes 或 None。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    dst = os.path.join(CACHE_DIR, hashlib.md5(url.encode('utf-8')).hexdigest() + '.html')
    if os.path.exists(dst) and os.path.getsize(dst) > 200:
        try:
            return open(dst, 'rb').read()
        except OSError:
            pass
    try:
        resp = get_session().get(url, timeout=20)
        if resp.status_code != 200 or len(resp.content) < 200:
            open(dst, 'wb').write(b'')  # 记住失败，避免反复重试
            return None
        open(dst, 'wb').write(resp.content)
        time.sleep(0.2)  # 对校内服务器礼貌限速
        return resp.content
    except Exception:
        return None


def compare(url, meta, old_recs, html):
    """返回该页的差异记录 dict。"""
    if html is None:
        return {'url': url, 'cat': 'unreachable', 'old': len(old_recs)}
    try:
        out = parsers.parse_detail(
            html, url, meta['college'], meta['campus'],
            list_title=meta.get('listTitle') or None,
            skip_news_filter=bool(meta.get('newsFilterBypass')))
    except Exception as e:
        return {'url': url, 'cat': 'parse_error', 'old': len(old_recs),
                'err': f'{type(e).__name__}: {e}'[:160]}
    new_recs = out if isinstance(out, list) else ([out] if out else [])
    # 出口闸门可能返回 None（单条）；统一为列表
    n_old, n_new = len(old_recs), len(new_recs)
    base = {'url': url, 'old': n_old, 'new': n_new,
            'manual': meta['manual']}
    if n_old >= 2 and n_new >= 2 and n_old != n_new:
        base['cat'] = 'split_count_change'
        base['new_topics'] = [(r.get('lectureIndex'), (r.get('topic') or '')[:24],
                               r.get('lectureStart')) for r in new_recs[:6]]
        return base
    if n_old == 1 and n_new >= 2:
        base['cat'] = 'split_new'
        base['new_topics'] = [(r.get('lectureIndex'), (r.get('topic') or '')[:24],
                               r.get('lectureStart')) for r in new_recs[:6]]
        return base
    if n_old >= 2 and n_new <= 1:
        base['cat'] = 'split_reverted'
        base['old_topics'] = [(r.get('lectureIndex'), (r.get('topic') or '')[:24])
                              for r in old_recs[:6]]
        if n_new == 1:
            base['new_topic'] = (new_recs[0].get('topic') or '')[:40]
        return base
    if n_old >= 1 and n_new == 0:
        base['cat'] = 'parse_empty'
        return base
    # 同场数：逐场逐字段比对。老记录若经 LLM 富化（llmTextEnhanced），规则层
    # 重解析不填 LLM 字段属**预期差异**——单列 field_diff_llm，不作为落库依据。
    field_diffs = []
    any_llm = False
    for i, (o, nw) in enumerate(zip(old_recs, new_recs), 1):
        if o.get('llmTextEnhanced'):
            any_llm = True
        for f in COMPARE_FIELDS:
            ov = str(o.get(f) or '').strip()
            nv = str(nw.get(f) or '').strip()
            if ov != nv:
                field_diffs.append({'i': o.get('lectureIndex') or i, 'field': f,
                                    'old': ov[:50], 'new': nv[:50],
                                    'llm': bool(o.get('llmTextEnhanced'))})
    if field_diffs:
        actionable = [d for d in field_diffs if not d['llm']]
        base['diffs'] = field_diffs[:12]
        base['n_diffs'] = len(field_diffs)
        if not actionable and any_llm:
            base['cat'] = 'field_diff_llm'
        else:
            base['cat'] = 'field_diff'
            base['n_actionable'] = len(actionable)
        return base
    base['cat'] = 'unchanged'
    return base


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    data = json.load(open(DATA_PATH, encoding='utf-8'))
    recs = data['data']
    excluded = set()
    try:
        sys.path.insert(0, os.path.join(ROOT, 'scripts'))
        from excluded_urls import load_excluded
        excluded = {u.rstrip('/') for u in load_excluded()}
    except Exception:
        pass

    from collections import defaultdict, OrderedDict
    by_url = OrderedDict()
    for r in recs:
        u = r.get('sourceUrl')
        if not u:
            continue
        if u not in by_url:
            by_url[u] = {'college': r.get('college'), 'campus': r.get('campus'),
                         'listTitle': r.get('listTitle'),
                         'newsFilterBypass': r.get('newsFilterBypass'),
                         'manual': False, 'recs': []}
        if r.get('splitMode') == 'manual':
            by_url[u]['manual'] = True
        by_url[u]['recs'].append(r)

    pages = list(by_url.items())
    if args.limit:
        pages = pages[:args.limit]
    total = len(pages)
    print(f'待盘点源页: {total}（排除名单内页面照常盘点、仅打标）', flush=True)

    results = []
    lock = threading.Lock()
    done = [0]
    t0 = time.time()

    def work(item):
        url, meta = item
        try:
            html = fetch_cached(url)
            r = compare(url, meta, meta['recs'], html)
        except Exception as e:
            r = {'url': url, 'cat': 'parse_error', 'old': len(meta['recs']),
                 'err': f'{type(e).__name__}: {e}'[:160]}
        r['excluded'] = (url.rstrip('/') in excluded)
        with lock:
            results.append(r)
            done[0] += 1
            if done[0] % 100 == 0:
                rate = done[0] / max(time.time() - t0, 1)
                eta = (total - done[0]) / max(rate, 0.1) / 60
                print(f'进度 {done[0]}/{total}（{rate:.1f} 页/秒，预计剩余 {eta:.0f} 分钟）', flush=True)

    q = queue.Queue()
    for it in pages:
        q.put(it)

    def worker():
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                return
            work(item)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # ── 汇总 ──
    from collections import Counter
    cats = Counter(r['cat'] for r in results)
    print('\n=== 差异分类汇总 ===')
    for k, v in cats.most_common():
        print(f'  {k}: {v}')

    json.dump(results, open(REPORT_BASE + '.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    # Markdown 报告
    interesting = ['split_new', 'split_count_change', 'split_reverted', 'parse_empty',
                   'parse_error', 'unreachable']
    md = []
    md.append(f'# 全库重解析差异盘点（{datetime.date.today().isoformat()}）\n')
    md.append(f'- 盘点源页：{total}；规则层重解析（无 LLM/VLM），对比口径见脚本头注。\n')
    md.append('| 分类 | 页数 | 说明 |')
    md.append('|---|---|---|')
    desc = {'split_new': '旧1条→新拆多场（候选升级新收益）',
            'split_count_change': '已拆页场次数变化',
            'split_reverted': '多场回退为单条（需人工判断是否回归）',
            'parse_empty': '新解析判空（全量重爬会丢失，需核对回顾判定）',
            'parse_error': '解析异常',
            'unreachable': '源页不可达（404/超时）',
            'field_diff': '逐场字段差异（可行动）',
            'field_diff_llm': '字段差异仅因老记录 LLM 富化（预期内，不作为落库依据）',
            'unchanged': '无差异'}
    for k, v in cats.most_common():
        md.append(f'| {k} | {v} | {desc.get(k, "")} |')
    for cat in ('split_new', 'split_count_change', 'split_reverted', 'parse_empty'):
        rows = [r for r in results if r['cat'] == cat]
        if not rows:
            continue
        md.append(f'\n## {cat}（{len(rows)} 页）\n')
        for r in rows[:300]:
            tag = ' ⚠manual' if r.get('manual') else ''
            ex = ''
            if r.get('new_topics'):
                ex = ' 新场样例: ' + '; '.join(
                    f'{i}:{t}@{s}' for i, t, s in r['new_topics'][:3])
            if r.get('old_topics'):
                ex = ' 旧场样例: ' + '; '.join(f'{i}:{t}' for i, t in r['old_topics'][:3])
            md.append(f'- {r["url"]}{tag}（旧{r["old"]}→新{r["new"]}）{ex}')
    fd = [r for r in results if r['cat'] == 'field_diff']
    if fd:
        md.append(f'\n## field_diff（{len(fd)} 页，样例前 60 页）\n')
        for r in fd[:60]:
            d = '; '.join(f"#{x['i']} {x['field']}: {x['old']} → {x['new']}"
                          for x in r.get('diffs', [])[:3])
            md.append(f'- {r["url"]}（可行动 {r.get("n_actionable", "?")} 处）{d}')
    open(REPORT_BASE + '.md', 'w', encoding='utf-8', newline='\n').write('\n'.join(md))
    print(f'\n报告已写: {REPORT_BASE}.md / .json')


if __name__ == '__main__':
    main()
