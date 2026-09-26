# -*- coding: utf-8 -*-
"""P3-1 数据杂项修复：lectureEnd 缺日期补全 + publishTime 补时刻 + em/6709 topic 错配。

1) lectureEnd 为「MM-DD HH:MM」（8条）或「HH:MM」（7条）——OCR/标签解析残留的
   无日期片段，用同记录 lectureStart 的日期补全（跨午夜场景源页未见，逐条核对
   月日一致性后才落库）。
2) publishTime 为纯日期「YYYY-MM-DD」（31条）→ 补「 00:00」统一为
   YYYY-MM-DD HH:MM。前端只用年份（app.js 仅 match 年），is_news_record 的
   字符串/时间比较语义不变；增量融合的 publishTime>= 比较在归一后更一致。
3) em/6709（华南经济论坛251期杨虎涛/252期张林）：源页两场题目不同
   「1、杨虎涛——赶超经济学传统…」「2、张林——经济学多元化运动：历史和趋势」，
   库内第2场 topic 误继承首场题目 → 按源页逐字修正（2026-09-26 实页核对）。

同批核对其余3组 topic 重复页均属仓库既有惯例，未改动：
  em/8855 联合报告拆两条（cf. d6476d0 physics/832 先例）、cs/4145 论坛三人
  各一条（连写拆分设计案例）、xz/289 同一活动4场次。

用法：python scripts/fix_time_formats.py [--apply]
"""
import os
import re
import sys
import json
import shutil
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')

URL_6709 = 'http://em.scnu.edu.cn/a/20181224/6709.html'
TOPIC_6709_2 = '张林教授——经济学多元化运动：历史和趋势'


def main():
    apply_mode = '--apply' in sys.argv
    data = json.load(open(DATA_PATH, encoding='utf-8'))
    recs = data['data']
    n_end = n_pub = 0
    for r in recs:
        end = r.get('lectureEnd') or ''
        start = r.get('lectureStart') or ''
        if end and re.match(r'^\d{2}-\d{2} \d{2}:\d{2}$', end) and start:
            # 「MM-DD HH:MM」：月日须与 start 一致才补年（防跨年/跨月错配）
            if end[:5] != start[5:10]:
                print(f'[WARN] end 月日与 start 不一致，跳过: {r.get("sourceUrl")} {start} / {end}')
                continue
            r['lectureEnd'] = f'{start[:4]}-{end}'
            n_end += 1
        elif end and re.match(r'^\d{2}:\d{2}$', end) and start:
            r['lectureEnd'] = f'{start[:10]} {end}'
            n_end += 1
        pt = r.get('publishTime') or ''
        if re.match(r'^\d{4}-\d{2}-\d{2}$', pt):
            r['publishTime'] = pt + ' 00:00'
            n_pub += 1

    n_topic = 0
    for r in recs:
        if r.get('sourceUrl') == URL_6709 and r.get('lectureIndex') == 2:
            if r.get('topic') != TOPIC_6709_2:
                r['topic'] = TOPIC_6709_2
                n_topic += 1

    print(f'lectureEnd 补日期: {n_end} 条 | publishTime 补时刻: {n_pub} 条 | em/6709 第2场 topic 修正: {n_topic} 条')
    if not apply_mode:
        print('演练结束（未落库）。加 --apply 落库。')
        return
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = f'{DATA_PATH}.bak-{ts}-timefix'
    shutil.copyfile(DATA_PATH, bak)
    print(f'已备份 → {bak}')
    data['updatedAt'] = datetime.datetime.now().isoformat(timespec='seconds')
    with open(DATA_PATH, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'落库完成，共 {len(recs)} 条')


if __name__ == '__main__':
    main()
