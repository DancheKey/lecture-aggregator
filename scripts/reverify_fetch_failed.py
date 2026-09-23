# -*- coding: utf-8 -*-
"""重抓 missing_verify_result.json 中 fetch_failed 的盲区 URL，更新判定。

运行：python scripts/reverify_fetch_failed.py
"""
import os
import sys
import re
import json
import time
import hashlib
import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_PATH = os.path.join(ROOT, '.workbuddy', 'missing_verify_result.json')
CACHE_PATH = os.path.join(ROOT, '.workbuddy', 'missing_verify_cache.json')
VLM_CACHE_PATH = os.path.join(ROOT, 'data', '.vlm_cache.json')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

MEETING_KWS = re.compile(r'论坛|大会|研讨会|工作坊|沙龙|通知|会议|典礼|颁奖|比赛|竞赛|'
                         r'答辩|招聘|宣讲会|培训|报名|选拔|面试|笔试|初赛|决赛')
SERIES_KWS = re.compile(r'第\s*\d+\s*(期|讲|场|次|届)')


def load_json(p):
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))
    return {}


def url_cache_key(url):
    return hashlib.md5(url.encode('utf-8')).hexdigest()


def fetch_text(url, cache, retries=3):
    key = url_cache_key(url)
    if key in cache:
        return cache[key], True
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            soup = BeautifulSoup(r.content, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                tag.decompose()
            text = soup.get_text('\n', strip=True)
            cache[key] = text
            return text, False
        except Exception as e:
            last_err = str(e)[:80]
            if i == retries - 1:
                break
            time.sleep(1.5)
    return None, False


def source_has_field(text, field, rec):
    if not text:
        return False, '', 'fetch_failed'
    # 地点
    if field == 'location':
        loc_labels = re.findall(r'(?:会议地点|讲座地点|报告地点|地点|地址|venue|location)[:：\s]*'
                                r'([^，。；\n]{2,40}?)(?=[，。；\n]|$)', text, re.I)
        if loc_labels:
            best = max(loc_labels, key=len)
            if re.search(r'(?:楼|室|厅|馆|栋|校区|校园|中心|会议室|报告厅|教室|礼堂)', best):
                return True, best.strip(), 'label'
        for m in re.finditer(r'[^，。；]{2,25}(?:楼|室|厅|馆|栋|校区|校园|中心|会议室|报告厅|教室|礼堂)', text):
            frag = m.group(0).strip()
            if '联系地址' in frag or '通讯地址' in frag:
                continue
            if len(frag) <= 30:
                return True, frag, 'fallback'
        return False, '', 'scan'

    # 主讲人
    if field == 'speaker':
        spk_labels = re.findall(r'(?:主讲人|报告人|演讲人|发言人|主讲嘉宾|特邀嘉宾|主讲)[:：\s]*'
                                r'([^，。；\n]{2,30}?)(?=[，。；\n]|$)', text)
        for lab in spk_labels:
            lab = lab.strip()
            if lab and len(lab) <= 20 and not re.match(r'^(待定|暂无|无|请见|详见)', lab):
                return True, lab, 'label'
        return False, '', 'scan'

    # 单位
    if field == 'speakerAffiliation':
        aff_labels = re.findall(r'(?:单位|所在单位|工作单位|主讲人单位|报告人单位|单位/职称|'
                                r'affiliation|organization)[:：\s]*'
                                r'([^，。；\n]{2,60}?)(?=[，。；\n]|$)', text, re.I)
        for lab in aff_labels:
            lab = lab.strip()
            if lab and len(lab) <= 80:
                return True, lab, 'label'
        spk = rec.get('speaker') or ''
        if spk:
            m = re.search(re.escape(spk) + r'[（(]([^）)]{2,60})[）)]', text)
            if m:
                return True, m.group(1).strip(), 'paren'
        return False, '', 'scan'

    return False, '', 'unknown'


def classify_missing(rec, field, has_field, evidence, method):
    title = (rec.get('title') or rec.get('topic') or '').strip()
    is_meeting = bool(MEETING_KWS.search(title))
    is_series = bool(SERIES_KWS.search(title))

    if has_field:
        return 'fixable', f'源页含{field}，证据：{evidence[:40]}'

    if field == 'location':
        if rec.get('college') == '教师发展中心':
            return 'nfix', 'CTLD 工作坊/每周一课多为线上或临时通知，源页通常不写固定地点'
        if is_meeting or is_series:
            return 'nfix', '大会/工作坊/系列通知通常不写具体地点'
        if not rec.get('speaker'):
            return 'nfix', '无主讲人的大会/活动通知，源页通常不写地点'
        return 'manual', '具体讲座源页可能写了地点但未提取到，需人工确认'

    if field == 'speakerAffiliation':
        if rec.get('speaker') and rec.get('college'):
            return 'nfix', '校内讲座未写外单位，默认即主办学院；如需补全可填主办学院'
        return 'manual', '源页未明确写单位，需人工判断是否可推断'

    if field == 'speaker':
        if is_meeting or not rec.get('speaker'):
            return 'nfix', '大会/活动通知通常无单一主讲人'
        return 'manual', '具体讲座源页可能写了主讲人但未提取到，需人工确认'

    return 'manual', '需人工判断'


def main():
    results = load_json(RESULT_PATH)
    if not results:
        print('ERROR: no result file', RESULT_PATH)
        return 1

    cache = load_json(CACHE_PATH)
    vlm_cache = load_json(VLM_CACHE_PATH)
    vlm_texts = {}
    for k, v in vlm_cache.items():
        if not k.startswith('text:'):
            continue
        if isinstance(v, dict):
            txt = v.get('text') or v.get('content') or ''
        else:
            txt = str(v)
        vlm_texts[k[5:]] = txt

    # 只重抓 fetch_failed 的 nfix
    to_recheck = [i for i, r in enumerate(results)
                  if r['fix_kind'] == 'nfix' and r['detect_method'] == 'fetch_failed']
    print(f'fetch_failed nfix items to recheck: {len(to_recheck)}')

    # 按 URL 去重，避免同一 URL 多次抓取
    url_to_indices = {}
    for i in to_recheck:
        url = results[i]['url']
        url_to_indices.setdefault(url, []).append(i)

    urls = list(url_to_indices.keys())
    print(f'unique URLs: {len(urls)}')

    # 加载 lectures.json 以获取记录上下文
    data = json.load(open(os.path.join(ROOT, 'data', 'lectures.json'), encoding='utf-8'))
    url_to_rec = {r.get('sourceUrl') or '': r for r in data['data']}

    changed = 0
    unchanged = 0
    failed = 0
    for ui, url in enumerate(urls):
        indices = url_to_indices[url]
        print(f'[{ui+1}/{len(urls)}] fetching {url}')
        text, from_cache = fetch_text(url, cache)
        src = 'page_cache' if from_cache else ('fetch' if text else 'fetch_failed')

        for idx in indices:
            r = results[idx]
            field = r['field']
            rec = url_to_rec.get(url) or {}

            # 优先 VLM 缓存，其次刚抓的 text
            key = url_cache_key(url)
            use_text = vlm_texts.get(key) or text
            if vlm_texts.get(key):
                src = 'vlm_cache'

            has_field, evidence, method = source_has_field(use_text, field, rec)
            if src == 'fetch_failed' or not use_text:
                method = 'fetch_failed'
                has_field = False
                evidence = ''

            kind, reason = classify_missing(rec, field, has_field, evidence, method)

            old = (r['fix_kind'], r['detect_method'], r['text_source'])
            r['has_field'] = has_field
            r['evidence'] = evidence
            r['detect_method'] = method
            r['text_source'] = src
            r['fix_kind'] = kind
            r['reason'] = reason
            new = (r['fix_kind'], r['detect_method'], r['text_source'])

            if old != new:
                changed += 1
                print(f'  -> {field} CHANGED {old} -> {new} (evidence={evidence[:40]!r})')
            else:
                unchanged += 1
            if method == 'fetch_failed':
                failed += 1

        if not from_cache and text:
            time.sleep(0.6)

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    json.dump(cache, open(CACHE_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    json.dump(results, open(RESULT_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    print(f'\nDone: changed={changed}, unchanged={unchanged}, still_failed={failed}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
