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


def _recon_baseline_path():
    """对账基线文件路径。

    ⚠ **刻意与 data/audit_baseline.json 分开**：后者是字段体检（audit_fields.py
      / daily.yml 的 high 基线对比）在用的文件，两边各写各的字段。共用一个文件
      会互相覆盖——实测踩过：对账写入 sourceCounts/total 时把 high 字段抹掉，
      test_module_compile.py 的 test_46 立刻变红。对账基线只服务于「数据有没有
      悄悄变少」，与「字段脏不脏」是两个正交维度，应各自独立。
    """
    return os.path.join(ROOT, 'data', 'reconcile_baseline.json')


def _write_baseline(base_path, doc):
    """原子写基线文件（tmp + replace），避免中途崩溃留下半份 JSON。"""
    tmp = base_path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(doc, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, base_path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def check_source_reconciliation(recs, errors):
    """按源对账（2026-10-10 改造 5a）：增量模式下每个 college 只增不减。

    为什么这道门禁是主力（而非总数对比）：
      增量抓取在正常情况下对每个源**只增不减**——历史数据永不删除。
      因此「某个源条数突然归零 / 缩水超阈值」必然是故障，无需与历史数字较劲：
        · 源站改版导致抓回 0 条    → 归零
        · 解析器回归导致整源解析失败 → 归零或大幅缩水
      这类故障在**总数层面可能完全看不出来**（掉 5% 远在容差内），
      却是实打实的静默数据丢失。按源对账能精确定位到是哪个源出了问题。

    阈值取 30%：正常增量单轮单源增幅远大于此；n_base<20 的小样本源不设防，
      避免「某学院只有 1~2 场讲座」这类正常波动刷出噪音告警。
    """
    from collections import Counter
    cur = Counter(r.get('college') or '' for r in recs)
    base_path = _recon_baseline_path()
    doc = {}
    if os.path.exists(base_path):
        try:
            doc = json.load(open(base_path, encoding='utf-8'))
        except Exception:
            doc = {}
    base = doc.get('sourceCounts') or {}
    if not base:
        # 首个CI 轮次：只落基线，不比对（否则首次运行必然全量报警）
        doc['version'] = 1
        doc['sourceCounts'] = dict(cur)
        _write_baseline(base_path, doc)
        print(f'[BASELINE] 已写入按源对账基线（{len(cur)} 个源）→ {base_path}')
        return

    dropped = []
    for name, n_base in base.items():
        n_cur = cur.get(name, 0)
        if n_base >= 20 and n_cur < n_base * 0.7:   # 小样本源不设防，避免噪声
            dropped.append((name, n_base, n_cur))
    vanished = [(name, n_base) for name, n_base in base.items()
                if n_base >= 20 and cur.get(name, 0) == 0]
    if vanished:
        errors.append(
            '[源消失] %d 个源归零：%s——疑似源站改版/迁移，'
            '请确认新地址并用 --full --source <名称> 补抓'
            % (len(vanished), '、'.join(f'{n}(原{b})' for n, b in vanished[:10])))
    for name, n_base, n_cur in dropped:
        if n_cur:
            errors.append(
                '[源缩水] %s %d → %d 条（跌 %d%%）——增量下本源不应减少，'
                '疑似抓取或解析故障；若为有意清理请忽略本条'
                % (name, n_base, n_cur, (n_base - n_cur) * 100 // n_base))
    # 基线随数据自然增长：逐源取较大值（max）。⚠ 取舍要读准：max 语义下
    # 基线**只升不降**——「有意清理」若让某源永久低于基线 30%，会**每轮
    # 持续报警**（fail-noisy，静默丢失同样持续报警直到人工修复）；此时应
    # 手工同步基线（更新 data/reconcile_baseline.json 中该源的数字）。
    # 有意清理后若记录被爬虫重新抓回，cur 涨回基线则自然消警。
    # 按源检查的价值在于抓「归零/断崖」，渐进的小幅下降由总量门禁兜。
    merged = {k: max(v, cur.get(k, 0)) for k, v in base.items()}
    for k, v in cur.items():
        merged[k] = max(merged.get(k, 0), v)
    if merged != base:
        doc['version'] = 1
        doc['sourceCounts'] = merged
        _write_baseline(base_path, doc)


def check_total_reconciliation(recs, errors):
    """总量对账（2026-10-10 改造 5b）：与**上一次记录的总量**比，只拦塌方。

    ⚠ 不用「本轮 >= 上轮」：维护者会主动做拆分/去重清洗（git 历史里同一天
      就出现过 3814 → 3812 的合法下降），直接比较会大量误报。
      基线取 data/audit_baseline.json 的 total 字段，只拦相对超容差的跌幅；
      固定常数 3000 已降级为「塌方兜底」，不是主要防线。

    ⚠ 基线**不取 max（只升不降）**：那样一旦某轮掉量，后续每轮都会因
      「基线仍停在高位」反复报警，有意清理后无法恢复。改为记录**上一次
      实际见到的总量**，让门禁只对「相邻两轮之间的突降」敏感——
      这正是静默丢数的形态（一次抓取丢掉几千条），而对渐进的、有意为之的
      人工清理不敏感。

    ⚠ 必须在 check_source_reconciliation **之前**读取 total：后者会重写
      同一个基线文件并写入 total，读晚了就变成「自己跟自己比」，永远不报。
    """
    base_path = _recon_baseline_path()
    try:
        doc = json.load(open(base_path, encoding='utf-8'))
    except Exception:
        doc = {}
    prev_total = doc.get('total')
    cur_total = len(recs)
    if prev_total and cur_total < prev_total * 0.97:   # 允许 3% 自然波动
        errors.append(
            f'总量异常: {prev_total} → {cur_total} 条（跌 {100 - cur_total * 100 // prev_total}%）'
            '——相对上一次抓取跌幅超 3%，疑似静默丢数')
    doc['total'] = cur_total        # 记录本次实际值（不取 max，见上文说明）
    _write_baseline(base_path, doc)


def main():
    recs = load_records()
    errors = []
    # 2026-09-27 二轮审计 P1-3：空库/截断防护——此前 {'data': []} 会打出
    # 「PASS（共 0 条记录）」退出 0，抓取脚本写坏主库时护栏无感、空库直接上公网。
    # ⚠ 3000 已降级为「塌方兜底」，不再是主要防线——主要防线是下面两道
    #   对账门禁（按源 + 总量），它们能发现 3000 以上的小幅静默丢失。
    if len(recs) < 3000:
        errors.append(
            f'记录数异常: {len(recs)} 条 < 3000 下限——疑似主库被清空/截断，'
            '拒绝通过（防止空库静默部署）')
    check_composite_key_unique(recs, errors)
    check_skc_title_integrity(recs, errors)
    check_images_no_local_path(recs, errors)
    test_incremental_merge_unit(errors)
    # ⚠ 顺序要紧：check_total_reconciliation 先跑（它要读 baseline 里的 total），
    #   check_source_reconciliation 后跑（它会重写同一个基线文件）。
    #   两者反序会让总量检查读到自己刚写的 total → 自我比较 → 永不报警。
    check_total_reconciliation(recs, errors)
    check_source_reconciliation(recs, errors)

    if errors:
        print(f"FAIL：{len(errors)} 处不变量违反（共 {len(recs)} 条记录）")
        for e in errors[:80]:
            print("  -", e)
        sys.exit(1)
    print(f"PASS：数据不变量全部通过（共 {len(recs)} 条记录）")
    sys.exit(0)


if __name__ == "__main__":
    main()
