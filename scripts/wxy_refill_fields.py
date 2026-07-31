"""对文学院展示记录中 abstract/speakerBio/title 退化字段做定向回填。

只重抓目标 URL，新值显著优于旧值时才覆盖；其它字段原样保留。
2337 为系列活动总预告（无具体主讲人/时间），直接排除。
"""
import sys, json, re, os
sys.path.insert(0, '.')
from scraper.scraper import fetch
from scraper.parsers import parse_detail

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LECT = os.path.join(BASE, 'data', 'lectures.json')
EXCL = os.path.join(BASE, 'data', 'excluded_urls.json')
URLS = os.path.join(BASE, 'tmp', 'wxy_field_refill.json')

GARBAGE = '学术研究学科简介学科方向学术活动科研项目科研获奖'

def _is_garbage(v):
    return not v or GARBAGE in v or v.strip() in ('', '。')


def _better(new, old):
    """新值是否显著优于旧值：非空、不含垃圾模板、长度更长或补齐括号。"""
    if not new:
        return False
    if _is_garbage(old):
        return not _is_garbage(new)
    # title 补齐括号
    if '（' in (new or '') and '）' in (new or '') and ('（' in (old or '') and '）' not in (old or '')):
        return True
    if '(' in (new or '') and ')' in (new or '') and ('(' in (old or '') and ')' not in (old or '')):
        return True
    return len(new) > len(old) * 1.2


def main():
    data = json.load(open(LECT, encoding='utf-8'))
    recs = data['data']
    excl = set(json.load(open(EXCL, encoding='utf-8')))
    urls = json.load(open(URLS, encoding='utf-8'))
    by_url = {}
    for r in recs:
        u = r.get('sourceUrl')
        if u in by_url:
            by_url[u].append(r)
        else:
            by_url[u] = [r]

    report = []
    exclude_urls = set()

    for url in urls:
        # 2337 系列活动总预告，无具体主讲人，直接排除
        if url.endswith('2337.html'):
            exclude_urls.add(url)
            report.append({'url': url, 'action': 'EXCLUDE', 'reason': '系列活动总预告，无具体主讲人/时间'})
            continue

        existing = by_url.get(url, [])
        m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/(\d+)\.html', url)
        year = int(m.group(1)) if m else 2024
        try:
            html = fetch(url, _retries=3)
        except Exception as e:
            report.append({'url': url, 'action': 'FETCH_FAIL', 'err': str(e)[:80]})
            continue

        out = parse_detail(html, url, '文学院', '大学城', year)
        if out is None:
            report.append({'url': url, 'action': 'FILTERED', 'reason': 'parse_detail returned None'})
            continue

        if isinstance(out, list):
            # 多讲座拆分：按 lectureIndex 匹配；缺失则追加
            used = set()
            for nr in out:
                idx = nr.get('lectureIndex', 0)
                # 找同 URL 同 lectureIndex 的记录
                cand = [r for r in existing if r.get('lectureIndex', 0) == idx and id(r) not in used]
                if cand:
                    r = cand[0]
                    used.add(id(r))
                elif existing:
                    r = existing[0]
                    used.add(id(r))
                else:
                    r = {'sourceUrl': url, 'college': '文学院', 'campus': '大学城'}
                    recs.append(r)
                _apply(r, nr, report, url)
        else:
            if existing:
                r = existing[0]
            else:
                r = {'sourceUrl': url, 'college': '文学院', 'campus': '大学城'}
                recs.append(r)
            _apply(r, out, report, url)

    # 把 exclude_urls 加入排除名单
    for u in exclude_urls:
        excl.add(u)

    json.dump(data, open(LECT, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    json.dump(sorted(excl), open(EXCL, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    json.dump(report, open(os.path.join(BASE, 'tmp', 'wxy_refill_report.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'处理 {len(urls)} 条，排除 {len(exclude_urls)} 条，报告见 tmp/wxy_refill_report.json')


def _apply(old_rec, new_rec, report, url):
    changed = []
    # 时间：新值真实且旧值为占位/空时更新
    for fld in ('lectureStart', 'lectureEnd'):
        new = new_rec.get(fld)
        old = old_rec.get(fld)
        if new and (not old or old.endswith(('08:00:00', '00:00:00'))):
            old_rec[fld] = new
            changed.append(fld)
    for fld in ('title', 'topic', 'abstract', 'speakerBio', 'speaker', 'speakerTitle', 'speakerAffiliation', 'location'):
        new = (new_rec.get(fld) or '').strip()
        old = (old_rec.get(fld) or '').strip()
        if _better(new, old):
            old_rec[fld] = new
            changed.append(fld)
    # 若 abstract 仍是垃圾而新 rec 提供了非空非垃圾 abstract
    if _is_garbage(old_rec.get('abstract')) and new_rec.get('abstract') and not _is_garbage(new_rec.get('abstract')):
        old_rec['abstract'] = new_rec['abstract'].strip()
        changed.append('abstract')
    if _is_garbage(old_rec.get('speakerBio')) and new_rec.get('speakerBio') and not _is_garbage(new_rec.get('speakerBio')):
        old_rec['speakerBio'] = new_rec['speakerBio'].strip()
        changed.append('speakerBio')
    report.append({'url': url, 'action': 'PATCH', 'changed': changed})


if __name__ == '__main__':
    main()
