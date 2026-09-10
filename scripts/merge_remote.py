#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地与远端分叉时的讲座数据语义合并（替代 git 的行级自动合并）。

为什么不能直接用 git merge
--------------------------
`data/lectures.json` 是多行 JSON（indent=2，约 6.3 MB、7.8 万行、2900+ 条记录）。
git 虽能按行比对，但**逐行合并只保证「文本能拼上」，不保证「语义正确」**：
本地是权威版本、远端只应并入真新增、水位线必须取远端——这三条都无法由 git 保证；
而且远端每日新增会改变数组行位置，与本地改动交错时结果难以核对。项目里实测过
自动合并产出错误结果（记录与主讲人对不上、条数漂移）。因此铁律：`lectures.json`
禁止行级三方合并，改由本脚本按 URL 语义合并（下有断言兜底）。

本脚本固化的动作
----------------
1. 数据文件取**本地语义版本**（本地是权威，人工精修不丢）；
2. 水位线 `data/last_scrape.json` 取**远端**（增量基线必须跟随远端，否则会重抓或漏抓）；
3. 远端独有的讲座按 **URL 语义**判定后并入：「重复复活」（本地已跨源合并进某条记录的
   `sources[]`）与排除名单内的 URL 不算新增；
4. 重跑生成脚本，并断言「本地 URL 全保留 + 远端 URL 全并入」。

URL 集合同时收集 `sourceUrl` 与 `sources[].sourceUrl`；**不要用条数判断**——「一页多
讲座」在不同版本里的拆分粒度不同，条数天然不等。

用法
----
    python scripts/merge_remote.py            # 只报告，不触碰任何文件（默认）
    python scripts/merge_remote.py --apply    # 写盘 + 重跑生成 + 断言

建议先 `git merge origin/main` 让冲突显形，再由本脚本消解；断言不通过时脚本以非零
退出，此时**不要提交**。
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_REL = 'data/lectures.json'
LAST_REL = 'data/last_scrape.json'
DATA_ABS = os.path.join(ROOT, DATA_REL)
LAST_ABS = os.path.join(ROOT, LAST_REL)

sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from excluded_urls import load_excluded  # noqa: E402

# 需要一并 git add 的产物（与 daily.yml 的提交清单保持一致）
SITE_PATHS = [
    'site/lectures.json',
    'site/lectures/latest.json',
    'site/lectures/stats.json',
    'site/lectures/chunks.json',
    'site/lectures/chunk_*.json',
    'site/index.html',
    'site/stats.html',
]


def norm(u):
    return (u or '').rstrip('/')


def _git(*args, check=True):
    r = subprocess.run(['git'] + list(args), cwd=ROOT, capture_output=True)
    out = r.stdout.decode('utf-8', 'replace')
    if check and r.returncode != 0:
        err = r.stderr.decode('utf-8', 'replace').strip()
        raise RuntimeError('git %s 失败：%s' % (' '.join(args), err))
    return out


def git_json(rev, rel):
    """取某个版本的文件内容并解析；文件不存在或解析失败时返回 None。"""
    out = _git('show', '%s:%s' % (rev, rel), check=False)
    if not out.strip():
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def records_of(obj):
    """兼容 {updatedAt, data} 包裹格式与旧版纯数组。"""
    if obj is None:
        return []
    if isinstance(obj, dict):
        return obj.get('data') or []
    return obj if isinstance(obj, list) else []


def url_index(records):
    """URL -> 记录。同时收录跨源合并记录的 sources[].sourceUrl。

    收录 sources 是为了识别「重复复活」：某场讲座被并入本地某条记录后，其原始 URL
    只存在于 sources[] 里；远端若用旧代码重抓同一页面，会再生成一条独立记录，
    其 sourceUrl 正是该 URL —— 落在索引内即判定为重复，不会二次并入。
    """
    idx = {}
    for r in records:
        urls = [r.get('sourceUrl')] + [s.get('sourceUrl') for s in (r.get('sources') or [])]
        for u in urls:
            k = norm(u)
            if k:
                idx.setdefault(k, r)
    return idx


def describe(r):
    return '%s | %s | %s' % (
        r.get('college') or '?',
        r.get('lectureStart') or '?',
        (r.get('title') or '(无标题)')[:48],
    )


def plan_merge(ours, theirs, excluded):
    """纯函数：算出待并入清单与统计（不触碰文件系统，便于单测）。"""
    oi, ti = url_index(ours), url_index(theirs)
    only_theirs = [u for u in ti if u not in oi]

    local_keys = set()
    for o in ours:
        spk = (o.get('speaker') or '').strip()
        day = (o.get('lectureStart') or '')[:10]
        if spk and day:
            local_keys.add((spk, day))

    fresh, suspect, skipped = [], [], []
    for u in sorted(only_theirs):
        r = ti[u]
        if u in excluded:
            skipped.append(r)
            continue
        spk = (r.get('speaker') or '').strip()
        day = (r.get('lectureStart') or '')[:10]
        # 疑似「同一场讲座但 URL 变了」：只做提示，不阻止并入（漏讲座比重复更严重）
        if spk and day and (spk, day) in local_keys:
            suspect.append(r)
        else:
            fresh.append(r)

    stats = {
        'ours': len(ours), 'theirs': len(theirs),
        'ours_urls': len(oi), 'theirs_urls': len(ti),
        'only_theirs': len(only_theirs),
        'fresh': len(fresh), 'suspect': len(suspect), 'excluded': len(skipped),
    }
    return fresh, suspect, skipped, stats


def _write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description='本地/远端讲座数据语义合并')
    ap.add_argument('--apply', action='store_true', help='实际写盘（默认只报告）')
    ap.add_argument('--remote', default='origin/main', help='远端版本（默认 origin/main）')
    args = ap.parse_args()

    print('[1/4] 拉取远端...')
    _git('fetch', 'origin', '--prune')

    ours = records_of(git_json('HEAD', DATA_REL))
    theirs = records_of(git_json(args.remote, DATA_REL))
    if not ours:
        print('[ABORT] 读不到本地 HEAD 的 %s，请确认版本号与工作树状态。' % DATA_REL, file=sys.stderr)
        return 2
    if not theirs:
        print('[ABORT] 读不到远端 %s 的 %s。' % (args.remote, DATA_REL), file=sys.stderr)
        return 2

    excluded = load_excluded() or set()
    fresh, suspect, skipped, stats = plan_merge(ours, theirs, excluded)

    print('\n[2/4] 分叉概览')
    print('  本地 %d 条 / %d 个 URL' % (stats['ours'], stats['ours_urls']))
    print('  远端 %d 条 / %d 个 URL' % (stats['theirs'], stats['theirs_urls']))
    print('  远端独有 URL：%d 个' % stats['only_theirs'])
    print('    ├ 真新增（待并入）      ：%d 条' % stats['fresh'])
    print('    ├ 疑似同讲座换 URL（提示）：%d 条' % stats['suspect'])
    print('    └ 命中排除名单（丢弃）   ：%d 条' % stats['excluded'])
    for tag, rows in (('真新增', fresh), ('疑似重复', suspect), ('已排除', skipped)):
        for r in rows[:20]:
            print('      [%s] %s' % (tag, describe(r)))
    if not stats['only_theirs']:
        print('  → 远端没有本地缺失的讲座（典型「空增量」提交：只推进了水位线）')

    if not args.apply:
        print('\n[3/4] 只报告模式，未改动任何文件。加 --apply 才写盘。')
        return 0

    merged = list(ours) + fresh + suspect
    merged.sort(key=lambda x: x.get('lectureStart') or '', reverse=True)

    # 安全闸门：合并结果不得少于本地（少于则说明判定出错，宁可停下）
    if len(url_index(merged)) < stats['ours_urls']:
        print('[ABORT] 合并后 URL 数少于本地，疑似逻辑错误，拒绝写盘。', file=sys.stderr)
        return 3

    now = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')
    _write_json(DATA_ABS, {'updatedAt': now, 'data': merged})

    remote_last = git_json(args.remote, LAST_REL)
    if remote_last is not None:
        _write_json(LAST_ABS, remote_last)
        print('\n[3/4] 已写盘：data/lectures.json（本地语义版本 + %d 条远端新增）'
              % (len(fresh) + len(suspect)))
        print('        last_scrape.json 取远端水位线：%s'
              % (remote_last.get('last_scrape') if isinstance(remote_last, dict) else remote_last))
    else:
        print('\n[3/4] 已写盘 data/lectures.json；远端无 last_scrape.json，水位线保持不变')

    print('\n[4/4] 重跑前端数据生成...')
    rc = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'generate_frontend_data.py')],
                        cwd=ROOT).returncode
    if rc != 0:
        print('[ABORT] 生成脚本失败，请勿提交。', file=sys.stderr)
        return 4

    ri = url_index(merged)
    miss_ours = [u for u in url_index(ours) if u not in ri]
    miss_theirs = [u for u in url_index(theirs) if u not in ri]
    print('\n=== 合并后断言 ===')
    print('  本地 URL 全保留：%s（缺失 %d）' % ('是' if not miss_ours else '否', len(miss_ours)))
    print('  远端 URL 全并入：%s（缺失 %d）' % ('是' if not miss_theirs else '否', len(miss_theirs)))
    print('  结果 URL 数：%d' % len(ri))

    if miss_ours or miss_theirs:
        print('[ABORT] 断言未通过，请勿提交。', file=sys.stderr)
        return 5

    _git('add', DATA_REL, LAST_REL, *SITE_PATHS)
    print('\n[OK] 已 git add（含 site 产物）。请人工确认后 commit 完成本次合并。')
    print('     提交前建议自查：git status --short')
    return 0


if __name__ == '__main__':
    sys.exit(main())
