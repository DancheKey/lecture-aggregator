# -*- coding: utf-8 -*-
"""定向重解析：对确认有问题的记录，用当前链路（规则 + 模型A + 模型B）重新解析并择优覆盖。

设计原则（严守项目铁律，避免破坏已精修数据）：
1. 只处理被 audit_data_quality.py 标记为问题的记录 + 字段，绝不整体替换。
2. 新值必须非空、必须通过污染检测（不脏才叫"更优"）。
3. 新值与旧值相同则不写（幂等）。
4. 改前自动备份；默认演练模式，加 --apply 才落库。

用法：
  python scripts/reparse_issues.py                 # 演练（默认 40 条抽样）
  python scripts/reparse_issues.py --limit 200     # 演练前 200 条
  python scripts/reparse_issues.py --all           # 演练全部问题记录
  python scripts/reparse_issues.py --apply         # 落库
"""
import os
import re
import io
import json
import time
import shutil
import argparse
import datetime
import requests
import urllib3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys_path_ok = False
import sys
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 载入 .env（模型 A/B 需要 key）
for _line in open(os.path.join(ROOT, '.env'), encoding='utf-8'):
    _line = _line.strip()
    if not _line or _line.startswith('#') or '=' not in _line:
        continue
    _k, _v = _line.split('=', 1)
    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
os.environ['SCNU_LLM_TEXT'] = '1'   # 强制开启全链路（规则 + A + B）

# 学校域名必须直连：系统 HTTPS_PROXY 只用于访问模型 API，
# 若让 scnu.edu.cn 也走代理会 ProxyError（项目既有经验）。
_no_proxy = os.environ.get('NO_PROXY', '')
os.environ['NO_PROXY'] = ','.join(
    x for x in [_no_proxy, '.scnu.edu.cn', 'scnu.edu.cn'] if x)
os.environ['no_proxy'] = os.environ['NO_PROXY']

from parsers import parse_detail  # noqa: E402

DATA_PATH = os.path.join(ROOT, 'data/lectures.json')
ISSUES_PATH = os.path.join(ROOT, 'reports/_dq_issues.json')
CACHE_DIR = os.path.join(ROOT, 'tmp/reparse_html')
BAK_DIR = os.path.join(ROOT, '_bak_20260908')
REPORT_PATH = os.path.join(ROOT, 'reports/reparse_report.html')

# 本次只修这 4 类问题字段（摘要/简介另有专门回填脚本，不在此动）
TARGET_FIELDS = ['speaker', 'speakerAffiliation', 'location', 'lectureStart', 'lectureEnd']
FIELD_ALIAS = {'speakerAffiliation': 'affiliation'}   # 双写字段同步

# ============ 污染检测闸门：新值必须"不脏"才可覆盖 ============
TITLE_RE = re.compile(
    r'(教授|副教授|助理教授|研究员|副研究员|讲师|高级教师|博士|硕士|院士|院长|'
    r'主任|书记|处长|校长|特聘|兼职|客座|博士后|研究生)')
EN_ORG_RE = re.compile(
    r'(University|Institute|Laboratory|Department|School|College|Academy|Center|Hospital)', re.I)
LOC_LABEL_RE = re.compile(r'(主办|承办|报告人|主讲人|主持人|时间\s*[:：]|地点\s*[:：]|摘要|对象|名额)')
DATE_FRAG_RE = re.compile(r'(日期\s*[:：]|\d{1,2}月\d{1,2}日)')


def dirty_speaker(v, speaker=''):
    if not v:
        return '空值'
    if len(v) < 2 or len(v) > 30:
        return '长度异常(%d)' % len(v)
    if TITLE_RE.search(v):
        return '含职称词'
    if EN_ORG_RE.search(v):
        return '含英文机构词'
    if DATE_FRAG_RE.search(v):
        return '含日期残片'
    if re.search(r'\d{4}-\d{2}-\d{2}', v):
        return '含完整日期'
    return ''


def dirty_aff(v, speaker=''):
    if not v:
        return '空值'
    if len(v) < 3 or len(v) > 60:
        return '长度异常(%d)' % len(v)
    if speaker and len(speaker) >= 2 and speaker in v:
        return '含主讲人姓名'
    if TITLE_RE.search(v):
        return '含职称/学位词'
    return ''


def dirty_loc(v):
    if not v:
        return '空值'
    if len(v) > 60:
        return '过长(%d)' % len(v)
    if LOC_LABEL_RE.search(v):
        return '含正文标签'
    return ''


DIRTY_CHECK = {
    'speaker': dirty_speaker,
    'speakerAffiliation': lambda v, s='': dirty_aff(v, s),
    'location': lambda v, s='': dirty_loc(v),
}


def field_ok(field, new, speaker=''):
    """新值是否可用（非空且不脏）"""
    if new in (None, ''):
        return False, '新值为空'
    if field in DIRTY_CHECK:
        why = DIRTY_CHECK[field](new, speaker)
        if why:
            return False, '新值仍脏: ' + why
    return True, ''


def _date_of(v):
    if not v:
        return None
    m = re.match(r'(\d{4}-\d{2}-\d{2})', str(v))
    return m.group(1) if m else None


def time_change_ok(old, new):
    """时间字段覆盖闸门：保护已正确的非占位日期，避免被误解析日期覆盖。

    - 新值无日期 → 拒绝
    - 旧值已是「真实日期」（非 00:00/08:00 占位）且新旧日期不同 → 拒绝（疑为误解析）
    - 旧值为占位（缺时刻）或新旧日期相同 → 允许（补时刻 / 精修）
    """
    if not new:
        return False, '新时间空'
    nm = _date_of(new)
    if not nm:
        return False, '新时间无日期'
    om = _date_of(old)
    if om:
        old_ph = str(old).endswith(('00:00:00', '08:00:00'))
        if not old_ph and om != nm:
            return False, '日期与现状冲突(现状%s)' % om
    return True, ''


# ============ 抓取（bytes 落盘，避免编码误判） ============
SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
})
SESSION.verify = False


def fetch_html(idx, url):
    os.makedirs(CACHE_DIR, exist_ok=True)
    dst = os.path.join(CACHE_DIR, f'{idx}.html')
    if os.path.exists(dst) and os.path.getsize(dst) > 500:
        return load_html(dst)
    try:
        resp = SESSION.get(url, timeout=20)
        open(dst, 'wb').write(resp.content)
        time.sleep(1.0)
        return load_html(dst)
    except Exception as e:
        return None, f'抓取失败: {type(e).__name__}'


def load_html(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8', 'gbk', 'gb18030'):
        try:
            return raw.decode(enc), ''
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='ignore'), '编码降级'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='实际写回（默认演练）')
    ap.add_argument('--limit', type=int, default=40, help='演练条数上限')
    ap.add_argument('--all', action='store_true', help='处理全部问题记录')
    ap.add_argument('--ids', default='', help='指定 idx，逗号分隔')
    args = ap.parse_args()

    data = json.load(open(DATA_PATH, encoding='utf-8'))
    recs = data['data']

    # 1) 目标 idx 集合
    if args.ids:
        targets = [int(x) for x in args.ids.split(',') if x.strip().isdigit()]
    else:
        issues = json.load(open(ISSUES_PATH, encoding='utf-8'))
        idxs = sorted({int(x['idx']) for x in issues if x.get('idx') not in (None, '')})
        targets = idxs if args.all else idxs[:args.limit]

    print('问题记录总数: %d，本次处理: %d' % (
        len({x['idx'] for x in json.load(open(ISSUES_PATH, encoding='utf-8'))}), len(targets)))

    # 2) 每条记录要修的字段（来自 audit 标记）
    issues = json.load(open(ISSUES_PATH, encoding='utf-8'))
    field_map = {}
    for x in issues:
        i = x.get('idx')
        if i in (None, ''):
            continue
        i = int(i)
        f = x.get('field')
        if f in TARGET_FIELDS:
            field_map.setdefault(i, set()).add(f)

    changes, skipped, failed = [], [], []
    t_start = time.time()

    for n, idx in enumerate(targets, 1):
        r = recs[idx]
        url = r.get('sourceUrl') or ''
        fields = field_map.get(idx, set(TARGET_FIELDS))   # 无标记则全字段比对
        if not url:
            failed.append({'idx': idx, 'why': '无 sourceUrl'})
            continue
        html, ferr = fetch_html(idx, url)
        if not html:
            failed.append({'idx': idx, 'why': ferr})
            continue

        try:
            out = parse_detail(html, url, r.get('college'), r.get('campus'),
                               skip_news_filter=True)
        except Exception as e:
            failed.append({'idx': idx, 'why': f'解析异常 {type(e).__name__}'})
            continue
        o = out[0] if isinstance(out, list) and out else (out or {})
        if not isinstance(o, dict):
            failed.append({'idx': idx, 'why': '解析结果非字典(%s)' % type(o).__name__})
            continue

        spk_new = o.get('speaker') or ''
        for f in sorted(fields):
            old = r.get(f)
            new = o.get(f)
            if f == 'speakerAffiliation' and not new:
                new = o.get('affiliation')
            if new == old:
                continue
            ok, why = field_ok(f, new, spk_new if f == 'speakerAffiliation' else '')
            if not ok:
                skipped.append({'idx': idx, 'field': f, 'old': old, 'new': new, 'why': why})
                continue
            if f in ('lectureStart', 'lectureEnd'):
                tok, twhy = time_change_ok(old, new)
                if not tok:
                    skipped.append({'idx': idx, 'field': f, 'old': old, 'new': new, 'why': twhy})
                    continue
            # 偏好闸门：旧 speaker 含中文、新值为纯英文（无中文）→ 保留中文，不覆盖
            if f == 'speaker' and old and re.search(r'[\u4e00-\u9fff]', str(old)) \
                    and not re.search(r'[\u4e00-\u9fff]', str(new)):
                skipped.append({'idx': idx, 'field': f, 'old': old, 'new': new,
                                'why': '新值纯英文/旧值含中文，按偏好保留中文'})
                continue
            changes.append({
                'idx': idx, 'field': f, 'old': old, 'new': new,
                'college': r.get('college'), 'url': url,
                'speaker': spk_new or r.get('speaker'),
                'start': (r.get('lectureStart') or '')[:10],
            })
            if args.apply:
                r[f] = new
                alias = FIELD_ALIAS.get(f)
                if alias and r.get(alias) == old:
                    r[alias] = new

        if n % 10 == 0:
            print('  ...%d/%d  用时 %.0fs  改 %d 跳过 %d 失败 %d'
                  % (n, len(targets), time.time() - t_start, len(changes), len(skipped), len(failed)))
            sys.stdout.flush()

    print('\n完成：拟修改 %d，跳过 %d，失败 %d，总耗时 %.0fs'
          % (len(changes), len(skipped), len(failed), time.time() - t_start))

    if args.apply and changes:
        os.makedirs(BAK_DIR, exist_ok=True)
        bak = os.path.join(BAK_DIR, 'lectures.json.pre_reparse_%s'
                           % datetime.datetime.now().strftime('%H%M%S'))
        shutil.copy2(DATA_PATH, bak)
        json.dump(data, open(DATA_PATH, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        print('已写回：%s（备份 %s）' % (DATA_PATH, bak))
    else:
        print('（演练模式，未修改数据；加 --apply 写回）')

    build_report(changes, skipped, failed, len(targets), args.apply)


def build_report(changes, skipped, failed, n_total, applied):
    import html
    e = lambda x: html.escape(str(x if x is not None else ''))
    p = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<title>定向重解析对照报告</title><style>',
         'body{font-family:"Microsoft YaHei",sans-serif;margin:24px;color:#222;background:#fff}',
         'h1{font-size:20px;border-bottom:2px solid #333;padding-bottom:8px}',
         'h2{font-size:16px;margin-top:26px;color:#333}',
         'table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}',
         'th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}',
         'th{background:#f5f5f5}',
         '.before{color:#c0392b;background:#fdf0ee;word-break:break-all}',
         '.after{color:#1e7e34;background:#eefaf1;word-break:break-all}',
         '.mini{color:#888;font-size:12px}',
         '.card{display:inline-block;background:#fafafa;border:1px solid #ddd;',
         'border-radius:6px;padding:10px 18px;margin:6px 12px 6px 0;text-align:center}',
         '.card .n{font-size:22px;font-weight:bold;color:#333}',
         '.card .l{font-size:12px;color:#777}',
         '.note{background:#fffbe6;border:1px solid #ffe58f;padding:10px 14px;',
         'border-radius:6px;margin:14px 0;font-size:13px}',
         '</style></head><body>',
         '<h1>定向重解析对照报告（规则 + 模型A + 模型B）</h1>',
         '<div class="note">对审计标记的问题记录，用当前解析链路重新解析；'
         '仅当新值<b>非空</b>且<b>通过污染检测</b>时才覆盖旧值，避免破坏已精修数据。'
         '状态：<b>%s</b></div>' % ('已应用' if applied else '演练（未修改数据）'),
         '<div>',
         '<div class="card"><div class="n">%d</div><div class="l">处理记录</div></div>' % n_total,
         '<div class="card"><div class="n">%d</div><div class="l">拟修改字段</div></div>' % len(changes),
         '<div class="card"><div class="n">%d</div><div class="l">闸门跳过</div></div>' % len(skipped),
         '<div class="card"><div class="n">%d</div><div class="l">抓取/解析失败</div></div>' % len(failed),
         '</div>']

    if changes:
        p.append('<h2>拟修改明细（%d 项）</h2>' % len(changes))
        p.append('<table><tr><th style="width:50px">idx</th><th style="width:130px">字段</th>'
                 '<th style="width:120px">学院</th><th class="before">当前值</th>'
                 '<th class="after">重解析值</th><th style="width:90px">时间</th>'
                 '<th style="width:60px">源页</th></tr>')
        for c in changes:
            p.append('<tr><td>%s</td><td>%s</td><td>%s</td><td class="before">%s</td>'
                     '<td class="after">%s</td><td>%s</td>'
                     '<td><a href="%s" target="_blank">源页 ↗</a></td></tr>'
                     % (e(c['idx']), e(c['field']), e(c['college']),
                        e(str(c['old'])[:200]), e(str(c['new'])[:200]),
                        e(c['start']), e(c['url'])))
        p.append('</table>')

    if skipped:
        p.append('<h2>闸门跳过（%d 项，新值不比旧值优）</h2>' % len(skipped))
        p.append('<table><tr><th style="width:50px">idx</th><th style="width:130px">字段</th>'
                 '<th class="before">当前值</th><th>重解析值</th><th style="width:180px">原因</th></tr>')
        for s in skipped[:120]:
            p.append('<tr><td>%s</td><td>%s</td><td class="before">%s</td><td>%s</td><td>%s</td></tr>'
                     % (e(s['idx']), e(s['field']), e(str(s['old'])[:160]),
                        e(str(s['new'])[:160]), e(s['why'])))
        p.append('</table>')

    if failed:
        p.append('<h2>抓取/解析失败（%d 条）</h2>' % len(failed))
        p.append('<table><tr><th style="width:50px">idx</th><th>原因</th></tr>')
        for f in failed:
            p.append('<tr><td>%s</td><td>%s</td></tr>' % (e(f['idx']), e(f['why'])))
        p.append('</table>')

    p.append('</body></html>')
    open(REPORT_PATH, 'w', encoding='utf-8').write('\n'.join(p))
    print('报告：%s' % REPORT_PATH)


if __name__ == '__main__':
    main()
