"""将 ctld/4290（实为表格多讲座页，非海报）由单条 VLM 记录定向拆分为 3 场。

仅修改 sourceUrl==4290 这一条记录，其余记录原样保留（符合已清洗数据保护铁律）。
文本信息来自页面表格：专题一/二/三 各自的日期+起止时间、课程内容（topic）。
"""
import json
import datetime

PATH = 'data/lectures.json'
URL = 'http://ctld.scnu.edu.cn/a/20240325/4290.html'

d = json.load(open(PATH, encoding='utf-8'))
recs = d['data']
idxs = [i for i, r in enumerate(recs) if r.get('sourceUrl') == URL]
assert len(idxs) == 1, f'期望唯一匹配 4290，实际 {idxs}'
pos = idxs[0]
old = recs[pos]

# 基础字段：复制原记录，清理 VLM/海报标记
base = dict(old)
for k in ('__idx', 'vlmExtracted', 'ocrExtracted', 'hasPosterImage'):
    base.pop(k, None)
base['imageParseMethod'] = 'none'
base['images'] = []
base['sessionNumber'] = ''
base['isMultiLecture'] = True
base['lectureCount'] = 3
# 文本抽取修正/补全
base['location'] = '华南师范大学广州校区石牌校园教师发展中心（校史文博馆楼）3楼308室'
base['speakerTitle'] = '教授'
base['speakerAffiliation'] = '北京大学教育学院'

# 表格逐场：topic（课程内容）、lectureStart、lectureEnd
SESSIONS = [
    ('质性研究问题的提出', '2024-03-26 14:30:00', '2024-03-26 17:30:00'),
    ('质性研究资料的收集', '2024-04-02 14:30:00', '2024-04-02 17:30:00'),
    ('质性研究的资料分析', '2024-04-16 14:30:00', '2024-04-16 17:30:00'),
]

new_recs = []
for i, (topic, st, en) in enumerate(SESSIONS, 1):
    r = dict(base)
    r['lectureIndex'] = i
    r['topic'] = topic
    r['lectureStart'] = st
    r['lectureEnd'] = en
    new_recs.append(r)

recs[pos:pos + 1] = new_recs
d['data'] = recs
d['updatedAt'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

json.dump(d, open(PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print(f'OK: 4290 拆分为 {len(new_recs)} 场；数据总数 {len(recs)}')
for r in new_recs:
    print(f"  [{r['lectureIndex']}] {r['topic']} | {r['lectureStart']} ~ {r['lectureEnd']} | {r['location']}")
