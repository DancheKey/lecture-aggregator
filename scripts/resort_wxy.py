# -*- coding: utf-8 -*-
"""对库中「文学院」全部记录做忠实重分类（套用当前新闻过滤规则，使用「重新解析出的真实日期」）。

做法：把每条记录当作「新发布的消息」重新抓取详情页，
调用 parse_detail(..., skip_news_filter=True) 取得「当前解析器重新抽出的真实讲座日/发布时间」，
再原样套用 parse_detail 自身的排除判定链：
    is_news_record(真实日期)            -> 事后回顾稿（发布晚于真实讲座日）
    is_news_article(标题,正文,真实日)  -> 回顾/报道/署名审签链等
命中任一即判定为非讲座。

安全性：
- 海报类记录正文为空 -> is_news_article 不命中、真实讲座日多为空 -> is_news_record 不命中 -> 保留，不会误删真讲座。
- 完全无法解析的页面（含海报无 VLM）-> 标记 UNPARSEABLE，保留并人工确认，绝不误删。
- 本脚本只做「分类与统计」，不修改数据；是否写入 excluded_urls.json 由后续步骤决定。
"""
import json
import os
import sys
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
import scraper
from parsers import parse_detail, is_news_record, is_news_article

EXCLUDED_PATH = os.path.join(ROOT, 'data', 'excluded_urls.json')
LECTURES_PATH = os.path.join(ROOT, 'data', 'lectures.json')
OUT_PATH = os.path.join(ROOT, 'tmp', 'wxy_resort.json')


def extract_body(htm):
    soup = BeautifulSoup(htm, 'html.parser')
    div = (soup.find('div', class_='news-text')
           or soup.find('div', class_='news-details-middle')
           or soup.find('article')
           or soup.find('div', class_='entry-content'))
    if not div:
        div = soup.find('div', class_=lambda c: c and ('content' in c or 'article' in c or 'text' in c))
    return div.get_text(' ', strip=True) if div else ''


def classify_rec(rec, body, fallback_title, poster_page=False):
    title = rec.get('title') or fallback_title or ''
    fr = is_news_record(rec, poster_page=poster_page)
    fa = is_news_article(title, body, rec.get('lectureStart'))
    return fr, fa


def main():
    payload = json.load(open(LECTURES_PATH, encoding='utf-8'))
    data = payload['data'] if isinstance(payload, dict) else payload
    excluded = set(json.load(open(EXCLUDED_PATH, encoding='utf-8')))
    wxy = [r for r in data if r.get('college') == '文学院']
    print('文学院库内记录数:', len(wxy))

    urls = [r.get('sourceUrl') for r in wxy]
    htmls = {}

    def do_fetch(u):
        return u, scraper.fetch(u, _retries=2)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(do_fetch, u): u for u in urls}
        for f in as_completed(futs):
            u, h = f.result()
            htmls[u] = h

    news, keep, uncertain = [], [], []
    for r in wxy:
        u = r.get('sourceUrl')
        if u in excluded:
            keep.append((r, 'ALREADY_EXCLUDED'))
            continue
        h = htmls.get(u)
        if h is None:
            uncertain.append((r, 'FETCH_FAIL'))
            continue
        campus = r.get('campus') or '大学城'
        year = r.get('yearOf')
        # 用 skip_news_filter=True 取得「当前解析器重新抽出的真实讲座日/发布时间」
        try:
            out = parse_detail(h, u, '文学院', campus, year,
                               list_title=r.get('title'), skip_news_filter=True)
        except Exception as e:
            uncertain.append((r, 'PARSE_ERR:%s' % e))
            continue
        if out is None:
            uncertain.append((r, 'UNPARSEABLE'))
            continue
        body = extract_body(h)
        # 海报退化判定：以 parse_detail 设置的 hasPosterImage 标志为准（不依赖
        # images 列表是否填充，因为 parse_detail 的 result['images'] 实际可能
        # 为空），配合正文长度双保险。
        imgs_all = []
        if isinstance(out, list):
            for rec in out:
                imgs_all.extend(rec.get('images') or [])
        else:
            imgs_all = (out.get('images') or [])
        # 海报页判定：hasPosterImage 标志（parse_detail 设置），兼容 list/dict
        if isinstance(out, dict):
            _hpi = out.get('hasPosterImage')
        elif out:
            _hpi = any(rec.get('hasPosterImage') for rec in out)
        else:
            _hpi = False
        poster_page_flag = bool(_hpi) or (len(body) < 150 and bool(imgs_all))
        if isinstance(out, list):
            # 对海报退化页用 stored lectureStart（08:00 铁律占位）而非 fresh（OCR
            # 失败回退到 00:00），以触发 is_news_record 的海报退化严格比较分支。
            flags = []
            for rec in out:
                rec_chk = dict(rec)
                if poster_page_flag and rec.get('lectureStart','').endswith(('00:00:00',)) and r.get('lectureStart','').endswith(('08:00:00',)):
                    rec_chk['lectureStart'] = r.get('lectureStart')
                flags.append(classify_rec(rec_chk, body, r.get('title'), poster_page=poster_page_flag))
            if all(fr or fa for fr, fa in flags):
                news.append((r, 'all-sessions-retro', len(body)))
            else:
                keep.append((r, 'has-lecture-session'))
        else:
            rec_chk = dict(out)
            if poster_page_flag and out.get('lectureStart','').endswith(('00:00:00',)) and r.get('lectureStart','').endswith(('08:00:00',)):
                rec_chk['lectureStart'] = r.get('lectureStart')
            fr, fa = classify_rec(rec_chk, body, r.get('title'), poster_page=poster_page_flag)
            if fr or fa:
                reason = []
                if fr:
                    reason.append('is_news_record(发布晚于真实讲座日 %s)' % out.get('lectureStart'))
                if fa:
                    reason.append('is_news_article:%s' % fa)
                news.append((r, ';'.join(reason), len(body)))
            else:
                keep.append((r, 'LECTURE'))

    print('\n===== NEWS（判定为非讲座，建议排除）=====', len(news))
    for r, why, blen in news:
        print(' -', r.get('sourceUrl'), '|', r.get('title'), '| 库内讲座日', r.get('lectureStart'),
              '|', why, '| body', blen)
    print('\n===== UNCERTAIN（无法解析/抓取异常，保留待人工确认）=====', len(uncertain))
    for r, why in uncertain:
        print(' !', r.get('sourceUrl'), '|', r.get('title'), '|', why)
    print('\n===== KEEP（判定为讲座/已排除）=====', len(keep), '(不逐条打印)')

    outp = {
        'news': [{'url': r.get('sourceUrl'), 'title': r.get('title'),
                  'stored_lectureStart': r.get('lectureStart'),
                  'publishTime': r.get('publishTime'), 'reason': why} for r, why, _ in news],
        'uncertain': [{'url': r.get('sourceUrl'), 'title': r.get('title'), 'why': why}
                      for r, why in uncertain],
        'summary': {'total': len(wxy), 'news': len(news), 'uncertain': len(uncertain),
                    'keep': len(keep)},
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    json.dump(outp, open(OUT_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('\n已写入', OUT_PATH)
    print('汇总:', outp['summary'])


if __name__ == '__main__':
    main()
