import json
import subprocess
import html as html_mod


def load_json_from_git(rev, path):
    out = subprocess.run(['git', 'show', f'{rev}:{path}'], capture_output=True, text=True)
    return json.loads(out.stdout)


cur = json.load(open('data/lectures.json', encoding='utf-8'))
cur_by_key = {(r.get('sourceUrl', ''), r.get('lectureIndex')): r for r in cur['data']}

old = load_json_from_git('a348ac2', 'data/lectures.json')
old_by_key = {(r.get('sourceUrl', ''), r.get('lectureIndex')): r for r in old['data']}

only_old = set(old_by_key) - set(cur_by_key)
only_cur = set(cur_by_key) - set(old_by_key)

rows = []
for k in sorted(only_old, key=lambda x: (old_by_key[x].get('lectureStart', ''), old_by_key[x].get('speaker', ''))):
    r = old_by_key[k]
    reason = '跨源合并删除'
    if r.get('sourceUrl') == 'http://sfs.scnu.edu.cn/a/20190520/2359.html' and r.get('lectureIndex') == 0:
        reason = 'sfs/2359 lectureIndex 0→1 调整（非删除）'
    rows.append((r, reason, 'minus'))

for k in sorted(only_cur, key=lambda x: (cur_by_key[x].get('lectureStart', ''), cur_by_key[x].get('speaker', ''))):
    r = cur_by_key[k]
    reason = 'CI 新增保留'
    if r.get('sourceUrl') == 'http://sfs.scnu.edu.cn/a/20190520/2359.html' and r.get('lectureIndex') == 4:
        reason = 'sfs/2359 lectureIndex 0→1 调整（对应旧 idx=3 改为 4）'
    rows.append((r, reason, 'plus'))


def h(s):
    return html_mod.escape(str(s) if s is not None else '')


lines = [
    '<!DOCTYPE html>',
    '<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>3120 vs 3109 变化清单</title>',
    '<style>',
    'body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;padding:24px;background:#f6f7fb;color:#1f2330}',
    'h1{font-size:20px;margin:0 0 4px}',
    'h2{font-size:16px;margin:20px 0 8px;color:#2f3b52}',
    '.meta{color:#666;font-size:13px;margin-bottom:16px}',
    'table{border-collapse:collapse;width:100%;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:20px}',
    'th,td{text-align:left;padding:8px 10px;font-size:13px;border-bottom:1px solid #eef0f4;vertical-align:top}',
    'th{background:#2f3b52;color:#fff;font-weight:600}',
    '.minus td{background:#fff0f0}',
    '.plus td{background:#f0fff0}',
    '.url a{color:#1a73e8;text-decoration:none}',
    '.url a:hover{text-decoration:underline}',
    '.mono{font-family:monospace;font-size:12px}',
    '.reason{color:#666;font-size:12px}',
    '.topic{color:#888;font-size:12px}',
    '</style></head><body>',
    '<h1>3120 vs 3109 变化清单</h1>',
    f'<div class="meta">当前 data/lectures.json {len(cur["data"])} 条，比 3120 版本（a348ac2）净减少 {len(only_old)-len(only_cur)} 条。其中 3120 独有 {len(only_old)} 条，3109 独有 {len(only_cur)} 条。</div>',
    '<table><tr><th>类型</th><th>日期</th><th>主讲</th><th>学院</th><th>标题</th><th>来源 URL</th><th>idx</th><th>原因</th></tr>',
]

for r, reason, typ in rows:
    cls = 'minus' if typ == 'minus' else 'plus'
    typ_label = '减少' if typ == 'minus' else '新增/调整'
    date = r.get('lectureStart', '')
    title = h(r.get('title', ''))
    topic = h((r.get('topic') or '')[:60])
    url = h(r.get('sourceUrl', ''))
    idx = '' if r.get('lectureIndex') is None else str(r.get('lectureIndex'))
    title_cell = f'{title}<br><span class="topic">{topic}</span>' if topic else title
    lines.append(
        f'<tr class="{cls}"><td>{typ_label}</td><td class="mono">{h(date)}</td><td>{h(r.get("speaker",""))}</td>'
        f'<td>{h(r.get("college",""))}</td><td>{title_cell}</td>'
        f'<td class="url"><a href="{url}" target="_blank">{url}</a></td><td>{idx}</td><td class="reason">{h(reason)}</td></tr>'
    )

lines += ['</table></body></html>']

with open('tmp/diff_3120_3109.html', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print(f'已生成 tmp/diff_3120_3109.html，净减少 {len(only_old)-len(only_cur)} 条')
