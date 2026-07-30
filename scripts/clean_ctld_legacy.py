# -*- coding: utf-8 -*-
"""定向清洗 ctld 存量两类脏数据（确定性，不动其它字段）：
1) location 含「参与方式」等相邻字段污染 -> 用 parsers._clean_location 截断；
2) topic/title 是「关于举办"XXX"…通知」行政壳 -> 用 parsers.extract_ad_title 提取引号内讲座名。
仅作用于显式列出的 URL，其它记录原样不动（符合「已清洗数据禁止全量替换」铁律）。
用法：python scripts/clean_ctld_legacy.py            # dry-run 预览
      python scripts/clean_ctld_legacy.py --apply     # 写回 data/lectures.json
"""
import json, sys, datetime, argparse
from zoneinfo import ZoneInfo
sys.path.insert(0, "scraper")
import parsers

DATA = "data/lectures.json"

LOC_TARGETS = {
    "http://ctld.scnu.edu.cn/a/20241101/4389.html",
    "http://ctld.scnu.edu.cn/a/20240308/4274.html",
    "http://ctld.scnu.edu.cn/a/20231208/4273.html",
    "http://ctld.scnu.edu.cn/a/20231202/4272.html",
    "http://ctld.scnu.edu.cn/a/20231117/4271.html",
    "http://ctld.scnu.edu.cn/a/20231024/4220.html",
}
TOPIC_TARGETS = {
    "http://ctld.scnu.edu.cn/a/20240325/4290.html",
    "http://ctld.scnu.edu.cn/a/20231208/4273.html",
    "http://ctld.scnu.edu.cn/a/20231202/4272.html",
    "http://ctld.scnu.edu.cn/a/20231024/4220.html",
    "http://ctld.scnu.edu.cn/a/20231011/4270.html",
    "http://ctld.scnu.edu.cn/a/20230923/4203.html",
    "http://ctld.scnu.edu.cn/a/20201222/1280.html",
    "http://ctld.scnu.edu.cn/a/20181220/905.html",
    "http://ctld.scnu.edu.cn/a/20190920/1031.html",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    d = json.load(open(DATA, encoding="utf-8"))
    recs = d["data"]
    changes = 0
    for r in recs:
        u = r.get("sourceUrl", "")
        cur_loc = (r.get("location") or "").strip()
        cur_title = (r.get("title") or "").strip()
        cur_topic = (r.get("topic") or "").strip()
        new_loc = cur_loc
        new_title = cur_title
        new_topic = cur_topic

        if u in LOC_TARGETS:
            new_loc = parsers._clean_location(cur_loc, cur_title)
        if u in TOPIC_TARGETS:
            extracted = parsers.extract_ad_title(cur_title)
            if extracted:
                new_title = extracted
                if "关于举办" in cur_title or (cur_topic.endswith("的") and "关于举办" in cur_topic):
                    new_topic = extracted
            else:
                # 边界：topic 是「关于举办…的」截断脏值，但 title 干净 -> topic 回退 title
                if cur_topic.startswith("关于举办") and cur_topic.endswith("的") and "关于举办" not in cur_title:
                    new_topic = cur_title

        if new_loc != cur_loc or new_title != cur_title or new_topic != cur_topic:
            changes += 1
            print(f"URL: {u}")
            if new_loc != cur_loc:
                print(f"  location: {cur_loc!r} -> {new_loc!r}")
            if new_title != cur_title:
                print(f"  title:    {cur_title!r} -> {new_title!r}")
            if new_topic != cur_topic:
                print(f"  topic:    {cur_topic!r} -> {new_topic!r}")
            r["location"] = new_loc
            r["title"] = new_title
            r["topic"] = new_topic

    print(f"\n变更记录数: {changes}")
    if not args.apply:
        print("(dry-run，未写回。加 --apply 写回)")
        return

    now = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    if isinstance(d, dict):
        d["updatedAt"] = now
    json.dump(d, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"已写回 {DATA}，updatedAt={now}")


if __name__ == "__main__":
    main()
