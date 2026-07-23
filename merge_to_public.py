#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
merge_to_public.py — 分支B合并：以远程 eda0ba7(2677, 已去重) 为基础，
叠加本地 gxb 增量（1128/125/126 字段修正 + 124 拆为 2 场），
保留公网去重结果与独有 URL(seri 2019/1129/40.html)，写回 data/lectures.json。
"""
import json
import subprocess
from datetime import datetime, timezone

BASE_REF = "eda0ba7"
base = json.loads(subprocess.check_output(["git", "show", BASE_REF + ":data/lectures.json"]))
local = json.load(open("data/lectures.json", encoding="utf-8"))


def is_gxb(u):
    return "gxb.scnu.edu.cn" in u


# 本地 gxb 增量：1128/125/126 的修正记录（各1条），124 的拆分记录（2条）
fix = {}
split124 = []
for r in local["data"]:
    u = r.get("sourceUrl", "")
    if not is_gxb(u):
        continue
    if u.endswith("1128.html") or u.endswith("125.html") or u.endswith("126.html"):
        fix[u] = r
    elif u.endswith("124.html"):
        split124.append(r)

print("fix(gxb 1128/125/126) 数量:", len(fix))
print("split124(gxb 124 拆分) 数量:", len(split124))

result = []
replaced = 0
skipped124 = 0
for r in base["data"]:
    u = r.get("sourceUrl", "")
    if u in fix:
        result.append(fix[u])
        replaced += 1
    elif is_gxb(u) and u.endswith("124.html"):
        skipped124 += 1
        continue
    else:
        result.append(r)
result.extend(split124)

out = dict(base)  # 保留顶层字段(updatedAt/total 等结构)
out["data"] = result
if "total" in out:
    out["total"] = len(result)
if "updatedAt" in out:
    out["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

json.dump(out, open("data/lectures.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

seri = "http://seri.scnu.edu.cn/Resources_CN/xueshujiangzuo/2019/1129/40.html"
print("\n=== 构建统计 ===")
print("base 条数:", len(base["data"]))
print("result 条数:", len(result))
print("replaced(gxb 1128/125/126):", replaced, " skipped124(删旧):", skipped124, " added split124:", len(split124))
print("seri 独有保留:", any(x.get("sourceUrl") == seri for x in result))
print("\n=== gxb 4条最终态 ===")
for key in ["1128.html", "125.html", "126.html", "124.html"]:
    recs = [x for x in result if x.get("sourceUrl", "").endswith(key) and "gxb" in x.get("sourceUrl", "")]
    for x in recs:
        print(f"  {key}: speaker={x.get('speaker')!r} loc={x.get('location')!r} "
              f"start={x.get('lectureStart')} idx={x.get('lectureIndex')}/{x.get('lectureCount')}")
