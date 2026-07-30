#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ctld title 去壳重推导（2026-07-30 用户方案）。

仅对教师发展中心(college=='教师发展中心')、且 listTitle 带「关于举办…通知」行政壳的
记录，把 title 设为 strip_admin_shell(listTitle)（去壳保留期号，使每期唯一）。
不动 listTitle / topic / 其它字段。从已存储 listTitle 确定性重推导，幂等。

用法：
    python scripts/restripe_ctld_titles.py            # 预览(dry-run)
    python scripts/restripe_ctld_titles.py --apply    # 写回 data/lectures.json
"""
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scraper"))
import parsers  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "lectures.json")
COLLEGE = "教师发展中心"
SHELL = re.compile(r"^(?:关于(?:举办|开展|组织|举行)\s*)?.{1,80}?\s*的?通知\s*$")

# 个别记录缺失 listTitle，但用户/页面提供了准确值，显式补回后再去壳（2026-07-30）。
# 4411 的 listTitle 由用户在对话中贴出，属权威来源。
MISSING_LISTTITLE = {
    "http://ctld.scnu.edu.cn/a/20250328/4411.html":
        "关于举办“智能升级 何以为师：华师教育人工智能系列通识课”培训第5期（教学创新工作坊总第94期）的通知",
}


def main():
    apply = "--apply" in sys.argv
    d = json.load(open(DATA, encoding="utf-8"))
    recs = d["data"] if isinstance(d, dict) else d
    n_changed = 0
    for r in recs:
        if r.get("college") != COLLEGE:
            continue
        url = r.get("sourceUrl")
        lt = (r.get("listTitle") or "").strip()
        # 缺失 listTitle 但已知准确值 -> 补回
        if (not lt) and url in MISSING_LISTTITLE:
            lt = MISSING_LISTTITLE[url]
            if apply:
                r["listTitle"] = lt
        # 去壳来源：优先 listTitle；listTitle 缺失但 title 本身是壳时，对 title 去壳
        src = lt if (lt and SHELL.match(lt)) else (r.get("title") or "")
        if not SHELL.match(src):
            continue
        new_title = parsers.strip_admin_shell(src)
        cur = (r.get("title") or "").strip()
        if new_title and new_title != cur:
            if apply:
                r["title"] = new_title
            n_changed += 1
            if not apply or n_changed <= 12:
                print(f"[{'APPLY' if apply else 'PREVIEW'}] {url}")
                print(f"    title: {cur[:70]!r} -> {new_title[:70]!r}")
    if apply:
        now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        if isinstance(d, dict):
            d["updatedAt"] = now
        json.dump(d, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n已写回 {n_changed} 条，updatedAt={now}")
    else:
        print(f"\n预览：将变更 {n_changed} 条（加 --apply 写回）")


if __name__ == "__main__":
    main()
