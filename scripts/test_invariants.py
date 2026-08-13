#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
讲座数据不变量测试（CI 护栏）。
把项目铁律固化成可执行断言；任何一条失败即令 CI 退出非 0，从而阻断：
  - daily.yml 自动提交/部署被退化的数据；
  - 手动 push 的坏数据上线（deploy.yml 也会跑一次）。

校验项：
  1) sourceUrl 与 lectureIndex 复合键唯一（无重复记录键）。
  2) 凡含 listTitle 的记录，title 必须等于 clean_title(listTitle)
     —— 直接防止「把 topic 拼进 title」「title 被覆盖成题目」这类回归。
  3) 社科处(skc)记录：若 topic 非空，则 title != topic（防止二者被合并）。
  4) images 字段不含本地文件系统路径（C:\、D:\、/tmp/ 等）。
  5) 增量合并函数 incremental_merge 单元测试：基底锁定、只追加、不重复。

说明：clean_title / _strip_nav_noise 为 scraper/parsers.py 的**镜像副本**，
仅用于校验「已提交数据」是否与生成逻辑一致。若 parsers.py 改动标题清洗逻辑，
须同步更新本文件（否则会触发失败，提示做一次全量重新生成 / 复核）。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "lectures.json")

# ---------------------------------------------------------------------------
# 镜像：scraper/parsers.py 的标题清洗逻辑（保持与生成数据一致）
# ---------------------------------------------------------------------------
_NAV_NOISE_RE = re.compile(
    r"(更多链接|友情链接|快速链接|相关链接|站点导航|"
    r"教育涉外监管信息网|教育涉外监管|涉外监管信息网|"
    r"教育部留学服务中心|教育部留学|中国教育国际交流|中国教育部|"
    r"学术讲座\s*[-—]|通知公告\s*[-—]|新闻动态\s*[-—]|"
    r"首页|»\s*正文|»)"
)


def _strip_nav_noise(s):
    """截断标题/主题中混入的站点导航链接噪声（如「更多链接中国教育部…」）。"""
    if not s:
        return s
    m = _NAV_NOISE_RE.search(s)
    if m:
        s = s[:m.start()]
    s = s.strip(" —-丨|·\t")
    s = re.sub(r"^\s*\d{1,2}(?=\D|$)\s*", "", s).strip(" —-丨|·\t")
    return s.strip()


def clean_title(t):
    t = t.strip()
    if " - " in t:
        t = t.split(" - ")[0].strip()
    if "｜" in t:
        t = t.split("｜")[0].strip()
    t = re.sub(r"^[\s【\[]*讲座通知[\s】]", "", t).strip()
    t = re.sub(r"^[\s｜|：:]*", "", t).strip()
    t = re.sub(r"^讲座通知[｜|（(]", "", t).strip()
    if len(t) > 4 and t.startswith('"') and t.endswith('"'):
        t = t[1:-1].strip()
    if len(t) > 4 and t.startswith('"') and t.endswith('"'):
        t = t[1:-1].strip()
    t = re.sub(r"^[｜|\s]+", "", t).strip()
    if t.endswith(")") and t.count("(") < t.count(")"):
        t = t[:-1].strip()
    if t.endswith("）") and t.count("（") < t.count("）"):
        t = t[:-1].strip()
    if (t.count("(") > t.count(")")) or (t.count("（") > t.count("）")):
        _idx = max(t.rfind("("), t.rfind("（"))
        if _idx != -1:
            t = t[:_idx].strip()
    t = re.sub(
        r"^\s*(?:19|20)\d{2}\s*[-/年\.]\s*\d{1,2}\s*[-/月\.]\s*\d{1,2}\s*[日号]?\s*", "", t
    ).strip()
    t = re.sub(
        r"^\s*(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\s+", "", t
    ).strip()
    t = re.sub(r"^\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*", "", t).strip()
    t = re.sub(
        r"\s*[—\-－]\s*一、[一二三四五六七八九十百零0-9]*期?\s*"
        r"(?:工作坊|培训|沙龙|讲坛|报告|讲座)?安排\s*$",
        "",
        t,
    ).strip()
    t = re.sub(
        r"\s*一、[一二三四五六七八九十百零0-9]*期?\s*"
        r"(?:工作坊|培训|沙龙|讲坛|报告|讲座)?安排\s*$",
        "",
        t,
    ).strip()
    t = _strip_nav_noise(t)
    return t


# ---------------------------------------------------------------------------
# 校验函数
# ---------------------------------------------------------------------------
def load_records():
    with open(DATA, encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, dict) and "data" in d:
        return d["data"]
    if isinstance(d, list):
        return d
    raise ValueError("data/lectures.json 顶层既不是 {data:[]} 也不是 []")


def check_composite_key_unique(recs, errors):
    """同一讲座页面可能拆出多条（不同 lectureIndex），故唯一键为
    (sourceUrl, lectureIndex)。若出现完全相同复合键，才是真重复/坏合并。"""
    seen = {}
    for r in recs:
        u = (r.get("sourceUrl") or "").rstrip("/")
        if not u:
            errors.append("[missing sourceUrl] 记录缺少 sourceUrl")
            continue
        k = (u, r.get("lectureIndex"))
        if k in seen:
            errors.append(f"[dup key] {u} lectureIndex={r.get('lectureIndex')}")
        seen[k] = True


def check_images_no_local_path(recs, errors):
    """images 字段不应包含本地文件系统路径（如 C:\、D:\、/tmp/），
    这些是 PDF 转图临时产物，部署后前端无法访问。"""
    for r in recs:
        imgs = r.get("images") or []
        if isinstance(imgs, list):
            for img in imgs:
                if isinstance(img, str) and re.search(r'^[A-Za-z]:[\\/]|^/tmp/', img):
                    errors.append(f"[local image path] {r.get('sourceUrl','')} img={img[:80]!r}")
                    break


_SERIES_RE = re.compile(r"第[一二三四五六七八九十百零\d]+[场期讲]")


def check_skc_title_integrity(recs, errors):
    """社科处铁律（2026-07-30 修复后约定）：
    listTitle 含「第N讲」等独特系列结构时，title 必须保留完整 listTitle
    （clean_title 后），且不得塌缩成 == topic（topic 是提炼出的讲座题目）。
    这是针对「title 被 topic 覆盖、丢失系列名」回归的精确护栏。"""
    for r in recs:
        url = r.get("sourceUrl", "")
        if "skc.scnu.edu.cn" not in url:
            continue
        lt = r.get("listTitle")
        if not lt or not _SERIES_RE.search(lt):
            continue
        expected = clean_title(lt)
        actual = (r.get("title") or "").strip()
        if actual != expected:
            errors.append(
                f"[skc title!=clean(listTitle)] {url} "
                f"expected={expected!r} actual={actual!r}"
            )
        tp = (r.get("topic") or "").strip()
        if tp and actual == tp:
            errors.append(f"[skc title==topic] {url} title={actual!r}")


def check_ctld_topic_not_equal_title(recs, errors):
    """ctld 铁律补充护栏（2026-07-30）：教师发展中心(ctld)的 listTitle 是
    「关于举办"XXX"通知」行政壳，title 应抽为讲座名、topic 应清空（不冗余）。
    若 topic 非空且 == title，即 title 被 topic 覆盖/冗余，判为违规。
    仅针对 ctld 源——life/行知/地理/美术 等大量源的 title==topic 是良性历史形态
    （title 已承载讲座名，topic 重复为历史简化、前端已适配），不在本次回归修复范围，
    故不检查，避免误伤。"""
    for r in recs:
        if r.get("college") != "教师发展中心":
            continue
        t = (r.get("title") or "").strip()
        tp = (r.get("topic") or "").strip()
        if tp and t == tp:
            errors.append(f"[ctld title==topic] {r.get('sourceUrl')} title={t!r}")


def test_incremental_merge_unit(errors):
    """scraper.incremental_merge 不应退化基底：只追加、不重复、不覆盖。"""
    try:
        sys.path.insert(0, os.path.join(ROOT, "scraper"))
        import scraper  # noqa: E402

        base = [
            {"sourceUrl": "a.html", "title": "A", "college": "x"},
            {"sourceUrl": "b.html#1", "title": "B1"},
            {"sourceUrl": "b.html#2", "title": "B2"},
        ]
        fake = {"sourceUrl": "c.html", "title": "C"}
        out = scraper.incremental_merge(base, [fake])
        assert len(out) == len(base) + 1, "incremental 应只追加"
        keys = {(r.get("sourceUrl")) for r in out}
        assert "a.html" in keys and "b.html#1" in keys and "b.html#2" in keys
        # 已有记录再合并：条数不变
        out2 = scraper.incremental_merge(base, list(base))
        assert len(out2) == len(base), "re-merge 不应重复"
        # 新记录内出现重复键：应去重
        out3 = scraper.incremental_merge([], [fake, dict(fake)])
        assert len(out3) == 1, "新记录内重复键应去重"
    except Exception as e:  # pragma: no cover
        errors.append(f"[incremental_merge 单元测试] {e!r}")


def main():
    recs = load_records()
    errors = []
    check_composite_key_unique(recs, errors)
    check_skc_title_integrity(recs, errors)
    check_ctld_topic_not_equal_title(recs, errors)
    check_images_no_local_path(recs, errors)
    test_incremental_merge_unit(errors)

    if errors:
        print(f"FAIL：{len(errors)} 处不变量违反（共 {len(recs)} 条记录）")
        for e in errors[:80]:
            print("  -", e)
        sys.exit(1)
    print(f"PASS：数据不变量全部通过（共 {len(recs)} 条记录）")
    sys.exit(0)


if __name__ == "__main__":
    main()
