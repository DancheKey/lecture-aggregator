# -*- coding: utf-8 -*-
"""批量清洗「可自动修」的数据质量问题（2026-09-08）

输入：reports/_dq_issues.json 中 fixKind=='auto' 的项（由 audit_data_quality.py 产出）
输出：① --apply 时就地修改 data/lectures.json  ② reports/auto_fix_report.html 前后对照报告

设计原则：
  - 只处理 fixKind=='auto' 的项；需人工核对(218) 与字段缺失(553) 一律不动
  - 清洗规则与检测规则同源：直接复用 audit_data_quality 的正则，避免两套标准漂移
  - 安全闸门：清洗后为空 / 过短 / 无变化 → 拒绝修改并写入报告，宁可漏改不可误伤

运行：
  python scripts/fix_auto_issues.py            # 演练，只看不改
  python scripts/fix_auto_issues.py --apply    # 实际写回（自动备份原文件）
"""
import os
import re
import sys
import json
import html
import shutil
import argparse
import collections
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
ISSUES_PATH = os.path.join(ROOT, 'reports', '_dq_issues.json')
REPORT_PATH = os.path.join(ROOT, 'reports', 'auto_fix_report.html')
BAK_DIR = os.path.join(ROOT, '_bak_20260908')

# 复用扫描脚本的正则，保证「检测」与「清洗」用同一套标准
from audit_data_quality import TITLE_RE, EN_TITLE_RE, LOC_LABEL_RE  # noqa: E402

# 去掉姓名后常见的残句前缀：「刘李目前是香港科技大学」→ 去姓名 → 再去「目前是」
AFF_PREFIX_RE = re.compile(r'^(目前是|现为|现任职于|现就职于|任职于|就职于|现在|现任)')
# 清洗后若以这些词开头，说明前面丢了校名（如「大学古籍所」），属不完整机构名 → 拒绝
AFF_HEAD_BAD_RE = re.compile(r'^(大学|学院|研究所|研究院|实验室)')
# 地点截断后尾部的常见残留：「理6栋525报告厅活动嘉宾」截断后残留「活动」
LOC_TAIL_RE = re.compile(r'(活动|参会|参加|对象|人员|名额|[一二三]、?)+$')


def clean_speaker(v, desc):
    """清洗主讲人姓名，返回新值；无对应规则返回 None"""
    if desc.startswith('姓名含职称'):
        new = TITLE_RE.sub('', v)
        return re.sub(r'\s{2,}', ' ', new).strip(' ,，、·')
    if desc.startswith('英文名带头衔'):
        return EN_TITLE_RE.sub('', v).strip().strip('. ').strip()
    if desc.startswith('姓名混入日期'):
        m = re.search(r'日期|\d{1,2}月\d{1,2}日', v)
        return v[:m.start()].strip(' ,，、·') if m else None
    if desc.startswith('姓名含换行'):
        return re.sub(r'[\n\t]+', ' ', v).strip()
    if desc.startswith('姓名含机构名'):
        m = re.search(r'[（(]', v)
        return v[:m.start()].strip(' ,，、·') if m else None
    return None


def clean_affiliation(v, spk, desc):
    """清洗单位，返回新值；无对应规则返回 None"""
    if desc.startswith('单位中含主讲人姓名'):
        if not (spk and spk in v):
            return None
        new = v.replace(spk, '', 1).strip(' ,，、·')
        return AFF_PREFIX_RE.sub('', new).strip(' ,，、·')
    if desc.startswith('单位含换行'):
        return re.sub(r'[\n\t]+', ' ', v).strip()
    return None


def clean_location(v, desc):
    """清洗地点，返回新值；无对应规则返回 None"""
    if desc.startswith('地点含正文标签'):
        m = LOC_LABEL_RE.search(v)
        if not m:
            return None
        new = v[:m.start()].strip(' ,，、·:：')
        return LOC_TAIL_RE.sub('', new).strip(' ,，、·:：')
    if desc.startswith('地点含长串英文'):
        m = re.search(r'[A-Za-z]{25,}', v)
        if not m:
            return None
        new = v[:m.start()].strip(' ,，、·:：')
        return LOC_TAIL_RE.sub('', new).strip(' ,，、·:：')
    if desc.startswith('地点含换行'):
        return re.sub(r'[\n\t]+', ' ', v).strip()
    return None


def clean_time_field(rec, desc):
    """时间类：返回 (字段名, 旧值, 新值)；置空结束时间或标记 timeUnknown"""
    if desc.startswith('时长异常') or desc.startswith('结束时间早于开始时间'):
        return 'lectureEnd', rec.get('lectureEnd'), ''
    if desc.startswith('占位时刻'):
        return 'timeUnknown', rec.get('timeUnknown'), True
    return None


def gate(field, old, new):
    """安全闸门：返回 (是否允许, 拒绝原因)"""
    if new is None:
        return False, '无匹配清洗规则'
    if new == old:
        return False, '清洗后无变化'
    if field in ('lectureEnd', 'timeUnknown'):
        return True, ''                      # 置空结束时间 / 打标记是预期行为
    if not new:
        return False, '清洗后为空（拒绝，防误删）'
    if field == 'speaker' and len(new) < 2:
        return False, '清洗后过短（%d 字）' % len(new)
    if field in ('speakerAffiliation', 'location') and len(new) < 3:
        return False, '清洗后过短（%d 字）' % len(new)
    if field == 'speakerAffiliation' and AFF_HEAD_BAD_RE.match(new):
        return False, '清洗后机构名不完整（以「大学/学院」等开头，疑似丢了校名）'
    if field == 'speakerAffiliation' and TITLE_RE.search(new):
        return False, '清洗后仍含职称/学位（如「博士毕业于…」），需人工确认真实单位'
    return True, ''


CSS = """
body{font-family:"Microsoft YaHei",sans-serif;max-width:1280px;margin:24px auto;
     padding:0 16px;color:#1a1a1a;background:#fff;line-height:1.6}
h1{font-size:22px;margin-bottom:6px} .sub{color:#888;font-size:13px;margin-bottom:18px}
h2{font-size:17px;margin-top:30px;border-left:4px solid #c33;padding-left:10px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.card{flex:1;min-width:130px;border:1px solid #e5e5e5;border-radius:8px;padding:12px 14px;background:#fafafa}
.card .n{font-size:24px;font-weight:700} .card .l{font-size:12px;color:#666;margin-top:2px}
.card.k .n{color:#0a7d3e} .card.r .n{color:#c98a00}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}
th,td{border:1px solid #e0e0e0;padding:7px 9px;text-align:left;vertical-align:top}
th{background:#f5f5f5;font-weight:600}
tr:nth-child(even) td{background:#fcfcfc}
.before{color:#c33;word-break:break-all;max-width:260px}
.after{color:#0a7d3e;font-weight:600;word-break:break-all;max-width:260px}
.val{word-break:break-all;max-width:300px}
a{color:#1668dc;text-decoration:none} a:hover{text-decoration:underline}
.note{background:#fffbe6;border:1px solid #ffe58f;padding:10px 14px;
      border-radius:6px;margin:14px 0;font-size:13px}
.mini{color:#888;font-size:12px}
"""


def build_report(changes, rejected, skipped, n_auto, apply_mode, out_path):
    esc = lambda x: html.escape(str(x if x is not None else ''))
    parts = ['<!doctype html><html lang="zh"><head><meta charset="utf-8">',
             '<title>自动清洗对照报告</title><style>%s</style></head><body>' % CSS]
    parts.append('<h1>自动清洗对照报告（可自动修 87 条）</h1>')
    parts.append('<div class="sub">数据源 data/lectures.json · 生成时间 %s · 模式：%s</div>'
                 % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                    '<b style="color:#0a7d3e">已应用</b>' if apply_mode
                    else '<b style="color:#c98a00">演练（未改动数据）</b>'))
    parts.append('<div class="cards">')
    parts.append('<div class="card k"><div class="n">%d</div><div class="l">已修改项</div></div>' % len(changes))
    parts.append('<div class="card r"><div class="n">%d</div><div class="l">闸门拒绝(转人工)</div></div>'
                 % len(rejected))
    parts.append('<div class="card"><div class="n">%d</div><div class="l">已覆盖(无需重复)</div></div>'
                 % len(skipped))
    parts.append('<div class="card"><div class="n">%d</div><div class="l">可自动修总数</div></div>' % n_auto)
    parts.append('<div class="card"><div class="n">%d</div><div class="l">涉及记录数</div></div>'
                 % len({c['idx'] for c in changes}))
    parts.append('</div>')
    parts.append('<div class="note">只处理扫描报告中标记「可自动修」的问题；'
                 '<b>需人工核对 218 条、字段缺失 553 条一律未动</b>。'
                 '每条都给源页链接，便于你逐条复核；'
                 '被闸门拒绝的项（清洗后会为空或过短）保持原值，不做臆造修改。</div>')

    parts.append('<h2>修改明细（%d 项）</h2>' % len(changes))
    parts.append('<table><tr><th style="width:50px">idx</th><th style="width:110px">字段</th>'
                 '<th style="width:140px">问题类型</th><th class="before">修改前</th>'
                 '<th class="after">修改后</th><th style="width:110px">学院</th>'
                 '<th style="width:60px">源页</th></tr>')
    for c in changes:
        parts.append(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td class="before">%s</td>'
            '<td class="after">%s</td><td>%s</td>'
            '<td><a href="%s" target="_blank">源页 ↗</a></td></tr>'
            % (esc(c['idx']), esc(c['field']), esc(c['desc']),
               esc(str(c['before'])[:200]), esc(str(c['after'])[:200]),
               esc(c['college']), esc(c['url'])))
    parts.append('</table>')

    if rejected:
        parts.append('<h2>被安全闸门拒绝（%d 项，保持原值未改）</h2>' % len(rejected))
        parts.append('<table><tr><th style="width:50px">idx</th><th style="width:110px">字段</th>'
                     '<th style="width:140px">问题类型</th><th class="val">当前值</th>'
                     '<th style="width:180px">拒绝原因</th><th style="width:60px">源页</th></tr>')
        for c in rejected:
            parts.append(
                '<tr><td>%s</td><td>%s</td><td>%s</td><td class="val">%s</td><td>%s</td>'
                '<td><a href="%s" target="_blank">源页 ↗</a></td></tr>'
                % (esc(c['idx']), esc(c['field']), esc(c['desc']),
                   esc(str(c['value'])[:200]), esc(c['why']), esc(c['url'])))
        parts.append('</table>')

    if skipped:
        parts.append('<h2>已覆盖、无需重复处理（%d 项）</h2>' % len(skipped))
        parts.append('<div class="note">同一记录的同一字段常命中多条规则'
                     '（如「Prof. 张三」同时命中「英文名带头衔」与「姓名含职称」）。'
                     '前一步清洗已把值改好，后续规则再跑发现「无变化」，'
                     '属正常覆盖，<b>不是漏改</b>。</div>')
        parts.append('<table><tr><th style="width:50px">idx</th><th style="width:110px">字段</th>'
                     '<th style="width:210px">问题类型</th><th class="val">当前值（已被前一步清洗）</th>'
                     '<th style="width:60px">源页</th></tr>')
        for c in skipped:
            parts.append(
                '<tr><td>%s</td><td>%s</td><td>%s</td><td class="val">%s</td>'
                '<td><a href="%s" target="_blank">源页 ↗</a></td></tr>'
                % (esc(c['idx']), esc(c['field']), esc(c['desc']),
                   esc(str(c['value'])[:200]), esc(c['url'])))
        parts.append('</table>')

    parts.append('</body></html>')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts))
    return out_path


def diff_report(bak_path, cur_path, out_path):
    """对比备份与当前数据，生成全量修改对照报告（覆盖分批清洗的全部改动）"""
    old = json.load(open(bak_path, encoding='utf-8'))['data']
    new = json.load(open(cur_path, encoding='utf-8'))['data']
    esc = lambda x: html.escape(str(x if x is not None else ''))
    rows = []
    for i, (a, b) in enumerate(zip(old, new)):
        for f in sorted(set(list(a.keys()) + list(b.keys()))):
            if a.get(f) != b.get(f):
                rows.append({'idx': i, 'field': f, 'before': a.get(f), 'after': b.get(f),
                             'url': b.get('sourceUrl'), 'college': b.get('college')})
    parts = ['<!doctype html><html lang="zh"><head><meta charset="utf-8">',
             '<title>全量清洗对照报告</title><style>%s</style></head><body>' % CSS]
    parts.append('<h1>全量清洗对照报告（原始 → 当前）</h1>')
    parts.append('<div class="sub">备份 %s → 当前 data/lectures.json · 生成时间 %s</div>'
                 % (esc(os.path.basename(bak_path)),
                    datetime.datetime.now().strftime('%Y-%m-%d %H:%M')))
    parts.append('<div class="cards">')
    parts.append('<div class="card k"><div class="n">%d</div><div class="l">修改项</div></div>' % len(rows))
    parts.append('<div class="card"><div class="n">%d</div><div class="l">涉及记录</div></div>'
                 % len({r['idx'] for r in rows}))
    parts.append('</div>')
    parts.append('<div class="note">这是「清洗前备份」与「当前数据」的逐字段差异，'
                 '涵盖所有批次的改动；每行都带源页链接，便于逐条复核。</div>')
    parts.append('<table><tr><th style="width:50px">idx</th><th style="width:130px">字段</th>'
                 '<th class="before">修改前</th><th class="after">修改后</th>'
                 '<th style="width:120px">学院</th><th style="width:60px">源页</th></tr>')
    for r in rows:
        parts.append('<tr><td>%s</td><td>%s</td><td class="before">%s</td><td class="after">%s</td>'
                     '<td>%s</td><td><a href="%s" target="_blank">源页 ↗</a></td></tr>'
                     % (esc(r['idx']), esc(r['field']), esc(str(r['before'])[:220]),
                        esc(str(r['after'])[:220]), esc(r['college']), esc(r['url'])))
    parts.append('</table></body></html>')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts))
    return out_path, len(rows), len({r['idx'] for r in rows})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='实际写回数据（默认只演练）')
    ap.add_argument('--replay', action='store_true',
                    help='重放：按实际应用方式串行清洗并出报告，但不写回（配合 --data 用备份复现）')
    ap.add_argument('--data', default=DATA_PATH, help='输入数据文件（默认 data/lectures.json）')
    ap.add_argument('--diff', default='',
                    help='与当前数据逐字段对比的备份文件，生成全量对照报告（覆盖所有批次改动）')
    args = ap.parse_args()

    if args.diff:
        out, n, nf = diff_report(args.diff, DATA_PATH, REPORT_PATH)
        print('全量对照报告: %s' % out)
        print('修改项 %d，涉及记录 %d 条' % (n, nf))
        return

    raw = open(args.data, encoding='utf-8').read()
    indent = 2 if '\n  ' in raw[:2000] else None
    data = json.loads(raw)
    recs = data['data']
    issues = json.load(open(ISSUES_PATH, encoding='utf-8'))

    auto = [x for x in issues if x.get('fixKind') == 'auto']
    plan = collections.defaultdict(list)
    for x in auto:
        plan[x['idx']].append(x)

    changes, rejected, skipped = [], [], []
    change_map = {}
    # 演练模式不回写内存，后续规则看到的仍是原值；应用/重放模式串行累积，行为与实际一致
    simulate = args.apply or args.replay
    for idx in sorted(plan):
        if idx >= len(recs):
            continue
        r = recs[idx]
        for x in plan[idx]:
            field, desc = x['field'], x['desc']
            # 时间类改的是 lectureEnd / timeUnknown，不是原字段本身
            if field in ('lectureEnd', 'lectureStart'):
                res = clean_time_field(r, desc)
                if res is None:
                    rejected.append({'idx': idx, 'field': field, 'value': r.get(field),
                                     'desc': desc, 'why': '无匹配清洗规则',
                                     'url': x.get('url'), 'college': x.get('college')})
                    continue
                f2, old, new = res
            else:
                f2 = field
                old = r.get(field)
                if field == 'speaker':
                    new = clean_speaker(old or '', desc)
                elif field == 'speakerAffiliation':
                    new = clean_affiliation(old or '', r.get('speaker') or '', desc)
                elif field == 'location':
                    new = clean_location(old or '', desc)
                else:
                    new = None

            ok, why = gate(f2, old, new)
            if not ok:
                item = {'idx': idx, 'field': f2, 'value': old, 'desc': desc, 'why': why,
                        'url': x.get('url'), 'college': x.get('college')}
                # 「无变化」不是真拒绝：该问题已被同字段前一步清洗覆盖
                (skipped if why == '清洗后无变化' else rejected).append(item)
                continue
            key = (idx, f2)
            if key in change_map:
                # 同一记录同一字段的多条 issue 是串行清洗的，报告里合并显示为「最初值 → 最终值」
                change_map[key]['after'] = new
                change_map[key]['desc'] += ' + ' + desc
            else:
                change_map[key] = {'idx': idx, 'field': f2, 'before': old, 'after': new,
                                   'desc': desc, 'url': x.get('url'), 'college': x.get('college'),
                                   'speaker': x.get('speaker'), 'start': x.get('start')}
                changes.append(change_map[key])
            if simulate:
                r[f2] = new
                # 单位双写字段同步（前端读 speakerAffiliation，历史字段 affiliation 保持一致）
                if f2 == 'speakerAffiliation' and r.get('affiliation') == old:
                    r['affiliation'] = new

    print('可自动修项: %d' % len(auto))
    print('拟修改: %d   真拒绝: %d   已覆盖无需重复: %d'
          % (len(changes), len(rejected), len(skipped)))
    print('按问题类型统计（拟修改）:')
    for d, n in collections.Counter(re.sub(r'\d+', 'N', c['desc']) for c in changes).most_common():
        print('  %-46s %d' % (d, n))
    if rejected:
        print('拒绝原因统计:')
        for w, n in collections.Counter(c['why'] for c in rejected).most_common():
            print('  %-30s %d' % (w, n))

    if args.apply:
        os.makedirs(BAK_DIR, exist_ok=True)
        # 分批清洗时避免覆盖上一批的备份（pre_autofix / pre_autofix2 / ...）
        bak = os.path.join(BAK_DIR, 'lectures.json.pre_autofix')
        n = 1
        while os.path.exists(bak):
            n += 1
            bak = os.path.join(BAK_DIR, 'lectures.json.pre_autofix%d' % n)
        shutil.copy2(DATA_PATH, bak)
        print('已备份: %s' % bak)
        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        print('已写回: %s' % DATA_PATH)
    elif args.replay:
        print('（重放模式：基于备份复现清洗过程生成报告，未写回任何文件）')
    else:
        print('（演练模式，未修改任何数据；加 --apply 才会写回）')

    out = build_report(changes, rejected, skipped, len(auto),
                       args.apply or args.replay, REPORT_PATH)
    print('对照报告: %s' % out)


if __name__ == '__main__':
    main()
