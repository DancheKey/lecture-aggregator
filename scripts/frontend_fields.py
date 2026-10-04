#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端下发字段白名单 + 长文本分离 —— 展示层加工的单一事实源。

背景（2026-09-28 首屏加载卡顿修复）：全库 3804 条的前端 JSON 里，
`llmSelfExtract`/`qaRepaired`/`images` 等 30 个内部审计与
溯源字段前端**一个都没读**，却占 8.3% 原始体积（0.46MB / gzip 后约 0.15MB）。
（例外：`timeConfidence` 于 2026-10-04 方案 A 第 5 步进入白名单——
  前端 isDateSuspect 用它给 low 置信度记录打「日期待核」角标。）
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
反向的「白名单漏了」由 tests/test_frontend_consistency.py 锁定。
"""
import hashlib

# 前端可消费的数据字段（条目级）。
FRONTEND_KEEP = frozenset({
    # 标识与跳转
    'sourceUrl', 'title', 'listTitle', 'topic',
    # 时间
    'lectureStart', 'lectureEnd', 'publishTime', 'timeUnknown',
    # 时间置信度（2026-10-04 方案 A 第 5 步）：low 的记录在卡片上显示「日期待核」
    # 角标（site/app.display.js::isDateSuspect）。此前它被白名单剥离、全链路无人
    # 消费，年份存疑的记录被「假装确定」地分组/排序。中/高置信度也随字段下发——
    # 体积代价约等于零（短字符串），换来筛选与排障时可直接看置信度。
    'timeConfidence',
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
    # 派生键（由 with_unit / _attach_unit_types / split_long_text 加工，strip 不得删除）
    'speakerKeys',
    'b',  # 长文本所在桶号（0-15）：展开某条时前端据此只拉那一桶 detail 分片
})


def strip_frontend_fields(item):
    """返回只含白名单键的**新** dict；原记录不被修改（不污染主数据）。"""
    return {k: v for k, v in item.items() if k in FRONTEND_KEEP}


def strip_frontend_fields_all(items):
    """列表版strip。就地返回新列表，元素为新 dict。"""
    return [strip_frontend_fields(it) for it in items]


# ---------- 长文本（简介/摘要）分离 ----------

def lt_key(item):
    """长文本条目的稳定键。必须与 site/app.data.js 的 _ltKey() 逐字一致。

    ⚠ 前端实现随分片拆分已从 app.js 移到 app.data.js（旧注释写 app.js 会误导定位）。
    两端无共享代码，一致性由 tests/js/app_ltkey_consistency.js 跨语言逐条比对守护：
    改任一侧即红（否则症状是全站简介/摘要静默变空、零报错）。
    """
    return '%s#%s' % (item.get('sourceUrl') or '',
                      '' if item.get('lectureIndex') is None else item.get('lectureIndex'))


DETAIL_BUCKETS = 16   # 2^4，按 sha1 前 1 位十六进制分 16 桶


def lt_bucket(item):
    """返回该条所属的长文本桶号；无长文本返回 None。

    供两条产物路径共用：split_long_text()（主分片，剥离长文本）与
    latest.json（内联截断预览，但**必须**带上桶号，否则前端展开那 50 条时
    无从知道去哪一桶取全文，只会永远停在 220 字预览）。
    """
    if not (item.get('speakerBio') or item.get('abstract')):
        return None
    h = hashlib.sha1(lt_key(item).encode('utf-8')).hexdigest()
    return int(h[0], 16) % DETAIL_BUCKETS


def split_long_text(data):
    """把 speakerBio / abstract 从条目剥离到独立 detail 分片。

    背景（2026-09-28 首屏加载卡顿）：这两个长文本占前端 JSON 的 68%
    （gzip 后约 1.57MB / 共 1.96MB），而列表里默认只显示 2~3 行 clamp，
    真正需要全文的场景只有「点展开」与「搜索命中简介/摘要正文」。
    放进主分片等于让每个访客首屏都为此付全部字节。

    产物：
      lectures/detail/manifest.json —— 桶号 -> 文件名
      lectures/detail/detail_XX.json —— [{key, speakerBio, abstract}, ...]
    前端按需取用（展开单条时取该条所在桶，搜索时预取全量），
    能力不丢，只是把加载时机推迟到真正需要的那一刻。

    ⚠ **仅 generate 端调用**：server.py 的 /api/lectures 是本地开发服务器，
    直连本机无带宽成本，故**有意**保持长文本内联（少一次请求、调试更直观）。
    这是一处刻意的两端差异，语义由 tests/test_frontend_consistency.py 的
    test_public_split_vs_local_inline 显式锁定——差异存在可以，差异漂移不行。
    """
    buckets = [[] for _ in range(DETAIL_BUCKETS)]
    stripped = []
    for item in data:
        it = dict(item)
        bio = it.pop('speakerBio', None)
        ab = it.pop('abstract', None)
        b = lt_bucket(item)
        if b is not None:
            # 条目上标出所在桶号（3 字符），前端展开时据此只拉那一桶。
            # 不能让前端自己算：WebCrypto 的 SHA-1 是异步的，映射逻辑会难维护。
            it['b'] = b
            buckets[b].append(
                {'key': lt_key(item), 'speakerBio': bio or '', 'abstract': ab or ''})
        stripped.append(it)
    return stripped, buckets
