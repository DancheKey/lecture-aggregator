"""对文学院被误杀的海报讲座，用 VLM 重新抽取真实结构化字段并回填。

背景：2026-07-31 误把「海报页 + 占位 08:00」当真实时刻比发布时间，导致说文论语
系列等真实讲座公告被 is_news_record 错杀并加入全局排除名单。本脚本：
1. 对 tmp/vlm_degraded.json 中的每条 URL 重新抓取海报图，调用 VLM 抽真实字段；
2. 仅回填记录中缺失/退化的字段（lectureStart/End、speaker、location、abstract 等），
   不覆盖已有有效值；title 保留系列名，VLM 抽到的讲座主题写入 topic；
3. 将该 URL 从 data/excluded_urls.json 移除（恢复展示）；
4. 写 tmp/refill_report.json 供核查。

用法：python scripts/refill_wxy_posters.py
"""
import sys, os, json, re
from datetime import datetime as _dt
sys.path.insert(0, 'scraper')
for line in open('.env', encoding='utf-8'):
    line = line.strip()
    if line and '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip()

import scraper
from bs4 import BeautifulSoup
from parsers import _vlm_extract_fields, _load_vlm_config, _vlm_cache_get

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LECT = os.path.join(BASE, 'data', 'lectures.json')
EXCL = os.path.join(BASE, 'data', 'excluded_urls.json')
DEG = os.path.join(BASE, 'tmp', 'vlm_degraded.json')
REP = os.path.join(BASE, 'tmp', 'refill_report.json')


def _norm_dt(s):
    """VLM 时间 '2024-10-30 15:00' -> '2024-10-30 15:00:00'。"""
    s = (s or '').strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return _dt.strptime(s[:19], fmt).isoformat(sep=' ')
        except (ValueError, TypeError):
            continue
    return None


def _get_poster_imgs(soup):
    cd = soup.find('div', class_='news-text') or soup.find('article')
    imgs = []
    if cd:
        for img in cd.find_all('img'):
            src = img.get('src') or ''
            if not src:
                continue
            if src.startswith('//'):
                src = 'http:' + src
            elif src.startswith('/'):
                src = 'http://wxy.scnu.edu.cn' + src
            imgs.append(src)
    return imgs


def main():
    data = json.load(open(LECT, encoding='utf-8'))
    recs = data['data']
    excl = json.load(open(EXCL, encoding='utf-8'))
    urls = json.load(open(DEG, encoding='utf-8'))
    cfg = _load_vlm_config()

    report = []
    for url in urls:
        recs_for_url = [r for r in recs if r.get('sourceUrl') == url]
        if not recs_for_url:
            report.append({'url': url, 'status': 'NO_RECORD'})
            continue
        try:
            html = scraper.fetch(url, _retries=3)
        except Exception as e:
            report.append({'url': url, 'status': 'FETCH_FAIL', 'err': str(e)[:80]})
            continue
        soup = BeautifulSoup(html, 'html.parser')
        imgs = _get_poster_imgs(soup)
        if not imgs:
            report.append({'url': url, 'status': 'NO_IMG'})
            continue
        fields = _vlm_extract_fields(imgs[:2], cfg)
        if not fields:
            report.append({'url': url, 'status': 'VLM_EMPTY'})
            continue
        # 单讲座海报返回 dict；多讲座返回 list（此处取首个并告警）
        if isinstance(fields, list):
            report.append({'url': url, 'status': 'VLM_MULTI', 'n': len(fields)})
            fields = fields[0]
        # 回填：只填缺失/退化字段
        ls = _norm_dt(fields.get('lectureStart'))
        le = _norm_dt(fields.get('lectureEnd'))
        for r in recs_for_url:
            changed = []
            if ls and (not r.get('lectureStart') or r['lectureStart'].endswith(('08:00:00', '00:00:00'))):
                r['lectureStart'] = ls
                changed.append('lectureStart=' + ls)
            if le and not r.get('lectureEnd'):
                r['lectureEnd'] = le
                changed.append('lectureEnd=' + le)
            for fld, key in (('speaker', 'speaker'), ('speakerTitle', 'speakerTitle'),
                             ('speakerAffiliation', 'speakerAffiliation'),
                             ('location', 'location'), ('abstract', 'abstract'),
                             ('speakerBio', 'speakerBio')):
                v = (fields.get(key) or '').strip()
                if v and not r.get(fld):
                    r[fld] = v
                    changed.append(fld)
            vt = (fields.get('title') or '').strip()
            if vt and not r.get('topic'):
                r['topic'] = vt
                changed.append('topic=' + vt)
            r['vlmExtracted'] = True
            r['hasPosterImage'] = True
        # 从排除名单移除
        if url in excl:
            excl.remove(url)
        report.append({'url': url, 'status': 'OK',
                       'title': recs_for_url[0].get('title'),
                       'lectureStart': recs_for_url[0].get('lectureStart'),
                       'speaker': recs_for_url[0].get('speaker'),
                       'changed': changed})

    json.dump(data, open(LECT, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    json.dump(excl, open(EXCL, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    json.dump(report, open(REP, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    n_ok = sum(1 for x in report if x['status'] == 'OK')
    print(f'处理 {len(urls)} 条，成功回填 {n_ok} 条，排除名单剩余 {len(excl)}')
    for x in report:
        if x['status'] == 'OK':
            print(f"  [OK] {x['url'].split('/')[-1]} | {x['lectureStart']} | {x['speaker']} | {x['title']}")
        else:
            print(f"  [{x['status']}] {x['url'].split('/')[-1]}")


if __name__ == '__main__':
    main()
