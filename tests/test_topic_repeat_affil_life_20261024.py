# -*- coding: utf-8 -*-
"""英文题目不被字段标签截断 + 重复主题标签取完整份 + bio 反推单位补全（2026-10-24）。

cs/1818 实测三连修：
① topic='Collaborative'——TOPIC_STOP 的英文字段标签（Topic/Title/Venue…）无冒号
   判据，英文题目 "Collaborative Topic Ranking: …" 里的 "Topic" 被当字段标签
   截断。修法：英文标签要求后跟冒号。
② 收紧后 topic 又吞了整页两遍主题文本（CMS 把主题同时写进 meta description 与
   正文 title，第一遍被 HTML 截断而不完整）。修法：STOP 命中重复「主题：/题目：」
   标签时，从重复标签起再提取一份，两份取更长者（正文完整份优先）。
③ affiliation 丢国别——规则层 _AFFIL_CURRENT_RE 不认「来自X大学」现职表述，
   只靠 LLM（还会省略国别成「维多利亚大学工程与科学学院」，与加拿大 University
   of Victoria 混淆）。修法：加「来自」锚点 + _apply_bio_fallback 补全式覆盖
   （bio 反推值以现有值为真后缀且更长才覆盖）。

离线单测：不发网络请求、不走 LLM（SCNU_LLM_*=0）。

2026-11-10 追加（cs/6029，见 SeriesTopicLabelTest）：页面同时写
「题目：真题目」与「论坛主题：系列名」且 N1 折叠后两者粘连时，题目类正则
必须优先于裸「主题」，系列主题标签不得被拆开命中。真实源页已固化为
tests/fixtures/html/cs6029.html；测试内剥离 <img>/<script> 以密封化。

2026-11-10 再追加（乙方案，见文件尾 ThreePageRulesTest）：life/6021 系列名
占位守卫、ggy/5492 正文单讲「第N讲：」、physics/11723「活动嘉宾」讲者标签
与地点终止符。三条真实源页固化为 life6021/ggy5492/physics11723 夹具。
"""
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scraper'))
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import hybrid  # noqa: E402
import parsers  # noqa: E402

# cs/1818 核心片段：meta description 与正文各写一遍主题（第一遍尾部被截断），
# 「主讲人：何静 博士」+「详细内容：」简介段。保留 CMS 双份主题的排版形态。
_HTML_1818 = '''<html><head>
<meta name="description" content="主题：Collaborative Topic Ranking: Leveraging Item Meta-data for Sparsity Reduction（协同话题排序：借助项目元数据减少稀疏"/>
</head><body><div class="content">
<h1>学术讲座信息</h1>
<p>主题： Collaborative Topic Ranking: Leveraging Item Meta-data for Sparsity Reduction（协同话题排序：借助项目元数据减少稀疏性）</p>
<p>主讲人：何静 博士</p>
<p>时间： 4月23日(本周四)下午4点</p>
<p>地点： 计算机学院院楼 102会议室</p>
<p>详细内容： 何静博士来自澳大利亚维多利亚大学工程与科学学院，主要研究方向为数据挖掘、人工智能。</p>
</div></body></html>'''


def _parse(html, url='http://cs.scnu.edu.cn/a/20150422/1818.html'):
    out = parsers.parse_detail(html, url, '计算机学院', '石牌',
                               default_year=2015, list_title='学术讲座信息',
                               skip_news_filter=True)
    return out if isinstance(out, list) else ([out] if out else [])


class TopicEnglishLabelTest(unittest.TestCase):
    """英文题目里的字段标签词（Topic/Title/Bio…）不得截断题目。"""

    def test_topic_not_cut_by_english_label(self):
        recs = _parse(_HTML_1818)
        self.assertEqual(len(recs), 1)
        tp = recs[0]['topic']
        self.assertIn('Topic Ranking', tp)
        self.assertIn('Leveraging Item Meta-data', tp)
        self.assertIn('减少稀疏性', tp)

    def test_topic_repeat_label_takes_complete_copy(self):
        """重复「主题：」标签时取完整份（meta 不完整份被正文完整份顶替）。"""
        recs = _parse(_HTML_1818)
        tp = recs[0]['topic']
        self.assertTrue(tp.rstrip().endswith('减少稀疏性)'), '应取正文完整份: %r' % tp)
        self.assertLess(tp.count('Collaborative'), 2, '不应把两遍主题都吞进来')


# em/8924 形态：主题含版本号「WEB 3.0下的…」——旧章节序号清理正则把 '3.'
# 当序号从根部截断（topic 只剩 'WEB'）。序号分隔收紧为「、」与全角「．」后恢复。
_HTML_8924 = '''<html><body><div class="content">
<p>主讲人：陈涛</p>
<p>时间：4月1日(周五）14:00</p>
<p>地址：文二栋536会议室</p>
<p>主题：WEB 3.0下的文化遗产图像智能应用-让价值触手可及</p>
<p>邀请人：张素芳 副教授</p>
</div></body></html>'''


class TopicVersionNumberTest(unittest.TestCase):
    """主题里的版本号（WEB 3.0）不得被章节序号清理误杀。"""

    def test_version_number_not_cut(self):
        recs = _parse(_HTML_8924, url='http://em.scnu.edu.cn/a/20220401/8924.html')
        self.assertEqual(recs[0]['topic'], 'WEB 3.0下的文化遗产图像智能应用-让价值触手可及')


class InferAffiliationLaiziTest(unittest.TestCase):
    """_infer_affiliation「来自」现职锚点（cs/1818）。"""

    def test_laizi_anchor_extracts_full_affiliation(self):
        aff = hybrid._infer_affiliation(
            '何静博士来自澳大利亚维多利亚大学工程与科学学院，主要研究方向为数据挖掘。')
        self.assertEqual(aff, '澳大利亚维多利亚大学工程与科学学院')

    def test_degree_school_still_rejected(self):
        """学历/历史任职表述仍不作依据（既有负例不回归）。"""
        self.assertEqual(hybrid._infer_affiliation('2016年获得大连理工大学博士学位。'), '')
        self.assertEqual(hybrid._infer_affiliation('李四 曾任 XX 大学教授'), '')

    def test_lead_form_still_works(self):
        """LEAD 形态（姓名，机构，职称）仍工作；姓名后紧跟学位词被学历守卫跳过
        （2026-10-22 既有口径，非本次改动回归）。"""
        self.assertEqual(hybrid._infer_affiliation('张三，华南师范大学教授。'),
                         '华南师范大学')
        self.assertEqual(hybrid._infer_affiliation('张三，博士，华南师范大学教授。'), '')


class BioFallbackCompleteTest(unittest.TestCase):
    """_apply_bio_fallback 补全式覆盖：LLM 省略国别时用 bio 现职原文补全。"""

    def test_completes_llm_truncated_value(self):
        result = {'speakerBio': '何静博士来自澳大利亚维多利亚大学工程与科学学院，主要研究方向为数据挖掘。',
                  'speakerAffiliation': '维多利亚大学工程与科学学院'}
        hybrid._apply_bio_fallback(result)
        self.assertEqual(result['speakerAffiliation'],
                         '澳大利亚维多利亚大学工程与科学学院')

    def test_no_cross_replace_when_not_suffix(self):
        """非后缀关系不动（防「北京大学」被「东京大学」张冠李戴）。"""
        result = {'speakerBio': '王五，博士，现任东京大学教授。',
                  'speakerAffiliation': '北京大学'}
        hybrid._apply_bio_fallback(result)
        self.assertEqual(result['speakerAffiliation'], '北京大学')

    def test_empty_affiliation_filled(self):
        result = {'speakerBio': '何静博士来自澳大利亚维多利亚大学工程与科学学院，主要研究数据挖掘。',
                  'speakerAffiliation': ''}
        hybrid._apply_bio_fallback(result)
        self.assertEqual(result['speakerAffiliation'],
                         '澳大利亚维多利亚大学工程与科学学院')


# ---------------------------------------------------------------------------
# 2026-11-10 cs/6029：系列主题标签（论坛/会议/研讨会/沙龙/活动/工作坊主题）
# 写的是系列名，不是本场讲座题目。旧规则裸「主题[：:]」把它拆开命中，且题目类
# 正则排在短路末位从未执行，topic 因此取成「第十九届…主论坛…」并被中点截断。
# 修法见 scraper/parsers.py: _SERIES_TOPIC_LABELS / _SERIES_TOPIC_STOP /
# _SERIES_NEG / 题目类优先 / 锚点排除。此处锁死真实源页与 9 种边界形态。
# ---------------------------------------------------------------------------
_CS6029_URL = 'http://cs.scnu.edu.cn/a/20261109/6029.html'
_CS6029_FIXTURE = os.path.join(_ROOT, 'tests', 'fixtures', 'html', 'cs6029.html')
_TRUE_TOPIC = 'SCHOLAT数智融合赋能教育发展研究与实践'
_META = ('<p>时间：2026年11月11日 14:30</p>'
         '<p>地点：计算机学院东阶梯学术报告厅</p>')
_TAIL = '<p>报告人：汤庸</p>'


def _parse_html(html, url=_CS6029_URL, list_title='第69期学术报告预告'):
    out = parsers.parse_detail(html, url, '计算机学院', '石牌',
                               list_title=list_title, skip_news_filter=True)
    return out if isinstance(out, list) else ([out] if out else [])


def _topic_of(body):
    """单页单场内联形态：只回读 topic，便于锁死正则行为。"""
    html = '<html><body><div class="content">' + body + '</div></body></html>'
    recs = _parse_html(html)
    return recs[0]['topic'] if recs else None


class SeriesTopicLabelTest(unittest.TestCase):
    """题目类标签优先；论坛/会议主题不得冒充题目；既有合法形态不回归。"""

    def test_real_fixture_topic_equals_source_title(self):
        """真实源页端到端：topic 与源页「题目：」原文完全一致。"""
        with open(_CS6029_FIXTURE, encoding='utf-8') as f:
            html = f.read()
        # 剥离正文海报与脚本：保留 CMS 正文排版与标签粘连形态，同时密封化
        # （零 OCR / 零 VLM / 零网络，且不改模块级状态，不污染其他套件）。
        html = re.sub(r'<img\b[^>]*>', '', html, flags=re.I)
        html = re.sub(r'<script\b.*?</script>', '', html, flags=re.S | re.I)
        recs = _parse_html(html)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['topic'], _TRUE_TOPIC)
        self.assertEqual(recs[0]['speaker'], '汤庸')

    def test_fixture_still_has_both_labels(self):
        """样本必须保留两个标签，防「把页面修成正确值」架空门禁。"""
        with open(_CS6029_FIXTURE, encoding='utf-8') as f:
            html = f.read()
        self.assertIn('论坛主题', html)
        self.assertIn('题目', html)
        self.assertIn('SCHOLAT', html)

    def test_title_before_series_label_not_suffixed(self):
        """N1 粘连「题目：X论坛主题：Y」：X 尾部不得多带「论坛」二字。"""
        body = (_META + '<p>题目：' + _TRUE_TOPIC + '</p>'
                '<p>论坛主题：第十九届勷勤论坛主论坛</p>' + _TAIL)
        self.assertEqual(_topic_of(body), _TRUE_TOPIC)

    def test_series_label_before_title_still_takes_title(self):
        """系列名在题目之前：更早锚点补全不得用更长的系列名顶替真题目。"""
        body = ('<p>论坛主题：第十九届勷勤论坛主论坛AI赋能之AI学伴·学科交融</p>'
                + _META + '<p>题目：' + _TRUE_TOPIC + '</p>' + _TAIL)
        self.assertEqual(_topic_of(body), _TRUE_TOPIC)

    def test_meeting_theme_does_not_override_report_title(self):
        """「会议主题：X … 报告题目：Y」→ 取 Y，不取 X。"""
        body = ('<p>会议主题：2026年人工智能大会</p>' + _META
                + '<p>报告题目：大模型推理加速</p>' + _TAIL)
        self.assertEqual(_topic_of(body), '大模型推理加速')

    def test_only_series_label_is_not_a_topic(self):
        """页面只有系列名、没写题目时，不得把系列名当 topic。"""
        body = (_META + '<p>论坛主题：第十九届勷勤论坛主论坛</p>' + _TAIL)
        tp = _topic_of(body) or ''
        self.assertNotIn('勷勤论坛', tp)
        self.assertNotIn('第十九届', tp)

    def test_legal_lecture_topic_label_unchanged(self):
        body = _META + '<p>讲座主题：图神经网络前沿进展</p>' + _TAIL
        self.assertEqual(_topic_of(body), '图神经网络前沿进展')

    def test_salon_topic_label_unchanged(self):
        """沙龙/工作坊主题既在系列词表、又是 pat_b 合法标签：题目类优先须保住它。"""
        body = _META + '<p>沙龙主题：人工智能与教育</p>' + _TAIL
        self.assertEqual(_topic_of(body), '人工智能与教育')

    def test_section_bare_topic_unchanged(self):
        """「一、主题X」章节形态仍走 pat_a，不得被题目类提前短路掉。"""
        body = _META + '<p>一、主题量子计算与密码学</p>' + _TAIL
        self.assertEqual(_topic_of(body), '量子计算与密码学')

    def test_plain_topic_and_prose_unchanged(self):
        """普通「主题：X」与散文「活动主题为…」不回归（1818 / 13295 口径）。"""
        body = _META + '<p>主题：WEB 3.0下的文化遗产图像智能应用</p>' + _TAIL
        self.assertEqual(_topic_of(body), 'WEB 3.0下的文化遗产图像智能应用')
        body2 = (_META + '<p>本场活动主题为生成式AI与教育变革的融合路径</p>'
                 '<p>主题：生成式AI与教育变革</p>' + _TAIL)
        self.assertEqual(_topic_of(body2), '生成式AI与教育变革')


# ---------------------------------------------------------------------------
# 2026-11-10 乙方案：三条真实源页 + 三处规则加固
#   ① life/6021  系列名占位守卫：标题「学术讲座：X教授“勷勤论坛”专场讲座」
#      的冒号后是系列名，不得当 topic 占住 VLM/正文真题目的位置。
#   ② ggy/5492   正文单讲「第N讲：题目」提为 topic，多编号/标题场次号不接管。
#   ③ physics/11723「活动嘉宾」进讲者标签词表，并作地点值终止符。
# 三条源页固化为 tests/fixtures/html/{life6021,ggy5492,physics11723}.html。
# ---------------------------------------------------------------------------
_FX_DIR = os.path.join(_ROOT, 'tests', 'fixtures', 'html')
_FX_META = {
    'life6021.html': (
        'http://life.scnu.edu.cn/a/20231010/6021.html', '生命科学学院', '石牌',
        '学术讲座：西北农林科技大学教授“勷勤论坛”专场讲座'),
    'ggy5492.html': (
        'http://ggy.scnu.edu.cn/a/20210510/5492.html', '政治与公共管理学院', '大学城',
        '华南师范大学政治与公共管理学院「社会保障与社会政策论坛」预告'),
    'physics11723.html': (
        'https://physics.scnu.edu.cn/a/20211008/11723.html', '物理学院', '大学城',
        '20211012  华南师范大学第十四届勷勤论坛之理科类论文写作指导'),
}


def _fixture_record(fn):
    """真实源页夹具 → 单条解析结果（零 OCR / 零 VLM / 零网络）。"""
    url, college, campus, list_title = _FX_META[fn]
    with open(os.path.join(_FX_DIR, fn), encoding='utf-8') as f:
        html = f.read()
    html = re.sub(r'<img\b[^>]*>', '', html, flags=re.I)
    html = re.sub(r'<script\b.*?</script>', '', html, flags=re.S | re.I)
    out = parsers.parse_detail(html, url, college, campus,
                               list_title=list_title, skip_news_filter=True)
    out = out if isinstance(out, list) else ([out] if out else [])
    assert len(out) == 1, (fn, len(out))
    return out[0]


class ThreePageRulesTest(unittest.TestCase):
    """三条真实源页端到端 + 规则边界形态。"""

    def test_01_fixture_files_keep_source_labels(self):
        """夹具必须保留原始标签，防「把样本改成正确值」架空门禁。"""
        for fn, keys in (
                ('life6021.html', ('勷勤论坛', '学术讲座')),
                ('ggy5492.html', ('第一讲', '城市贫困人口兜底保障')),
                ('physics11723.html', ('活动嘉宾', '薛正远', '刘勇', '肖洋'))):
            with open(os.path.join(_FX_DIR, fn), encoding='utf-8') as f:
                html = f.read()
            for k in keys:
                self.assertIn(k, html, '%s 缺少原文标签 %s' % (fn, k))

    def test_02_life6021_series_name_not_used_as_topic(self):
        """占位系列名须在出口清空；title 恒为源页列表标题。"""
        rec = _fixture_record('life6021.html')
        self.assertEqual(rec['title'], _FX_META['life6021.html'][3])
        self.assertEqual(rec['topic'], '')
        self.assertNotIn('专场讲座', rec['topic'] or '')

    def test_03_ggy5492_single_nth_label_becomes_topic(self):
        rec = _fixture_record('ggy5492.html')
        self.assertEqual(rec['topic'], '城市贫困人口兜底保障的焦点、难点与路径选择')

    def test_04_physics11723_all_guests_and_clean_location(self):
        rec = _fixture_record('physics11723.html')
        self.assertEqual(rec['speaker'], '薛正远、刘勇、肖洋')
        self.assertEqual(rec['location'], '理6栋525报告厅')

    def test_05_legal_series_topic_not_cleared(self):
        """守卫只拦「系列名占位」，合法系列题目（砺儒讲坛第80讲：…）不动。"""
        self.assertFalse(parsers._is_series_topic_placeholder(
            '砺儒讲坛第80讲：新发展阶段的城乡和区域格局',
            '砺儒讲坛第80讲：新发展阶段的城乡和区域格局'))
        self.assertFalse(parsers._is_series_topic_placeholder(
            '华南师范大学文学院汉语言文字学专业“说文论语”系列讲座之四',
            '华南师范大学文学院汉语言文字学专业“说文论语”系列讲座之四'))
        self.assertTrue(parsers._is_series_topic_placeholder(
            '西北农林科技大学教授“勷勤论坛”专场讲座',
            '学术讲座：西北农林科技大学教授“勷勤论坛”专场讲座'))

    def test_06_multi_nth_labels_not_taken_as_single_topic(self):
        """正文出现多个场次编号 → 交给多场拆分，整页 topic 不接管。"""
        text = ('第一讲：城市贫困人口兜底保障 主讲人：张三 '
                '第二讲：社会救助国际比较 主讲人：李四')
        self.assertEqual(parsers._single_nth_session_topic(text, '社会保障论坛预告'), '')

    def test_07_title_with_nth_label_not_taken(self):
        """标题自带「第N讲：」交给 R9/R10，正文规则不抢。"""
        self.assertEqual(parsers._single_nth_session_topic(
            '第80讲：新发展阶段 主讲人：张三',
            '砺儒讲坛第80讲：新发展阶段的城乡和区域格局'), '')

    def test_08_site_navigation_not_taken_as_topic(self):
        """<title>/导航里的「第148讲：」带站点噪声，不得提为题目。"""
        text = ('第148讲:社会合作规范——一个信息设计的解释(熊卫教授) - 学术活动预告 - '
                '华南师范大学社会科学处首页机构设置办事指南 主讲人：熊卫')
        self.assertEqual(parsers._single_nth_session_topic(text, '讲座预告'), '')

    def test_09_repeated_same_nth_label_is_still_single_session(self):
        """同一场次编号在 meta/正文重复多遍仍是单讲（CMS 常见）。"""
        text = ('第一讲：城市贫困人口兜底保障 主讲人：张三 '
                '第一讲：城市贫困人口兜底保障 主讲人：张三')
        self.assertEqual(parsers._single_nth_session_topic(text, '论坛预告'),
                         '城市贫困人口兜底保障')

    def test_10_explicit_topic_label_wins_over_nth(self):
        """正文已有明确「题目：」时，正文单讲规则不得覆盖。"""
        # 「主讲人：」是既有 topic 终止标签，把两段字段分开；页面同时写了
        # 明确题目标签与正文单讲标签时，前者优先，后者不得顶替。
        body = (_META + '<p>题目：图神经网络前沿进展</p>'
                '<p>主讲人：张三</p>'
                '<p>第一讲：另一个题目 主讲人：李四</p>')
        self.assertEqual(_topic_of(body), '图神经网络前沿进展')

    def test_11_guest_label_does_not_swallow_next_field(self):
        """「活动嘉宾」值到「活动内容」为止，不得把分享内容吞进讲者。"""
        body = ('<p>地点：理6栋525报告厅</p>'
                '<p>活动嘉宾：张三教授、李四老师</p>'
                '<p>活动内容：分享论文写作技巧</p>')
        html = '<html><body><div class="content">' + body + '</div></body></html>'
        recs = _parse_html(html, url='https://physics.scnu.edu.cn/a/20211008/11723.html',
                           list_title='理科类论文写作指导')
        rec = recs[0]
        self.assertEqual(rec['speaker'], '张三、李四')
        self.assertEqual(rec['location'], '理6栋525报告厅')


if __name__ == '__main__':
    unittest.main(verbosity=2)
