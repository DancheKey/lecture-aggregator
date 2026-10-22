# -*- coding: utf-8 -*-
"""无摘要回填：重抓源页、用当前解析器重解析，只补 abstract 字段（dry-run 默认）。

背景
----
库内 1465 条无摘要（38.1%）。抽样 155 条实测：51.6% 源站正文根本没有可当摘要的
段落（只有标题/时间/地点/讲者），42.6% 那段文字就是主讲人简介，只有约 4% 疑似
漏提取。但另一项抽样发现：**部分记录用当前解析器重解析后能拿到 abstract**——
说明历史数据停留在旧版解析结果，解析器改进没惠及存量。

本脚本只做一件事：把「重解析后确实有 abstract」的记录补上，其余一概不动。

安全设计
--------
- **默认 dry-run**，只写 reports/，不碰 data/；`--apply` 才落库。
- **只改 abstract 一个字段**，其余字段原样保留（不做「以新解析结果整条覆盖」，
  避免把人工核对过的字段冲掉）。
- 落库前过与前端 `app.display.js::abstractOf` 同一套过滤：bio 前缀、与 speakerBio
  高度重叠、行政通知噪声——这些在页面上本来就不显示，补进去只是脏数据。
- 落库走 `lectures_writer.write_lectures`（备份 + 原子写）。
- HTML 缓存到 tmp/backfill_cache/，中断可续跑。

用法
----
    python scripts/backfill_abstract.py                 # dry-run，出报告
    python scripts/backfill_abstract.py --workers 4 --limit 100
    python scripts/backfill_abstract.py --apply         # 落库（先看报告再执行）
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import threading
from queue import Queue

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import scraper as S            # noqa: E402
import parsers as P            # noqa: E402
from lectures_writer import write_lectures  # noqa: E402

CACHE_DIR = os.path.join(ROOT, 'tmp', 'backfill_cache')
REPORT_DIR = os.path.join(ROOT, 'reports')

# 与 site/app.display.js::abstractOf 一致的 bio 前缀（页面本来就不显示）
BIO_PREFIXES = ('主讲人简介', '报告人简介', '嘉宾介绍', '专家介绍', '报告人介绍',
                '主讲人介绍', '演讲者简介', 'About the Speaker')

# 行政通知列表噪声（页面本来就不显示）
_ADMIN_RE = re.compile(r'资讯及通知|(?:关于.{2,40}(?:通知|公告|申报|征集|转发|招标|遴选).*){2,}')

MIN_LEN = 30          # 太短的「摘要」没有信息量
MAX_LEN = 5000        # 与前端 truncate 上限一致
BIO_OVERLAP_MAX = 0.25  # abstract 与 bio 的 4-gram 重合率上限，超过即判为简介续段
# 取值依据（2026-10-22，环境研究院 25 条无摘要实测）：真摘要与 bio 的 4-gram 重合
# 普遍 <0.07（中文 bio vs 英文摘要近乎 0）；bio 续段则 ≥0.25（「地表系统中有机碳」
# 「Uptake, Transport…」两条的 abstract 是同一段简介的后半截）。阈值取 0.25 偏向
# 严格——本判据只决定「补不补」，误判代价是少补一条，不会写入脏数据。

# 简介画像判据（2026-10-22）：字面重合抓不住「换了说法的同一段简介」。
# 实测「碳中和目标与环境保护」：abstract 与 bio 的 4-gram 重合仅 0.062，但内容是
# 「中国分析测试协会理事长…主持了…担任…首席科学家」——通篇头衔与项目，无一字讲
# 讲座内容。故按「简历特征词 ≥2 且内容特征词 = 0」判为简介。
_BIO_MARKERS = ('主持了', '主持国家', '主持教育部', '担任', '现任', '曾任', '获得了',
                '获得博士学位', '发表论文', '发表学术论文', 'h指数', 'h-index',
                '入选', '博士生导师', '享受国务院', '特聘教授', '讲座教授', '兼职教授',
                '首席科学家', '专家组组长', '重点实验室主任', '协会理事长')
_CONTENT_MARKERS = ('本讲座', '本次报告', '本报告', '本次讲座', '将介绍', '将讨论',
                    '将围绕', '将结合', '将分析', '将阐述', 'we will', 'this talk',
                    'this lecture', 'in this speech', 'in this talk', 'abstract',
                    'we introduce', 'we consider', 'we discuss', 'let ')


def _norm(s):
    """归一：去空白 + 常见中英文标点。

    比前端 `app.display.js::abstractOf`（只 replace(/\\s/g,'')）更严格。差异是有意的：
    前端是**展示层最后一道防线**，本脚本是**写入层**——写入侧过滤得更狠，只会「少补」，
    不会「多补」。反之若写入侧更宽松，就会出现「数据里有 bio 型摘要、页面不显示」的
    脏数据。实测必要性：VLM 输出的简介用半角逗号、页面正文用全角，不做标点归一则
    「刘喆,教育学博士,…」与「刘喆，教育学博士，…」判不出重复。
    """
    return re.sub(r'[\s，。、；：！？（）()《》〈〉【】「」『』“”‘’"\'\'.,;:!?\-—…·]', '', s or '')


def _overlap_ratio(a, b, n=4):
    """a 的 n-gram 有多大比例也出现在 b 里（0~1）。

    用途：识别「abstract 其实是 bio 的续段」。实测（2026-10-22 环境研究院）——
    bio 写「张干，博导，现任…国家重点实验室主任…」，abstract 却是同一段简介的
    后半截「本科和硕士期在南京大学地质系就读…」，两者既非前缀关系也非包含关系，
    只有字符级重合能抓住。
    """
    if not a or not b or len(a) < n:
        return 0.0
    A = {a[i:i + n] for i in range(len(a) - n + 1)}
    B = {b[i:i + n] for i in range(len(b) - n + 1)}
    if not A:
        return 0.0
    return len(A & B) / float(len(A))


def judge(new_abs, old_bio, new_bio=''):
    """返回 (是否接受, 原因)。口径与前端 abstractOf 一致，另加「重解析 bio」比对。"""
    ab = (new_abs or '').strip()
    if not ab:
        return False, 'empty'
    if len(ab) < MIN_LEN:
        return False, 'too-short'
    for p in BIO_PREFIXES:
        if ab.startswith(p):
            return False, 'bio-prefix'
    # 两侧 bio 取更长者比对：库内 bio 常为空，重解析出的 bio 才是「这段话到底是啥」的答案
    bio = old_bio if len(_norm(old_bio)) >= len(_norm(new_bio)) else new_bio
    sb = _norm(bio)
    nab = _norm(ab)
    if len(sb) > 10 and nab and (nab in sb or sb in nab):
        return False, 'dup-bio'
    # 续段检测：abstract 与 bio 的 4-gram 重合 ≥ BIO_OVERLAP_MAX 即视为同一段简介
    if len(sb) > 30 and _overlap_ratio(nab, sb) >= BIO_OVERLAP_MAX:
        return False, 'bio-overlap'
    # 简介画像：通篇头衔/项目、无一字讲讲座内容 → 是简历不是摘要
    n_bio = sum(1 for k in _BIO_MARKERS if k in ab)
    n_content = sum(1 for k in _CONTENT_MARKERS if k.lower() in ab.lower())
    if n_bio >= 2 and n_content == 0:
        return False, 'bio-profile'
    if _ADMIN_RE.search(ab):
        return False, 'admin-noise'
    if len(ab) > MAX_LEN:
        ab = ab[:MAX_LEN]
    return True, 'ok'


def _cache_path(url):
    h = hashlib.md5(url.encode('utf-8')).hexdigest()
    return os.path.join(CACHE_DIR, h + '.html')


def _fetch_cached(url):
    p = _cache_path(url)
    if os.path.exists(p):
        try:
            with open(p, encoding='utf-8', errors='ignore') as f:
                return f.read()
        except OSError:
            pass
    html = S.fetch(url, allowed_domains=['scnu.edu.cn'])
    if html:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(html)
    return html


def _pick_abstract(out, rec):
    """从解析结果里挑与库内记录对应的那条，返回 (abstract, speakerBio)。

    同时返回重解析得到的 speakerBio：库内 bio 可能为空（无摘要的 498 条里就有
    433 条连 bio 都没有），只拿库内 bio 比对判不出「abstract 其实是 bio」。
    实测（2026-10-22 环境研究院）：3 条 abstract 与重解析 bio 逐字相同，却因库内
    bio 为空而通过了 dup-bio 判定。
    """
    if out is None:
        return None, ''
    lst = out if isinstance(out, list) else [out]
    target = (rec.get('lectureStart') or '')[:16]
    for x in lst:
        if target and (x.get('lectureStart') or '')[:16] == target \
                and (x.get('abstract') or '').strip():
            return x['abstract'], (x.get('speakerBio') or '')
    for x in lst:
        if (x.get('abstract') or '').strip():
            return x['abstract'], (x.get('speakerBio') or '')
    return None, ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 条（调试用）')
    ap.add_argument('--college', action='append', default=[],
                    help='只处理指定学院（可多次）。抽样显示可回填集中在环境研究院/数学科学学院')
    ap.add_argument('--apply', action='store_true', help='落库（默认 dry-run）')
    args = ap.parse_args()

    with open(os.path.join(ROOT, 'data', 'lectures.json'), encoding='utf-8') as f:
        doc = json.load(f)
    recs = doc['data']

    targets = [i for i, r in enumerate(recs) if not (r.get('abstract') or '').strip()]
    if args.college:
        want = set(args.college)
        targets = [i for i in targets if (recs[i].get('college') or '') in want]
    if args.limit:
        targets = targets[:args.limit]
    total = len(targets)
    print('无摘要记录 %d 条，本次处理 %d 条，并发 %d，%s'
          % (sum(1 for r in recs if not (r.get('abstract') or '').strip()),
             total, args.workers, '落库模式' if args.apply else 'dry-run'))

    results = []
    lock = threading.Lock()
    done = [0]
    q = Queue()
    for i in targets:
        q.put(i)

    def worker():
        while True:
            try:
                idx = q.get_nowait()
            except Exception:
                return
            r = recs[idx]
            url = r.get('sourceUrl') or ''
            item = {'idx': idx, 'url': url, 'college': r.get('college'),
                    'title': (r.get('title') or '')[:60], 'status': '', 'reason': '',
                    'new': '', 'oldBio': (r.get('speakerBio') or '')[:40]}
            if not url:
                item['status'] = 'skip'
                item['reason'] = 'no-url'
            else:
                try:
                    html = _fetch_cached(url)
                    if not html:
                        item['status'] = 'skip'
                        item['reason'] = 'fetch-fail'
                    else:
                        out = P.parse_detail(html, url, r.get('college'),
                                             r.get('campus'), None,
                                             list_title=r.get('listTitle') or '',
                                             skip_news_filter=bool(r.get('newsFilterBypass')))
                        ab, new_bio = _pick_abstract(out, r)
                        if ab is None:
                            item['status'] = 'empty'
                            item['reason'] = 'no-abstract'
                        else:
                            ok, why = judge(ab, r.get('speakerBio'), new_bio)
                            item['status'] = 'pass' if ok else 'reject'
                            item['reason'] = why
                            item['new'] = ab.strip()[:MAX_LEN]
                except Exception as e:
                    item['status'] = 'skip'
                    item['reason'] = 'error:%s' % type(e).__name__
            with lock:
                results.append(item)
                done[0] += 1
                if done[0] % 20 == 0 or done[0] == total:
                    n_pass = sum(1 for x in results if x['status'] == 'pass')
                    print('  进度 %d/%d  可回填 %d' % (done[0], total, n_pass), flush=True)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    passed = [x for x in results if x['status'] == 'pass']
    by_status = {}
    for x in results:
        by_status[x['status']] = by_status.get(x['status'], 0) + 1
    print()
    print('=== 结果 ===')
    for k, v in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print('  %-8s %d' % (k, v))
    print('可回填 %d / %d (%.1f%%)' % (len(passed), total, 100.0 * len(passed) / max(1, total)))

    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M')
    rp = os.path.join(REPORT_DIR, 'backfill-abstract-%s.json' % stamp)
    with open(rp, 'w', encoding='utf-8') as f:
        json.dump({'generatedAt': datetime.datetime.now().isoformat(timespec='seconds'),
                   'total': total, 'byStatus': by_status,
                   'items': results}, f, ensure_ascii=False, indent=2)
    print('报告 → %s' % rp)

    if not args.apply:
        print('\n[dry-run] 未改动 data/lectures.json。确认报告后加 --apply 落库。')
        return

    if not passed:
        print('无可回填项，跳过落库。')
        return
    for x in passed:
        recs[x['idx']]['abstract'] = x['new']
    doc['updatedAt'] = datetime.datetime.now().isoformat(timespec='seconds')
    write_lectures(doc, tag='backfill_abstract')
    print('已回填 %d 条 abstract。请运行 python scripts/generate_frontend_data.py 生成前端切片。' % len(passed))


if __name__ == '__main__':
    main()
