# -*- coding: utf-8 -*-
"""会议议程页拆分落地：把 2026-09-26 解析器升级（质量闸门/候选10泛化/候选11/候选1
多人异名兜底/表格真表头扫描）的拆分能力落到存量数据。

两类处理：
1. AUTO 页（升级后解析器可自动拆）：用当前链路重解析，替换原单条记录。
   —— em/3769(6) 3771(11) 4169(24) 7264(31) 7542(4)
2. MANUAL 页（解析器只能拆出部分结构，按 6769/6973/7635 人工拆分先例补全为完整议程）：
   —— em/5983 岭南经济论坛：32 场（上午4主题报告@10:10 + 25分组讨论@13:30 + 3获奖发言@16:25）
   —— em/5949 行为实验年会：12 场（3主题演讲 + 9分会场报告，分会场结构在 content 外
      无法被正文候选看到，人工补全）

铁律：改前备份；不触碰其他记录；拆分记录 lectureIndex 1..N 连续、lectureCount=N。
用法：python scripts/fix_conf_splits.py [--apply]（默认演练）
"""
import os
import re
import sys
import json
import shutil
import datetime
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from parsers import parse_detail  # noqa: E402

DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
CACHE = os.path.join(ROOT, 'tmp', 'conf_split_html')

SESSION = requests.Session()
SESSION.verify = False
SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})

# ── AUTO：重解析替换 ──────────────────────────────────────────────
AUTO_URLS = [
    'http://em.scnu.edu.cn/a/20150104/3769.html',
    'http://em.scnu.edu.cn/a/20150110/3771.html',
    'http://em.scnu.edu.cn/a/20160108/4169.html',
    'http://em.scnu.edu.cn/a/20191012/7264.html',
    'http://em.scnu.edu.cn/a/20191206/7542.html',
]
# 静态字段：拆分各场共享，从原记录继承（解析输出缺哪个补哪个）
_STATIC_KEYS = ('sourceUrl', 'images', 'college', 'campus', 'title', 'listTitle',
                'publishTime', 'publishTimeSource', 'organizer', 'newsFilterBypass')

# ── MANUAL：em/5983 岭南经济论坛 2017-12-02 ────────────────────────
# 上午主题报告 4 场（议程无各场独立时刻，共享环节窗 10:10-12:00 → 10:10 开场）
URL_5983 = 'http://em.scnu.edu.cn/a/20171202/5983.html'
LOC_5983_HALL = '华师图书馆后栋一楼国际会议厅'
KEYNOTES_5983 = [
    ('张曙光', '著名经济学家', '中国社会科学院', '新经济对经济学理论的挑战'),
    ('晏智杰', '著名经济学家', '北京大学经济学院', '坚持市场化改革方向,建设现代化经济体系'),
    ('董志强', '副院长', '华南师范大学经济与管理学院', '理解企业家精神和营商环境'),
    ('陶一桃', '副会长', '深圳大学', '中国三大湾区经济带比较及深圳在粤港澳大湾区经济带中的地位与作用'),
]
# 获奖作者代表主题发言 3 场（16:25-17:10 → 16:25 开场，每人15分钟）
AWARDS_5983 = [
    ('王鹏', '副教授', '暨南大学特区港澳经济研究所',
     '"一带一路"背景下粤港澳联合打造综合创新试验田研究'),
    ('罗明忠', '教授', '华南农业大学经济管理学院',
     '社会资本、风险容忍与农民创业组织形式选择'),
    ('郑方辉', '教授', '华南理工大学公共管理学院',
     '财政"股权投资":现状、矛盾与出路'),
]
# 分组讨论三组会场（议程：第一组 行政楼7楼第2会议室 / 第二组 第6 / 第三组 第5）
GROUP_LOCS = ('行政楼7楼第2会议室', '行政楼7楼第6会议室', '行政楼7楼第5会议室')
# 分组讨论各场归属（与议程源页逐字核对；共 25 场：8+9+8）
GROUP_SIZES = (8, 9, 8)

# ── MANUAL：em/5949 行为实验和计算经济学研讨会 2017-11-18 ──────────
URL_5949 = 'http://em.scnu.edu.cn/a/20171118/5949.html'
TALKS_5949 = [
    # (speaker, aff, topic, start, location) —— 按议程时刻排序后赋 lectureIndex
    ('Soo-Hong Chew', '新加坡国立大学',
     'Ellsberg meets Keynes: Missing links among attitudes toward sources of uncertainty',
     '09:10', '裕通大酒店二楼北美厅'),
    ('叶航', '浙江大学', '高阶社会困境及其破解——经验实证方法在社会科学中的应用',
     '10:15', '裕通大酒店二楼北美厅'),
    ('贺京同', '南开大学', '行为经济学与中国经济行为',
     '11:00', '裕通大酒店二楼北美厅'),
    ('张钰', '西南财经大学', 'Endogenous Fundamental and Stock Cycles',
     '13:30', '裕通大酒店四楼香港厅'),
    ('周晔馨', '北京师范大学', '成年人他涉偏好的演变及影响因素——基于独裁者实验的随机入户现场实验',
     '13:30', '裕通大酒店四楼澳门厅'),
    ('李建标', '南开大学', 'Transcranial Stimulation Over the Right Inferior Frontal Gyrus',
     '14:00', '裕通大酒店二楼欧洲厅'),
    ('王崎琦', '山东大学',
     'Gender differences, other-regarding preferences, and prescription behavior',
     '14:00', '裕通大酒店二楼南美厅'),
    ('王凯悦', '西南大学', '收费作为解决布雷斯悖论的最优策略--演化博弈视角',
     '16:15', '裕通大酒店二楼欧洲厅'),
    ('崔驰', '东北师范大学', '亲社会行为是否是理性的？基于显示偏好原理下独裁者博弈的实验研究',
     '16:15', '裕通大酒店二楼南美厅'),
    ('金淦', '中山大学',
     'Binomial trees used in valuation of contingent claims in a market with sudden breaks',
     '16:15', '裕通大酒店四楼香港厅'),
    ('于同奎', '西南大学', '社会规范的叠代演化模型及惩罚的作用',
     '16:15', '裕通大酒店四楼澳门厅'),
    ('黄湛冰', '陕西师范大学', '行为经济学助推在我国的运用前景不确定',
     '16:45', '裕通大酒店二楼南美厅'),
]


def fetch(url):
    os.makedirs(CACHE, exist_ok=True)
    dst = os.path.join(CACHE, re.sub(r'[^0-9A-Za-z]', '_', url) + '.html')
    if os.path.exists(dst) and os.path.getsize(dst) > 500:
        return open(dst, 'rb').read()
    resp = SESSION.get(url, timeout=20)
    open(dst, 'wb').write(resp.content)
    return resp.content


def build_manual(base, rows):
    """rows: [(topic, speaker, aff, title_holder, start, end, location)] → 记录列。"""
    out = []
    n = len(rows)
    for i, (topic, speaker, aff, stitle, start, end, loc) in enumerate(rows, 1):
        rec = {k: base.get(k) for k in _STATIC_KEYS}
        rec.update({
            'topic': topic, 'speaker': speaker, 'speakerAffiliation': aff,
            'speakerTitle': stitle, 'lectureStart': start, 'lectureEnd': end,
            'location': loc, 'speakerBio': '', 'inviter': '',
            'timeConfidence': 'high', 'timeNote': 'agenda-slot-inherited',
            'isMultiLecture': True, 'lectureIndex': i, 'lectureCount': n,
            'splitMode': 'manual', 'sourceCount': 0, 'notes': [],
            'speakerSource': 'manual-agenda',
        })
        out.append(rec)
    return out


def main():
    apply_mode = '--apply' in sys.argv
    data = json.load(open(DATA_PATH, encoding='utf-8'))
    recs = data['data']
    print(f'改前记录数: {len(recs)}  apply={apply_mode}')

    if apply_mode:
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        bak = f'{DATA_PATH}.bak-{ts}-confsplit'
        shutil.copyfile(DATA_PATH, bak)
        print(f'已备份 → {bak}')

    changed = 0
    # ── 1) MANUAL：em/5983（解析器分组行 + 人工主报告/获奖发言）──
    base5983 = next(r for r in recs if r.get('sourceUrl') == URL_5983)
    html = fetch(URL_5983)
    parsed = parse_detail(html, URL_5983, base5983.get('college'),
                          base5983.get('campus'), skip_news_filter=True)
    group_rows = [r for r in (parsed if isinstance(parsed, list) else [parsed])
                  if r.get('splitMode') == 'label-speakers']
    assert len(group_rows) == sum(GROUP_SIZES), \
        f'5983 分组行数异常: {len(group_rows)} (期望 {sum(GROUP_SIZES)})'
    rows5983 = []
    for spk, stitle, aff, topic in KEYNOTES_5983:
        rows5983.append((topic, spk, aff, stitle, '2017-12-02 10:10:00',
                         '2017-12-02 12:00:00', LOC_5983_HALL))
    gi = 0
    for gsize, gloc in zip(GROUP_SIZES, GROUP_LOCS):
        for r in group_rows[gi:gi + gsize]:
            rows5983.append((r['topic'], r.get('speaker') or '', '',
                             '', '2017-12-02 13:30:00', '2017-12-02 14:50:00', gloc))
        gi += gsize
    for spk, stitle, aff, topic in AWARDS_5983:
        rows5983.append((topic, spk, aff, stitle, '2017-12-02 16:25:00',
                         '2017-12-02 17:10:00', LOC_5983_HALL))
    new5983 = build_manual(base5983, rows5983)
    print(f'em/5983: 1 条 → {len(new5983)} 场（4主报告+25分组+3获奖）')
    show = [f'{r["lectureIndex"]}.{r["speaker"]}|{r["topic"][:14]}' for r in new5983[:3]]
    print('   预览:', ' '.join(show), '...')

    # ── 2) MANUAL：em/5949 ──
    base5949 = next(r for r in recs if r.get('sourceUrl') == URL_5949)
    rows5949 = [(topic, spk, aff, '', f'2017-11-18 {hm}:00', None, loc)
                for spk, aff, topic, hm, loc in TALKS_5949]
    # 议程时刻排序（13:30 两场按文档顺序保持稳定）
    rows5949.sort(key=lambda x: x[4])
    new5949 = build_manual(base5949, rows5949)
    print(f'em/5949: 1 条 → {len(new5949)} 场（3主题演讲+9分会场）')

    # ── 3) AUTO：重解析替换 ──
    auto_news = {}
    for url in AUTO_URLS:
        base = next(r for r in recs if r.get('sourceUrl') == url)
        html = fetch(url)
        parsed = parse_detail(html, url, base.get('college'), base.get('campus'),
                              skip_news_filter=True)
        plist = parsed if isinstance(parsed, list) else [parsed]
        assert len(plist) >= 2, f'{url} 重解析未拆出多场'
        merged = []
        for pr in plist:
            rec = dict(pr)
            for k in _STATIC_KEYS:
                if k not in rec or rec.get(k) in (None, ''):
                    rec[k] = base.get(k)
            merged.append(rec)
        auto_news[url] = merged
        print(f'{url.rsplit("/", 1)[-1]}: 1 条 → {len(merged)} 场 '
              f'[{sorted({r.get("splitMode") for r in merged})}]')

    if not apply_mode:
        print('演练结束（未落库）。加 --apply 落库。')
        return

    # 落库：删旧单条，按 sourceUrl 插入新场（保持原有相对位置）
    repl = {URL_5983: new5983, URL_5949: new5949, **auto_news}
    out = []
    seen = set()
    for r in recs:
        u = r.get('sourceUrl')
        if u in repl and u not in seen:
            out.extend(repl[u])
            seen.add(u)
        elif u not in repl:
            out.append(r)
    data['data'] = out
    data['updatedAt'] = datetime.datetime.now().isoformat(timespec='seconds')
    json.dump(data, open(DATA_PATH, 'w', encoding='utf-8'),
              ensure_ascii=False)
    print(f'落库完成: {len(recs)} → {len(out)} 条（+{len(out) - len(recs)}）')


if __name__ == '__main__':
    main()
