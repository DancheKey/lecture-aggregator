# -*- coding: utf-8 -*-
"""窄探针：验证「增量摄入时能否跨源检测并合并」。

背景（用户 2026-09-23 命题）：
  A 学院先发讲座 -> B 学院后发同一讲座。B 被抓取时，应能在摄入阶段检测到
  A 已存在并「融合」为一条（merged + sources），而非仅丢弃 B。

铁律（用户提醒）：
  同部门/同学院发布的「同一讲座后发」= 多轮会议通知，走多轮替换（保留最新），
  **不做跨源融合**。融合（merged/sources）只针对「不同学院」。

本脚本只跑现状（不改 scraper.py），打印：
  1) 跨源检测是否命中（_cross_source_dedup_with_existing）
  2) incremental_merge 实际产出的动作（丢弃 / 保留 / 融合）
  3) 缺口分析：现状下 B 是否被丢弃而非融合。

用法：
  D:/Tools/Python 312/python.exe scripts/probe_incremental_cross_source.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

from scraper import (  # noqa: E402
    incremental_merge,
    _cross_source_dup_with_existing,
    _normalize_speaker,
    _is_valid_speaker_name,
    _has_round_marker,
)


def _build_existing_index(existing):
    """复刻 incremental_merge 的基底跨源索引（scraper.py:988-997）。"""
    idx = {}
    for r in existing:
        spk = _normalize_speaker(r.get('speaker') or '')
        if not _is_valid_speaker_name(spk):
            continue
        date = (r.get('lectureStart') or '')[:10]
        if not date or date.startswith('0000'):
            continue
        idx.setdefault((spk, date), []).append(r)
    return idx


def _brief(r):
    return (f"speaker={r.get('speaker')!r} college={r.get('college')!r} "
            f"start={r.get('lectureStart')} url={r.get('sourceUrl')} "
            f"merged={r.get('merged')} sources={len(r.get('sources') or [])} "
            f"pub={r.get('publishTime')}")


def _report(title, existing, new):
    print('=' * 78)
    print('场景：', title)
    print('-' * 78)
    print('【基底 existing（已在库）】')
    for r in existing:
        print('   ', _brief(r))
    print('【新增 new（本轮抓取）】')
    for r in new:
        print('   ', _brief(r))

    # 1) 跨源检测（仅看 _cross_source_dedup_with_existing 命中与否）
    idx = _build_existing_index(existing)
    print('【跨源检测 _cross_source_dedup_with_existing】')
    for r in new:
        hit = _cross_source_dup_with_existing(r, idx)
        print(f"   B={r.get('sourceUrl')} -> 命中已有重复? {hit}")

    # 2) incremental_merge 实际动作
    out = incremental_merge(existing, new)
    print('【incremental_merge 实际产出】')
    print(f'   产出条数: {len(out)}（基底 {len(existing)} + 新增 {len(new)}）')
    for r in out:
        print('   ', _brief(r))

    # 3) 缺口判定（精确区分四种动作）
    new_urls = {r.get('sourceUrl') for r in new}
    existing_urls = {r.get('sourceUrl') for r in existing}
    out_urls = {r.get('sourceUrl') for r in out}
    new_dropped = new_urls - out_urls
    existing_removed = existing_urls - out_urls
    new_kept = new_urls & out_urls
    fused = any(r.get('merged') for r in out)
    print('【判定】')
    if fused:
        print('   ✅ 已融合：产出含 merged=True 记录，sources 聚合了多源')
    elif new_dropped and not existing_removed:
        print('   ❌ 缺口：新记录被【丢弃】，未融合（A 保持原样、无 sources/merged）')
    elif existing_removed and new_kept:
        print('   ✅ 多轮替换：旧轮(existing)被删、新轮(new)保留最新 —— 不融合(merged 为空)，符合同学院约定')
    elif new_kept and not new_dropped and not existing_removed:
        print('   ⚠ 漏检：新记录被当作新事件原样追加（未判为重复，可能产生重复）')
    else:
        print('   ? 其他（请人工核对输出）')
    print()
    return out


# ---------- 场景 A：跨源（不同学院），B 后发于 A ----------
def scenario_cross_source():
    A = {
        'sourceUrl': 'http://iqm.scnu.edu.cn/a/20191114/103.html',
        'college': 'iqm', 'campus': '石牌',
        'speaker': 'Blaizot', 'speakerAffiliation': '法国巴黎萨克雷大学',
        'lectureStart': '2019-11-15T14:30:00',
        'topic': '量子材料的反常输运性质',
        'title': '量子物质研究院学术讲座：量子材料的反常输运性质',
        'location': '理8-210',
        'publishTime': '2019-11-14T10:00:00',
    }
    B = {
        'sourceUrl': 'https://physics.scnu.edu.cn/a/20191118/806.html',
        'college': 'physics', 'campus': '石牌',
        'speaker': 'Blaizot', 'speakerAffiliation': '法国巴黎萨克雷大学',
        'lectureStart': '2019-11-15T14:30:00',
        'topic': '量子材料的反常输运性质研究',
        'title': '物理学院学术报告：量子材料的反常输运性质研究',
        'location': '大学城理六栋301',
        'publishTime': '2019-11-18T10:00:00',
    }
    return 'A 跨源：iqm(Blaizot) 已在库，physics 后抓同一讲座', [A], [B]


# ---------- 场景 B：同学院多轮会议通知（B 带轮次标记、发布更晚） ----------
def scenario_same_college_multiround():
    A = {
        'sourceUrl': 'http://x.scnu.edu.cn/a/20240101/1.html',
        'college': 'xyz', 'campus': '石牌',
        'speaker': '张三', 'speakerAffiliation': '某大学',
        'lectureStart': '2024-01-10T09:00:00',
        'topic': '某某学术研讨会',
        'title': '某某学术研讨会第一轮会议通知',
        'location': '教学楼101',
        'publishTime': '2024-01-01T10:00:00',
    }
    B = {
        'sourceUrl': 'http://x.scnu.edu.cn/a/20240105/2.html',
        'college': 'xyz', 'campus': '石牌',
        'speaker': '张三', 'speakerAffiliation': '某大学',
        'lectureStart': '2024-01-10T09:00:00',
        'topic': '某某学术研讨会',
        'title': '某某学术研讨会第二轮会议通知',
        'location': '教学楼101',
        'publishTime': '2024-01-05T10:00:00',  # 更晚 = 最新一轮
    }
    return ('B 同学院多轮：xyz 先发第一轮，后发第二轮（带标记、发布更晚）'
            '——应保留最新、不融合'), [A], [B]


# ---------- 场景 C（观察项）：同学院同讲座、B 后发但无轮次标记 ----------
def scenario_same_college_no_marker():
    A = {
        'sourceUrl': 'http://x.scnu.edu.cn/a/20240201/1.html',
        'college': 'xyz', 'campus': '石牌',
        'speaker': '李四', 'speakerAffiliation': '某大学',
        'lectureStart': '2024-02-10T15:00:00',
        'topic': '关于某某研究的进展',
        'title': '关于某某研究的进展（通知）',
        'location': '理科楼201',
        'publishTime': '2024-02-01T10:00:00',
    }
    B = {
        'sourceUrl': 'http://x.scnu.edu.cn/a/20240205/2.html',
        'college': 'xyz', 'campus': '石牌',
        'speaker': '李四', 'speakerAffiliation': '某大学',
        'lectureStart': '2024-02-10T15:00:00',
        'topic': '关于某某研究的进展',
        'title': '关于某某研究的进展（补充通知）',
        'location': '理科楼201',
        'publishTime': '2024-02-05T10:00:00',  # 更晚
    }
    return ('C 观察项：同学院同讲座、B 后发但【无轮次标记】'
            '——现状下会怎么处理（丢弃较新B? 还是漏检?）'), [A], [B]


# ---------- 场景 D：跨源·地点写错（同主讲+同精确时刻+同单位，但地点错、题目文本差异大） ----------
def scenario_cross_source_wrong_location():
    A = {
        'sourceUrl': 'http://iqm.scnu.edu.cn/a/20200301/10.html',
        'college': 'iqm', 'campus': '石牌',
        'speaker': 'Blaizot', 'speakerAffiliation': '法国巴黎萨克雷大学',
        'lectureStart': '2020-03-10T14:30:00',
        'topic': '量子材料的反常输运性质',
        'title': '量子物质研究院学术讲座：量子材料的反常输运性质',
        'location': '理8-210',
        'publishTime': '2020-03-01T10:00:00',
    }
    B = {
        'sourceUrl': 'https://physics.scnu.edu.cn/a/20200305/20.html',
        'college': 'physics', 'campus': '石牌',
        'speaker': 'Blaizot', 'speakerAffiliation': '法国巴黎萨克雷大学',
        'lectureStart': '2020-03-10T14:30:00',   # 同精确时刻 -> 兜底层命中
        'topic': '凝聚态物理前沿报告',            # 题目文本差异大，相似度<0.25
        'title': '物理学院学术报告：凝聚态物理前沿报告',
        'location': '教学楼999（地址写错）',       # ⚠️ 错误地点，不得参与判重/覆盖
        'publishTime': '2020-03-05T10:00:00',
    }
    return ('D 跨源·地点错误：同主讲+同精确时刻+同单位，但地点写错、题目文本差异大'
            '——验证「不依赖地点也能检测」'), [A], [B]


def main():
    print('注：本探针只验证 scraper.py 现状，不改任何代码。\n')
    a_title, a_ex, a_new = scenario_cross_source()
    _report(a_title, a_ex, a_new)

    b_title, b_ex, b_new = scenario_same_college_multiround()
    _report(b_title, b_ex, b_new)

    c_title, c_ex, c_new = scenario_same_college_no_marker()
    _report(c_title, c_ex, c_new)

    d_title, d_ex, d_new = scenario_cross_source_wrong_location()
    _report(d_title, d_ex, d_new)

    print('=' * 78)
    print('汇总')
    print('=' * 78)
    print('· 场景 A：跨源后发 —— 现状是否能「融合」？ 见上方❌/✅判定')
    print('· 场景 B：同学院多轮 —— 必须保留最新一轮、且 merged 必须为空（不融合）')
    print('· 场景 C：同学院无标记后发 —— 现状行为，供判断是否也归入多轮保留最新')
    print('· 场景 D：跨源·地点错误 —— 检测不应依赖地点；融合写回时禁止用 B 覆盖 A 已有地点')


if __name__ == '__main__':
    main()
