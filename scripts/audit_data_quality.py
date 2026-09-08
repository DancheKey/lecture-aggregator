# -*- coding: utf-8 -*-
"""讲座数据质量专项扫描（2026-09-08）

扫描 data/lectures.json，检测主讲人 / 单位 / 时间 / 地点四类字段的明显污染，
产出可点击 HTML 清单（每条带源页链接，支持勾选与导出）。

与 scripts/audit_fields.py 的区别：
  - audit_fields 偏「字段完整性」体检（缺失/基本格式）
  - 本脚本偏「值内容污染」专项（正文残段粘入、结束时间跨天、职称混入姓名等）

运行： python scripts/audit_data_quality.py [--html reports/data_quality_report.html]
"""
import os
import re
import json
import html
import argparse
import datetime
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')


def get(r, *keys):
    """取第一个非空字段值"""
    for k in keys:
        v = (r.get(k) or '')
        if isinstance(v, str):
            v = v.strip()
        if v:
            return v
    return ''


# ---------------- 规则库 ----------------
# 职称/职务词（姓名与单位都不该含）
# 注：不含「研究生」——「XX研究生院」是机构名，会造成大量误报（清华深研院、工程物理研究院研究生院）
TITLE_RE = re.compile(
    r'(教授|副教授|研究员|副研究员|助理研究员|讲师|院士|博士|硕士|博士后|'
    r'老师|主任|院长|所长|书记|校长|会长|主席|主编|编辑|编委|工程师|'
    r'博导|硕导|特聘|客座|兼职|名誉|系主任|教授级|高级教师|正高级|'
    r'同学|女士|先生|Prof\.|Dr\.|Professor|Ph\.?D|PhD)'
)
# 机构词
# 机构词（2026-09-08 补充英文：此前只认中文机构词，导致「姓名+英文单位」粘连未被检出）
ORG_RE = re.compile(r'(大学|学院|研究所|研究院|实验室|中心|公司|集团|学会|协会|医院|中学|小学|出版社|'
                    r'Publishing|University|Institute|Laboratory|Department|School|College|'
                    r'Academy|Hospital|Centre|Center)')
# 期刊/出版社（误当单位）
# 注：不含裸「新闻」——「新闻与传播学院」是正规学院名，会误伤清华/暨大
JOURNAL_RE = re.compile(
    r'(Journal|Proceedings|Nature|Letters|Physical Review|'
    r'PRB|PRL|ACS|IEEE|Elsevier|Springer|学报|杂志|期刊|出版社|日报|周报)',
    re.I
)
# 含 Science 但属于合法机构/学科名的搭配，需先剔除再判期刊，
# 否则「Southern University of Science and Technology」「Computer Science」会被误判
SCI_OK = ('Science and Technology', 'Computer Science', 'of Science', 'School of Science',
          'College of Science', 'Natural Science', 'Life Science', 'Space Science',
          'Materials Science', 'Data Science', 'Social Science', 'Political Science',
          'Environmental Science', 'Information Science', 'Cognitive Science',
          'Fundamental Science', 'Basic Science')
# 地点正文标签残段（说明地点抓取时吞进了正文）
LOC_LABEL_RE = re.compile(
    r'(主办|承办|协办|报告人|主讲人|主持人|邀请人|时间[:：]|地点[:：]|摘要|'
    r'报告题目|内容简介|欢迎|参加|报名|您想|如何|培训对象|名额|对象[:：]|'
    r'嘉宾|领导|议程|日程|备注|联系人|联系电话)'
)
# 多人联讲分隔符
MULTI_SEP_RE = re.compile(r'[、,，/&]| and | AND ')
# 英文头衔前缀
EN_TITLE_RE = re.compile(r'^\s*(Prof|Doctor|Dr|Professor|Mr|Ms|Mrs|Sir|Madam)\.?\s+', re.I)


def is_journal_name(a):
    """判断是否期刊/出版社误当单位。
    先剔除含 Science 的合法机构/学科名搭配，避免误伤
    「Southern University of Science and Technology」「Department of Computer Science」等。
    """
    t = a
    for ok in SCI_OK:
        t = t.replace(ok, '')
    return bool(JOURNAL_RE.search(t))


def is_multi_speaker(s):
    """判断是否多人联讲（正常形态，不算错误）"""
    if not MULTI_SEP_RE.search(s):
        return False
    # 含机构/职务词的逗号串不算多人，是污染
    if ORG_RE.search(s) or TITLE_RE.search(s):
        return False
    parts = [p.strip() for p in re.split(r'[、,，/&]| and | AND ', s) if p.strip()]
    return len(parts) >= 2 and all(len(p) <= 12 for p in parts)


def has_unclosed_paren(s):
    return s.count('(') != s.count(')') or s.count('（') != s.count('）')


# ---------------- 可修性判定 ----------------
# auto   = 规则明确，可批量自动清洗（低风险）
# manual = 能修，但需人工回源页确认真实值，自动有臆造风险
# nfix   = 源页本身无信息 / 当前值属合规正常形态（不是错误），不建议改
FIX_RULES = [
    (r'^姓名含职称', 'auto', '去掉职称/职务词，保留纯姓名'),
    (r'^英文名带头衔', 'auto', '去掉 Prof./Dr. 等英文头衔前缀，保留姓名'),
    (r'^姓名混入日期', 'auto', '截断「日期」及其后残片，保留姓名'),
    (r'^姓名含换行', 'auto', '换行/制表符替换为空格'),
    (r'^姓名含机构名', 'auto', '把括号里的机构名移到单位字段，姓名取括号前部分'),
    (r'^姓名过长', 'manual', '回源页确认真实姓名（复姓、长英文名可能是正常的）'),
    (r'^括号未闭合', 'manual', '抓取被截断，需回源页重新解析（自动补全有臆造风险）'),
    (r'^单位中含主讲人姓名', 'auto', '去掉姓名前缀，保留机构名'),
    (r'^单位含换行', 'auto', '换行替换为空格'),
    (r'^单位含职称', 'manual', '回源页确认真实单位'),
    (r'^疑似期刊', 'manual', '需回源页查真实所属单位，无法凭空推断'),
    (r'^单位过长', 'manual', '人工判断是否为真实长机构名（如带重点实验室后缀）'),
    (r'^英文单词粘连', 'manual', '人工恢复空格（自动加空格易切错单词边界）'),
    (r'^时间格式非法', 'manual', '回源页重新解析时间'),
    (r'^年份异常', 'manual', '回源页核对年份'),
    (r'^占位时刻', 'auto', '标记 timeUnknown=true，前端按「时刻未知」处理'),
    (r'^结束时间早于开始时间', 'auto', '结束时间不可信，置空'),
    (r'^时长异常', 'auto', '结束时间抓错（跨天/跨月），置空结束时间'),
    (r'^时长偏长', 'manual', '可能是全天会议/多日议程，回源页确认'),
    (r'^地点含换行', 'auto', '换行替换为空格'),
    (r'^地点含正文标签', 'auto', '截断到「主办/报告人/时间:」等标签之前'),
    (r'^地点含长串英文', 'auto', '截断到长串英文之前（英文为报告题目误入）'),
    (r'^地点过长', 'manual', '人工截断（正文边界不固定，自动截断易截错）'),
    (r'^地点偏长', 'manual', '人工确认是否为完整地址'),
    (r'^发布日期晚于讲座日', 'manual', '疑似回顾稿/新闻稿：按规则应删除而非修复，需人工确认'),
]

FIX_LABEL = {'auto': ('可自动修', 'fx-a'),
             'manual': ('需人工核对', 'fx-m'),
             'nfix': ('无法修 / 无需修', 'fx-n')}


def fixability(cat, desc, val=''):
    """返回 (kind, 修复建议)"""
    v = str(val or '')
    if cat == '缺失':
        return 'nfix', ('源页未提供则无法凭空补；可回源页重解析或用 LLM/VLM 补录，'
                        '若源页本身未写则确实无法修')
    # 院士称号+机构名是项目规则允许的合规写法，不是污染
    if desc.startswith('单位含职称') and re.search(r'院士\s*$', v):
        return 'nfix', ('院士称号+机构名属项目规则允许的合规写法（如「中国科学院院士，清华大学」），'
                        '不是错误，无需修改')
    # 姓名混机构名：有括号时可自动拆，无括号分隔则需人工
    if desc.startswith('姓名含机构名') and not (('(' in v) or ('（' in v)):
        return 'manual', '姓名里混了机构名但无括号分隔，需人工拆分出真实姓名'
    for pat, kind, advice in FIX_RULES:
        if re.match(pat, desc):
            return kind, advice
    return 'manual', '需人工回源页核对'


def scan(recs):
    """返回 issues: [(分类, 字段, 严重度, 问题描述, 当前值, 记录)]"""
    issues = []

    for i, r in enumerate(recs):
        url = r.get('sourceUrl') or ''
        spk = get(r, 'speaker')
        aff = get(r, 'speakerAffiliation', 'affiliation')
        loc = get(r, 'location')
        st = get(r, 'lectureStart')
        en = get(r, 'lectureEnd')

        # ---------- 主讲人 ----------
        if not spk:
            issues.append(('缺失', 'speaker', '低', '主讲人为空', '(空)', r))
        else:
            if TITLE_RE.search(spk):
                issues.append(('主讲人', 'speaker', '高', '姓名含职称/职务', spk, r))
            if ORG_RE.search(spk):
                issues.append(('主讲人', 'speaker', '高', '姓名含机构名', spk, r))
            if re.search(r'日期|\d{1,2}月\d{1,2}日', spk):
                issues.append(('主讲人', 'speaker', '高', '姓名混入日期残片', spk, r))
            if has_unclosed_paren(spk):
                issues.append(('主讲人', 'speaker', '高', '括号未闭合（抓取截断）', spk, r))
            if EN_TITLE_RE.match(spk):
                issues.append(('主讲人', 'speaker', '中', '英文名带头衔前缀', spk, r))
            if len(spk) > 6 and re.search(r'[\u4e00-\u9fa5]', spk) and not is_multi_speaker(spk):
                issues.append(('主讲人', 'speaker', '中', f'姓名过长({len(spk)}字)，疑似混入其他内容', spk, r))
            if '\n' in spk or '\t' in spk:
                issues.append(('主讲人', 'speaker', '高', '姓名含换行/制表符', spk, r))

        # ---------- 单位 ----------
        if not aff:
            issues.append(('缺失', 'speakerAffiliation', '低', '单位为空', '(空)', r))
        else:
            if TITLE_RE.search(aff):
                issues.append(('单位', 'speakerAffiliation', '高', '单位含职称/学位', aff, r))
            if is_journal_name(aff):
                issues.append(('单位', 'speakerAffiliation', '高', '疑似期刊/出版社误当单位', aff, r))
            if spk and spk in aff:
                issues.append(('单位', 'speakerAffiliation', '高', '单位中含主讲人姓名', aff, r))
            if has_unclosed_paren(aff):
                issues.append(('单位', 'speakerAffiliation', '中', '括号未闭合（抓取截断）', aff, r))
            if len(aff) > 30:
                issues.append(('单位', 'speakerAffiliation', '中', f'单位过长({len(aff)}字)', aff, r))
            # 英文单词粘连：连续 >18 个字母且无空格
            if re.search(r'[A-Za-z]{18,}', aff) and ' ' not in aff.strip():
                issues.append(('单位', 'speakerAffiliation', '中', '英文单词粘连（空格丢失）', aff, r))
            if '\n' in aff:
                issues.append(('单位', 'speakerAffiliation', '高', '单位含换行', aff, r))

        # ---------- 时间 ----------
        if not st:
            issues.append(('缺失', 'lectureStart', '低', '讲座开始时间缺失', '(空)', r))
        else:
            m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', st)
            if not m:
                issues.append(('时间', 'lectureStart', '高', '时间格式非法', st, r))
            else:
                y = int(m.group(1))
                # 数据实际覆盖 2009~2026，故 <2005 才判异常（避免把真实老讲座误报）
                if y < 2005 or y > 2031:
                    issues.append(('时间', 'lectureStart', '高', f'年份异常({y})', st, r))
                hm = st[11:16] if len(st) >= 16 else ''
                if hm in ('00:00', '08:00') and not r.get('timeUnknown'):
                    issues.append(('时间', 'lectureStart', '中', f'占位时刻 {hm} 但未标记 timeUnknown', st, r))
            if en:
                try:
                    ds = datetime.datetime.strptime(st[:19], '%Y-%m-%d %H:%M:%S')
                    de = datetime.datetime.strptime(en[:19], '%Y-%m-%d %H:%M:%S')
                    delta_h = (de - ds).total_seconds() / 3600.0
                    if delta_h < 0:
                        issues.append(('时间', 'lectureEnd', '高', '结束时间早于开始时间', f'{st} → {en}', r))
                    elif delta_h > 24:
                        issues.append(('时间', 'lectureEnd', '高',
                                       f'时长异常({delta_h:.0f}小时)，疑似把页面其他日期当成结束时间',
                                       f'{st} → {en}', r))
                    elif delta_h > 8:
                        issues.append(('时间', 'lectureEnd', '中',
                                       f'时长偏长({delta_h:.1f}小时)，可能是全天会议或误抓',
                                       f'{st} → {en}', r))
                except Exception:
                    pass

        # ---------- 地点 ----------
        if not loc:
            issues.append(('缺失', 'location', '低', '地点缺失', '(空)', r))
        else:
            if len(loc) > 40:
                issues.append(('地点', 'location', '高', f'地点过长({len(loc)}字)，疑似正文段落混入', loc, r))
            elif len(loc) > 30:
                issues.append(('地点', 'location', '中', f'地点偏长({len(loc)}字)', loc, r))
            if LOC_LABEL_RE.search(loc):
                issues.append(('地点', 'location', '高', '地点含正文标签/正文句子', loc, r))
            if re.search(r'[A-Za-z]{25,}', loc):
                issues.append(('地点', 'location', '高', '地点含长串英文（疑似标题混入）', loc, r))
            if '\n' in loc:
                issues.append(('地点', 'location', '中', '地点含换行', loc, r))

        # ---------- 跨字段 ----------
        pt = get(r, 'publishTime')
        if pt and st and len(pt) >= 10 and len(st) >= 10:
            try:
                d = (datetime.datetime.strptime(pt[:10], '%Y-%m-%d') -
                     datetime.datetime.strptime(st[:10], '%Y-%m-%d')).days
                if d > 1:
                    issues.append(('跨字段', 'publishTime', '中',
                                   f'发布日期晚于讲座日 {d} 天（疑似回顾稿/新闻）', f'{pt[:10]} vs {st[:10]}', r))
            except Exception:
                pass

    return issues


def build_html(recs, issues, out_path):
    by_cat = collections.defaultdict(list)
    for it in issues:
        by_cat[it[0]].append(it)

    sev_rank = {'高': 0, '中': 1, '低': 2}
    for k in by_cat:
        by_cat[k].sort(key=lambda x: (sev_rank.get(x[2], 9), x[3]))

    missing = by_cat.get('缺失', [])
    pollute = [it for it in issues if it[0] != '缺失']
    total_high = sum(1 for it in pollute if it[2] == '高')
    total_mid = sum(1 for it in pollute if it[2] == '中')
    # 涉及多少条不同记录（仅污染类）
    n_recs = len({it[5].get('sourceUrl') for it in pollute})
    n_missing = len(missing)
    # 可修性统计（仅针对污染类；缺失类统一归为 nfix，在统计区单独说明）
    fx_cnt = collections.Counter()
    for it in pollute:
        k, _ = fixability(it[0], it[3], it[4])
        fx_cnt[k] += 1

    CSS = """
    body{font-family:"Microsoft YaHei",sans-serif;max-width:1200px;margin:24px auto;
         padding:0 16px;color:#1a1a1a;background:#fff;line-height:1.6}
    h1{font-size:22px;margin-bottom:6px}
    .sub{color:#888;font-size:13px;margin-bottom:18px}
    h2{font-size:17px;margin-top:32px;border-left:4px solid #c33;padding-left:10px}
    .cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
    .card{flex:1;min-width:130px;border:1px solid #e5e5e5;border-radius:8px;padding:12px 14px;background:#fafafa}
    .card .n{font-size:24px;font-weight:700}
    .card .l{font-size:12px;color:#666;margin-top:2px}
    .card.h .n{color:#c33} .card.m .n{color:#c98a00} .card.k .n{color:#0a7d3e}
    table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}
    th,td{border:1px solid #e0e0e0;padding:7px 9px;text-align:left;vertical-align:top}
    th{background:#f5f5f5;font-weight:600;position:sticky;top:0}
    tr:nth-child(even) td{background:#fcfcfc}
    .sev{font-weight:700;white-space:nowrap}
    .sev.高{color:#c33} .sev.中{color:#c98a00}
    .val{color:#333;word-break:break-all;max-width:430px}
    .mini{color:#888;font-size:12px}
    a{color:#1668dc;text-decoration:none} a:hover{text-decoration:underline}
    .chk{width:16px;height:16px;cursor:pointer}
    .bar{position:sticky;top:0;background:#fff;padding:10px 0;border-bottom:1px solid #eee;
         margin-bottom:10px;z-index:5}
    button{font-family:inherit;font-size:13px;padding:6px 14px;border:1px solid #ccc;
           background:#fff;border-radius:5px;cursor:pointer;margin-right:8px}
    button:hover{background:#f0f0f0}
    .note{background:#fffbe6;border:1px solid #ffe58f;padding:10px 14px;
          border-radius:6px;margin:14px 0;font-size:13px}
    h3{font-size:15px;margin:20px 0 6px}
    .fx-a{color:#0a7d3e} .fx-m{color:#c98a00} .fx-n{color:#999}
    h3.fx-a{border-left:4px solid #0a7d3e;padding-left:8px}
    h3.fx-m{border-left:4px solid #c98a00;padding-left:8px}
    h3.fx-n{border-left:4px solid #bbb;padding-left:8px}
    .fxtag{font-weight:700;font-size:12px;white-space:nowrap}
    details{margin:14px 0} summary{cursor:pointer;font-weight:600;color:#1668dc;
            padding:6px 0}
    .adv{color:#666;font-size:12px}
    """

    parts = ['<!doctype html><html lang="zh"><head><meta charset="utf-8">',
             '<title>讲座数据质量专项扫描报告</title><style>%s</style></head><body>' % CSS]
    parts.append('<h1>讲座数据质量专项扫描报告</h1>')
    parts.append('<div class="sub">数据源 data/lectures.json · 共 %d 条记录 · 生成时间 %s</div>'
                 % (len(recs), datetime.datetime.now().strftime('%Y-%m-%d %H:%M')))

    parts.append('<div class="cards">')
    parts.append('<div class="card h"><div class="n">%d</div><div class="l">高优先级问题</div></div>' % total_high)
    parts.append('<div class="card m"><div class="n">%d</div><div class="l">中优先级问题</div></div>' % total_mid)
    parts.append('<div class="card k"><div class="n">%d</div><div class="l">可自动修</div></div>'
                 % fx_cnt.get('auto', 0))
    parts.append('<div class="card m"><div class="n">%d</div><div class="l">需人工核对</div></div>'
                 % fx_cnt.get('manual', 0))
    parts.append('<div class="card"><div class="n">%d</div><div class="l">无法修/无需修</div></div>'
                 % fx_cnt.get('nfix', 0))
    parts.append('<div class="card"><div class="n">%d</div><div class="l">字段缺失项</div></div>' % n_missing)
    parts.append('</div>')

    parts.append('<div class="note"><b>分级说明</b>：'
                 '<span class="sev 高">高</span> = 值内容被明显污染（正文残段、职称混入姓名、结束时间跨天等），建议修正；'
                 '<span class="sev 中">中</span> = 疑似问题或轻度不规范，需人工确认。'
                 '每行「当前值」即数据库里的原始内容，点「源页」可直接打开原通知核对。</div>')
    parts.append('<div class="note"><b>可修性图例</b>：'
                 '<span class="fxtag fx-a">可自动修</span> = 规则明确、可批量清洗，误伤风险低；'
                 '<span class="fxtag fx-m">需人工核对</span> = 能修，但要回源页确认真实值，'
                 '自动补全有臆造风险；'
                 '<span class="fxtag fx-n">无法修 / 无需修</span> = 源页本身未提供该信息，'
                 '或当前值是合规写法（并非错误）。'
                 '每个类别下先给「问题类型 → 条数 → 修复建议」，再逐条列出当前值与源页链接。</div>')

    parts.append('<div class="bar"><button onclick="selAll(true)">全选</button>'
                 '<button onclick="selAll(false)">全不选</button>'
                 '<button onclick="expSel()">导出选中为 CSV</button>'
                 '<span id="cnt" class="mini"></span></div>')

    order = ['主讲人', '单位', '时间', '地点', '跨字段']
    for cat in order:
        lst = by_cat.get(cat, [])
        if not lst:
            continue
        nh = sum(1 for x in lst if x[2] == '高')
        parts.append('<h2>%s（%d 项，其中高优先级 %d）</h2>' % (cat, len(lst), nh))

        # 本类可修性概览
        fxstat = collections.Counter()
        for x in lst:
            k, _ = fixability(x[0], x[3], x[4])
            fxstat[k] += 1
        segs = []
        for k in ('auto', 'manual', 'nfix'):
            if fxstat.get(k):
                lab, cls = FIX_LABEL[k]
                segs.append('<span class="fxtag %s">%s %d 条</span>' % (cls, lab, fxstat[k]))
        parts.append('<div class="note">本类构成：' + '　'.join(segs) + '</div>')

        for k in ('auto', 'manual', 'nfix'):
            sub = [x for x in lst if fixability(x[0], x[3], x[4])[0] == k]
            if not sub:
                continue
            lab, cls = FIX_LABEL[k]
            parts.append('<h3 class="%s">%s（%d 条）</h3>' % (cls, lab, len(sub)))

            # 档内「问题类型 → 条数 → 修复建议」聚合（把数字归一，便于归并同一类问题）
            agg = collections.Counter()
            advice_of = {}
            for x in sub:
                key = re.sub(r'\d+', 'N', x[3])
                agg[key] += 1
                advice_of[key] = fixability(x[0], x[3], x[4])[1]
            parts.append('<table><tr><th style="width:190px">问题类型</th>'
                         '<th style="width:56px">条数</th><th>修复建议</th></tr>')
            for key, n in agg.most_common():
                parts.append('<tr><td>%s</td><td><b>%d</b></td><td class="adv">%s</td></tr>'
                             % (html.escape(key), n, html.escape(advice_of[key])))
            parts.append('</table>')

            # 逐条明细：每条都带源页链接，便于人工回源核对
            parts.append('<table><tr><th style="width:34px"></th><th style="width:44px">级别</th>'
                         '<th style="width:150px">问题</th><th class="val">当前值</th>'
                         '<th style="width:120px">学院</th><th style="width:110px">主讲人</th>'
                         '<th style="width:100px">讲座时间</th><th style="width:60px">源页</th></tr>')
            for _, field, sev, desc, val, r in sub:
                url = r.get('sourceUrl') or ''
                idx = r.get('__dbg_idx', '')
                esc = lambda x: html.escape(str(x or ''))
                parts.append(
                    '<tr data-url="%s" data-cat="%s" data-sev="%s" data-fix="%s" data-desc="%s">'
                    '<td><input type="checkbox" class="chk"></td>'
                    '<td><span class="sev %s">%s</span></td>'
                    '<td>%s<div class="mini">数据 idx %s</div></td>'
                    '<td class="val">%s</td>'
                    '<td>%s</td><td>%s</td><td>%s</td>'
                    '<td><a href="%s" target="_blank">源页 ↗</a></td></tr>'
                    % (esc(url), esc(cat), esc(sev), esc(lab), esc(desc),
                       esc(sev), esc(sev), esc(desc), esc(idx), esc(val[:300]),
                       esc(r.get('college')), esc(r.get('speaker')),
                       esc((r.get('lectureStart') or '')[:10]), esc(url))
                )
            parts.append('</table>')

    # ---------- 缺失统计区（只给分布，不逐条列：缺失项无「当前值」可核对）----------
    if missing:
        per_field = collections.Counter(it[1] for it in missing)
        per_college = collections.defaultdict(collections.Counter)
        for it in missing:
            per_college[it[5].get('college') or '(未标)'][it[1]] += 1
        parts.append('<h2>字段缺失统计（%d 项，仅列分布）</h2>' % n_missing)
        parts.append('<div class="note">缺失项没有「当前值」可核对，先用分布判断规模；'
                     '下方折叠区给出逐条明细与源页链接，便于人工回源补录。'
                     '<b>注意：若源页本身就没写该字段，则确实无法修复</b>，只能保持为空 '
                     '（这类归为「无法修 / 无需修」）。</div>')
        parts.append('<table><tr><th>缺失字段</th><th>缺失条数</th><th>占总记录比</th></tr>')
        FIELD_CN = {'speaker': '主讲人', 'speakerAffiliation': '主讲人单位',
                    'lectureStart': '讲座开始时间', 'location': '讲座地点'}
        for f, n in per_field.most_common():
            parts.append('<tr><td>%s</td><td><b>%d</b></td><td>%.1f%%</td></tr>'
                         % (FIELD_CN.get(f, f), n, 100.0 * n / len(recs)))
        parts.append('</table>')

        col_rank = sorted(per_college.items(),
                          key=lambda kv: -sum(kv[1].values()))[:12]
        parts.append('<table style="margin-top:14px"><tr><th>学院</th>' +
                     ''.join('<th>%s</th>' % FIELD_CN.get(f, f)
                             for f, _ in per_field.most_common()) +
                     '<th>合计</th></tr>')
        fields_order = [f for f, _ in per_field.most_common()]
        for col, cc in col_rank:
            tot = sum(cc.values())
            parts.append('<tr><td>%s</td>' % html.escape(str(col)) +
                         ''.join('<td>%d</td>' % cc.get(f, 0) for f in fields_order) +
                         '<td><b>%d</b></td></tr>' % tot)
        parts.append('</table>')

        # 逐条明细（默认折叠）：缺失项虽无「当前值」，仍需给链接供人工回源补录
        parts.append('<details><summary>展开逐条缺失明细（%d 条，含源页链接，供人工补录核对）</summary>'
                     % n_missing)
        for f in fields_order:
            sub = [it for it in missing if it[1] == f]
            if not sub:
                continue
            parts.append('<h3 class="fx-n">%s 缺失（%d 条）</h3>' % (FIELD_CN.get(f, f), len(sub)))
            parts.append('<table><tr><th style="width:34px"></th><th style="width:44px">级别</th>'
                         '<th style="width:150px">问题</th><th class="val">当前值（标题）</th>'
                         '<th style="width:120px">学院</th><th style="width:110px">主讲人</th>'
                         '<th style="width:100px">讲座时间</th><th style="width:60px">源页</th></tr>')
            for _, _, sev, desc, val, r in sub:
                url = r.get('sourceUrl') or ''
                esc = lambda x: html.escape(str(x or ''))
                parts.append(
                    '<tr data-url="%s" data-cat="缺失" data-sev="低" '
                    'data-fix="无法修 / 无需修" data-desc="%s">'
                    '<td><input type="checkbox" class="chk"></td>'
                    '<td><span class="sev">低</span></td>'
                    '<td>%s</td>'
                    '<td class="val">%s</td>'
                    '<td>%s</td><td>%s</td><td>%s</td>'
                    '<td><a href="%s" target="_blank">源页 ↗</a></td></tr>'
                    % (esc(url), esc(desc), esc(desc),
                       esc((r.get('title') or r.get('topic') or '')[:120]),
                       esc(r.get('college')), esc(r.get('speaker')),
                       esc((r.get('lectureStart') or '')[:10]), esc(url))
                )
            parts.append('</table>')
        parts.append('</details>')

    parts.append("""
<script>
function rows(){return Array.from(document.querySelectorAll('tr[data-url]'))}
function selAll(v){rows().forEach(r=>r.querySelector('.chk').checked=v);upd()}
function upd(){const n=rows().filter(r=>r.querySelector('.chk').checked).length;
 document.getElementById('cnt').textContent='已选中 '+n+' 项';}
function expSel(){
 const sel=rows().filter(r=>r.querySelector('.chk').checked);
 if(!sel.length){alert('请先勾选要导出的行');return}
 const q=s=>'"'+String(s).replace(/"/g,'""')+'"';
 let csv=['可修性,类别,级别,问题,当前值,学院,主讲人,讲座时间,源页'].join(',')+'\\n';
 sel.forEach(r=>{const d=r.dataset;
  const tds=r.querySelectorAll('td');
  const val=tds[3].innerText.replace(/\\s+/g,' ').trim();
  csv+=[q(d.fix),q(d.cat),q(d.sev),q(d.desc),q(val),q(tds[4].innerText),q(tds[5].innerText),
        q(tds[6].innerText),q(d.url)].join(',')+'\\n'});
 const b=new Blob(['\\ufeff'+csv],{type:'text/csv;charset=utf-8'});
 const a=document.createElement('a');a.href=URL.createObjectURL(b);
 a.download='讲座数据问题清单.csv';a.click();
}
document.addEventListener('change',e=>{if(e.target.classList.contains('chk'))upd()});
upd();
</script>
""")
    parts.append('</body></html>')

    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts))
    return out_path, total_high, total_mid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--html', default=os.path.join(ROOT, 'reports', 'data_quality_report.html'))
    ap.add_argument('--json', default='')
    args = ap.parse_args()

    data = json.load(open(DATA_PATH, encoding='utf-8'))
    recs = data['data']
    for i, r in enumerate(recs):
        r['__dbg_idx'] = i

    issues = scan(recs)
    out, nh, nm = build_html(recs, issues, args.html)

    print('扫描记录: %d 条' % len(recs))
    print('问题项: %d （高 %d / 中 %d）' % (len(issues), nh, nm))
    cnt = collections.Counter((it[0], it[2]) for it in issues)
    for (cat, sev), n in sorted(cnt.items()):
        print('  %-6s %s : %d' % (cat, sev, n))
    print('HTML 报告: %s' % out)

    if args.json:
        payload = []
        for c, f, s, d, v, r in issues:
            k, adv = fixability(c, d, v)
            payload.append({'cat': c, 'field': f, 'sev': s, 'desc': d, 'value': v,
                            'fixKind': k, 'fix': FIX_LABEL[k][0], 'advice': adv,
                            'url': r.get('sourceUrl'), 'college': r.get('college'),
                            'speaker': r.get('speaker'), 'start': r.get('lectureStart'),
                            'idx': r.get('__dbg_idx')})
        os.makedirs(os.path.dirname(args.json) or '.', exist_ok=True)
        json.dump(payload, open(args.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print('JSON 清单: %s' % args.json)


if __name__ == '__main__':
    main()
