# -*- coding: utf-8 -*-
"""修复型回填：重抓源页重新解析，替换被污染的摘要/简介/邀请人字段。

与 backfill_llm_by_year.py 的区别：
  backfill 是「仅填空」——只补空字段，不覆盖任何已有值，用于增量补全；
  本脚本是「修复」——当库内存量值过不了统一出口闸门（field_vocab 定义的
  元信息块/邀请语/页脚/悬挂残段污染），用当前代码重新解析出的干净值【替换】。
  这就是全局诊断（2026-09-05）指出的缺失环节：词表修复必须能重放存量，
  否则只能逐案手补（头痛医头）。

修复范围（只碰富文本字段，结构字段绝不改动）：
  abstract / speakerBio —— 库内值过不了出口闸门 → 用新解析值替换
                           （新值为空且旧值过不了闸门 → 置空，整段皆污染时
                            空比残段诚实）；旧值本身干净 → 不动（防抖动）。
  inviter —— 括号残段（「余虓) 中南大学」）等闸门可修复项。

用法：
  python scripts/repair_backfill.py --dry-run                 # 只统计待修复清单
  python scripts/repair_backfill.py --urls http://... http://...
  python scripts/repair_backfill.py --host maths.scnu.edu.cn [--limit 20]
  python scripts/repair_backfill.py --all                     # 全库扫描并修复
  python scripts/repair_backfill.py --year 2024 --dry-run

说明：
- 默认纯规则重放（SCNU_LLM_RICH=0，零 LLM 成本、结果确定）。闸门把脏摘要
  截掉后规则往往能给出干净值；确需模型 A 重新补全空摘要时加 --llm。
- 写盘前自动备份 data/lectures.json（bak-时间戳-repair），临时文件原子替换。
- 抓不到的页（404/超时）保持原记录不变，计入 unreachable。
- 每条被修改的记录打 rec['qaRepaired'] = field_vocab.VOCAB_VERSION，可审计。
"""
import os
import re
import sys
import json
import time
import argparse
import datetime
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

# —— 环境设定必须在 import parsers 之前（开关在 parsers 模块顶层读取）——
for _k in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy'):
    os.environ.pop(_k, None)        # 国内域名直连约定

import requests  # noqa: E402
import charset_normalizer  # noqa: E402

# 先以纯规则模式 import parsers（--llm 时再放开）
os.environ.setdefault('SCNU_LLM_TEXT', '0')
os.environ.setdefault('SCNU_LLM_RICH', '0')

import parsers  # noqa: E402
import field_vocab as _fv  # noqa: E402
from parsers import apply_exit_gate  # noqa: E402

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SCNULectureAggregator/0.1)'}
_REPAIR_FIELDS = ('abstract', 'speakerBio')

# speaker 脏值特征：职称/单位粘进姓名（含括号）或以英文头衔开头
# （iqm447 存量 'Prof.Takaaki Nomura (Sichuan University, 四川大学 )'）。
# 结构字段本不轻动，但这类值展示即错，且新解析的 speaker 有姓名卫生校验兜底。
_SPEAKER_DIRTY = re.compile(r'[（(]|^(?:Prof|Dr|Mr|Mrs|Ms)\.?\s', re.I)


def _speaker_dirty(sp):
    return bool(sp and _SPEAKER_DIRTY.search(sp.strip()))


def _speaker_clean(sp):
    return bool(sp and not _SPEAKER_DIRTY.search(sp.strip()))


def _abstract_is_bio_copy(abstract, bio):
    """摘要前 30 字出现在简介中 → 摘要是简介的复制/截断（无摘要页被硬填的产物）。

    页面本无摘要时 A 时代常把简介开头塞进 abstract；置空无信息损失（简介仍在）。
    """
    ab = (abstract or '').strip()
    bio = (bio or '').strip()
    return len(ab) >= 30 and len(bio) >= 30 and ab[:30] in bio


def _decode_html(raw):
    """鲁棒解码 HTML：meta charset 声明 → UTF-8 → GB18030 → charset_normalizer。"""
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


def _gate_changes(rec):
    """在不改动 rec 的前提下返回出口闸门会修改的字段 {field: new_value}。"""
    fields = ('abstract', 'speakerBio', 'inviter')
    probe = {k: rec.get(k) for k in fields}
    apply_exit_gate(probe)
    diffs = {}
    for k in fields:  # 固定键迭代：_gate_record 会往 probe 里塞 qaGate 标记，不能算差异
        if (probe.get(k) or '') != (rec.get(k) or ''):
            diffs[k] = probe.get(k)
    return diffs


def _time_inconsistent(rec):
    """lectureEnd 早于 lectureStart → 时间字段矛盾（physics 存量 60 条）。"""
    ls, le = rec.get('lectureStart'), rec.get('lectureEnd')
    if not (ls and le):
        return False
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(str(le)) < _dt.datetime.fromisoformat(str(ls))
    except Exception:
        return False


def _is_dirty(rec):
    """库内记录是否带闸门可判定的污染（富文本 + 摘要=简介复制 + 时间矛盾）。"""
    if _gate_changes(rec):
        return True
    if _time_inconsistent(rec):
        return True
    return _abstract_is_bio_copy(rec.get('abstract'), rec.get('speakerBio'))


def _year_of(s):
    m = str(s or '')[:4]
    return int(m) if m.isdigit() else None


def _match_result(parse_res, rec):
    """parse_detail 结果与库内记录按 lectureIndex 匹配（多讲座拆分页）。

    匹配失败时退化兜底：若 parse_res 各条目的 (topic, time, speaker) 签名完全
    一致（= MS 双份误拆被守卫拦成单条语义，physics12723 类），取首条作为修复源，
    避免这些页的字段残留永远修不上。
    """
    if isinstance(parse_res, dict):
        return parse_res
    if isinstance(parse_res, list):
        li = rec.get('lectureIndex')
        for r in parse_res:
            if isinstance(r, dict) and r.get('lectureIndex') == li:
                return r
        sigs = {(re.sub(r'\s+', '', str(r.get('topic') or '')),
                 str(r.get('lectureStart') or ''),
                 re.sub(r'\s+', '', str(r.get('speaker') or '')))
                for r in parse_res if isinstance(r, dict)}
        if len(sigs) == 1 and parse_res:
            return next(r for r in parse_res if isinstance(r, dict))
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('--urls', nargs='*', default=[], help='显式指定源页 URL 列表')
    ap.add_argument('--host', action='append', default=[],
                    help='按站点域名筛选（可多次），如 maths.scnu.edu.cn')
    ap.add_argument('--year', type=int, default=None)
    ap.add_argument('--all', action='store_true', help='全库扫描（默认只扫有污染的记录）')
    ap.add_argument('--limit', type=int, default=0, help='最多处理 N 个 URL（冒烟用）')
    ap.add_argument('--dry-run', action='store_true', help='不写盘、不抓取，仅统计')
    ap.add_argument('--force', action='store_true',
                    help='旧值干净也用新解析值覆盖（默认只动过不了闸门的值）')
    ap.add_argument('--llm', action='store_true',
                    help='放开 SCNU_LLM_RICH（用模型 A 补全规则为空的摘要/简介；默认纯规则零成本）')
    ap.add_argument('--fields', default='abstract,speakerBio',
                    help='逗号分隔的修复字段集。默认 abstract,speakerBio；'
                         '加 speaker（speaker 三件套，旧值脏才动）、'
                         'lectureStart（新解析 high 置信且不同才动）、location（新值非空才动）')
    args = ap.parse_args()
    _field_set = {f.strip() for f in args.fields.split(',') if f.strip()}

    if args.llm:
        os.environ['SCNU_LLM_RICH'] = '1'
        # 运行期切换需在 parse_detail 读取前完成：parsers 顶层已读开关，此处仅对
        # 子进程语义有效；故 --llm 要求进程内首次 parse 前环境已就绪（本脚本已在
        # import 前按默认关闭，--llm 时改用子进程方式不在本版支持，见 README）。
        print('[WARN] --llm 需在环境变量 SCNU_LLM_RICH=1 下运行本脚本：'
              'SCNU_LLM_RICH=1 python scripts/repair_backfill.py ...')
        args.llm = False

    data_path = os.path.join(ROOT, 'data', 'lectures.json')
    raw = json.load(open(data_path, encoding='utf-8'))
    recs = raw['data'] if isinstance(raw, dict) else raw

    # ---- 选记录 ----
    scope = []
    for r in recs:
        u = str(r.get('sourceUrl', '')).rstrip('/')
        if not u.startswith('http'):
            continue
        if args.host and urlparse(u).netloc not in args.host:
            continue
        if args.year and _year_of(r.get('lectureStart')) != args.year \
                and _year_of(r.get('publishTime')) != args.year:
            continue
        scope.append(r)

    dirty = [r for r in scope if _is_dirty(r)]
    if args.urls:
        url_set = {u.rstrip('/') for u in args.urls}
        targets = [r for r in scope if str(r.get('sourceUrl', '')).rstrip('/') in url_set]
    elif args.all:
        targets = scope
    else:
        targets = dirty

    by_url = {}
    for r in targets:
        by_url.setdefault(str(r.get('sourceUrl', '')).rstrip('/'), []).append(r)
    # dirty 的 URL 排队首：--limit 截断时优先覆盖污染记录（--all 全量跑批时省时）
    _dirty_urls = {str(r.get('sourceUrl', '')).rstrip('/') for r in dirty}
    urls = list(by_url.keys())
    urls.sort(key=lambda u: 0 if u in _dirty_urls else 1)
    if args.limit:
        urls = urls[:args.limit]

    print(f'[INFO] 扫描范围 {len(scope)} 条，其中闸门判定带污染 {len(dirty)} 条；'
          f'本次处理 {len(targets)} 条 / {len(urls)} 个 URL'
          + ('（dry-run，仅统计不抓取）' if args.dry_run else ''))

    stat = {'repaired': 0, 'clean': 0, 'unreachable': 0, 'parse_err': 0,
            'news_skip': 0, 'no_match': 0, 'emptied': 0, 'fields': {}}

    if not args.dry_run:
        sess = requests.Session()

    for i, url in enumerate(urls, 1):
        if args.dry_run:
            for rec in by_url[url]:
                d = _gate_changes(rec)
                print(f'  [DRY] {url} | {list(d.keys())} | '
                      f'abstract={str(rec.get("abstract"))[:60]!r}')
                stat['repaired'] += 1
            continue
        try:
            resp = sess.get(url, headers=HEADERS, timeout=30,
                            proxies={'http': None, 'https': None})
            if resp.status_code != 200:
                stat['unreachable'] += 1
                print(f'  [{i}/{len(urls)}] SKIP {resp.status_code} {url}')
                continue
        except Exception as e:
            stat['unreachable'] += 1
            print(f'  [{i}/{len(urls)}] ERR {url} -> {e}')
            continue
        html = _decode_html(resp.content)

        first = by_url[url][0]
        dy = (_year_of(first.get('lectureStart'))
              or _year_of(first.get('publishTime')))
        try:
            parse_res = parsers.parse_detail(
                html, url, first.get('college') or '', first.get('campus') or '',
                default_year=dy,
                list_title=(first.get('listTitle') or first.get('title') or ''))
        except Exception as e:
            stat['parse_err'] += 1
            print(f'  [{i}/{len(urls)}] PARSE-ERR {url} -> {e}')
            continue
        if parse_res is None:
            stat['news_skip'] += 1
            print(f'  [{i}/{len(urls)}] NEWS-SKIP {url}')
            continue

        # ---- 一拆多：存量单条、新解析拆出多条（physics791 双讲拆分）----
        # 条件：新解析为 ≥2 条、库内该 URL 仅 1 条且无 lectureIndex（非既有拆分页）。
        # 守卫：拆出各场 topic 须互异（同一人讲两场合法——13312 涂展春两场；
        # topic 重复才是双份文本误拆，且 MS 守卫已在 parse 端拦截）。
        # 处理：移除存量、插入全部新记录；绝不反向合并。
        _split_ok = False
        if (isinstance(parse_res, list) and len(parse_res) >= 2
                and len(by_url[url]) == 1 and not by_url[url][0].get('lectureIndex')):
            _tp_names = [re.sub(r'\s+', '', str(r.get('topic') or ''))[:20]
                         for r in parse_res if isinstance(r, dict)]
            _split_ok = (len(_tp_names) == len(parse_res) and all(_tp_names)
                         and len(set(_tp_names)) == len(_tp_names))
        if _split_ok:
            old_rec = by_url[url][0]
            _idx = recs.index(old_rec)
            new_recs = []
            for nr in parse_res:
                if not isinstance(nr, dict):
                    continue
                nr = dict(nr)
                if not nr.get('listTitle'):
                    nr['listTitle'] = old_rec.get('listTitle') or old_rec.get('title')
                nr['qaRepaired'] = _fv.VOCAB_VERSION + '-split'
                new_recs.append(nr)
            if len(new_recs) >= 2:
                recs.remove(old_rec)
                recs[_idx:_idx] = new_recs
                stat['repaired'] += len(new_recs)
                stat['split'] = stat.get('split', 0) + 1
                stat['fields']['split:x' + str(len(new_recs))] = \
                    stat['fields'].get('split:x' + str(len(new_recs)), 0) + 1
                print(f'  [{i}/{len(urls)}] SPLIT 1->{len(new_recs)} {url}')
                continue

        url_changed = False
        for rec in by_url[url]:
            src = _match_result(parse_res, rec)
            if src is None:
                stat['no_match'] += 1
                continue
            touched = []
            for fld in _REPAIR_FIELDS:
                old = (rec.get(fld) or '').strip()
                new = (src.get(fld) or '').strip()
                old_dirty = bool(_gate_changes({fld: old}).get(fld))
                if fld == 'abstract' and _abstract_is_bio_copy(old, rec.get('speakerBio')):
                    old_dirty = True
                if not old_dirty and not args.force:
                    stat['clean'] += 1
                    continue
                if new == old:
                    # 新解析同样给不出更好的值（如整页污染置空）——记置空统计
                    if not new and old_dirty:
                        rec[fld] = ''
                        touched.append(fld + ':emptied')
                    continue
                if not new and old_dirty:
                    # 摘要本是简介的复制：置空无损失（简介仍完整），不走 salvage
                    if fld == 'abstract' and _abstract_is_bio_copy(old, rec.get('speakerBio')):
                        rec[fld] = ''
                        touched.append(fld + ':bio-copy')
                        continue
                    # 新解析为空但旧值只是尾部污染：先修剪旧值保底（ salvage），
                    # 只有修剪后所剩无几（<12 字）才置空——空比残段诚实，
                    # 但绝不能把"真实内容+污染尾巴"整体丢掉。
                    salv = _fv.trim_dangling_unit_prefix(_fv.truncate_rich_text(old))
                    rec[fld] = salv if len(salv) >= 12 else ''
                    touched.append(fld + (':trimmed' if salv else ':emptied'))
                    continue
                rec[fld] = new
                touched.append(fld if new else fld + ':emptied')
            # 邀请人残段：闸门修复后的新值直接采用
            old_inv = (rec.get('inviter') or '').strip()
            new_inv = (src.get('inviter') or '').strip()
            if new_inv and new_inv != old_inv:
                rec['inviter'] = new_inv
                touched.append('inviter')
            # speaker 脏值（职称/单位粘进姓名、英文头衔开头，iqm447 式）
            # 或为空（psy229 式：繁体姓名曾被守卫清空）：
            # 新解析 speaker 干净且非空才替换，三件套（speaker/title/affiliation）同步
            if 'speaker' in _field_set:
                old_sp = (rec.get('speaker') or '').strip()
                new_sp = (src.get('speaker') or '').strip()
                if ((not old_sp or _speaker_dirty(old_sp))
                        and _speaker_clean(new_sp) and new_sp != old_sp):
                    rec['speaker'] = new_sp
                    rec['speakerTitle'] = (src.get('speakerTitle') or '').strip()
                    rec['speakerAffiliation'] = (src.get('speakerAffiliation') or '').strip()
                    touched.append('speaker')
            # lectureStart：仅当新解析给出 high 置信（权威标签）且与存量不同才替换
            # （wxy3238 式：旧值来自受限正文误抓报名截止日）
            if 'lectureStart' in _field_set:
                old_t = (rec.get('lectureStart') or '').strip()
                new_t = (src.get('lectureStart') or '').strip()
                if (new_t and new_t != old_t
                        and (src.get('timeConfidence') or '').startswith('high')):
                    rec['lectureStart'] = new_t
                    rec['lectureEnd'] = src.get('lectureEnd')
                    rec['timeConfidence'] = src.get('timeConfidence')
                    rec['timeNote'] = src.get('timeNote')
                    touched.append('lectureStart')
                elif (new_t and new_t == old_t and _time_inconsistent(rec)
                      and (src.get('timeConfidence') or '').startswith('high')):
                    # start 相同但存量 end 与 start 矛盾 → 用新解析的 end 消除矛盾
                    rec['lectureEnd'] = src.get('lectureEnd')
                    rec['timeConfidence'] = src.get('timeConfidence')
                    rec['timeNote'] = src.get('timeNote')
                    touched.append('lectureEnd')
            # location：新值非空且不同才替换（旧值为空/尾部残段均可修复）
            if 'location' in _field_set:
                old_l = (rec.get('location') or '').strip()
                new_l = (src.get('location') or '').strip()
                if new_l and new_l != old_l:
                    rec['location'] = new_l
                    touched.append('location')
            if touched:
                rec['qaRepaired'] = _fv.VOCAB_VERSION
                # 富文本来源已刷新，同步溯源标记为新解析的产出
                for k in ('llmTextEnhanced', 'llmVerdict', 'llmAdopted'):
                    if k in src:
                        rec[k] = src[k]
                    else:
                        rec.pop(k, None)
                stat['repaired'] += 1
                url_changed = True
                for t in touched:
                    stat['fields'][t] = stat['fields'].get(t, 0) + 1
                print(f'  [{i}/{len(urls)}] REPAIR {"|".join(touched)} {url}')
            else:
                stat['clean'] += 1
        time.sleep(0.3)

    print(f"[SUMMARY] repaired={stat['repaired']} clean={stat['clean']} "
          f"unreachable={stat['unreachable']} parse_err={stat['parse_err']} "
          f"news_skip={stat['news_skip']} no_match={stat['no_match']}")
    print(f"[FIELDS] {stat['fields']}")

    if args.dry_run or stat['repaired'] == 0:
        return

    # ---- 写盘：备份 + 原子替换 ----
    # 格式必须与仓库既有 lectures.json 一致（indent=1；Windows 文本模式天然 CRLF），
    # 否则一次修复会产生全文件级 git diff，淹没真实变更。
    ts = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    bak = f'{data_path}.bak-{ts}-repair'
    with open(data_path, 'rb') as f:
        data_bytes = f.read()
    with open(bak, 'wb') as f:
        f.write(data_bytes)
    out = {'data': recs} if isinstance(raw, dict) else recs
    tmp = data_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, data_path)
    print(f'[DONE] 已写盘 {data_path}（备份 {bak}）；'
          f'请运行 scripts/generate_frontend_data.py 刷新前端数据')


if __name__ == '__main__':
    main()
