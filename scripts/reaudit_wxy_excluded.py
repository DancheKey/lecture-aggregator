"""文学院排除名单全面复审（dry-run 优先）。

对 data/excluded_urls.json 中 college=文学院 的全部 URL 重新抓取判定：
- 海报页：VLM 抽真实字段；抽到 speaker/topic/时间 即视为真实讲座预告 → RESTORE。
- 文本页：is_news_article 标题/正文关键词命中回顾稿/报道 → KEEP（维持排除）；
  否则若含讲座信号（speaker/topic/讲座/报告/论坛/讲学/研讨会） → RESTORE。
默认 dry-run 只打印分类报告；加 --apply 才真正：移除排除 + VLM/文本回填真实字段。

用法：
  python scripts/reaudit_wxy_excluded.py            # 仅报告
  python scripts/reaudit_wxy_excluded.py --apply    # 落地
"""
import sys, os, json, re
sys.path.insert(0, 'scraper')
for line in open('.env', encoding='utf-8'):
    line = line.strip()
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip()

import scraper
from bs4 import BeautifulSoup
from parsers import (parse_detail, is_news_article, is_news_record,
                    _vlm_extract_fields, _load_vlm_config, _vlm_cache_get)
from datetime import datetime as _dt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LECT = os.path.join(BASE, 'data', 'lectures.json')
EXCL = os.path.join(BASE, 'data', 'excluded_urls.json')
APPLY = '--apply' in sys.argv


def _body(html):
    soup = BeautifulSoup(html, 'html.parser')
    cd = (soup.find('div', class_='news-text')
          or soup.find('div', class_='news-details-middle')
          or soup.find('article')
          or soup.find('div', class_='entry-content'))
    return cd.get_text(' ', strip=True) if cd else ''


def _norm_dt(s):
    s = (s or '').strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return _dt.strptime(s[:19], fmt).isoformat(sep=' ')
        except (ValueError, TypeError):
            continue
    return None


def main():
    data = json.load(open(LECT, encoding='utf-8'))
    recs = data['data']
    excl = json.load(open(EXCL, encoding='utf-8'))
    cfg = _load_vlm_config()

    wxy_excl = [u for u in excl if u.endswith('.html')
                and any(r.get('sourceUrl') == u and r.get('college') == '文学院' for r in recs)]
    print(f'文学院在排除名单: {len(wxy_excl)} 条\n')

    # 标题公告词（真实讲座预告）优先于页脚/叙事规则；回顾完成词（真·报道）维持排除
    LEC_TITLE = re.compile(r'讲座|报告|论坛|讲学|预告|研讨会|工作坊|座谈会|名家系列|专场')
    RECAP_TITLE = re.compile(r'圆满举办|圆满落幕|顺利举行|顺利完成|在华南师范大学举行|在华南师大举行|发布会召开|出版发布会|年会暨|入选|荣获|喜讯|招聘|征文|顺利结束')
    restore, keep = [], []
    for url in wxy_excl:
        rec0 = next(r for r in recs if r.get('sourceUrl') == url)
        m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/(\d+)\.html', url)
        year = int(m.group(1)) if m else 2024
        try:
            html = scraper.fetch(url, _retries=3)
        except Exception as e:
            keep.append((url, 'FETCH_FAIL:' + str(e)[:40], rec0.get('title')))
            continue
        body = _body(html)
        soup = BeautifulSoup(html, 'html.parser')
        page_title = (soup.title.string.strip() if soup.title and soup.title.string
                      else (soup.find('h1').get_text(' ', strip=True) if soup.find('h1') else ''))
        out = parse_detail(html, url, '文学院', rec0.get('campus') or '大学城', year,
                           list_title=rec0.get('title'), skip_news_filter=True)
        if out is None:
            keep.append((url, 'UNPARSEABLE', rec0.get('title')))
            continue
        rec = out[0] if isinstance(out, list) else out
        title = rec.get('title') or page_title or rec0.get('title') or ''
        ls = rec.get('lectureStart')
        # 1) 标题含回顾完成词（真·报道/回顾稿）→ 维持排除
        if RECAP_TITLE.search(title):
            keep.append((url, 'RECAP_TITLE', title))
            continue
        # 2) 标题含讲座公告词（真实预告）→ 恢复
        if LEC_TITLE.search(title):
            restore.append((url, rec, title, body))
            continue
        # 3) 兜底：is_news_article 关键词
        fa = is_news_article(title, body, ls)
        if fa:
            keep.append((url, 'NEWS_ARTICLE:' + fa, title))
            continue
        # 4) 仍有讲座信号（speaker/topic）
        if rec.get('speaker') or rec.get('topic'):
            restore.append((url, rec, title, body))
        else:
            keep.append((url, 'NO_LECTURE_SIGNAL', title))

    # 报告
    print(f'=== 建议 RESTORE（真实讲座）: {len(restore)} 条 ===')
    for url, rec, title, _ in restore:
        print(f"  {url.split('/')[-1]} | {rec.get('lectureStart')} | {rec.get('speaker') or '(空)'} | {title[:38]}")
    print(f'\n=== 建议 KEEP（维持排除/回顾稿）: {len(keep)} 条 ===')
    for url, reason, title in keep:
        print(f"  {url.split('/')[-1]} | {reason} | {title[:38]}")

    if not APPLY:
        print('\n[DRY-RUN] 未做任何改动。确认后加 --apply 落地。')
        return

    # 落地：仅移除排除；保留 lectures.json 中已有字段（避免覆盖/退化）。
    # 这些记录本就在 lectures.json（只是被 excluded 隐藏），移除排除即恢复展示。
    # 仅补充 re-parse 能拿到、而库中缺失的字段（不覆盖已有，防止退化）。
    excl_set = set(excl)
    filled = 0
    for url, rec, title, _ in restore:
        excl_set.discard(url)
        for r in recs:
            if r.get('sourceUrl') == url:
                for fld in ('lectureStart', 'lectureEnd', 'speaker', 'speakerTitle',
                            'speakerAffiliation', 'location', 'abstract', 'speakerBio',
                            'topic'):
                    v = rec.get(fld)
                    if v and not r.get(fld):
                        r[fld] = v
                        filled += 1
                break
    excl[:] = [u for u in excl if u in excl_set]
    json.dump(data, open(LECT, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    json.dump(excl, open(EXCL, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'\n[APPLY] 恢复 {len(restore)} 条，排除名单剩余 {len(excl)}，补充缺失字段 {filled} 处')


if __name__ == '__main__':
    main()
