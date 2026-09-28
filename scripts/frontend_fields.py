#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端下发字段白名单——源数据保持全量，只在**展示层输出**时裁掉无人消费的键。

背景（2026-09-28 首屏加载卡顿修复）：全库 3804 条的前端 JSON 里，
`llmSelfExtract`/`qaRepaired`/`timeConfidence`/`images` 等 31 个内部审计与
溯源字段前端**一个都没读**，却占 8.3% 原始体积（0.46MB / gzip 后约 0.15MB）。
GitHub Pages 只支持 gzip（brotli/zstd 均不识别），编码层面已无空间，
所以体积只能从「少传字段」和「长文本按需」两头抠。

⚠ 铁律：本模块**只影响展示层输出**（site/*.json 与 server.py 的 /api/lectures），
`data/lectures.json` 永远保持全量——images、llmSelfExtract、qaRepaired 等是
溯源与日后重处理用的索引数据，删了就再也回不来（项目约定：images 全量存储、
不截断，保留作日后重处理索引）。

⚠ 两处必须同步调用（generate_frontend_data.py 与 server.py），
否则本地 /api/lectures 与公网静态切片的字段集分叉，
tests/test_frontend_consistency.py 会逐条比对失败（这正是该守卫的作用）。

白名单怎么定的：机械提取 site/index.html + site/app.js 中所有 `对象.字段`
引用，扣掉 JS/Vue 内置与局部变量，剩下的**且**存在于 data/lectures.json 的键取并集，
再逐个人工复核（`lectureCount` 曾在候选里，但实际是 app.js:255 的 `head.lectureCount`
——分组头对象属性，不是条目字段，故不入白名单）。
新增前端字段时必须同步本白名单，否则该字段在前端恒为 undefined；
tests/test_frontend_schema.py 会拦截「前端读了但产出方没有」，
反向的「白名单漏了」由 tests/test_frontend_fields.py 锁定。
"""

# 前端可消费的数据字段（条目级）。
FRONTEND_KEEP = frozenset({
    # 标识与跳转
    'sourceUrl', 'title', 'listTitle', 'topic',
    # 时间
    'lectureStart', 'lectureEnd', 'publishTime', 'timeUnknown',
    # 人员
    'speaker', 'speakerAffiliation', 'speakerBio', 'host', 'participants',
    # 地点与组织
    'location', 'campus', 'college', 'organizer', 'notes',
    # 长文本（列表内clamp 展示 + 搜索）
    'abstract',
    # 多场拆分
    'lectureIndex', 'isMultiLecture', 'unitType',
    # 跨源合并
    'merged', 'sources', 'sourceCount',
    # 海报重处理台账（2026-09-10 决策：前端虽不渲染，但这三个键是
    # 「哪些讲座靠海报、用的哪种解析方式」的处理台账，随产物携带，勿删）
    'images', 'hasPosterImage', 'imageParseMethod',
    # 派生键（由 with_unit / _attach_unit_types 加工，strip 不得删除）
    'speakerKeys',
})


def strip_frontend_fields(item):
    """返回只含白名单键的**新** dict；原记录不被修改（不污染主数据）。"""
    return {k: v for k, v in item.items() if k in FRONTEND_KEEP}


def strip_frontend_fields_all(items):
    """列表版strip。就地返回新列表，元素为新 dict。"""
    return [strip_frontend_fields(it) for it in items]
