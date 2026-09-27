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
  3) 社科处(skc)记录：listTitle 含「第N讲」等系列结构时，title 必须保留完整
     clean_title(listTitle)（防止 title 塌缩、丢失系列名）。
  4) images 字段不含本地文件系统路径（C:\、D:\、/tmp/ 等）。
  5) 增量合并函数 incremental_merge 单元测试：基底锁定、只追加、不重复。

说明：title 与 topic 内容相同**不再**视为违规（2026-09-04 决策）——两者是卡片上的
独立元素，均需保留；topic 承载讲座题目，即使与 title 重复也不应被清空。

说明：标题清洗校验直接引用 scraper/parsers._clean_title（2026-09-27 二轮
审计 P1-2：镜像副本已漂移删除，改为单一事实源引用）。
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
# ---------------------------------------------------------------------------
# 标题清洗单一事实源（2026-09-27 二轮审计 P1-2）：此处原维护 clean_title /
# _strip_nav_noise 的镜像副本，已与 parsers.py 实际实现漂移——缺 _nfkc_radicals
# （康熙部首还原）与日期前缀剥离，「[2023年4月6日] X」「从微观量⼦…」类污染
# 恰好蒙混过栏。镜像删除，校验时直接引用 parsers._clean_title（护栏校验的
# 就是生成端同一份逻辑，永不再漂移）。
# ---------------------------------------------------------------------------
def _parsers_clean_title(t):
    sys.path.insert(0, os.path.join(ROOT, "scraper"))
    import parsers
    return parsers._clean_title(t or "")


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
    （clean_title 后）。这是针对「title 被 topic 覆盖、丢失系列名」回归的精确护栏。
    注：2026-09-04 起不再检查 title==topic——topic 是卡片上的独立元素，
    与 title 内容相同属正常形态，不应判违规（原检查随 CTLD 同款检查一并移除）。"""
    for r in recs:
        url = r.get("sourceUrl", "")
        if "skc.scnu.edu.cn" not in url:
            continue
        lt = r.get("listTitle")
        if not lt or not _SERIES_RE.search(lt):
            continue
        expected = _parsers_clean_title(lt)
        actual = (r.get("title") or "").strip()
        if actual != expected:
            errors.append(
                f"[skc title!=clean(listTitle)] {url} "
                f"expected={expected!r} actual={actual!r}"
            )


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
    # 2026-09-27 二轮审计 P1-3：空库/截断防护——此前 {'data': []} 会打出
    # 「PASS（共 0 条记录）」退出 0，抓取脚本写坏主库时护栏无感、空库直接上公网。
    # 下限取 3000：当前 3804 条，留出正常清理余量，只拦「数量级塌方」。
    if len(recs) < 3000:
        errors.append(
            f'记录数异常: {len(recs)} 条 < 3000 下限——疑似主库被清空/截断，'
            '拒绝通过（防止空库静默部署）')
    check_composite_key_unique(recs, errors)
    check_skc_title_integrity(recs, errors)
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
