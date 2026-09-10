# -*- coding: utf-8 -*-
"""字段边界统一词表（单一事实源，2026-09-05 全局诊断产物）。

背景：此前"摘要/简介在哪里结束"这一定义散落四处且互不一致——
  parsers 摘要正则的 lookahead + 尾部清理、hybrid._RICH_CUT、
  llm_provider._ABSTRACT_BOUNDS、模型A prompt 第7条。
漂移导致 physics787（规则裸「单位」锚点命中「组织单位」内部留下残尾）与
maths8806（模型A脏值覆盖规则干净值，锚点表漏"题 目/报 告 人"空格变体）两类污染。

本模块只定义词表与纯函数，不 import 项目内其他模块，parsers / hybrid /
llm_provider 三方均可安全引用。改词表 = 改全局标准。

VOCAB_VERSION：词表内容版本号。llm_provider 的文本缓存按此失效——词表或
截断逻辑变更后递增，已缓存的模型提取结果自动作废重提，修复得以重放。
"""

import re

# 词表/边界逻辑版本。变更本文件任何影响提取结果的词表后必须递增。
# 2026-09-05.2：5 页验收修补——①移除裸「感兴趣」锚点（iqm552 正文被拦腰截断）；
# ②FIELD 锚点补「讲座人」（physics791 旧页标签）；③出口闸门新增"纯短中文残段"
# 识别（psy229 摘要='学术讲座'式标题垃圾）。
# 2026-09-05.3：残留清单 194 条专项——①站点系列名模板串锚点（"物理学院学术报告
# （第N期）/新世纪论坛学术报告"，拖挂 159 条 speakerBio）；②尾部残段修剪
# trim_dangling_labels（" 报告"×18、"…实验室主办"×5）；③摘要 lookahead 复合
# 标签（报告时间/报告地点）防"报告"两字残留。
VOCAB_VERSION = '2026-09-05.3'

# ---------------------------------------------------------------------------
# 字段标签规范名（用于折叠 CMS 把标签拆成单字加空格的形态，如「报 告 人」→「报告人」）
# 顺序无关；折叠时按"长标签优先"匹配，防止「主办单位」被「主办」先吃掉。
# ---------------------------------------------------------------------------
FIELD_LABELS = (
    # 主题/题目
    '讲座主题', '讲座题目', '报告题目', '演讲题目', '报告主题', '题目', '主题',
    # 时间地点人物
    '讲座地点', '讲座时间', '会议时间', '会议地点', '地点', '时间',
    '主讲嘉宾介绍', '主讲人简介', '主讲人简历', '报告人简介', '专家简介', '个人简介',
    '主讲嘉宾', '主讲人', '主讲师', '报告人', '讲座嘉宾', '演讲人', '主讲',
    '学术主持', '主持人', '邀请人',
    # 单位类（复合标签整体必须先于裸词折叠/截断，防「单位」命中「组织单位」内部）
    '主办单位', '承办单位', '协办单位', '组织单位', '支持单位', '指导单位',
    # 简介/摘要/内容
    '讲座内容提要', '讲座内容摘要', '内容提要', '讲座摘要', '报告摘要', '内容摘要',
    '内容简介', '讲座简介', '报告内容', '讲座概要', '内容概要', '主要内容', '摘要',
    '简历', '简介',
    # 其他常用字段（防越界）
    '面向对象', '报名方式', '联系方式', '发布时间', '发布日期', '来源',
)

# ---------------------------------------------------------------------------
# 摘要/简介值内出现即视为"后续元信息块/模板尾部"的锚点。
# 分三档，截断口径由 _rich_boundary_re(aggressive) 控制：
#   SECTION：节标题式标签，几乎不在散文出现——无需冒号即可锚定；
#   FIELD  ：字段标签，散文中可能出现（如"这个讲座的题目是…"）——必须紧跟冒号才锚定；
#   INVITE/SIG/FOOTER：模板语/署名/页脚公式——无需冒号。
# ---------------------------------------------------------------------------
_SECTION_LABELS = (
    '主讲嘉宾介绍', '主讲人简介', '主讲人简历', '报告人简介', '专家简介', '个人简介',
    '主讲人介绍', '报告人介绍', '主讲介绍', '专家介绍', '作者简介',
    '组织单位', '主办单位', '承办单位', '协办单位', '支持单位', '指导单位',
    '主办：', '承办：', '协办：',
    '研讨会议程', '会议简介', '论坛简介', '沙龙简介', '讲座预告',
    '资讯及通知', '相关新闻', '最新动态', '推荐阅读', '相关文章', '相关链接',
    '附件下载', '通知公告', '站内搜索', '快速导航',
)

_FIELD_LABELS = (
    '题目', '主题', '报告题目', '讲座题目', '演讲题目', '报告主题', '讲座主题',
    '报告摘要', '报告时间', '报告地点',
    '时间', '地点', '主讲人', '报告人', '讲座人', '主持人', '邀请人', '演讲人',
    '主讲嘉宾', '报告专家', '讲座嘉宾', '点评人', '评议人', '与谈人',
    '报名方式', '报名链接', '参会方式', '联系方式', '面向对象', '参与方式',
    '会议时间', '会议地点', '讲座时间', '讲座地点',
    '编辑', '审核', '摄影', '来源', '点击',
)

_INVITE_PHRASES = (
    '诚挚邀请', '敬请期待', '敬请光临', '请各位',
    '欢迎老师', '欢迎同学', '欢迎广大', '欢迎各位', '欢迎师生', '欢迎全校',
    '欢迎大家', '欢迎有兴趣', '欢迎感兴趣', '欢迎参加', '欢迎莅临', '欢迎光临',
    '欢迎届时', '欢迎踊跃', '诚邀', '期待您的', '欢迎扫码', '欢迎关注',
    '欢迎各位老师', '欢迎广大师生',
    # 注意：裸「感兴趣」不能作锚点——"第一代恒星感兴趣的伽莫夫能区"这类正文
    # 会被拦腰截断（iqm552 验收实测）。邀请语形态一律以「欢迎/诚邀」开头。
)

_FOOTER_PHRASES = (
    '版权所有', '粤ICP', 'All Rights Reserved', 'ICP备',
    '上一篇', '下一篇', '相关推荐', '网友评论', '点击查看', '阅读原文', '更多资讯',
    '腾讯会议', '会议号', 'Meeting ID', 'Zoom', '直播链接', '观看方式',
    '扫描二维码', '长按识别', '会议密码',
)

# 站点系列名模板串（2026-09-05 残留清单 194 条专项）：JS 渲染站的 div.content 在
# 简介/摘要之后拼接页头系列名（physics 站"物理学院学术报告（第N期）"、
# "新世纪论坛学术报告"、physics 老页"物理与电信工程学院学术报告"），曾拖挂在
# 159 条 speakerBio 尾部。锚点带"（第N期）"特征或"新世纪论坛"前缀——散文正文中
# 的"学术报告"一词不会误截。
_SERIES_TEMPLATE_RE = re.compile(
    r'[\u4e00-\u9fff]{0,12}学术报告\s*[（(]\s*第.{1,6}期\s*[）)]'
    r'|新世纪论坛学术报告'
    r'|[\u4e00-\u9fff]{0,14}学术报告$'
)

# 尾部标签残段修剪（出口闸门调用）：值尾为"空白+标签前缀/署名"形态即修剪。
# 均要求空白前缀——自然散文句尾不会以空格+单字标签收尾；修剪后 <12 字则放弃
# （保底不把短值剪残）。
_DANGLING_TAIL_RE = re.compile(
    r'\s+(?:'
    r'[\u4e00-\u9fff]{0,14}学术报告(?:\s*[（(]\s*第.{1,6}期\s*[）)])?'
    r'|报告'
    r'|讲座'
    r'|[\u4e00-\u9fff&\u3000\s""'']{1,45}主办'
    r')$'
)


def trim_dangling_labels(value):
    """修剪 rich 文本尾部的模板串/标签残段（physics 模板串、iqm『 报告』、psy『主办』署名）。

    与 trim_dangling_unit_prefix 同族：只动"空白+已知名词"的尾部形态，且修剪后
    仍 ≥12 字才采用；否则返回原值（不把短值剪残）。
    """
    if not value:
        return value
    v = str(value).strip()
    prev = None
    while prev != v and len(v) >= 12:
        prev = v
        v2 = _DANGLING_TAIL_RE.sub('', v).strip()
        v3 = _SERIES_TEMPLATE_RE.sub('', v2).strip() if _SERIES_TEMPLATE_RE.search(v2) else v2
        if len(v3) < 12:
            break
        v = v3
    return v

# 元信息块标签（含「组织单位：」等复合标签）。这些标签出现在 rich 文本尾部
# 说明后续是字段元数据而非正文；锚定时须匹配到冒号，防止散文误伤。
UNIT_LABEL_ALTS = ('组织单位', '主办单位', '承办单位', '协办单位', '支持单位', '指导单位')


def _spaced(label):
    """把标签转成容忍字间空格的正则片段：「报告人」→ r'报\\s*告\\s*人'。"""
    return r'\s*'.join(re.escape(c) for c in label)


def _alts(labels, spaced=True):
    src = (_spaced(x) if spaced else re.escape(x) for x in labels)
    return '(?:' + '|'.join(src) + ')'


def rich_boundary_re(aggressive=False):
    """摘要/简介值内"到此为止"的边界正则（未编译前缀由调用方拼接）。

    aggressive=True（模型A输出专用）：追加章节序号「一、/1.」与「第N期」——
    A 常把后续整段章节吞入，需更激进的截断；规则值出口闸门用默认口径，
    避免截断正文中合法的枚举段落。
    """
    parts = [
        _alts(_SECTION_LABELS),                                   # 节标题（无需冒号）
        _alts(_FIELD_LABELS) + r'\s*[：:]',                       # 字段标签（须冒号）
        _alts(UNIT_LABEL_ALTS) + r'\s*[：:]?',                    # 单位类复合标签
        r'[\u4e00-\u9fff]{0,12}学术报告\s*[（(]\s*第.{1,6}期\s*[）)]',  # 站点系列名模板串
        r'新世纪论坛学术报告',
        _alts(_INVITE_PHRASES, spaced=False),
        _alts(_FOOTER_PHRASES, spaced=False),
    ]
    if aggressive:
        parts += [
            r'[一二三四五六七八九十百零0-9]+\s*[、.．](?!\d)',
            r'第.{1,6}期',
        ]
    return re.compile(r'(?:' + '|'.join(parts) + ')')


# 模块级缓存，避免每次调用重新编译
_RICH_BOUNDARY_CONSERVATIVE = rich_boundary_re(aggressive=False)
_RICH_BOUNDARY_AGGRESSIVE = rich_boundary_re(aggressive=True)


def fold_labels(text):
    """折叠字段标签内的 CMS 拆分空格：「报 告 人：」「题 目：」→「报告人：」「题目：」。

    与 parsers._normalize_label_text 的第2步同口径（该函数还含 N1e/方括号形态，
    那些仅在 HTML 正文链路需要）。模型A返回值与出口闸门在截断前先过此函数，
    保证"报 告 人："这类空格变体也能被 FIELD 锚点（须冒号）命中。
    """
    if not text:
        return text
    for label in sorted(set(FIELD_LABELS) | set(_SECTION_LABELS), key=len, reverse=True):
        text = re.sub(_spaced(label), label, text)
    return text


def truncate_rich_text(value, aggressive=False, min_len=0):
    """把摘要/简介截断到第一个边界锚点，去除吞入的元信息块/邀请语/页脚。

    返回截断后的值（strip）；min_len>0 时若截断后长度不足 min_len 则返回原值
    （护栏：边界词出现在头部时不硬截，交给调用方决定置空/标记）。
    """
    if not value:
        return value
    text = fold_labels(str(value))
    m = (_RICH_BOUNDARY_AGGRESSIVE if aggressive else _RICH_BOUNDARY_CONSERVATIVE).search(text)
    if not m:
        return text.strip()
    cut = text[:m.start()].strip()
    if min_len and len(cut) < min_len:
        return text.strip()
    return cut


# ---------------------------------------------------------------------------
# llm_provider 文本通道专用：ABSTRACT_BOUNDS（find 式 earliest-substring 语义，
# 与历史 _ABSTRACT_BOUNDS 行为一致），由统一词表派生，防止再度漂移。
# 注意：find 式匹配无法校验冒号邻接，散文高频裸词（时间/地点/题目等）若不带
# 冒号混入会截断合法正文（如"时间序列分析"），故字段标签一律带全/半角冒号。
# ---------------------------------------------------------------------------
_COLON_FIELD_BOUNDS = tuple(
    lbl + c
    for lbl in ('题目', '主题', '时间', '地点', '主讲人', '报告人', '主持人',
                '邀请人', '演讲人', '主讲嘉宾', '报告专家', '讲座嘉宾',
                '编辑', '审核', '摄影', '来源', '点击',
                '报名方式', '报名链接', '参会方式', '联系方式',
                '面向对象', '参与方式', '会议时间', '会议地点',
                '讲座时间', '讲座地点')
    for c in ('：', ':')
)

ABSTRACT_BOUNDS = (
    '附件', '附录', '课程名称', '授课对象',
) + tuple(x for x in _SECTION_LABELS if ':' not in x) \
    + UNIT_LABEL_ALTS \
    + _COLON_FIELD_BOUNDS \
    + tuple(x for x in _INVITE_PHRASES if ':' not in x) \
    + tuple(x for x in _FOOTER_PHRASES if ':' not in x)


# 邀请语尾部清理（ parsers 三处历史拷贝统一引用）。
# 仅剔除「邀请类」尾部，不误伤正文合法的「受到欢迎」「广受欢迎」等表述，
# 更不能截断含「感兴趣」的正文句子（裸「感兴趣」已移除，见 _INVITE_PHRASES 注）。
INVITATION_TAIL_RE = re.compile(
    r'\s*(?:'
    r'诚挚邀请|敬请|请各位|'
    r'欢迎\s*(?:广大|各位|师生|同学|老师|全校|大家|有兴趣|感兴趣|'
    r'莅临|参加|光临|届时|踊跃|提出|关注)'
    r').*$'
)


def trim_dangling_unit_prefix(value):
    """去除句末悬挂的单位词残段：「…相关工作。 组织」→「…相关工作。」。

    仅当残段紧跟句末标点（。．.!！;；）+ 空白时才修剪——「由学生会组织」这类
    以动词收尾的合法句子（残段紧跟普通汉字）不受影响。
    """
    if not value:
        return value
    return re.sub(r'[。．.！!；;]\s*(?:组织|主办|承办|协办|支持|指导)$', '', str(value)).strip()


# ===========================================================================
# 语义词表：职称 / 行政职务 / 尊称（G4 收敛，2026-09-10）
#
# 收敛前同一类词表在 5+ 处各写各的：
#   parsers._ORG_TITLE_SUFFIXES（16 项职务）+ 倒装职务正则内联同一份表
#   hybrid._SPEAKER_TITLE_NOISE / _AFFIL_INVALID / _TITLE_RE / 尾部剥离正则 ×3 / _DIRTY_TITLE_RE
#   scripts/audit_fields._AFFIL_TITLE_ONLY
#   server._speaker_keys / scripts/generate_frontend_data.speaker_keys
# 新增一个职称要改 5 处，漏改即产生「某模块认识、某模块不认识」的静默分叉
# （C2 多主讲人误判、C3 守卫缺失即由此暴露）。现统一在此定义，各处按语义组合引用。
#
# 注意：本块只影响「姓名/单位的职称剥离与判脏」，不参与摘要边界截断，
# 故**不递增 VOCAB_VERSION**（不触发 LLM 文本缓存失效）。
# ===========================================================================

# A) 学术职称/称号后缀。剥离姓名尾部职称时用；顺序须长词优先。
#    内容与 server/generate 原 speaker_keys 表逐字一致（收敛不得改变既有行为）。
NAME_TITLE_SUFFIXES = (
    '博士生导师', '硕士生导师', '特聘教授', '特任教授', '长聘教授', '副教授',
    '助理教授', '副研究员', '助理研究员', '研究员', '教授', '讲师', '博士后',
    '博士', '院士', '老师', '导师',
)

# B) 行政职务后缀。用于「倒装职务 → 机构名」推导，以及单位尾部的职务剥离。
#    ⚠ 复合职务必须整体成词且排在单体之前，否则会被单体截断：
#    「总经理」被「经理」截成「总」、「副校长」被「校长」截成「副」、
#    「主任医师」被「主任」截成「医师」。
ORG_TITLE_SUFFIXES = (
    '副总编辑', '副主任', '副校长', '副会长', '总经理', '主任医师', '副主任医师',
    '总编辑', '主任', '处长', '院长', '所长', '科长', '局长', '部长', '总监',
    '经理', '工程师', '书记', '主席', '顾问', '会长',
)

# C) 通用尊称
HONORIFIC_SUFFIXES = ('先生', '女士')

# 尾部剥离用超集：姓名/单位末尾出现即应去掉的职称、职务、尊称字样。
TAIL_TITLE_SUFFIXES = NAME_TITLE_SUFFIXES + ORG_TITLE_SUFFIXES + HONORIFIC_SUFFIXES

# 机构性单位结尾词：判断「XX 中心主任」里的 XX 是否为机构名
# （parsers._derive_org_from_title 用）。
ORG_UNIT_ENDS = (
    '中心', '总公司', '学院', '院', '系', '所', '处', '局', '部', '公司', '集团',
    '大学', '办公室', '馆', '站', '室', '社', '刊', '报', '台',
)


def _alt(words):
    """构造「备选词」正则片段：长度降序 + `(?:...)` 分组。

    ⚠️ 分组不可省：`'a|b$'` 的 `$` 只绑定最后一个分支 b，前面的 a 会退化成
    任意位置匹配（G4 收敛时实测踩到此坑，见 docs 记录）。
    """
    return '(?:' + '|'.join(re.escape(w) for w in sorted(set(words), key=len, reverse=True)) + ')'


NAME_TITLE_SUFFIX_RE = re.compile(_alt(NAME_TITLE_SUFFIXES))
ORG_TITLE_SUFFIX_RE = re.compile(_alt(ORG_TITLE_SUFFIXES))
TAIL_TITLE_RE = re.compile(_alt(TAIL_TITLE_SUFFIXES) + '$')
# 整串**仅由**职称/职务/尊称构成（可重复），如「教授」「副教授研究员」。
# 显式带 ^...$：调用方可能用 match / search / fullmatch，锚定后三者语义一致。
TITLE_ONLY_RE = re.compile('^(?:' + _alt(TAIL_TITLE_SUFFIXES) + ')+$')


def strip_name_title_suffix(name):
    """去掉姓名尾部的**一个**职称/称号后缀（长词优先）。

    与旧 server._speaker_keys / generate.speaker_keys 内的循环逐字等价；
    按长度降序比较，保证「副教授」先于「教授」命中（否则会截成「张三副」）。
    """
    if not name:
        return name
    t = str(name)
    for s in sorted(set(NAME_TITLE_SUFFIXES), key=len, reverse=True):
        if t.endswith(s) and len(t) > len(s):
            return t[:-len(s)]
    return t


def split_speaker_names(raw):
    """按 `[、,，/]` 拆分多人姓名并去空白。"""
    return [p.strip() for p in re.split(r'[、,，/]', str(raw)) if p.strip()]


def speaker_keys(name):
    """讲者归一化键：全角转半角、去职称后缀；英文 lower；多人逐人拆键。

    本函数是 server.py `_speaker_keys` 与 scripts/generate_frontend_data.py
    `speaker_keys()` 的**唯一实现**——两者必须严格一致（前端一致性守卫测试会
    拦截部署），收敛到此处后天然一致，不会再出现两侧漂移。
    """
    if not name:
        return []
    t = ''.join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in str(name))
    t = strip_name_title_suffix(t)
    keys = []
    for part in split_speaker_names(t):
        if re.search(r'[A-Za-z]', part):
            part = part.lower()
        keys.append(part)
    return keys


def is_title_only(value):
    """整串是否**仅**由职称/职务/尊称构成——用于拒绝把职称当单位/姓名的脏值。"""
    if not value:
        return False
    return bool(TITLE_ONLY_RE.fullmatch(str(value).strip()))
