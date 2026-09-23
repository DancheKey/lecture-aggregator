# -*- coding: utf-8 -*-
"""窄探针：验证「增量摄入阶段跨源融合」定稿行为（2026-09-23）。

定稿口径（用户拍板）：
  - 跨单位（不同学院）命中 -> merge-into-A：A 打 merged=True、B 进 sources、
    sourceCount 自增、用 B 非空字段补全 A 空字段（location 已有则不覆盖）。
  - 同单位（同学院）：
      * 有轮次标记（第X轮/补充/更新）-> 多轮替换，保留最新一轮，不融合（merged 空）。
      * 无标记 -> 维持现状（命中即 skip 丢弃后发，不融合）。
  - 幂等守卫：sources 已含 B 的 URL 则不重复追加 / sourceCount 不虚高。
  - 地点红线：location 不参与判重；A 已有 location 时绝不用 B 覆盖。

用法：D:/Tools/Python 312/python.exe scripts/probe_incremental_cross_source.py
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
            f"start={r.get('lectureStart')} loc={r.get('location')!r} "
            f"bio={ (r.get('speakerBio') or '')[:8]!r} "
            f"merged={r.get('merged')} sc={r.get('sourceCount')} "
            f"srcs={len(r.get('sources') or [])}")


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

    idx = _build_existing_index(existing)
    print('【跨源检测 _cross_source_dup_with_existing】')
    for r in new:
        hit = _cross_source_dup_with_existing(r, idx)
        print(f"   B={r.get('sourceUrl')} -> 命中已有重复记录? {hit is not None} "
              f"(同单位={hit.get('college')==r.get('college') if hit else '-'})")

    out = incremental_merge(existing, new)
    print('【incremental_merge 实际产出】')
    print(f'   产出条数: {len(out)}（基底 {len(existing)} + 新增 {len(new)}）')
    for r in out:
        print('   ', _brief(r))

    new_urls = {r.get('sourceUrl') for r in new}
    out_urls = {r.get('sourceUrl') for r in out}
    new_dropped = new_urls - out_urls
    fused = any(r.get('merged') for r in out)
    print('【判定】')
    if fused:
        print('   ✅ 已融合：含 merged=True 记录，sources 聚合多源，B 信息补全 A')
    elif new_dropped:
        print('   ➡ 同单位：命中被丢弃（维持现状，不融合），符合同学院约定')
    elif not new_dropped:
        print('   ⚠ 原样追加（未判为重复，可能漏检）')
    print()
    return out


# ---------- 场景 A：跨单位融合（A 缺 speakerBio，B 补全；A 地点正确不被 B 覆盖） ----------
def scenario_cross_source():
    A = {
        'sourceUrl': 'http://iqm.scnu.edu.cn/a/20191114/103.html',
        'college': 'iqm', 'campus': '石牌',
        'speaker': 'Blaizot', 'speakerAffiliation': '法国巴黎萨克雷大学',
        'lectureStart': '2019-11-15T14:30:00',
        'topic': '量子材料的反常输运性质',
        'title': '量子物质研究院学术讲座：量子材料的反常输运性质',
        'location': '理8-210',                 # A 地点正确
        'publishTime': '2019-11-14T10:00:00',
        # 注意：A 无 speakerBio
    }
    B = {
        'sourceUrl': 'https://physics.scnu.edu.cn/a/20191118/806.html',
        'college': 'physics', 'campus': '石牌',
        'speaker': 'Blaizot', 'speakerAffiliation': '法国巴黎萨克雷大学',
        'lectureStart': '2019-11-15T14:30:00',
        'topic': '量子材料的反常输运性质研究',
        'title': '物理学院学术报告：量子材料的反常输运性质研究',
        'location': '大学城理六栋301',          # 与 A 不同写法，但不参与判重/覆盖
        'speakerBio': '法国科学院院士，凝聚态物理专家',  # B 有 bio，应补全进 A
        'publishTime': '2019-11-18T10:00:00',
    }
    return 'A 跨单位融合：iqm(Blaizot)已在库，physics后抓同讲座', [A], [B]


# ---------- 场景 B：跨单位·地点写错（同精确时刻兜底命中） ----------
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
        'topic': '凝聚态物理前沿报告',            # 文本差异大，相似度<0.25
        'title': '物理学院学术报告：凝聚态物理前沿报告',
        'location': '教学楼999（地址写错）',       # 错误地点，不得覆盖 A 的正确地点
        'publishTime': '2020-03-05T10:00:00',
    }
    return 'B 跨单位·地点错误：验证不依赖地点检测 + 不覆盖 A 正确地点', [A], [B]


# ---------- 场景 C：同单位多轮（有轮次标记，B 发布更晚） ----------
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
    return 'C 同单位多轮：xyz先发第一轮后发第二轮（带标记）-> 保留最新、不融合', [A], [B]


# ---------- 场景 D：同单位无标记、时间相同（维持现状 skip 丢B，不融合） ----------
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
        'lectureStart': '2024-02-10T15:00:00',   # 同精确时刻
        'topic': '关于某某研究的进展',              # 相似度≥0.25
        'title': '关于某某研究的进展（再通知）',
        'location': '理科楼201',
        'publishTime': '2024-02-05T10:00:00',  # 更晚，但同单位无标记 -> 维持现状丢弃
    }
    return 'D 同单位无标记同时间：维持现状丢弃后发、不融合', [A], [B]


def main():
    print('验证 scraper.py 增量跨源融合定稿行为（不改库）。\n')
    a_title, a_ex, a_new = scenario_cross_source()
    out_a = _report(a_title, a_ex, a_new)
    # 验证 A 的地点未被 B 覆盖、bio 已补全
    a_merged = out_a[0]
    print(f'   [断言] A.merged={a_merged.get("merged")} '
          f'A.bio已补全={bool(a_merged.get("speakerBio"))} '
          f'A.location保持={a_merged.get("location")!r}（应仍为理8-210）')

    b_title, b_ex, b_new = scenario_cross_source_wrong_location()
    out_b = _report(b_title, b_ex, b_new)
    b_merged = out_b[0]
    print(f'   [断言] B.merged={b_merged.get("merged")} '
          f'B.location未覆盖A={b_merged.get("location")!r}（应仍为理8-210，非教学楼999）')

    c_title, c_ex, c_new = scenario_same_college_multiround()
    out_c = _report(c_title, c_ex, c_new)
    print(f'   [断言] C 应多轮替换留最新（merged 空，含B url）：'
          f'{[r.get("sourceUrl") for r in out_c]}')

    d_title, d_ex, d_new = scenario_same_college_no_marker()
    _report(d_title, d_ex, d_new)

    # ---------- 场景 E：幂等守卫（跨单位对 incremental_merge 调两次） ----------
    print('=' * 78)
    print('场景 E：幂等守卫')
    print('-' * 78)
    A = {
        'sourceUrl': 'http://iqm.scnu.edu.cn/a/20191114/103.html',
        'college': 'iqm', 'campus': '石牌',
        'speaker': 'Blaizot', 'speakerAffiliation': '法国巴黎萨克雷大学',
        'lectureStart': '2019-11-15T14:30:00',
        'topic': '量子材料的反常输运性质',
        'title': '量子物质研究院学术讲座', 'location': '理8-210',
        'publishTime': '2019-11-14T10:00:00',
    }
    B = {
        'sourceUrl': 'https://physics.scnu.edu.cn/a/20191118/806.html',
        'college': 'physics', 'campus': '石牌',
        'speaker': 'Blaizot', 'speakerAffiliation': '法国巴黎萨克雷大学',
        'lectureStart': '2019-11-15T14:30:00',
        'topic': '量子材料的反常输运',
        'title': '物理学院学术报告', 'location': '大学城理六栋301',
        'publishTime': '2019-11-18T10:00:00',
    }
    out1 = incremental_merge([dict(A)], [dict(B)])
    a1 = out1[0]
    out2 = incremental_merge(out1, [dict(B)])  # 二次摄入同一 B
    a2 = out2[0]
    print(f'   第一次融合后：sources={len(a1.get("sources") or [])} '
          f'sourceCount={a1.get("sourceCount")}')
    print(f'   第二次融合后：sources={len(a2.get("sources") or [])} '
          f'sourceCount={a2.get("sourceCount")}')
    print(f'   [断言] 幂等：第二次 sources 不增长 = {len(a1.get("sources") or []) == len(a2.get("sources") or [])}')

    print('=' * 78)
    print('汇总（定稿预期）')
    print('=' * 78)
    print('· A 跨单位：merged=True + sources含B + B.bio补全A + A.location不被B覆盖')
    print('· B 跨单位地点错：merged=True 且 A.location 保真（不依赖地点判重/覆盖）')
    print('· C 同单位多轮：多轮替换留最新，merged 空（不融合）')
    print('· D 同单位无标记：维持现状 skip 丢后发，merged 空（不融合）')
    print('· E 幂等：重复摄入同一 B，sources 不重复追加、sourceCount 不虚高')


if __name__ == '__main__':
    main()
