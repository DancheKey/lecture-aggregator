#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
diag_7diff.py — 跨源去重误并核查工具

把「本地 data/lectures.json」与「远程某快照(默认 eda0ba7)」做 sourceUrl 差集，
反向在远程 merged 记录的 sources[] 里定位被合并进哪条、合并去向，
据此逐条判断「真重复 vs 误并」。

用法:
  python diag_7diff.py [--remote-file PATH] [--local-file PATH] [--remote-commit eda0ba7]

远程快照获取优先级:
  1. 显式 --remote-file PATH          (本地已下载好的远程 data json)
  2. 本地 git 含 --remote-commit 时:  git show <commit>:data/lectures.json
  3. urllib 下载 raw.githubusercontent.com/<repo>/<commit>/data/lectures.json

输出:
  - 差集(本地有 / 远程顶层 sourceUrl 无) 的每一条 URL
  - 若该 URL 出现在远程某 merged 记录的 sources[] 中 -> 标记 [被合并] 并打印合并目标
  - 否则 -> 标记 [孤儿/未见] (本地有、远程完全无, 可能是新抓取或未覆盖项)
"""
import json
import sys
import argparse
import subprocess
import urllib.request
import ssl

REPO = "DancheKey/lecture-aggregator"
DEFAULT_COMMIT = "eda0ba7"
RAW_URL = "https://raw.githubusercontent.com/{repo}/{commit}/data/lectures.json"


def load_remote(remote_file, commit):
    """按优先级获取远程 data 字典。失败抛异常。"""
    if remote_file:
        with open(remote_file, encoding="utf-8") as f:
            return json.load(f)
    # 2) 本地 git object
    try:
        out = subprocess.check_output(
            ["git", "show", f"{commit}:data/lectures.json"],
            stderr=subprocess.DEVNULL,
        )
        return json.loads(out.decode("utf-8"))
    except Exception:
        pass
    # 3) urllib 下载 raw
    url = RAW_URL.format(repo=REPO, commit=commit)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        data = r.read()
    return json.loads(data.decode("utf-8"))


def get_records(d):
    if isinstance(d, dict):
        return d.get("data", [])
    return d


def collect_remote_map(records):
    """返回 (top_urls:set 顶层 sourceUrl, swallowed: {被吞url -> 合并目标记录})。"""
    top = set()
    swallowed = {}
    for rec in records:
        u = rec.get("sourceUrl")
        if u:
            top.add(u)
        if rec.get("merged") and rec.get("sources"):
            for s in rec["sources"]:
                su = s.get("sourceUrl") if isinstance(s, dict) else s
                if su and su not in swallowed:
                    swallowed[su] = rec
    return top, swallowed


def main():
    ap = argparse.ArgumentParser(description="跨源去重误并核查")
    ap.add_argument("--remote-file", default=None, help="本地已下载的远程 data json 路径")
    ap.add_argument("--local-file", default="data/lectures.json")
    ap.add_argument("--remote-commit", default=DEFAULT_COMMIT)
    args = ap.parse_args()

    remote = load_remote(args.remote_file, args.remote_commit)
    local = json.load(open(args.local_file, encoding="utf-8"))
    rrecs = get_records(remote)
    lrecs = get_records(local)
    top, swallowed = collect_remote_map(rrecs)

    local_urls = set()
    for rec in lrecs:
        u = rec.get("sourceUrl")
        if u:
            local_urls.add(u)

    diff = local_urls - top
    print(f"本地条数: {len(lrecs)}  远程快照({args.remote_commit})条数: {len(rrecs)}")
    print(f"本地 sourceUrl 唯一数: {len(local_urls)}  远程顶层 sourceUrl 唯一数: {len(top)}")
    print(f"差集(本地有 / 远程顶层无): {len(diff)} 条\n")

    merged_count = 0
    orphan_count = 0
    for u in sorted(diff):
        if u in swallowed:
            merged_count += 1
            t = swallowed[u]
            print(f"[被合并] {u}")
            print(f"    -> 合并目标 sourceUrl : {t.get('sourceUrl')}")
            print(f"       目标标题          : {t.get('title')}")
            print(f"       目标时间          : {t.get('lectureStart')} ~ {t.get('lectureEnd')}")
            print(f"       目标 college/campus: {t.get('college')}/{t.get('campus')}")
            print(f"       目标 sources 数    : {len(t.get('sources', []))}")
            # 顺带列出目标 sources 全貌，便于判断 Jaccard 是否真高
            srcs = t.get("sources", [])
            for i, s in enumerate(srcs):
                if isinstance(s, dict):
                    print(f"         src[{i}] {s.get('sourceUrl')}  ({s.get('college')}/{s.get('campus')})")
                else:
                    print(f"         src[{i}] {s}")
        else:
            orphan_count += 1
            print(f"[孤儿/未见] {u}")
            for rec in lrecs:
                if rec.get("sourceUrl") == u:
                    print(f"   本地标题: {rec.get('title')}  时间: {rec.get('lectureStart')}  "
                          f"college: {rec.get('college')}/{rec.get('campus')}")
                    break
    print(f"\n汇总: 被合并 {merged_count} 条, 孤儿/未见 {orphan_count} 条, 共 {len(diff)} 条差集")


if __name__ == "__main__":
    main()
