# -*- coding: utf-8 -*-
"""全库字段质量审计：时间 / 地点 / 讲座人 / 单位的错误与缺失清单。

检查维度：
  时间    —— 缺失、格式异常、超出合理域（2000~当前+2年）、lectureEnd<start、
             发布时间晚于讲座时间超过 1 年（年份错位信号）
  地点    —— 缺失、过长（>50字）、含页脚/导航特征、以标签残段结尾
  讲座人  —— 缺失（细分"疑似漏抓"与"疑似真空"，依据 bio/标题的人名信号）、
             含括号/职称/单位污染、过长
  单位    —— speaker 非空但单位缺失、单位含职称词、单位与主讲人同名、粘连英文

空讲座人真假判定启发（不联网，纯库内信号）：
  疑似漏抓 = speakerBio 非空且开头像"姓名，单位"，或 title 含"姓名+职称"形态，
             或含合并来源（曾经有值）/ qaRepaired（修复过但字段仍空）
  疑似真空 = bio 与标题均无人名信号（通知/论坛/征稿类页面本无单一主讲人）

用法：
  python scripts/audit_fields.py                 # 控制台汇总
  python scripts/audit_fields.py --html out.html # 另存 HTML 明细报告
"""
import os
import re
import sys
import json
import argparse
import datetime
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

sys.stdout.reconfigure(encoding='utf-8')

_TODAY = datetime.date.today()
_MIN_DATE = datetime.date(2000, 1, 1)
_MAX_DATE = datetime.date(_TODAY.year + 2, 12, 31)

_TITLE_NAME_RE = re.compile(r'[\u4e00-\u9fff]{2,4}(?:教授|副教授|研究员|讲师|博士|老师|院士|先生|女士)')
_BIO_HEAD_NAME_RE = re.compile(
    r'^[\u4e00-\u9fff·]{2,4}\s*[,，,]\s*|'
    r'^[\u4e00-\u9fff]{2,4}\s*[（(]|'
    r'^(?:Professor|Dr\.?|Mr\.?|Ms\.?|Mrs\.?)\s+[A-Z][A-Za-z]+')
_LOC_BAD = re.compile(r'版权所有|首页|联系我们|地址：广州市|邮编|Copyright|粛ICP|粤ICP|点击')
_LOC_TAIL_TAG = re.compile(r'(?:报告|讲座|主讲人|报告人|时间|地点)$')
_AFFIL_TITLE_ONLY = re.compile(r'^(?:特聘教授|特任教授|助理教授|副教授|副研究员|助理研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士)+$')
_EN_GLUE = re.compile(r'(?<![A-Z])[a-z]{2,}[A-Z]')     # camelCase 粘连（SeoulNational），排除 UNLV/SIUE 全大写缩写
_NAV_CHAIN = re.compile(
    r'(?:学术活动|科研项目|科研成果|科研平台|研究方向|重大项目|学科方向|'
    r'人才招聘|本科生教育|研究生教育|党建工作){2,}')


def _iso(s):
    try:
        return datetime.datetime.fromisoformat(str(s))
    except Exception:
        return None


def audit(recs):
    issues = []   # (类型, 字段, 严重度, 描述, 记录)

    for r in recs:
        url = r.get('sourceUrl', '')
        idx = r.get('lectureIndex')
        rid = f"{url}{'#' + str(idx) if idx else ''}"
        college = r.get('college', '')

        # ---------- 时间 ----------
        ls_raw = r.get('lectureStart')
        ls = _iso(ls_raw) if ls_raw else None
        if not ls_raw or not ls:
            issues.append(('时间', 'lectureStart', '中', '讲座时间缺失或无法解析', r))
        else:
            d = ls.date()
            if d < _MIN_DATE or d > _MAX_DATE:
                issues.append(('时间', 'lectureStart', '高',
                               f'超出合理域 {ls_raw}', r))
            le = _iso(r.get('lectureEnd')) if r.get('lectureEnd') else None
            if le and le < ls:
                issues.append(('时间', 'lectureEnd', '高',
                               f'结束时间早于开始时间 ({r.get("lectureEnd")} < {ls_raw})', r))
            pub = _iso(r.get('publishTime')) if r.get('publishTime') else None
            if pub and pub.date() > d and (pub.date() - d).days > 365:
                issues.append(('时间', 'lectureStart', '中',
                               f'发布晚于讲座 {(pub.date() - d).days} 天（年份错位信号）pub={r.get("publishTime")}', r))

        # ---------- 地点 ----------
        loc = (r.get('location') or '').strip()
        if not loc:
            issues.append(('地点', 'location', '中', '地点缺失', r))
        else:
            if len(loc) > 50:
                issues.append(('地点', 'location', '中', f'过长({len(loc)}字): {loc[:40]}…', r))
            if _LOC_BAD.search(loc):
                issues.append(('地点', 'location', '高', f'含页脚/导航特征: {loc[:40]}', r))
            if _LOC_TAIL_TAG.search(loc) and len(loc) <= 12:
                issues.append(('地点', 'location', '中', f'疑似标签残段: {loc}', r))

        # ---------- 讲座人 ----------
        spk = (r.get('speaker') or '').strip()
        bio = (r.get('speakerBio') or '').strip()
        title = (r.get('title') or '')
        if not spk:
            signals = []
            if bio and _BIO_HEAD_NAME_RE.match(bio):
                signals.append('bio开头像"姓名,单位"')
            if _TITLE_NAME_RE.search(title):
                signals.append('标题含"姓名+职称"')
            if r.get('sources'):
                signals.append('有合并来源')
            if r.get('qaRepaired'):
                signals.append('修复过但仍空')
            if bio and _NAV_CHAIN.search(bio[:60]):
                kind, sev = '疑似真空(页面为通知/导航页)', '低'
            elif signals:
                kind, sev = '疑似漏抓', '高' if ('bio开头' in ''.join(signals) or '标题含' in ''.join(signals)) else '中'
            else:
                kind, sev = '疑似真空(无信号)', '低'
            issues.append(('讲座人', 'speaker', sev,
                           f'{kind}' + (f'（信号: {"; ".join(signals)}）' if signals else ''), r))
        else:
            _has_latin = bool(re.search(r'[A-Za-z]', spk))
            _has_unit_word = bool(re.search(
                r'University|College|Department|Institute|School|Laboratory|'
                r'大学|学院|研究院|研究所|系|中心|实验室', spk))
            if _has_latin and _has_unit_word:
                issues.append(('讲座人', 'speaker', '高', f'含单位词: {spk}', r))
            elif (not _has_latin and len(spk) > 5) or (_has_latin and len(spk) > 30):
                issues.append(('讲座人', 'speaker', '中', f'过长({len(spk)}字): {spk}', r))
            if re.search(r'[（(]', spk):
                issues.append(('讲座人', 'speaker', '高', f'含括号(职称/单位粘入): {spk}', r))
            if _AFFIL_TITLE_ONLY.fullmatch(spk):
                issues.append(('讲座人', 'speaker', '高', f'纯职称词: {spk}', r))
            if _NAV_CHAIN.search(spk):
                issues.append(('讲座人', 'speaker', '高', '含导航串', r))

        # ---------- 单位 ----------
        aff = (r.get('speakerAffiliation') or '').strip()
        if spk and not aff:
            pass  # 单位缺失常见（页面只写姓名），不列为问题；统计口径单独输出
        if aff:
            if _AFFIL_TITLE_ONLY.fullmatch(aff):
                issues.append(('单位', 'speakerAffiliation', '高', f'纯职称词: {aff}', r))
            if _EN_GLUE.search(aff):
                issues.append(('单位', 'speakerAffiliation', '中', f'英文粘连: {aff}', r))
            if spk and aff.strip(' ()（）') == spk:
                issues.append(('单位', 'speakerAffiliation', '高', '与主讲人同名', r))

    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--html', default=None, help='输出 HTML 明细报告路径')
    args = ap.parse_args()

    data = json.load(open(os.path.join(ROOT, 'data', 'lectures.json'), encoding='utf-8'))['data']
    issues = audit(data)

    by_cat = Counter((i[0], i[2]) for i in issues)
    print(f'审计范围: {len(data)} 条记录')
    print('=' * 60)
    for (cat, sev), n in sorted(by_cat.items()):
        print(f'  [{sev}] {cat}: {n}')
    print(f'  合计: {len(issues)}')

    # 空 speaker 真假判定汇总
    empty_spk = [i for i in issues if i[0] == '讲座人' and 'speaker' in i[2] or
                 (i[0] == '讲座人' and i[3].startswith(('疑似',)))]
    kinds = Counter(i[3].split('（')[0] for i in issues if i[0] == '讲座人')
    print()
    print('空讲座人真假分布:')
    for k, n in kinds.most_common():
        print(f'  {k}: {n}')

    if args.html:
        _write_html(args.html, data, issues)
        print(f'\nHTML 明细报告: {args.html}')


def _write_html(path, recs, issues):
    by_cat = Counter((i[0], i[2]) for i in issues)
    rows = []
    for cat, fld, sev, desc, r in issues:
        u = r.get('sourceUrl', '')
        idx = r.get('lectureIndex')
        rid = u + (f'#{idx}' if idx else '')
        spk = r.get('speaker') or ''
        aff = r.get('speakerAffiliation') or ''
        loc = r.get('location') or ''
        ls = r.get('lectureStart') or ''
        bio_head = (r.get('speakerBio') or '')[:40]
        rows.append(
            f'<tr><td>{cat}</td><td>{sev}</td>'
            f'<td><a href="{u}" target="_blank">{rid[-42:]}</a></td>'
            f'<td>{r.get("college","")}</td><td>{fld}</td><td>{desc}</td>'
            f'<td>spk={spk!r}<br>aff={aff!r}<br>loc={loc[:24]!r}<br>time={ls}<br>bio头={bio_head!r}</td></tr>')

    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<title>全库字段质量审计报告</title><style>'
        'body{font-family:微软雅黑;margin:24px;color:#222;line-height:1.6;font-size:13px}'
        'h2{border-bottom:2px solid #c44;padding-bottom:8px}'
        'table{border-collapse:collapse;width:100%}'
        'th,td{border:1px solid #ccc;padding:6px 8px;vertical-align:top;text-align:left}'
        'th{background:#fdf0f0;position:sticky;top:0}'
        '.box{background:#f6f8fa;border-left:4px solid #c44;padding:10px 16px;margin:12px 0}'
        '</style></head><body>'
        '<h2>全库字段质量审计 · ' + str(len(issues)) + ' 条 / ' + str(len(recs)) + ' 条记录</h2>'
        '<div class="box"><b>按类型×严重度：</b><br>' +
        '<br>'.join(f'{c[0]} [{c[1]}]: <b>{n}</b>' for c, n in sorted(by_cat.items())) +
        '</div>'
        '<table><tr><th>类型</th><th>严重度</th><th>记录</th><th>学院</th>'
        '<th>字段</th><th>问题描述</th><th>字段现值</th></tr>' +
        '\n'.join(rows) + '</table></body></html>'
    )
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)


if __name__ == '__main__':
    main()
