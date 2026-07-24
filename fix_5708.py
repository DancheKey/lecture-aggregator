import sys, json, re, urllib.request as ureq, ssl
sys.path.insert(0, 'scraper')
import parsers

URL = 'http://cs.scnu.edu.cn/a/20240516/5708.html'
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE

html = ureq.urlopen(ureq.Request(URL, headers={'User-Agent': 'Mozilla/5.0'}), timeout=25, context=CTX).read().decode('utf-8', 'ignore')
recs = parsers.parse_detail(html, URL, '计算机学院', '石牌', default_year=2024)
recs = recs if isinstance(recs, list) else [recs]
assert len(recs) == 2, f'期望拆 2 条，实际 {len(recs)} 条'

# abstract 未按场隔离：两条都是整页全文，且 bio 混在 abstract。在此手动隔离。
full = recs[0].get('abstract', '') or ''

m1 = re.search(r'(.*?)(?:报告人1|报告2)', full, re.S)
abs1 = (m1.group(1).strip() if m1 else '').strip()
m2 = re.search(r'报告2\s*内容简介[:：]?\s*(.*?)(?:报告人2|$)', full, re.S)
abs2 = (m2.group(1).strip() if m2 else '').strip()

def bio_of(tag):
    m = re.search(tag + r'[:：]?\s*(.*?)(?=报告2|报告人2|$)', full, re.S)
    return m.group(1).strip() if m else ''
bio1 = bio_of('报告人1')
bio2 = bio_of('报告人2')

# 取现有记录的元数据
d = json.load(open('data/lectures.json', encoding='utf-8'))
arr = d['data']
meta = None
for it in arr:
    if '5708.html' in it.get('sourceUrl', ''):
        meta = {k: it[k] for k in ('college', 'campus', 'organizer', 'publishTime',
                                   'publishTimeSource', 'listTitle', 'timeConfidence',
                                   'timeNote', 'sourceCount') if k in it}
        break
assert meta, '未找到 5708 现有元数据'
# 删除旧的 5708（半成品 1 条）
arr = [it for it in arr if '5708.html' not in it.get('sourceUrl', '')]

for r, ab, bio in zip(recs, [abs1, abs2], [bio1, bio2]):
    r.update(meta)
    r['abstract'] = ab
    if bio:
        r['speakerBio'] = bio
    arr.append(r)

d['data'] = arr
json.dump(d, open('data/lectures.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('done, total', len(arr))
for r in recs:
    print(f"  场{r['lectureIndex']}: {r['speaker']}({r.get('speakerTitle')}/{r.get('speakerAffiliation')}) "
          f"| {r['lectureStart']}~{r['lectureEnd']} | abs_len={len(r['abstract'])} bio_len={len(r.get('speakerBio',''))}")
