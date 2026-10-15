# -*- coding: utf-8 -*-
"""姓名+机构首字粘连修复的回归门禁（2026-10-14，评审 P2 + 复核补）。

物理学院 / 计算机学院同一套 CMS 模板的标签行形如
「报告专家：马余强 中国科学院院士」。CJK 间空格被折叠后到达 speaker 提取处
为「马余强中国科学院院士」，4→2 字降序取姓名时把机构首字吞进姓名
（speaker='马余强中'），且 rest 起点随之右移导致 affiliation 同步丢首字
（affiliation='国科学院'）——**同一处截断的两个症状**。

现状为两层保护：
  ① N1a 词块空格主判据（两侧 >=2 字 CJK 空格保留，见 test_n1a_word_boundary_spaces）——
     源页有空格时姓名/单位天然分开，不依赖词表；
  ② _extract_speaker 的单位首字守卫（parsers.py:4914 起）——处理**源页无空格**
     的折叠形态。2026-10-14 复核收紧：拆分除 _AFF_END_KW（末字归还后以单位词
     收尾）外还须**原文空格边界证据**（_sp_glue_boundary_evidence）：末字后无
     空白=被吞的机构首字→拆；末字后有空白=真名→不拆。复核实测单靠
     _AFF_END_KW 会把真实 4 字姓名砍成 3 字（陈谢沅澧→陈谢沅、aff='澧国防…'）。

⚠ 本门禁同时锁住**反例**（末字巧合，库中值本就正确）——上一版用
「机构通名前缀枚举」的修法正是因为误杀这批而废弃（实测把
秦晓华/华南师范大学 改成 秦晓华南、彭再云/云南师范大学 改成 彭再云云）。
前缀枚举不可行：粘连与巧合在字符串层完全同构
（马余强中+国科学院 → 中国科学院   vs   秦晓华+南师范大学 → 华南师范大学），
只能靠「name 是否已达 4 字上限」+「原文空格位置」区分。

⚠ 2026-10-15 CI 修复：本仓库所有门禁测试均为 unittest 直跑范式
（workflow 里 `python tests/xxx.py`，CI 不装 pytest）——本文件从 92a4bdf
引入起是 pytest 风格（import pytest + 模块级函数），CI 上 `import pytest`
直接 ModuleNotFoundError，golden 门禁红（本地因装有 pytest 两种跑法都不暴露）。
现转为与仓库其余 40+ 测试文件一致的 unittest 范式，测试内容不变。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scraper'))

# 锁纯规则基线：必须在 import parsers 之前设置（env_flags 模块级常量导入即求值）。
# 否则本地 .env 有 ZHIPU_API_KEY 时 LLM 通道激活，VLM 会把 affiliation 等
# 字段补全，GLUE_CASES 的断言恒绿（2026-10-14 实测：v4 的 maths 回归桩
# 在 LLM 通道下 aff 被 VLM 填上而假绿，纯规则通道才复现 aff=''）。
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers  # noqa: E402


def _parse(text, college='物理学院'):
    """以物理/cs 模板的最小页面跑一次 parse_detail，返回 (speaker, affiliation)。"""
    html = f"<html><body><div class='content'><p>{text}</p></div></body></html>"
    r = parsers.parse_detail(html, 'https://physics.scnu.edu.cn/a/2024/1.html', college, '石牌')
    return r.get('speaker'), r.get('speakerAffiliation')


# ---- 正例：粘连须被拆开，且 affiliation 补回首字 --------------------------
# (源文标签行, 期望 speaker, 期望 affiliation 或 None=不校验)
GLUE_CASES = [
    ('报告专家：马余强 中国科学院院士', '马余强', '中国科学院'),
    ('报告人：宗成庆 中科院自动化所', '宗成庆', '中科院自动化所'),
    ('报告人：马余强 中国科学院', '马余强', '中国科学院'),
    ('报告人：胡一南 中科院生物物理研究所', '胡一南', '中科院生物物理研究所'),
    ('报告人：鹿泽光 中科国鼎数据科学研究院', '鹿泽光', '中科国鼎数据科学研究院'),
    ('报告人：何晓东 中科院精密测量科学与技术创新研究院', '何晓东', None),
    ('报告人：周忆如 慕尼黑大学', '周忆如', None),
    ('报告人：金耀初 英国萨里大学', '金耀初', None),
    ('报告人：林学民 新南威尔士大学', '林学民', None),
    # 2026-10-14 修正：原写作「郭庆华 华中师范大学 / 期望 郭庆华」——那是把
    # 输入改成 3 字名来适配 HEAD 的错误产出（测试造假）。源页
    # physics.scnu.edu.cn/a/20241106/12852.html 原文为「报告专家：郭庆 华中师范大学」
    # （meta description 逐字可查），真名就是「郭庆」。期望值按源页原文锁定。
    ('报告人：郭庆 华中师范大学', '郭庆', '华中师范大学'),
    ('报告人：赵天明 华南师范大学', '赵天明', None),
    ('报告人：张文生 中国科学院自动化研究所', '张文生', None),
    ('报告人：陈伟 中山大学', '陈伟', None),
    ('报告人：孙为军 广东工业大学', '孙为军', None),
    ('报告人：韩锦森 国防科技大学', '韩锦森', None),
    ('报告人：袁锦昀 东莞理工大学', '袁锦昀', None),
    ('报告人：王玉成 南方科技大学', '王玉成', None),
    ('报告人：王平辉 西安交通大学', '王平辉', None),
]

# ---- 反例：末字巧合，库中值本就正确，严禁被改 ------------------------------
# 这批是上一版「机构前缀枚举」修法的误杀对象（已实测被改成 秦晓华南 / 彭再云云 等）。
COINCIDENT_CASES = [
    ('报告人：秦晓华 华南师范大学', '秦晓华', '华南师范大学'),
    ('报告人：钟新华 华南农业大学', '钟新华', '华南农业大学'),
    ('报告人：王震中 中国社会科学院', '王震中', '中国社会科学院'),
    ('报告人：彭再云 云南师范大学', '彭再云', '云南师范大学'),
    ('报告人：麻小南 南开大学', '麻小南', '南开大学'),
    ('报告人：陈玺 上海大学', '陈玺', '上海大学'),
    ('报告人：杨曦 厦门大学', '杨曦', '厦门大学'),
    ('报告人：王知津 南开大学教授', '王知津', '南开大学'),
]

# N1a 词块空格（姓名/单位的主判据）---------------------------------------
# 2026-10-14：N1a 的 O3a「两侧各 >=2 字保留词块空格」曾是**死代码**——正则
# r'([\u4e00-\u9fff])(\s{1,2})([\u4e00-\u9fff])' 只捕获单字作 left/right，
# 回调判据 len(left)>=2 恒为假，空格实际被全删（与 keep_word_boundaries=False
# 同构）。后果：get_text(' ') 从标签边界产生的空格、源页手打空格全部灭失，
# _extract_speaker 的「空格边界优先」路径（parsers.py 4780-4802，为 bug 4267
# 设计）从未激活，所有「姓名 单位」案例被迫走 4→2 字降序 + 词表守卫兜底，
# 词表覆盖不全即双错（郭庆华中/中师范大学实测）。
#
# 修复后：两侧均为 >=2 字 CJK 词块的空格保留，成为姓名/单位的主判据；
# 单字侧空格的删除行为与旧实现逐字节一致（OCR 噪声语义不变）。
N1A_CASES = [
    # (输入, 期望输出, 说明)
    ('郭庆 华中师范大学', '郭庆 华中师范大学', '两侧>=2字词块空格保留（主判据）'),
    ('马余强 中国科学院院士', '马余强 中国科学院院士', '同上'),
    ('维护 王婧 传统', '维护 王婧 传统', 'OCR 词块边界（O3a 原始动机）'),
    ('张三 教授', '张三 教授', '姓名-职称空格保留，交由讲者链路消费'),
    ('题 目：时间', '题目：时间', '单字侧空格仍删（OCR 噪声语义不变）'),
    ('王 教授', '王教授', '姓名内部误插空格仍删（OCR 纠偏语义不变）'),
]

# 端到端：真实 4 字姓名 + 机构（带空格，真实源页形态）——守卫不得误拆。
# 修复前（_AFF_END_KW 单独判据）实测：陈谢沅澧→陈谢沅 / aff='澧国防科技大学…'。
FOUR_CHAR_NAME_CASES = [
    ('报告人：陈谢沅澧 国防科技大学智能科学学院', '陈谢沅澧', '国防科技大学智能科学学院'),
    ('报告人：松野诚也 日本明治学院大学国际和平研究所', '松野诚也', None),
    ('报告人：姆文当卡 民主共和国国家社会工作者学院', '姆文当卡', None),
    ('报告人：王冠中 中国科学技术大学', '王冠中', '中国科学技术大学'),
    ('报告人：范传东 东莞市教育局教研室', '范传东', None),
]


class TestGlueSplit(unittest.TestCase):
    """正例：粘连须被拆开，且 affiliation 补回首字。"""

    def test_glue_split(self):
        for text, exp_sp, exp_aff in GLUE_CASES:
            with self.subTest(text=text):
                sp, aff = _parse(text)
                self.assertEqual(sp, exp_sp, f'{text!r} speaker 应为 {exp_sp!r}，实得 {sp!r}')
                if exp_aff is not None:
                    self.assertEqual(aff, exp_aff, f'{text!r} affiliation 应为 {exp_aff!r}，实得 {aff!r}')


class TestCoincidenceUntouched(unittest.TestCase):
    """反例：末字巧合，库中值本就正确，严禁被改。"""

    def test_coincidence_untouched(self):
        """3 字姓名 + 末字恰为机构首字 —— 属巧合，不得回退。"""
        for text, exp_sp, exp_aff in COINCIDENT_CASES:
            with self.subTest(text=text):
                sp, aff = _parse(text)
                self.assertEqual(sp, exp_sp, f'{text!r} speaker 应保持 {exp_sp!r}，实得 {sp!r}（末字巧合被误判为粘连）')
                self.assertEqual(aff, exp_aff, f'{text!r} affiliation 应保持 {exp_aff!r}，实得 {aff!r}')


class TestGuardLoadBearing(unittest.TestCase):
    """破坏验证：把守卫整段摘掉，本门禁必须变红。"""

    def test_guard_is_actually_load_bearing(self):
        """守卫失效时正例必须失败。

        2026-10-14 修正：原版用**带空格**输入「马余强 中国科学院院士」——N1a 词块
        空格修复（见 test_n1a_word_boundary_spaces）后，空格优先路径会天然把姓名
        切到 3 字，摘掉守卫也恒绿，破坏验证失效。改用**无空格**输入：4→2 字降序
        取名得 4 字「马余强中」，只有第一段守卫能归还末字（实测摘除后复现
        speaker='马余强中' + aff='国科学院'）。
        """
        html = "<html><body><div class='content'><p>报告专家：马余强中国科学院院士</p></div></body></html>"
        r = parsers.parse_detail(html, 'https://physics.scnu.edu.cn/a/2024/1.html', '物理学院', '石牌')
        sp, aff = r.get('speaker'), r.get('speakerAffiliation')
        # 修复后：粘连被拆开，且 affiliation 补回首字
        self.assertEqual(sp, '马余强', '守卫失效：speaker 仍粘连机构首字')
        self.assertEqual(aff, '中国科学院', '守卫失效：affiliation 仍缺首字')


class TestN1aWordBoundary(unittest.TestCase):
    """N1a 词块空格（姓名/单位的主判据）。"""

    def test_n1a_word_boundary_spaces(self):
        """N1a 词块空格门禁：保留/删除两类行为都必须锁死。"""
        for raw, exp, note in N1A_CASES:
            with self.subTest(raw=raw):
                self.assertEqual(parsers._n1a_normalize(raw), exp, note)

    def test_n1a_speaker_uses_space_as_primary_cut(self):
        """空格在时，讲者提取必须靠空格切分，不依赖词表守卫。

        这条是「主判据恢复」的端到端证据：郭庆案例源页有空格（已回源核实），
        修复前产出 speaker='郭庆华' + aff='中师范大学'（双错），修复后按空格
        天然切出 2 字姓名。无空格形态由 test_guard_is_actually_load_bearing 看守。
        """
        sp, aff = _parse('报告人：郭庆 华中师范大学')
        self.assertEqual(sp, '郭庆')
        self.assertEqual(aff, '华中师范大学')


class TestSpacedPipeline(unittest.TestCase):
    """v5 双轨文本管线（spaced 供讲者切分 / folded 供 topic·location）。

    N1a 词块空格修复引入双轨：_st.text 取保留词块空格的 spaced 版（讲者切分
    主判据），同时保留 folded 版（删空格，供页脚截断决策与 topic/location）。
    v4→v5 修掉的两个暗坑都在此锁定：

    ① spaced 版必须与 folded 版**同样过 N1 全角→半角标点归一与 N1f 部首还原**。
       v4 的 spaced 版只过 _n1a_normalize（纯空格），「报告人：」在全角冒号
       页面保持全角而 folded 版是半角 → _realign_spaced_cut 逐字对齐失败 →
       保守不截 → spaced 保留 folded 已截掉的尾部 meta 区 → speaker label
       走偏，rest 只剩 '(' → affiliation 丢失（maths 7710 实测 aff=''）。

    ② topic/location 必须在 folded 版上提取。v4 保留词块空格后，导航区
      「党建工作 主题教育」出现空格，_topic_pat_a 的裸「主题」+ 空格前界
      假阳性命中「主题教育」，topic 变成整条导航串（ctld 4267 实测）。
      HEAD 上该处粘连故不命中——folded 即恢复 HEAD 行为。
    """

    def test_spaced_pipeline_keeps_label_punctuation_normalized(self):
        """maths 7710 回归：全角冒号 + meta description 页面，affiliation 不丢。"""
        html = (
            '<html><head><meta name="description" content="学术报告题目：再论吸收的马氏决策过程'
            '报告人：张宜 副教授 (邀请人：刘秋丽 ) 英国伯明翰大学时间：11月24日 地点：数科院西楼111报告厅">'
            "</head><body><div class='content'>"
            '<p>题 目： 再论吸收的马氏决策过程</p>'
            '<p>报 告 人： 张宜 副教授 (邀请人：刘秋丽 ) 英国伯明翰大学</p>'
            '<p>时 间：11 月24日 16：30-17：30</p>'
            '<p>地 点： 数科院西楼111报告厅</p>'
            '<p>报告人简介: 英国伯明翰大学数学系副教授。研究兴趣：马氏决策过程。</p>'
            '<p>版权所有 华南师范大学数学科学学院 地址：广州市天河区中山大道西55号 邮编：510631</p>'
            '</div></body></html>'
        )
        r = parsers.parse_detail(
            html, 'https://maths.scnu.edu.cn/a/20231124/9999.html', '数学科学学院', '石牌')
        self.assertEqual(r.get('speaker'), '张宜', 'spaced 标点归一缺失致 label 走偏')
        self.assertEqual(r.get('speakerAffiliation'), '英国伯明翰大学', 'spaced 截断不一致致 affiliation 丢失')

    def test_topic_extraction_uses_folded_text(self):
        """ctld 4267 回归：导航词块空格不得让 topic 吸进整条导航串。"""
        html = (
            "<html><body><div class='content'>"
            '<p>指引 教育部和广东省 华南师范大学 教师发展中心 党建工作 主题教育 党建资料 党史学习'
            ' 支部快讯 下载专区 当前位置： 首页 » 近期活动 »'
            ' 关于举办“青年教师成长经验分享”木铎教师沙龙 的通知</p>'
            '<p>主题：青年教师成长经验分享</p>'
            '<p>主讲人：陈家文</p>'
            '<p>时间：2023年6月2日</p>'
            '<p>地点：教师发展中心三楼308室</p>'
            '</div></body></html>'
        )
        r = parsers.parse_detail(
            html, 'https://ctld.scnu.edu.cn/a/20230602/9999.html', '教师发展中心', '石牌')
        self.assertEqual(r.get('topic'), '青年教师成长经验分享', (
            f'topic 被导航串污染：{r.get("topic")!r}'))
        self.assertEqual(r.get('speaker'), '陈家文')

    def test_text_folded_syncs_ocr_append(self):
        """bd 56 回归：海报页 OCR 文本追加必须同步进 text_folded。

        v5 初版只在管线赋值一次 text_folded，漏同步 _do_ocr / PDF 两处
        「_st.text 追加」→ 海报页 folded 停在页脚截断处（实测 459 vs spaced
        1048）→ location/topic 提取读不到 OCR 里的「地点：」→ 字段丢失。
        """
        html = ("<html><body><div class='content'>"
                '<img src="/poster.jpg"/>'
                '</div></body></html>')
        with mock.patch.object(parsers, '_img_to_text',
                               lambda img: '地点：南海校区行政楼317会议室 主讲人：田志鹏'):
            r = parsers.parse_detail(
                html, 'http://bd.scnu.edu.cn/a/20230618/56.html', '北斗研究院', '佛山')
        self.assertEqual(r.get('location'), '南海校区行政楼317会议室', (
            f'OCR 文本未同步进 folded：location={r.get("location")!r}'))


class TestAffiliationFold(unittest.TestCase):
    """v5c：aff 抽取的 CJK 空白折叠（spaced 轨副作用回归）。

    2026-10-15：v5 双轨管线让 _st.text 保留词块空格（讲者切分主判据），
    但源页 HTML 标签边界会在**单位内部**留下 CJK-CJK 空白
    （「（深圳大学<i>心理学院</i>教授）」→ spaced 轨「（深圳大学 心理学院 教授）」），
    而 _extract_affiliation 的 _UNIT_RE 字符类不含空白 → 单位被截到首个空白
    （psy 2840 实测 aff='深圳大学'；em 5512「暨南 大学…」→ aff='大学'）。
    修法：_extract_affiliation 入口折叠 CJK 间空白。folded 轨（HEAD 行为）
    从不含此类空白，折叠对 HEAD 输入是 no-op；英文词内空白不受影响。
    """

    def test_affiliation_extraction_folds_cjk_spaces(self):
        """单元：aff 抽取必须对 CJK 间空白免疫，且不碰英文词内空格。"""
        self.assertEqual(parsers._extract_affiliation('（深圳大学 心理学院 教授）'), '深圳大学心理学院')
        self.assertEqual(parsers._extract_affiliation('（暨南 大学经济学院教授）'), '暨南大学经济学院')
        self.assertEqual(parsers._extract_affiliation('Duke University'), 'Duke University')

    def test_affiliation_survives_tag_boundary_spaces(self):
        """端到端：源页在单位内部打标签边界时 affiliation 不得被截断。

        构造与 psy 2840 / em 5512 同构：标签值「姓名（单位）」的单位部分被
        HTML 标签切成两截，spaced 轨保留词块空格。修复前 aff 截到首个空白。
        """
        html = ("<html><body><div class='content'>"
                '<p>主讲人：何振宏（深圳大学<i>心理学院</i>教授）</p>'
                '<p>时间：2025年5月21日</p>'
                '<p>地点：心理学院513报告厅</p>'
                '</div></body></html>')
        r = parsers.parse_detail(
            html, 'http://psy.scnu.edu.cn/a/20250521/2840.html', '心理学院', '大学城')
        self.assertEqual(r.get('speaker'), '何振宏', f'speaker={r.get("speaker")!r}')
        self.assertEqual(r.get('speakerAffiliation'), '深圳大学心理学院', (
            f'单位被标签边界空格截断：affiliation={r.get("speakerAffiliation")!r}'))


class _FakeSt:
    """守卫只读 body_text_llm / body_text，构造最小替身。"""

    def __init__(self, raw):
        self.body_text_llm = raw
        self.body_text = raw


class TestBoundaryEvidence(unittest.TestCase):
    """2026-10-14 复核补：守卫的原文空格边界证据。

    复核实测：单靠 _AFF_END_KW（末字归还后以单位词收尾即拆）会把**真实 4 字姓名**
    砍成 3 字并把尾字塞进 affiliation——
      陈谢沅澧 国防科技大学智能科学学院 → sp='陈谢沅澧'→'陈谢沅' + aff='澧国防科技大学…'
      松野诚也 日本明治学院大学…      → sp='松野诚也'→'松野诚' + aff='也日本明治…'
    粘连与真名在折叠文本上完全同构（rest 完整性也区分不了：两者 rest 都"像机构"），
    唯一可靠判据是 N1a 折叠**前**的原文空格位置：
      粘连 → name 末字之后无空白（「张文生 中国…」的「中」已跨空格被吞进 name）
      真名 → name 末字之后紧跟空白（「陈谢沅澧 国防…」的空格分隔姓名与机构）
    见 parsers._sp_glue_boundary_evidence。
    """

    def test_boundary_evidence_detects_glue(self):
        """原文中 name 末字之后无空白 → 判粘连（应拆）。"""
        st = _FakeSt('报告专家：马余强 中国科学院院士')
        self.assertIs(parsers._sp_glue_boundary_evidence(st, '马余强中', '国科学院院士'), True)
        st = _FakeSt('报告人：张文生中国科学院自动化研究所')
        self.assertIs(parsers._sp_glue_boundary_evidence(st, '张文生中', '国科学院自动化研究所'), True)

    def test_boundary_evidence_detects_real_4char_name(self):
        """原文中 name 末字之后紧跟空白 → 判真名（不拆）。"""
        st = _FakeSt('报告人：陈谢沅澧 国防科技大学智能科学学院')
        self.assertIs(
            parsers._sp_glue_boundary_evidence(st, '陈谢沅澧', '国防科技大学智能科学学院'), False)
        st = _FakeSt('报告人：松野诚也 日本明治学院大学国际和平研究所')
        self.assertIs(
            parsers._sp_glue_boundary_evidence(st, '松野诚也', '日本明治学院大学国际和平研究所'), False)

    def test_boundary_evidence_conservative_on_locate_fail(self):
        """原文定位失败（raw 缺失/不含该串）→ 保守判非粘连，宁漏拆不误拆。"""
        st = _FakeSt('')
        self.assertIs(parsers._sp_glue_boundary_evidence(st, '马余强中', '国科学院院士'), False)
        st = _FakeSt('完全无关的正文内容')
        self.assertIs(parsers._sp_glue_boundary_evidence(st, '马余强中', '国科学院院士'), False)

    def test_real_names_survive_glue_guard(self):
        """真实姓名（含 4 字）在守卫存在时必须原样保留。"""
        for text, exp_sp, exp_aff in FOUR_CHAR_NAME_CASES:
            with self.subTest(text=text):
                sp, aff = _parse(text)
                self.assertEqual(sp, exp_sp, f'{text!r} speaker 应保持 {exp_sp!r}，实得 {sp!r}（守卫误拆真实姓名）')
                if exp_aff is not None:
                    self.assertEqual(aff, exp_aff, f'{text!r} affiliation 应保持 {exp_aff!r}，实得 {aff!r}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
