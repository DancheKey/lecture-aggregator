# -*- coding: utf-8 -*-
"""职称残片扫描（P2 第一步）：全库找出 speaker 尾部带职称词前缀残片的记录，
并**回源页取原文**判定「真残片」还是「正常人名尾字」。

背景（2026-09-30 commit 9c2132b 遗留）：
  姓名按 4→2 字截取 + 职称表只剥学术职称，导致「刘维泉副总裁」截成「刘维泉副」。
  同类残片还有「丁洁瑶助」「张炼研究」「刘曙光编」等。
  难点：同一个字既是职称词前缀、又是常见人名尾字（「博」「学」「理」「长」「特」），
  纯字形判据必然误杀 —— **必须回源页核实**，这也是字段值取自源页原文的要求。

用法：
  python scripts/scan_title_fragment_residues.py            # 扫描 + 回源页判定
  python scripts/scan_title_fragment_residues.py --no-net  # 只用本地缓存
输出：
  reports/title-fragments.json  机器可读
  reports/title-fragments.html  人工核实清单（可点击源页链接）
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
import field_vocab as FV  # noqa: E402

DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')
CACHE_DIR = os.path.join(ROOT, 'tmp', 'reparse_sweep')
REPORT_DIR = os.path.join(ROOT, 'reports')
JSON_OUT = os.path.join(REPORT_DIR, 'title-fragments.json')
HTML_OUT = os.path.join(REPORT_DIR, 'title-fragments.html')

CONTEXT = 70          # 命中位置前后取多少字
_local = __import__('threading').local()


def get_session():
    if not hasattr(_local, 's'):
        s = requests.Session()
        s.verify = False
        s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        _local.s = s
    return _local.s


def fetch_cached(url, allow_net=True):
    """复用 scripts/reparse_diff_report.py 的磁盘缓存（md5(url).html）。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    dst = os.path.join(CACHE_DIR, hashlib.md5(url.encode('utf-8')).hexdigest() + '.html')
    if os.path.exists(dst) and os.path.getsize(dst) > 200:
        try:
            return open(dst, 'rb').read(), 'cache'
        except OSError:
            pass
    if not allow_net:
        return None, 'miss'
    try:
        resp = get_session().get(url, timeout=20)
        if resp.status_code != 200 or len(resp.content) < 200:
            return None, 'http-%s' % resp.status_code
        open(dst, 'wb').write(resp.content)
        return resp.content, 'net'
    except Exception as e:
        return None, 'err:%s' % type(e).__name__


def decode(raw):
    for enc in ('utf-8', 'gb18030', 'gbk', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'ignore')


_TAG_STRIP = re.compile(
    r'(?is)<(script|style)\b.*?</\1>|<[^>]+>')
_WS = re.compile(r'[ \t\u00a0]+')


def to_text(html):
    t = _TAG_STRIP.sub(' ', html)
    t = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</tr>', '\n', t)
    t = _TAG_STRIP.sub('', t)
    import html as _h
    t = _h.unescape(t)
    t = _WS.sub(' ', t)
    return re.sub(r'\n\s*\n+', '\n', t)


# ---------------------------------------------------------------- 残片判据
def build_prefixes():
    """所有职称/职务词的**真前缀**（本身不是完整词）。"""
    wset = set(FV.TAIL_TITLE_SUFFIXES)
    pref = set()
    for w in wset:
        for k in range(1, len(w)):
            if w[:k] not in wset:
                pref.add(w[:k])
    return pref, wset


def tail_fragment(name, pref):
    """返回 speaker 尾部的职称残片（最长优先），无则 None。"""
    for k in (4, 3, 2, 1):
        if len(name) > k and name[-k:] in pref:
            return name[-k:]
    return None


def source_words_for(frag, wset):
    """该残片可能来自哪些完整职称词（供人工判断）。"""
    return sorted(w for w in wset if w.startswith(frag) and len(w) > len(frag))


# ---------------------------------------------------------------- 源页判定
# 字段标签噪声：源页「专家姓名：赵蕙心 工作单位：…」这类，残字来自标签而非职称。
_LABEL_HEADS = ('工作单位', '单位', '职务', '职称', '研究方向', '专家姓名', '主讲人',
                '报告人', '嘉宾', '简介', '个人简历')
_PUNCT_HEAD = re.compile(r'^[^0-9A-Za-z\u4e00-\u9fff]')


def _is_title_head(tail):
    """tail 是否以「完整职称词」或「职称词的 ≥2 字前缀」开头。"""
    for w in sorted(set(FV.TAIL_TITLE_SUFFIXES), key=len, reverse=True):
        if tail.startswith(w):
            return w
    for w in sorted(set(FV.TAIL_TITLE_SUFFIXES), key=len, reverse=True):
        for k in range(len(w) - 1, 1, -1):
            if tail.startswith(w[:k]):
                return w[:k] + '…'
    return None


def judge(text, speaker, frag):
    """在源页纯文本里定位讲者名，看它后面跟的是什么。

    返回 (verdict, snippet, evidence)
      verdict: 'residue'  真残片 —— 源页里该处是「姓名 + 职称/标签/标点」，
                          库里的 speaker 在姓名后又多截了一个职称首字
               'realname' 正常人名 —— 源页里完整出现库里的 speaker 值本身
               'unknown'  源页未命中（多为正文已下线的老页面）
    """
    stem = speaker[:-len(frag)]          # 去掉残片后的主干
    if not stem:
        return 'unknown', '', ''

    # 最强证据：源页里完整出现了库里的 speaker 值 → 这就是原文，不是残片
    if speaker in text:
        i = text.index(speaker)
        snippet = text[max(0, i - CONTEXT):i + CONTEXT].replace('\n', ' ⏎ ')
        return 'realname', snippet, '源页原文含完整 "%s"' % speaker

    for m in re.finditer(re.escape(stem) + r'\s*([^ ]{0,14})', text):
        i = m.start()
        snippet = text[max(0, i - CONTEXT):i + CONTEXT].replace('\n', ' ⏎ ')
        tail = m.group(1).strip()
        hit = _is_title_head(tail)
        if hit:
            return 'residue', snippet, '源页「%s」后紧跟 %s' % (stem, hit)
        for lb in _LABEL_HEADS:
            if tail.startswith(lb):
                return 'residue', snippet, '源页「%s」后是标签「%s」' % (stem, lb)
        if not tail or _PUNCT_HEAD.match(tail):
            return 'residue', snippet, '源页「%s」后即断句（%r）' % (stem, tail[:8])
    return 'unknown', '', ''


PLANS_HTML = """
<h2>下一步：三条可选路线</h2>
<div class="plans">
  <div class="plan rec">
    <div class="ptag">推荐</div>
    <h3>A · 根治：按「职称词边界」切姓名</h3>
    <p class="psum">改 <code>parsers.py:4475-4494</code> 的姓名截取：不再固定按 4→3→2 字贪心截取，
    而是先在讲者串里定位<strong>职称词的起始位置</strong>，以它为切点取姓名。</p>
    <table class="mini">
      <tr><th>优点</th><td>从源头不再产生残片；职务词（副总裁）与职称词（特聘研究员）一并覆盖，
        上一轮加的宽松版正则可被它取代，规则更统一</td></tr>
      <tr><th>代价</th><td>改动落在讲者提取主干，需重跑全库 dry-run 复核；切点必须限制在第一个左括号之前，
        否则会切到单位里的职称（psy127 那类页面）</td></tr>
      <tr><th>风险</th><td>中。姓名本身含职称字的人名（如「包特」）要靠「切点 ≥2 字 + 姓名校验」双重守卫</td></tr>
    </table>
  </div>
  <div class="plan">
    <h3>B · 补救：事后剥残片词表</h3>
    <p class="psum">沿用上一轮「副总裁」的做法，把 <code>助/编/研究/特/硕/工</code> 等残片加进剥离表。</p>
    <table class="mini">
      <tr><th>优点</th><td>改动小、见效快，与既有实现同构</td></tr>
      <tr><th>代价</th><td><strong>会误杀真人名</strong>：这些字同时是常见人名尾字——本次扫描 42 条候选里
        29 条是「周博」「胡理」「谢久书」这类正常人名，纯字形判据分不开</td></tr>
      <tr><th>风险</th><td>高。误剥会凭空造出错误的跨源合并目标，比不剥更坏</td></tr>
    </table>
  </div>
  <div class="plan">
    <h3>C · 只修存量 + 扩大判脏</h3>
    <p class="psum">只修正这 9 条，并把 <code>is_title_only</code> 放宽，让「博士生」这类残值被判脏标出。</p>
    <table class="mini">
      <tr><th>优点</th><td>不动解析主干，零回归风险</td></tr>
      <tr><th>代价</th><td>判脏只标记不修正，新抓取仍会持续产生同类脏数据；
        且放宽判脏会影响全库判脏结果，属另一次口径变更</td></tr>
      <tr><th>风险</th><td>低，但治标不治本</td></tr>
    </table>
  </div>
</div>
"""


def _esc(s):
    return (str(s or '').replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def write_html(cands, stats):
    """生成人工核实清单：每条带源页链接、源页原文佐证、建议值、勾选导出。"""
    res = [c for c in cands if c['verdict'] == 'residue']
    unk = [c for c in cands if c['verdict'] == 'unknown']
    ok = [c for c in cands if c['verdict'] == 'realname']

    def card(c, idx, cls):
        return """
    <div class="item %s" data-speaker="%s" data-fix="%s" data-url="%s">
      <label class="pick"><input type="checkbox" class="ck" checked></label>
      <div class="body">
        <div class="row1">
          <span class="no">%d</span>
          <span class="old">%s</span><span class="arrow">→</span><span class="new">%s</span>
          <span class="frag">去掉残片「%s」</span>
          <span class="colg">%s</span>
        </div>
        <div class="row2"><b>源页原文：</b>%s</div>
        <div class="row3"><b>判定依据：</b>%s &nbsp;·&nbsp; <b>库中职称：</b>%s</div>
        <div class="row4"><a href="%s" target="_blank">%s</a></div>
      </div>
    </div>""" % (
            cls, _esc(c['speaker']), _esc(c['stem']), _esc(c['url']),
            idx, _esc(c['speaker']), _esc(c['stem']), _esc(c['frag']),
            _esc(c['college']),
            _esc((c['snippet'] or '（源页未取到上下文）').strip())[:220],
            _esc(c['evidence'] or '—'), _esc(c['title'] or '（空）'),
            _esc(c['url']), _esc(c['url']))

    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>讲者姓名职称残片 · 核实清单</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;padding:32px 20px 60px;background:#f5f6f8;color:#1a1a1a;
       font-family:"Microsoft YaHei","微软雅黑",-apple-system,sans-serif;line-height:1.6}
  .wrap{max-width:1080px;margin:0 auto}
  h1{font-size:22px;margin:0 0 6px;font-weight:700}
  h2{font-size:17px;margin:34px 0 12px;padding-left:9px;border-left:4px solid #4a5568}
  .sub{color:#5a6570;font-size:13px;margin-bottom:20px}
  .sum{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0 8px}
  .chip{background:#fff;border:1px solid #dfe3e8;border-radius:8px;padding:9px 15px;font-size:13px}
  .chip b{font-size:19px;margin-right:5px}
  .r b{color:#c0392b}.u b{color:#b7791f}.g b{color:#2f7a4d}
  .item{display:flex;gap:11px;background:#fff;border:1px solid #e3e6ea;border-radius:9px;
        padding:13px 15px;margin-bottom:11px}
  .item.res{border-left:4px solid #c0392b}
  .item.unk{border-left:4px solid #b7791f}
  .item.ok{border-left:4px solid #cbd2d9;opacity:.72}
  .pick{padding-top:3px}.ck{width:15px;height:15px}
  .body{flex:1;min-width:0}
  .row1{display:flex;align-items:center;gap:9px;flex-wrap:wrap;font-size:15px}
  .no{color:#98a2ad;font-size:12px;min-width:18px}
  .old{font-weight:700;color:#c0392b}
  .arrow{color:#98a2ad}
  .new{font-weight:700;color:#2f7a4d}
  .frag{font-size:12px;background:#fdf0ee;color:#a93226;border-radius:4px;padding:2px 7px}
  .colg{font-size:12px;color:#5a6570;background:#eef1f4;border-radius:4px;padding:2px 7px}
  .row2{font-size:13px;color:#3d4753;margin-top:7px;background:#fafbfc;
        border-radius:5px;padding:7px 10px;word-break:break-all}
  .row3{font-size:12px;color:#5a6570;margin-top:6px}
  .row4{margin-top:6px;font-size:12px;word-break:break-all}
  .row4 a{color:#1d6fb8;text-decoration:none}.row4 a:hover{text-decoration:underline}
  .plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:14px}
  .plan{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:15px 17px;position:relative}
  .plan.rec{border:2px solid #2f7a4d}
  .ptag{position:absolute;top:-11px;right:13px;background:#2f7a4d;color:#fff;
        font-size:11px;border-radius:10px;padding:2px 10px}
  .plan h3{margin:0 0 8px;font-size:15px}
  .psum{font-size:13px;color:#3d4753;margin:0 0 10px}
  table.mini{width:100%%;border-collapse:collapse;font-size:12px}
  table.mini th{text-align:left;vertical-align:top;color:#5a6570;font-weight:600;
                padding:4px 8px 4px 0;white-space:nowrap;width:38px}
  table.mini td{padding:4px 0;color:#3d4753}
  .bar{position:sticky;bottom:0;background:#fff;border-top:1px solid #dfe3e8;
       margin-top:22px;padding:12px 16px;border-radius:9px;display:flex;
       align-items:center;gap:12px;flex-wrap:wrap;font-size:13px}
  button{background:#2f7a4d;color:#fff;border:0;border-radius:6px;padding:8px 17px;
         font-size:13px;cursor:pointer;font-family:inherit}
  button.gh{background:#fff;color:#3d4753;border:1px solid #cbd2d9}
  #n{font-weight:700;color:#2f7a4d}
  details{margin-top:8px}
  summary{cursor:pointer;font-size:13px;color:#1d6fb8;padding:6px 0}
</style></head><body><div class="wrap">
<h1>讲者姓名尾部的职称残片 · 核实清单</h1>
<div class="sub">扫描口径：全库 3810 条 → speaker 尾部命中「职称词的真前缀」共 %d 条候选；
逐条回源页取原文比对后三分。<b>判定一律以源页原文为准，不由字形推断。</b>
生成时间：%s</div>
<div class="sum">
  <div class="chip r"><b>%d</b>确证脏（源页佐证）</div>
  <div class="chip u"><b>%d</b>源页正文已缺，无法核实</div>
  <div class="chip g"><b>%d</b>正常人名，误报</div>
</div>
%s
<h2>一、确证脏 · 建议按源页原文修正（%d 条）</h2>
<div id="res">%s</div>
<h2>二、源页正文已缺失，无法核实（%d 条）</h2>
<div class="sub">这几条源页返回 200 但正文已清空（只剩导航），无法回源比对。
从人名常见度与库中职称看，倾向是<strong>正常人名、不应改动</strong>，请人工确认。</div>
%s
<h2>三、误报：尾字恰好是职称词首字的正常人名（%d 条，不动）</h2>
<details><summary>展开查看（周博 / 胡理 / 谢久书 / 王硕 …）</summary>
<div>%s</div>
</details>
<div class="bar">
  <span>已勾选 <span id="n">%d</span> 条</span>
  <button onclick="exp()">导出勾选清单（JSON）</button>
  <button class="gh" onclick="tog(false)">全不选</button>
  <button class="gh" onclick="tog(true)">全选（仅确证脏）</button>
</div>
<script>
var box=document.querySelectorAll('.ck');
function cnt(){var n=0;box.forEach(function(b){if(b.checked)n++});
  document.getElementById('n').textContent=n;}
box.forEach(function(b){b.onchange=cnt});
function tog(v){document.querySelectorAll('.item.res .ck').forEach(function(b){b.checked=v});cnt();}
function exp(){var out=[];document.querySelectorAll('.item.res').forEach(function(d){
  if(d.querySelector('.ck').checked) out.push({speaker:d.dataset.speaker,
    fix:d.dataset.fix, url:d.dataset.url});});
  var b=new Blob([JSON.stringify(out,null,1)],{type:'application/json'});
  var a=document.createElement('a');a.href=URL.createObjectURL(b);
  a.download='speaker-fragment-fix.json';a.click();}
cnt();
</script>
</div></body></html>""" % (
        len(cands), __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M'),
        len(res), len(unk), len(ok),
        PLANS_HTML,
        len(res), ''.join(card(c, i + 1, 'res') for i, c in enumerate(res)),
        len(unk), ''.join(card(c, i + 1, 'unk') for i, c in enumerate(unk)),
        len(ok), ''.join(card(c, i + 1, 'ok') for i, c in enumerate(ok)),
        len(res))

    io.open(HTML_OUT, 'w', encoding='utf-8', newline='\n').write(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-net', action='store_true', help='只用本地缓存，不联网')
    ap.add_argument('--apply', action='store_true', help='（暂未启用）落库修正')
    args = ap.parse_args()

    rows = json.load(io.open(DATA_PATH, encoding='utf-8'))['data']
    pref, wset = build_prefixes()

    cands = []
    seen = set()
    for x in rows:
        s = str(x.get('speaker') or '').strip()
        f = tail_fragment(s, pref)
        if not f:
            continue
        url = str(x.get('sourceUrl') or '')
        key = (s, url)
        if key in seen:
            continue
        seen.add(key)
        cands.append({
            'speaker': s, 'frag': f, 'url': url,
            'stem': s[:-len(f)],
            'candidates': source_words_for(f, wset),
            'title': x.get('speakerTitle') or '',
            'topic': x.get('topic') or x.get('title') or '',
            'college': x.get('college') or '',
        })
    cands.sort(key=lambda d: (d['frag'], d['speaker']))
    print('候选 %d 条（去重后）' % len(cands))

    stats = {}
    for c in cands:
        raw, src = fetch_cached(c['url'], allow_net=not args.no_net)
        c['fetch'] = src
        if not raw:
            c['verdict'], c['snippet'], c['evidence'] = 'unknown', '', 'fetch=%s' % src
        else:
            c['verdict'], c['snippet'], c['evidence'] = judge(
                to_text(decode(raw)), c['speaker'], c['frag'])
        stats[c['verdict']] = stats.get(c['verdict'], 0) + 1
        print('  [%-8s] %-10s 残片%-4s <- %s' % (
            c['verdict'], c['speaker'], c['frag'],
            '/'.join(c['candidates'][:3]) or '-'))

    print('\n汇总:', stats)
    os.makedirs(REPORT_DIR, exist_ok=True)
    json.dump({'total': len(cands), 'stats': stats, 'items': cands},
              io.open(JSON_OUT, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('已写入', JSON_OUT)
    write_html(cands, stats)
    print('已写入', HTML_OUT)


if __name__ == '__main__':
    main()
