"""对 ctld/4290 走纯文本抽取（关闭 VLM），验证表格多讲座是否能被拆成 3 场。

用法（在仓库根目录运行）：
  D:/Tools/Python 312/python.exe tmp/reparse_4290_text.py tmp/4290.html
  # 或把页面 HTML 另存到其它路径后传入

说明：
- 强制 _load_vlm_config -> None，使 parse_detail 完全不走 VLM/海报路径，纯文本+表格拆分。
- 打印 parse_detail 返回的每条记录关键字段，便于人工核对 3 场时间/题目。
"""
import json
import sys

sys.path.insert(0, 'scraper')
import parsers as P

# 强制关闭 VLM：海报页也会降级到 OCR/纯文本，表格页直接走文本多讲座拆分
P._load_vlm_config = lambda: None

URL = 'http://ctld.scnu.edu.cn/a/20240325/4290.html'
COLLEGE = '教师发展中心'
CAMPUS = '校级'
DEFAULT_YEAR = 2024

html_path = sys.argv[1] if len(sys.argv) > 1 else 'tmp/4290.html'
with open(html_path, encoding='utf-8', errors='ignore') as f:
    html = f.read()

recs = P.parse_detail(html, URL, COLLEGE, CAMPUS, default_year=DEFAULT_YEAR)
print('return type:', type(recs).__name__)
if recs is None:
    print('=> 被新闻过滤或其它规则丢弃，无记录')
elif isinstance(recs, list):
    print(f'=> 多讲座拆分，共 {len(recs)} 场')
    for i, r in enumerate(recs, 1):
        print(f'--- 第 {i} 场 ---')
        for k in ('title', 'topic', 'lectureStart', 'lectureEnd', 'location',
                  'speaker', 'speakerAffiliation', 'lectureIndex', 'lectureCount',
                  'isMultiLecture', 'imageParseMethod', 'vlmExtracted'):
            print(f'  {k}: {r.get(k)!r}')
else:
    print('=> 单场记录')
    for k in ('title', 'topic', 'lectureStart', 'lectureEnd', 'location',
              'speaker', 'imageParseMethod', 'vlmExtracted', 'isMultiLecture'):
        print(f'  {k}: {recs.get(k)!r}')
