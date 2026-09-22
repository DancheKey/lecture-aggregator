# -*- coding: utf-8 -*-
"""双轨解析 + 分歧裁决的离线单元测试。

用 MockProvider 验证核心场景，不依赖真实 API，不触碰 parsers.golden 测试。
运行：python tests/test_hybrid.py

2026-09-02 下午修订：融合语义改为「仅填空 + snippet 溯源闸门」，测试用例同步更新，
并补充职称/单位补全、幻觉拒绝、单位粘连截断三组用例。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scraper'))
from llm_provider import MockProvider
from hybrid import (apply_llm_text_hybrid, compare_struct, _clean_affiliation,
                    _infer_title, _is_plausible_affiliation, _infer_affiliation)
import field_vocab as _fv

# 正文含可溯源片段，供 snippet 闸门匹配
BODY = ('讲座通知。题目：深度学习前沿。主讲人：温永立 教授（清华大学计算机系）。'
        '时间：2026-09-02 14:00。地点：理6栋302。'
        '摘要：本报告介绍深度学习的最新进展与应用。'
        '简介：温永立，清华大学教授，主要从事人工智能研究。')


class TestCompareStruct(unittest.TestCase):
    def test_identical_speakers(self):
        r = {'speaker': '温永立', 'location': '理6栋302'}
        a = {'speaker': '温永立', 'location': '理6栋302'}
        self.assertEqual(compare_struct(r, a), [])

    def test_speaker_conflict(self):
        r = {'speaker': '张三', 'location': '理6栋302'}
        a = {'speaker': '李四', 'location': '理6栋302'}
        self.assertIn('speaker', compare_struct(r, a))

    def test_location_norm(self):
        r = {'location': '理6栋302室'}
        a = {'location': '理6栋302'}
        self.assertEqual(compare_struct(r, a), [])


class TestHybridScenarios(unittest.TestCase):
    """场景 1: 一致 -> 放行；场景 2: A 失效 -> 规则保底；场景 3: 分歧 -> B 裁决。"""

    def _make_result(self):
        return {
            'sourceUrl': 'http://example.com/a/1.html',
            'speaker': '温永立',
            'topic': '规则题目',
            'lectureStart': '2026-09-02 14:00:00',
            'location': '理6栋302',
            'speakerSource': 'label',
        }

    def test_scenario_1_consistent(self):
        """A 与规则一致 -> 放行，规则为空的 abstract/bio 由 A 补全。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'abstract': {'value': '本报告介绍深度学习的最新进展与应用。',
                         'snippet': '本报告介绍深度学习的最新进展与应用。'},
            'speakerBio': {'value': '温永立，清华大学教授，主要从事人工智能研究。',
                           'snippet': '温永立，清华大学教授，主要从事人工智能研究。'},
        })
        apply_llm_text_hybrid(rule_result, BODY, None, provider, None)
        self.assertTrue(rule_result.get('llmTextEnhanced'))
        self.assertEqual(rule_result['llmVerdict'], 'consistent')
        self.assertEqual(rule_result['abstract'], '本报告介绍深度学习的最新进展与应用。')
        self.assertEqual(rule_result['speakerBio'], '温永立，清华大学教授，主要从事人工智能研究。')
        self.assertEqual(rule_result['speaker'], '温永立')

    def test_scenario_2_a_fails(self):
        """A 失效（返回 None）-> 规则保底，llmTextEnhanced=False。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result=None)
        apply_llm_text_hybrid(rule_result, BODY, None, provider, None)
        self.assertFalse(rule_result.get('llmTextEnhanced'))
        self.assertIsNone(rule_result.get('llmVerdict'))
        self.assertEqual(rule_result['speaker'], '温永立')

    def test_scenario_3_judge_rule(self):
        """分歧 -> B 裁决支持 rule -> 保留规则，打 needsHumanReview 标记。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result={
            'speaker': {'value': '李四', 'snippet': '主讲人：温永立 教授'},
        })
        judge = MockProvider(verdict={
            'verdict': 'rule', 'reason': '规则主讲人与标题一致', 'fields': {},
        })
        apply_llm_text_hybrid(rule_result, BODY, None, provider, judge)
        self.assertFalse(rule_result.get('llmTextEnhanced'))
        self.assertEqual(rule_result['llmVerdict'], 'rule')
        self.assertTrue(rule_result.get('needsHumanReview'))
        self.assertEqual(rule_result['speaker'], '温永立')  # 保守保留规则

    def test_scenario_3_judge_llm(self):
        """分歧 -> B 裁决支持 llm：B 列名字段进入 force（可覆盖），但 speaker='李四'
        无法通过值级溯源（不在原文）-> 闸门拦截，规则已有值保留（2026-09-09 新语义）。"""
        rule_result = self._make_result()
        provider = MockProvider(text_result={
            'speaker': {'value': '李四', 'snippet': '主讲人：温永立 教授'},
            'abstract': {'value': '本报告介绍深度学习的最新进展与应用。',
                         'snippet': '本报告介绍深度学习的最新进展与应用。'},
        })
        judge = MockProvider(verdict={
            'verdict': 'llm', 'reason': '正文明确写的是李四',
            'fields': {'speaker': '李四', 'abstract': 'A的摘要'},
        })
        apply_llm_text_hybrid(rule_result, BODY, None, provider, judge)
        self.assertTrue(rule_result.get('llmTextEnhanced'))
        self.assertEqual(rule_result['llmVerdict'], 'llm')
        self.assertEqual(rule_result['speaker'], '温永立')  # 已有值不覆盖
        self.assertEqual(rule_result['abstract'], '本报告介绍深度学习的最新进展与应用。')


class TestForceOverride(unittest.TestCase):
    """B 裁决支持 llm 的字段走 force_fields「有闸门覆盖」（2026-09-09 新语义）。"""

    def test_force_override_when_traceable(self):
        """B 支持且 A 值可溯源 -> 覆盖规则脏值。"""
        rule = {'speaker': '温永立清华', 'speakerAffiliation': '某某机构'}
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立'},
            'speakerAffiliation': {'value': '清华大学计算机系',
                                   'snippet': '（清华大学计算机系）'},
        })
        judge = MockProvider(verdict={
            'verdict': 'llm',
            'fields': {'speaker': '温永立', 'speakerAffiliation': '清华大学计算机系'},
        })
        apply_llm_text_hybrid(rule, BODY, None, provider, judge)
        self.assertEqual(rule['speaker'], '温永立')
        self.assertEqual(rule['speakerAffiliation'], '清华大学计算机系')

    def test_force_blocked_by_trace_gate(self):
        """B 支持但 A 值无法溯源 -> 闸门拦截，保留规则值并记 llmRejected。"""
        rule = {'speaker': '温永立', 'speakerAffiliation': ''}
        provider = MockProvider(text_result={
            'speaker': {'value': '李四', 'snippet': 'nowhere'},
        })
        judge = MockProvider(verdict={
            'verdict': 'llm', 'fields': {'speaker': '李四'},
        })
        apply_llm_text_hybrid(rule, BODY, None, provider, judge)
        self.assertEqual(rule['speaker'], '温永立')
        self.assertIn('speaker', (rule.get('llmRejected') or ''))


class TestFillEmptyOnly(unittest.TestCase):
    """仅填空语义：规则已有值的字段，A 绝不覆盖。"""

    def _rule(self):
        return {'speaker': '温永立', 'topic': '规则题目',
                'location': '理6栋302', 'abstract': '规则摘要'}

    def test_existing_fields_not_overridden(self):
        r = self._rule()
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'topic': {'value': 'A的题目', 'snippet': '题目：深度学习前沿'},
            'location': {'value': 'A的地点', 'snippet': '地点：理6栋302'},
            'abstract': {'value': 'A的摘要', 'snippet': '本报告介绍深度学习的最新进展与应用。'},
        })
        apply_llm_text_hybrid(r, BODY, None, provider, None)
        self.assertEqual(r['speaker'], '温永立')
        self.assertEqual(r['topic'], '规则题目')
        self.assertEqual(r['location'], '理6栋302')
        self.assertEqual(r['abstract'], '规则摘要')  # 规则已有摘要，A 不覆盖

    def test_speaker_title_and_affiliation_filled(self):
        """职称/单位属填空型字段：规则没抓到时由 A 补上。"""
        r = {'speaker': '温永立', 'topic': '规则题目'}
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'speakerTitle': {'value': '教授', 'snippet': '主讲人：温永立 教授'},
            'speakerAffiliation': {'value': '清华大学计算机系',
                                   'snippet': '主讲人：温永立 教授（清华大学计算机系）'},
        })
        apply_llm_text_hybrid(r, BODY, None, provider, None)
        self.assertEqual(r['speakerTitle'], '教授')
        self.assertEqual(r['speakerAffiliation'], '清华大学计算机系')

    def test_affiliation_cut_at_metadata(self):
        """单位粘连后续元数据标记时，按标记截断，避免吞进日期/地点。"""
        r = {'speaker': '温永立'}
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'speakerAffiliation': {'value': '清华大学计算机系）日期：2026-09-02 地点：理6栋302',
                                   'snippet': '主讲人：温永立 教授（清华大学计算机系）'},
        })
        apply_llm_text_hybrid(r, BODY, None, provider, None)
        self.assertEqual(r['speakerAffiliation'], '清华大学计算机系')


class TestSnippetGate(unittest.TestCase):
    """溯源闸门：值能在原文中找到出处才采用，否则判幻觉。"""

    def test_hallucination_rejected(self):
        """snippet 在原文中找不到 -> 拒绝采用，并记入 llmRejected。"""
        r = {'speaker': '温永立'}
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'abstract': {'value': '一段凭空编造的摘要内容', 'snippet': '这段原文里根本没有'},
        })
        apply_llm_text_hybrid(r, BODY, None, provider, None)
        self.assertNotIn('abstract', r)
        self.assertIn('abstract', r.get('llmRejected', ''))

    def test_missing_snippet_rejected(self):
        """A 未附 snippet -> 无法溯源 -> 拒绝。"""
        r = {'speaker': '温永立'}
        provider = MockProvider(text_result={
            'speaker': {'value': '温永立', 'snippet': '主讲人：温永立 教授'},
            'abstract': {'value': '无出处的摘要'},
        })
        apply_llm_text_hybrid(r, BODY, None, provider, None)
        self.assertNotIn('abstract', r)
        self.assertIn('abstract', r.get('llmRejected', ''))


class TestAffiliationBrackets(unittest.TestCase):
    """单位字段括号清理：只剥「不配对」的悬挂括号，保留合法闭合括号。

    2026-09-22 回归守卫——旧实现用 str.strip(字符集合) 剥两端括号，把
    「广东以色列理工学院（GTIIT）」的合法右括号一并剥成「…（GTIIT」，
    前端模板再补一对括号，用户看到的就是「少一个右括号」（iqm557）。
    """

    BALANCED = (
        '广东以色列理工学院（GTIIT）',
        'Guangdong Technion–Israel Institute of Technology (GTIIT)',
        '北京大学（北京）',
        '香港中文大学(深圳)',
        '兰州大学威尔士学院；英国威尔士三一圣大卫大学（UWTSD）',
    )

    def test_balanced_parens_kept(self):
        """配对的合法括号必须原样保留。"""
        for v in self.BALANCED:
            self.assertEqual(_fv.trim_dangling_brackets(v), v, v)

    def test_dangling_brackets_still_removed(self):
        """真悬挂括号仍要清理——修 bug 不得丢掉这个既有能力。"""
        cases = {
            '助理研究员 (': '助理研究员',
            '北京大学)': '北京大学',
            '（北京大学）': '北京大学',      # 整体被包裹 → 剥外层（前端模板自带括号）
            '  清华大学  ': '清华大学',
            '清华大学：': '清华大学',
        }
        for raw, want in cases.items():
            self.assertEqual(_fv.trim_dangling_brackets(raw), want, raw)

    def test_clean_affiliation_keeps_balanced(self):
        """hybrid._clean_affiliation（A 模型值写回路径）同样不得削掉合法括号。"""
        for v in self.BALANCED:
            self.assertEqual(_clean_affiliation(v), v, v)
        # 元数据粘连截断与空值行为保持不变
        self.assertEqual(_clean_affiliation('清华大学 日期:3月3日'), '清华大学')
        self.assertEqual(_clean_affiliation(''), '')
        self.assertEqual(_clean_affiliation(None), '')


class TestInferTitleNoCrash(unittest.TestCase):
    """_infer_title 崩溃修复（2026-09-22）。

    _TITLE_RE 由 field_vocab 的全 (?:...) 非捕获组词表拼成，捕获组数为 0；
    旧代码取 group(1) 命中即抛 IndexError，把 bio 回退整段带崩
    （全库 221 条满足触发条件，golden 里记作 expected failure）。
    """

    def test_regex_has_no_capture_group(self):
        """守卫前提：词表若改成带捕获组，本测试与新实现都需重新审视。"""
        from hybrid import _TITLE_RE
        self.assertEqual(_TITLE_RE.groups, 0)

    def test_returns_title_without_raising(self):
        cases = {
            '张三，北京大学教授，博士生导师': '教授',
            '李四，博士毕业于清华大学，现为副教授': '副教授',
            '王五，博士，华南师范大学研究员': '博士',  # 首个匹配即返回（预存词表行为）
            '赵六': '',
            '': '',
        }
        for bio, want in cases.items():
            self.assertEqual(_infer_title(bio), want, bio)

    def test_bio_fallback_fills_title_on_real_record(self):
        """端到端：兜底确实能把职称写进 result（旧代码此处抛异常）。"""
        result = {'speakerBio': '温永立，清华大学教授，主要从事人工智能研究。'}
        from hybrid import _apply_bio_fallback
        _apply_bio_fallback(result)
        self.assertEqual(result.get('speakerTitle'), '教授')


class TestAffiliationValueTrace(unittest.TestCase):
    """单位字段值级溯源闸门（2026-09-22）。

    旧实现只走 snippet 级闸门：A 附一段真实原文作背书、值却自行翻译时照样放行
    （iqm557：源页与 snippet 全英文，A 的值是「广东以色列理工学院（GTIIT）」）。
    """

    SRC = ('Hongkai Liu is an Associate Professor at the Guangdong '
           'Technion-Israel Institute of Technology (GTIIT). '
           'He obtained his PhD from Tsinghua University.')

    def test_translated_value_rejected(self):
        """A 自行翻译的中文机构名 -> 值在原文中不存在 -> 拒绝。"""
        self.assertFalse(_is_plausible_affiliation('广东以色列理工学院（GTIIT）', self.SRC))
        self.assertFalse(_is_plausible_affiliation('南华师范大学', 'South China Normal University'))

    def test_literal_value_adopted(self):
        """原文中字面存在的机构名 -> 采纳（含 en dash / 省略括号注记）。"""
        for v in ('Guangdong Technion–Israel Institute of Technology (GTIIT)',
                  'Guangdong Technion-Israel Institute of Technology',
                  'Tsinghua University',
                  'Technion-Israel Institute of Technology'):
            self.assertTrue(_is_plausible_affiliation(v, self.SRC), v)

    def test_chinese_source_keeps_chinese_value(self):
        self.assertTrue(_is_plausible_affiliation('华南师范大学心理学院',
                                                  '主讲人：曹艺轩（华南师范大学心理学院）'))
        self.assertTrue(_is_plausible_affiliation('清华大学（北京）', '清华大学计算机系'))

    def test_unmatched_and_empty_rejected(self):
        """原文完全没有、或溯源文本为空 -> 一律拒绝（不是照单全收）。"""
        self.assertFalse(_is_plausible_affiliation('北京大学', '清华大学'))
        self.assertFalse(_is_plausible_affiliation('北京大学', ''))
        self.assertFalse(_is_plausible_affiliation('', self.SRC))
        self.assertFalse(_is_plausible_affiliation(None, self.SRC))


class TestInferAffiliation(unittest.TestCase):
    """bio 兜底单位提取：只认现职依据，学历/历史任职一律不取（2026-09-22）。

    旧实现回退时硬取 bio 开头第一个机构，把「2016年获得大连理工大学博士学位」
    取成「得大连理工大学」（懒惰量词把「获得」切剩「得」）——全库 11 条线上数据
    被污染，含把学历学校当现职单位。新实现只认两条有据路径，取不到就返回空。
    """

    def test_lead_form_with_unit(self):
        """「姓名，机构，职称…」开头形态（机构须以分隔符紧邻姓名）。"""
        self.assertEqual(
            _infer_affiliation('刘科海,松山湖材料实验室副研究员。2016年获得大连理工大学博士学位。'),
            '松山湖材料实验室')
        self.assertEqual(
            _infer_affiliation('刘淇,中国科学技术大学认知智能全国重点实验室教授,博导。'),
            '中国科学技术大学')
        self.assertEqual(
            _infer_affiliation('钟子信 (Eric T. Chung) , 香港中文大学教授, 数学系副主任。'),
            '香港中文大学')

    def test_current_position_phrase(self):
        """明确现职表达（含「X年X月起在…工作」这类带年份的现职句）。"""
        self.assertEqual(
            _infer_affiliation('占萌,现任华中科技大学电气与电子工程学院教授。'),
            '华中科技大学电气与电子工程学院')
        self.assertEqual(
            _infer_affiliation('杨文,研究员。2015年获得加拿大英属哥伦比亚大学博士学位。'
                               '2023年12月起在澳门大学工作。'),
            '澳门大学')
        self.assertEqual(
            _infer_affiliation('王展鹏,博士,教授。1998年起在北京外国语大学英语系任教。'),
            '北京外国语大学英语系')
        self.assertEqual(
            _infer_affiliation('郭雁秋,现任职于佛罗里达国际大学,tenured副教授。'),
            '佛罗里达国际大学')

    def test_degree_only_returns_empty(self):
        """只有学历/博士后经历 -> 不取（学历学校不得当现职单位）。"""
        self.assertEqual(
            _infer_affiliation('陈淑慧,中医内科学博士。2008年毕业于广州中医药大学。曾在广东省中医院工作7年。'), '')
        self.assertEqual(
            _infer_affiliation('韩彪,于2016年获得法国图卢兹大学博士学位,师从某博士。'), '')
        self.assertEqual(
            _infer_affiliation('张振，副研究员，硕士生导师，研究生院副院长。本科毕业于华东理工大学。'), '')

    def test_history_position_returns_empty(self):
        """历史任职（「X年起任…」，无「现职」语义）/ 空输入 -> 不取。"""
        self.assertEqual(
            _infer_affiliation('肖林博士, 2010 年起任第二军医大学神经科学研究所副教授。'), '')
        self.assertEqual(_infer_affiliation(''), '')
        self.assertEqual(_infer_affiliation(None), '')

    def test_no_separator_not_matched(self):
        """姓名与机构间无分隔符 -> 不匹配（防「李博士是新加坡…」切出「士是」残片）。"""
        self.assertEqual(_infer_affiliation('李博士是新加坡南洋理工大学教授'), '')


if __name__ == '__main__':
    unittest.main()
