# -*- coding: utf-8 -*-
"""生成「站点访问量趋势」报告（自包含单文件 HTML）。

数据来源：
    data/visits_history.json —— 由 scripts/fetch_visits_snapshot.py 每日在 CI 中
    从 busuanzi 取快照累积而成（用 GET，不计数）。这是公网访问量的**唯一本地备份**：
    busuanzi 是外部免费服务，一旦它失效，累计访问量只在这个台账里还活着。

与旧的 gen_visits_report.py 的区别（勿混淆）：
    gen_visits_report.py  ← data/visits.json（**本地 localhost** 访问，server.py 写的）
    本脚本                ← data/visits_history.json（**公网**访问，CI 从 busuanzi 取的）

输出：
    reports/visits-trend.html
    - 自包含：数据内联进页面，双击即可查看（file:// 也行），无需服务器。
    - **入库**：.gitignore 只忽略旧的 visits-by-month.html，本报告可随仓库同步，
      便于在任意机器上 git pull 后直接查看。
    - 不会出现在公网页面上——GitHub Pages 只发布 site/ 目录。

用法：
    python scripts/gen_visits_trend.py
"""
import os
import json
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(ROOT, 'data', 'visits_history.json')
OUT_DIR = os.path.join(ROOT, 'reports')
OUT_PATH = os.path.join(OUT_DIR, 'visits-trend.html')

# 样式单独放常量：避免大括号与 f-string 冲突
CSS = """
body{margin:0;padding:32px 20px;background:#f8fafc;color:#0f172a;
  font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;line-height:1.6}
.wrap{max-width:900px;margin:0 auto}
h1{font-size:20px;font-weight:700;margin:0 0 6px}
.sub{color:#64748b;font-size:13px;margin-bottom:24px}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:28px}
.card{flex:1 1 160px;background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px}
.card .k{font-size:12px;color:#64748b}
.card .v{font-size:26px;font-weight:700;margin-top:4px}
h2{font-size:15px;font-weight:700;margin:28px 0 12px}
table{width:100%;border-collapse:collapse;background:#fff;
  border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;font-size:13px}
th,td{padding:8px 12px;text-align:right;border-bottom:1px solid #f1f5f9}
th{background:#f8fafc;font-weight:600;color:#475569}
td:first-child,th:first-child{text-align:left}
tr:last-child td{border-bottom:none}
.bar-row{display:flex;align-items:center;gap:10px;margin-bottom:6px;font-size:12px}
.bar-date{width:84px;color:#64748b;flex:none}
.bar-track{flex:1;background:#f1f5f9;border-radius:4px;height:16px;overflow:hidden}
.bar-fill{height:100%;background:#0ea5e9;border-radius:4px}
.bar-val{width:56px;text-align:right;color:#334155;flex:none}
.note{color:#b45309;font-size:12px}
.empty{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:24px;color:#64748b}
.foot{margin-top:32px;color:#94a3b8;font-size:12px}
"""


def load_snapshots():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, encoding='utf-8') as f:
            return json.load(f).get('snapshots') or []
    except (ValueError, OSError) as e:
        print(f'[warn] 台账读取失败：{type(e).__name__}: {e}')
        return []


def render_bars(snaps, limit=30):
    """近 N 天「新增访客」横向条形图。"""
    recent = [s for s in snaps if s.get('uv_delta') is not None][-limit:]
    if not recent:
        return '<div class="empty">暂无日增数据（需要至少两条快照才能算出日增）。</div>'
    max_v = max((int(s.get('uv_delta') or 0) for s in recent), default=0) or 1
    rows = []
    for s in recent:
        v = int(s.get('uv_delta') or 0)
        pct = int(v * 100 / max_v)
        rows.append(
            f'<div class="bar-row"><span class="bar-date">{s.get("date", "")}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{pct}%"></span></span>'
            f'<span class="bar-val">{v}</span></div>')
    return ''.join(rows)


def render_table(snaps):
    rows = []
    for s in reversed(snaps):
        note = s.get('note') or ''
        note_html = f'<span class="note">{note}</span>' if note else ''
        rows.append(
            '<tr>'
            f'<td>{s.get("date", "")}</td>'
            f'<td>{s.get("site_pv", "")}</td>'
            f'<td>{s.get("site_uv", "")}</td>'
            f'<td>{s.get("pv_delta", 0)}</td>'
            f'<td>{s.get("uv_delta", 0)}</td>'
            f'<td style="text-align:left">{note_html}</td>'
            '</tr>')
    return ''.join(rows)


def main():
    snaps = load_snapshots()
    if not snaps:
        print('[skip] 台账为空，未生成报告（先跑 fetch_visits_snapshot.py）')
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    last = snaps[-1]
    first = snaps[0]
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

    cards = [
        ('累计访问次数', last.get('site_pv', 0)),
        ('累计访客数', last.get('site_uv', 0)),
        ('记录天数', len(snaps)),
        ('起止', f"{first.get('date', '')} → {last.get('date', '')}"),
    ]
    cards_html = ''.join(
        f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div></div>'
        for k, v in cards)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>站点访问量趋势</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>站点访问量趋势</h1>
<div class="sub">数据来源：busuanzi（公网）每日快照 · 生成于 {now} ·
数据截至 {last.get('date', '')}</div>
<div class="cards">{cards_html}</div>

<h2>近 30 天新增访客（UV）</h2>
{render_bars(snaps)}

<h2>快照明细</h2>
<table>
<thead><tr><th>日期</th><th>累计 PV</th><th>累计 UV</th>
<th>日增 PV</th><th>日增 UV</th><th>备注</th></tr></thead>
<tbody>{render_table(snaps)}</tbody>
</table>

<div class="foot">
说明：日增 = 本次快照 − 上一条记录。负值一律记 0 并标注（意味着对方计数器重置）。
每日由 CI 用 GET 取数（不计数，避免污染统计）；台账存于 data/visits_history.json。
</div>
</div></body></html>
"""

    tmp = OUT_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(html)
    os.replace(tmp, OUT_PATH)
    print(f'[done] {os.path.relpath(OUT_PATH, ROOT)}（{len(snaps)} 条快照）')


if __name__ == '__main__':
    main()
