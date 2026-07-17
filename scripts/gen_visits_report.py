#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「站点访问量 · 按年 / 月」独立报告页（自包含单文件）。

数据来源：
    data/visits.json  —— 由本地 server.py 的 /api/visits 按「本地日期」累计写入，
    结构 {"total": N, "by_day": {"YYYY-MM-DD": 次数, ...}}。完全本地、不依赖任何
    外部计数服务（busuanzi / countapi.xyz 等），因此统计不会因为外链失效而丢失。

输出：
    reports/visits-by-month.html
    - 自包含：数据以 JSON 内联进页面，双击即可在浏览器打开查看（file:// 也行）。
    - 若由 server.py 托管后访问（http://localhost:8000/reports/visits-by-month.html），
      页面会再拉一次 /api/visits 取实时数据并刷新。

用法：
    python scripts/gen_visits_report.py

说明（重要）：
    纯静态站点（GitHub Pages）没有后端，busuanzi/countapi 这类外部服务扮演的正是
    「接收每次点击的远端」——但它们只返回累计总数，永远给不了按月明细。要想拿到
    「每年每月」，必须在服务端按日记录（本脚本/本计数器就是干这个的）。历史月份明细
    外部服务从未记录过，无法补回；本报告的按月数据从启用按日记录之日起累加。
"""
import os
import json
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VISITS_PATH = os.path.join(ROOT, 'data', 'visits.json')
OUT_DIR = os.path.join(ROOT, 'reports')
OUT_PATH = os.path.join(OUT_DIR, 'visits-by-month.html')

MONTHS = ['1月', '2月', '3月', '4月', '5月', '6月',
          '7月', '8月', '9月', '10月', '11月', '12月']


def load_visits():
    try:
        with open(VISITS_PATH, encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        return {'total': 0, 'by_day': {}}
    if not isinstance(d, dict):
        return {'total': 0, 'by_day': {}}
    d.setdefault('total', 0)
    bd = d.get('by_day')
    if not isinstance(bd, dict):
        bd = {}
    d['by_day'] = bd
    return d


def build_matrix(by_day):
    years = {}
    for day, cnt in by_day.items():
        try:
            y, m, _ = str(day).split('-')
            mi = int(m) - 1
        except Exception:
            continue
        if y not in years:
            years[y] = [0] * 12
        try:
            years[y][mi] += int(cnt)
        except (TypeError, ValueError):
            pass
    return years


# ---------------------------------------------------------------------------
# 页面外壳（静态 CSS + 渲染 JS）。数据通过 __DATA__ 占位符注入，渲染逻辑通过
# __RENDER__ 占位符注入；二者均为普通字符串替换，避免 Python f-string 与 JS 花括号冲突。
# ---------------------------------------------------------------------------
SHELL = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>站点访问量 · 按年 / 月</title>
<style>
  :root { --indigo: #4f46e5; --line: #e2e8f0; --ink: #0f172a; --sub: #64748b; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #f7f8fa; color: var(--ink);
    font-family: "Microsoft YaHei","PingFang SC",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 28px 20px 60px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--sub); font-size: 13px; margin-bottom: 20px; }
  .cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 22px; }
  .card { flex: 1 1 150px; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
  .card .k { font-size: 12px; color: var(--sub); }
  .card .v { font-size: 24px; font-weight: 700; margin-top: 4px; }
  .banner { background: #fff7ed; border: 1px solid #fed7aa; color: #9a3412;
    border-radius: 10px; padding: 12px 14px; font-size: 13px; margin-bottom: 18px; }
  table { border-collapse: separate; border-spacing: 0; width: 100%; background: #fff;
    border: 1px solid var(--line); border-radius: 12px; overflow: hidden; font-size: 13px; }
  th, td { text-align: center; padding: 8px 6px; border-bottom: 1px solid var(--line); }
  thead th, .ycol { background: #f8fafc; position: sticky; }
  .ycol { text-align: left; padding-left: 12px; font-weight: 600; white-space: nowrap; }
  thead th { font-weight: 600; color: var(--sub); }
  .cell { font-variant-numeric: tabular-nums; min-width: 46px; }
  .tot { background: #f8fafc; font-weight: 600; font-variant-numeric: tabular-nums; }
  tr.foot td, tr.foot th { border-top: 2px solid var(--line); border-bottom: none; }
  .grand { color: var(--indigo); font-size: 15px; }
  .note { margin-top: 18px; color: var(--sub); font-size: 12px; line-height: 1.7; }
  .note code { background: #eef2ff; color: #3730a3; padding: 1px 6px; border-radius: 5px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>站点访问量统计 · 按年 / 月</h1>
  <div class="sub" id="meta">加载中…</div>
  <div class="cards" id="cards"></div>
  <div id="banner"></div>
  <table id="tbl"></table>
  <div class="note">
    数据来源：<code>data/visits.json</code>（由本地 <code>server.py</code> 的 <code>/api/visits</code> 按本地日期累计，
    完全本地、不依赖任何外部计数服务）。<br/>
    生成命令：<code>python scripts/gen_visits_report.py</code> —— 重新运行即可刷新本页。<br/>
    说明：纯静态站点（GitHub Pages）没有后端，外部计数服务（busuanzi / countapi）只返回累计总数、
    给不了按月明细；要拿到「每年每月」，必须在服务端按日记录。历史月份明细外部服务从未记录，无法补回；
    本报告的按月数据从启用按日记录之日起累加。
  </div>
</div>
<script>__DATA__</script>
<script>__RENDER__</script>
</body>
</html>'''


RENDER_JS = r'''
(function () {
  var months = ["1月","2月","3月","4月","5月","6月","7月","8月","9月","10月","11月","12月"];
  function color(v) {
    if (!v) return "background:#fff;color:#cbd5e1;";
    var r = Math.max(0.06, Math.min(1, v / (META.maxv || 1)));
    var a = 0.10 + r * 0.80;
    return "background:rgba(79,70,229," + a.toFixed(3) + ");color:" + (r > 0.55 ? "#fff" : "#1e1b4b") + ";";
  }
  function build() {
    var ykeys = Object.keys(SEED.years).sort();
    var head = '<tr><th class="ycol">年份 / 月</th>' +
      months.map(function (m) { return "<th>" + m + "</th>"; }).join("") +
      "<th>年计</th></tr>";
    var rows = "";
    ykeys.forEach(function (y) {
      var cells = "";
      for (var i = 0; i < 12; i++) {
        cells += '<td class="cell" style="' + color(SEED.years[y][i]) + '">' + (SEED.years[y][i] || "") + "</td>";
      }
      rows += '<tr><th class="ycol">' + y + "</th>" + cells +
        '<td class="tot">' + (SEED.yearTot[y] || 0) + "</td></tr>";
    });
    var mcells = "";
    for (var i = 0; i < 12; i++) { mcells += '<td class="tot">' + (SEED.monthTot[i] || 0) + "</td>"; }
    var foot = '<tr class="foot"><th class="ycol">合计</th>' + mcells +
      '<td class="tot grand">' + SEED.grand + "</td></tr>";
    document.getElementById("tbl").innerHTML = head + rows + foot;
    document.getElementById("meta").textContent = META.note;
    // 概览卡片
    var cards = "";
    function card(k, v) { return '<div class="card"><div class="k">' + k + '</div><div class="v">' + v + "</div></div>"; }
    cards += card("站点累计总数", META.total);
    cards += card("按日明细合计", META.daySum);
    cards += card("历史遗留(无日期)", META.legacy);
    cards += card("有记录天数", META.days);
    cards += card("最早记录", META.first || "—");
    cards += card("最晚记录", META.last || "—");
    document.getElementById("cards").innerHTML = cards;
    var banner = document.getElementById("banner");
    if (!ykeys.length) {
      banner.className = "banner";
      banner.textContent = "暂无按日访问记录：当前仅有历史累计总数 " + META.total +
        "。请通过本地 server.py 运行站点（访问首页/统计页即计数），按日明细将自动累加；" +
        "之后重新运行本脚本即可生成按月明细。";
    } else { banner.className = ""; banner.textContent = ""; }
  }
  build();
  // 若由 server.py 托管（非 file://），拉取实时数据并刷新
  if (location.protocol !== "file:") {
    fetch("/api/visits", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.by_day) {
          var Y = {}, mv = 1;
          Object.keys(j.by_day).forEach(function (d) {
            var p = d.split("-"); var yi = p[0]; var mi = parseInt(p[1], 10) - 1;
            Y[yi] = Y[yi] || [0,0,0,0,0,0,0,0,0,0,0,0];
            Y[yi][mi] += j.by_day[d];
          });
          Object.keys(Y).forEach(function (y) { Y[y].forEach(function (v) { if (v > mv) mv = v; }); });
          var mt = [0,0,0,0,0,0,0,0,0,0,0,0], yt = {}, g = 0;
          Object.keys(Y).forEach(function (y) {
            var s = 0; for (var i = 0; i < 12; i++) { s += Y[y][i]; mt[i] += Y[y][i]; }
            yt[y] = s; g += s;
          });
          SEED.years = Y; SEED.monthTot = mt; SEED.yearTot = yt; SEED.grand = g; META.maxv = mv;
          META.note = "已拉取实时数据 · " + META.note;
          build();
        }
      })
      .catch(function () {});
  }
})();
'''


def main():
    data = load_visits()
    total = int(data.get('total', 0) or 0)
    by_day = data.get('by_day', {})

    years = build_matrix(by_day)
    ykeys = sorted(years.keys())
    year_tot = {y: sum(years[y]) for y in ykeys}
    month_tot = [0] * 12
    for y in ykeys:
        for i in range(12):
            month_tot[i] += years[y][i]
    grand = sum(year_tot.values())
    day_sum = sum(int(v) for v in by_day.values())
    legacy = max(0, total - day_sum)

    maxv = 1
    for y in ykeys:
        for v in years[y]:
            if v > maxv:
                maxv = v

    days = len(by_day)
    sorted_days = sorted(by_day.keys())
    first = sorted_days[0] if sorted_days else ""
    last = sorted_days[-1] if sorted_days else ""

    gen_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    note = ("本地 server.py /api/visits 按日累计（不依赖任何外部计数服务）· 生成于 " + gen_time +
            " · 站点累计总访问 " + str(total) +
            "（其中按日明细合计 " + str(day_sum) + "，历史遗留无日期明细 " + str(legacy) + "）")

    seed = {
        "years": years,
        "yearTot": year_tot,
        "monthTot": month_tot,
        "grand": grand,
    }
    meta = {
        "maxv": maxv,
        "note": note,
        "total": total,
        "daySum": day_sum,
        "legacy": legacy,
        "days": days,
        "first": first,
        "last": last,
    }

    data_js = "var SEED=" + json.dumps(seed, ensure_ascii=False) + ";var META=" + json.dumps(meta, ensure_ascii=False) + ";"
    html = SHELL.replace("__DATA__", data_js).replace("__RENDER__", RENDER_JS)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print("已生成报告：", OUT_PATH)
    print("  累计总数 =", total, " | 按日明细合计 =", day_sum, " | 历史遗留 =", legacy)
    print("  有记录天数 =", days, " | 范围 =", first or "—", "~", last or "—")
    if not ykeys:
        print("  （提示：暂无按日明细，运行 server.py 后访问站点即可累加；本报告结构已就绪。）")


if __name__ == "__main__":
    main()
