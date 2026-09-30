"""重解析 2 条脏数据的源页并回写字段（2026-09-30）。

⚠ 铁律：字段值必须取自源页原文 —— 故此处**不手工改值**，而是用当前解析器
重新解析源页，把产出的 speaker / speakerTitle / speakerAffiliation 回写。
只改这 3 个字段，其余字段保持不动（避免连带污染）。

用法：
  python scripts/apply_duty_suffix_fix.py           # dry-run，只打印差异
  python scripts/apply_duty_suffix_fix.py --apply   # 落库
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402

DATA = os.path.join(ROOT, 'data', 'lectures.json')
FIX = os.path.join(ROOT, 'tests', 'fixtures')

# 目标：sourceUrl 标识 → (fixture 文件, 学院, 校区, 年份)
TARGETS = {
    'https://module.scnu.edu.cn/article-3685-10806-1.html':
        ('spk_duty_module_3685.html', '经济与管理学院', '石牌', 2025),
    'http://iqm.scnu.edu.cn/a/20211122/189.html':
        ('spk_duty_iqm_189.html', '心理学院', '石牌', 2021),
}
FIELDS = ('speaker', 'speakerTitle', 'speakerAffiliation')


def _reparse(fname, url, college, campus, year):
    body = io.open(os.path.join(FIX, fname), encoding='utf-8').read()
    html = ('<html><head><title>讲座预告</title></head><body>' + body
            + '</body></html>')
    out = P.parse_detail(html, url, college, campus, year,
                         skip_news_filter=True)
    if isinstance(out, list):
        out = out[0] if out else {}
    return out or {}


def main():
    apply = '--apply' in sys.argv
    raw = io.open(DATA, encoding='utf-8').read()
    doc = json.loads(raw)
    rows = doc['data']

    changed = 0
    for row in rows:
        url = str(row.get('sourceUrl') or '')
        tgt = TARGETS.get(url)
        if not tgt:
            continue
        new = _reparse(tgt[0], url, tgt[1], tgt[2], tgt[3])
        print('源页: ' + url)
        for f in FIELDS:
            old = row.get(f)
            got = new.get(f)
            mark = '  ' if old == got else '→'
            print('  %s %-20s %r -> %r' % (mark, f, old, got))
            if old != got:
                row[f] = got
                changed += 1
        print('')

    if not changed:
        print('无字段变化，未写盘')
        return
    if not apply:
        print('[dry-run] 共 %d 个字段待改，加 --apply 落库' % changed)
        return

    out = json.dumps(doc, ensure_ascii=False, indent=2)
    # 保持原有行尾风格（LF），并原子写盘
    tmp = DATA + '.tmp'
    io.open(tmp, 'w', encoding='utf-8', newline='\n').write(out)
    os.replace(tmp, DATA)
    print('[apply] 已写回 %s，改动 %d 个字段' % (DATA, changed))


if __name__ == '__main__':
    main()