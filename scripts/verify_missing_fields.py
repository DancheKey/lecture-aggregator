# -*- coding: utf-8 -*-
"""批量核查缺失字段：判断源页是否真的没提供该字段，还是解析器误判。

运行： python scripts/verify_missing_fields.py [--limit N] [--cache .workbuddy/missing_verify_cache.json]
产出 JSON：每条缺失记录的 URL、字段、源页是否含该字段、证据片段、建议动作。
"""
import os
import sys
import re
import json
import time
import hashlib
import argparse
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
CACHE_PATH_DEFAULT = os.path.join(ROOT, '.workbuddy', 'missing_verify_cache.json')
VLM_CACHE_PATH = os.path.join(ROOT, 'data', '.vlm_cache.json')


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}


# 大会/活动类标题关键词：这些通知通常不给出固定地点
MEETING_KWS = re.compile(r'论坛|大会|研讨会|工作坊|沙龙|通知|会议|典礼|颁奖|比赛|竞赛|'
                          r'答辩|招聘|宣讲会|培训|报名|选拔|面试|笔试|初赛|决赛')
# 系列讲座标题关键词
SERIES_KWS = re.compile(r'第\s*\d+\s*(期|讲|场|次|届)')


def load_json(p):
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))
    return {}


def url_cache_key(url):
    return hashlib.md5(url.encode('utf-8')).hexdigest()


def fetch_text(url, cache, retries=2):
    key = url_cache_key(url)
    if key in cache:
        return cache[key], True
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            # 取可见文本
            soup = BeautifulSoup(r.content, 'html.parser')
            # 去掉脚本/样式/导航
            for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                tag.decompose()
            text = soup.get_text('\n', strip=True)
            cache[key] = text
            return text, False
        except Exception as e:
            if i == retries - 1:
                return None, False
            time.sleep(1.0)
    return None, False


def source_has_field(text, field, rec):
    """判断源页正文 text 是否包含字段 field 的真实值。
    返回 (has: bool, evidence: str, method: str)
    """
    if not text:
        return False, '', 'fetch_failed'
    t = text.replace(' ', '').replace('\u3000', '').replace('\n', '')
    # 地点：找常见地点标签或含楼/室/厅的片段
    if field == 'location':
        # 标签式
        loc_labels = re.findall(r'(?:会议地点|讲座地点|报告地点|地点|地址|venue|location)[:：\s]*'
                                r'([^，。；\n]{2,40}?)(?=[，。；\n]|$)', text, re.I)
        if loc_labels:
            best = max(loc_labels, key=len)
            if re.search(r'(?:楼|室|厅|馆|栋|校区|校园|中心|会议室|报告厅|教室|礼堂)', best):
                return True, best.strip(), 'label'
        # 兜底：找含建筑词的短片段（20字内）
        for m in re.finditer(r'[^，。；]{2,25}(?:楼|室|厅|馆|栋|校区|校园|中心|会议室|报告厅|教室|礼堂)', text):
            frag = m.group(0).strip()
            # 排除可能是联系地址的
            if '联系地址' in frag or '通讯地址' in frag:
                continue
            if len(frag) <= 30:
                return True, frag, 'fallback'
        return False, '', 'scan'

    # 主讲人：找常见主讲人标签附近的人名
    if field == 'speaker':
        spk_labels = re.findall(r'(?:主讲人|报告人|演讲人|发言人|主讲嘉宾|特邀嘉宾|主讲)[:：\s]*'
                                r'([^，。；\n]{2,30}?)(?=[，。；\n]|$)', text)
        for lab in spk_labels:
            lab = lab.strip()
            # 过滤掉只含职称/空值的
            if lab and len(lab) <= 20 and not re.match(r'^(待定|暂无|无|请见|详见)', lab):
                return True, lab, 'label'
        return False, '', 'scan'

    # 主讲人单位：找常见单位标签
    if field == 'speakerAffiliation':
        aff_labels = re.findall(r'(?:单位|所在单位|工作单位|主讲人单位|报告人单位|单位/职称|'
                                r'affiliation|organization)[:：\s]*'
                                r'([^，。；\n]{2,60}?)(?=[，。；\n]|$)', text, re.I)
        for lab in aff_labels:
            lab = lab.strip()
            if lab and len(lab) <= 80:
                return True, lab, 'label'
        # 兜底：主讲人字段后括号内的机构
        spk = rec.get('speaker') or ''
        if spk:
            pat = re.escape(spk) + r'[（(]([^）)]{2,60})[）)]'
            m = re.search(pat, text)
            if m:
                return True, m.group(1).strip(), 'paren'
        return False, '', 'scan'

    return False, '', 'unknown'


def classify_missing(rec, field, has_field, evidence, method):
    """返回 (fix_kind, reason)
    fix_kind: fixable(可自动修/回填), manual(需人工判断), nfix(源页无信息/无需修)
    """
    title = (rec.get('title') or rec.get('topic') or '').strip()
    is_meeting = bool(MEETING_KWS.search(title))
    is_series = bool(SERIES_KWS.search(title))

    if has_field:
        # 源页有字段 → 误判，可修复
        return 'fixable', f'源页含{field}，证据：{evidence[:40]}'

    # 源页无字段：判断是否真的无法修
    if field == 'location':
        if rec.get('college') == '教师发展中心':
            return 'nfix', 'CTLD 工作坊/每周一课多为线上或临时通知，源页通常不写固定地点'
        if is_meeting or is_series:
            return 'nfix', '大会/工作坊/系列通知通常不写具体地点'
        if not rec.get('speaker'):
            return 'nfix', '无主讲人的大会/活动通知，源页通常不写地点'
        return 'manual', '具体讲座源页可能写了地点但未提取到，需人工确认'

    if field == 'speakerAffiliation':
        # 校内老师：本单位为默认单位，可视为无需修（但也可填 college）
        if rec.get('speaker') and rec.get('college'):
            return 'nfix', '校内讲座未写外单位，默认即主办学院；如需补全可填主办学院'
        return 'manual', '源页未明确写单位，需人工判断是否可推断'

    if field == 'speaker':
        if is_meeting or not rec.get('speaker'):
            return 'nfix', '大会/活动通知通常无单一主讲人'
        return 'manual', '具体讲座源页可能写了主讲人但未提取到，需人工确认'

    return 'manual', '需人工判断'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='限制处理的缺失条数（0=全部）')
    ap.add_argument('--cache', default=CACHE_PATH_DEFAULT)
    ap.add_argument('--out', default=os.path.join(ROOT, '.workbuddy', 'missing_verify_result.json'))
    ap.add_argument('--delay', type=float, default=0.8)
    args = ap.parse_args()

    data = json.load(open(DATA_PATH, encoding='utf-8'))
    recs = data['data']

    cache = load_json(args.cache)
    vlm_cache = load_json(VLM_CACHE_PATH)

    # 构造 VLM text 缓存索引（键形如 text:md5，值可能是字符串或 dict）
    vlm_texts = {}
    for k, v in vlm_cache.items():
        if not k.startswith('text:'):
            continue
        if isinstance(v, dict):
            txt = v.get('text') or v.get('content') or ''
        else:
            txt = str(v)
        vlm_texts[k[5:]] = txt

    # 收集缺失项
    missing = []
    for rec in recs:
        url = rec.get('sourceUrl') or ''
        if not rec.get('speaker'):
            missing.append((rec, 'speaker'))
        if not rec.get('speakerAffiliation'):
            missing.append((rec, 'speakerAffiliation'))
        if not rec.get('location'):
            missing.append((rec, 'location'))

    if args.limit:
        missing = missing[:args.limit]

    results = []
    for idx, (rec, field) in enumerate(missing):
        url = rec.get('sourceUrl') or ''
        print(f'[{idx+1}/{len(missing)}] {field} {url}')
        key = url_cache_key(url)

        # 优先用 VLM 缓存的 text
        text = vlm_texts.get(key)
        src = 'vlm_cache'
        if not text:
            text, from_cache = fetch_text(url, cache)
            src = 'fetch' if not from_cache else 'page_cache'
            if text and not from_cache:
                time.sleep(args.delay)

        has_field, evidence, method = source_has_field(text, field, rec)
        kind, reason = classify_missing(rec, field, has_field, evidence, method)

        results.append({
            'url': url,
            'field': field,
            'college': rec.get('college'),
            'title': rec.get('title') or rec.get('topic'),
            'lectureStart': rec.get('lectureStart'),
            'has_field': has_field,
            'evidence': evidence,
            'detect_method': method,
            'text_source': src,
            'fix_kind': kind,
            'reason': reason,
        })

    # 保存缓存
    os.makedirs(os.path.dirname(args.cache), exist_ok=True)
    json.dump(cache, open(args.cache, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(results, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    # 统计
    cnt = {}
    for r in results:
        cnt[(r['field'], r['fix_kind'])] = cnt.get((r['field'], r['fix_kind']), 0) + 1
    print('\n=== 统计 ===')
    for (field, kind), n in sorted(cnt.items()):
        print(f'{field:20s} {kind:10s} : {n}')
    print(f'结果已保存: {args.out}')


if __name__ == '__main__':
    main()
