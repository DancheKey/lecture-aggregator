"""按年份把历史讲座记录定向回填（parse_detail 全流程 + 仅填空补丁，不整体替换）。

对该年记录的 sourceUrl 重新抓取正文，调用 parsers.parse_detail 完整管线
（规则解析 + rich 模式 LLM 增强，内置全套守卫：snippet 溯源、单位质量校验、
host 校名拦截、bio 截断），把结果按「仅填空」原则回填进现有记录——
绝不覆盖已有非空值。多讲座拆分页按 lectureIndex 一一匹配；news filter
命中的页（返回 None）整页跳过；海报页跳过（VLM 独立路线，不烧额度）。

用法：
  python scripts/backfill_llm_by_year.py --year 2024
  python scripts/backfill_llm_by_year.py --year 2024 --limit 5     # 冒烟
  python scripts/backfill_llm_by_year.py --year 2024 --dry-run     # 不写盘

说明：
- 脚本强制 SCNU_LLM_TEXT=0（不启用全字段双轨与 B 裁决）；SCNU_LLM_RICH
  尊重外部设置（默认 1=rich 增强，仅填摘要/简介/规则空的职称与单位；
  设 0 可跑纯规则模式，零 LLM 成本）。
- 抓取与 LLM 调用均直连（清除代理环境变量，国内域名约定）。
- 每个 URL 只抓一次；LLM 调用走 llm_provider 内置缓存与限速。
- 写盘前自动备份 data/lectures.json，再用临时文件 + os.replace 原子替换。
- 抓不到的页（404/超时）保持原记录不变。
"""
import os
import re
import sys
import json
import time
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

# —— 环境设定必须在 import parsers 之前（开关在 parsers 模块顶层读取）——
os.environ['SCNU_LLM_TEXT'] = '0'   # 强制关闭全字段双轨：backfill 只用 rich 增强
for _k in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy'):
    os.environ.pop(_k, None)        # 国内域名直连约定

import requests  # noqa: E402
import charset_normalizer  # noqa: E402
import parsers  # noqa: E402

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SCNULectureAggregator/0.1)'}


def _lev(a, b):
    """短字符串字符级 Levenshtein 编辑距离。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    n, m = len(a), len(b)
    prev = list(range(m + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            cur.append(min(cur[-1] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = cur
    return prev[-1]


def _speaker_suspicious(db_sp, rule_sp):
    """db 与 rule 高度怀疑错配（编辑距离 ≤2 且 rule 是合法 CJK 姓名）→ 触发回填。"""
    a = (db_sp or '').strip()
    b = (rule_sp or '').strip()
    if not a or not b or a == b:
        return False
    if not re.match(r'^[\u4e00-\u9fa5·]{2,4}$', b):
        return False
    if abs(len(a) - len(b)) > 2:
        return False
    return _lev(a, b) <= 2


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


def _year_of(s):
    if not s:
        return None
    m = str(s)[:4]
    return int(m) if m.isdigit() else None


# 回填目标字段：全部仅填空；身份/去重键（sourceUrl/college/title/lectureIndex）不碰。
_FILL_FIELDS = ('topic', 'speaker', 'location', 'abstract', 'speakerBio',
                'speakerAffiliation', 'speakerTitle', 'lectureStart', 'lectureEnd')


def _is_empty(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _match_result(parse_res, rec):
    """parse_detail 结果与库内记录匹配。

    单页返回 dict → 所有 rec 共用；多讲座返回 list → 按 lectureIndex
    （两侧均为 1-based）一一匹配，匹配不上返回 None（不回写，防串值）。
    """
    if isinstance(parse_res, dict):
        return parse_res
    if isinstance(parse_res, list):
        li = rec.get('lectureIndex')
        for r in parse_res:
            if isinstance(r, dict) and r.get('lectureIndex') == li:
                return r
    return None


def _fill_empty(rec, src):
    """仅填空回写：rec 字段为空且 src 非空才写入。返回写入的字段名列表。"""
    filled = []
    for f in _FILL_FIELDS:
        v = src.get(f)
        if _is_empty(rec.get(f)) and not _is_empty(v):
            rec[f] = v.strip() if isinstance(v, str) else v
            filled.append(f)
    return filled


def _fix_speaker(rec, src):
    """speaker 可疑（疑似串值/错配）时用 parse_detail 结果校正（8777/13327 同款）。"""
    sp = (src.get('speaker') or '').strip()
    if sp and _speaker_suspicious(rec.get('speaker'), sp):
        rec['speaker'] = sp
        return True
    return False


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
    # 按 sourceUrl 聚合（多讲座拆分页一 URL 对多条记录）
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

    stat = {'ok': 0, 'fail': 0, 'news_skipped': 0, 'poster_skip': 0,
            'enhanced': 0, 'rule_only': 0, 'fixed_speaker': 0, 'no_match': 0}
    sess = requests.Session()

    for i, url in enumerate(urls, 1):
        try:
            # 显式禁用代理：scnu.edu.cn 为国内域名须直连。
            # 注意 proxies={'http':None,'https':None}（空字典才阻止 requests 回退读环境变量）。
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

        html = _decode_html(r.content)

        # 海报页跳过：文本增强不适用（正文是 OCR 碎片），VLM 是独立路线
        if any(rr.get('hasPosterImage') or rr.get('imageParseMethod') for rr in by_url[url]):
            stat['poster_skip'] += 1
            print(f'  [{i}/{len(urls)}] POSTER-SKIP {url}')
            continue

        first = by_url[url][0]
        try:
            parse_res = parsers.parse_detail(
                html, url, first.get('college') or '', first.get('campus') or '',
                default_year=args.year,
                list_title=(first.get('listTitle') or first.get('title') or ''))
        except Exception as e:
            stat['fail'] += 1
            print(f'  [{i}/{len(urls)}] PARSE-ERR {url} -> {e}')
            continue
        if parse_res is None:
            # news filter / 成立大会剔除等：整页跳过，不动记录
            stat['news_skipped'] += 1
            print(f'  [{i}/{len(urls)}] NEWS-SKIP {url}')
            continue

        any_change = False
        for rec in by_url[url]:
            src = _match_result(parse_res, rec)
            if src is None:
                stat['no_match'] += 1
                print(f'  [{i}/{len(urls)}] NO-MATCH lectureIndex={rec.get("lectureIndex")} {url}')
                continue
            filled = _fill_empty(rec, src)
            if _fix_speaker(rec, src):
                stat['fixed_speaker'] += 1
                filled.append('speaker*')
            if filled:
                any_change = True
                rec['llmTextEnhanced'] = bool(src.get('llmTextEnhanced'))
                if src.get('llmVerdict'):
                    rec['llmVerdict'] = src['llmVerdict']
                if src.get('llmTextEnhanced'):
                    stat['enhanced'] += 1
                else:
                    stat['rule_only'] += 1
            tag = ('FILL[' + ','.join(filled) + ']') if filled else 'no-op'
            print(f'  [{i}/{len(urls)}] {tag} {url}')
        if any_change:
            stat['ok'] += 1

    print(f'\n[SUMMARY] 有回写 {stat["ok"]} / 抓取或解析失败 {stat["fail"]} / '
          f'新闻稿跳过 {stat["news_skipped"]} / 海报页跳过 {stat["poster_skip"]} / '
          f'index未匹配 {stat["no_match"]}\n'
          f'          LLM增强 {stat["enhanced"]} 条 / 纯规则填充 {stat["rule_only"]} 条 / '
          f'speaker校正 {stat["fixed_speaker"]} 条')

    if args.dry_run:
        print('[DRY-RUN] 未写盘。')
        return

    # 原子写盘（先备份）—— 任一路径修改都需持久化
    if stat['enhanced'] == 0 and stat['rule_only'] == 0 and stat['fixed_speaker'] == 0:
        print('[INFO] 无记录被回填，跳过写盘。')
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
