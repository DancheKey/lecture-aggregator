# -*- coding: utf-8 -*-
"""VLM 题目单字互校（2026-10-25，mks/20「勷→勠」实测驱动，方案A）+ 缓存归因（方案B）。

## 根因链（生产实录）

http://mks.scnu.edu.cn/a/20261020/20.html 的讲座信息全在海报里、正文无「主题：」
标签 → poster_only 分支（parsers.py poster_only 判定）→ VLM（glm-4v-flash）读海报
把题目里的「勷」（襄+力）认成「勠」（翏+力）。title 恒取源页列表锚文本（方案A
2026-09-30，不经 VLM）所以显示正确，只有 topic 走海报 → 同一记录「同源不同字」。
当时该记录自带 needsHumanReview='location|topic' 但无人复核，错字静默上线。

同时暴露监控盲点：VLM 缓存 key 只含「模型组合」（agnes 主 | zhipu 备），value
无产出者信息，653 条缓存无法归因到具体 provider，排查被迫靠复现实验反推。

## 本套件锁定

A（单字互校，_reconcile_vlm_topic_with_title）：
  ① 正例：前缀内错字 + 尾部省略（mks/20：title「...校长」、topic 全称）→ 修正；
  ② 正例：完全同长一字差（lswh/84「家」→「别」）→ 修正；
  ③ 正例：头部增删 + 1 错字（两端增删是标题省略的正常形态）→ 修正；
  ④ 负例（swc/21 实测假阳性）：中段增删（标题比题目少「报告」二字）→ 不动作；
  ⑤ 负例（swc/2025/0415/91 实测假阳性）：title 与 topic 是大段不同的两个串
     （difflib 能对齐出 2 个等长 replace 段共 14 个差异字）→ 不动作；
  ⑥ 负例：X 在源页有出现（源页本身这么写）→ 绝不动；
  ⑦ 保守路径：X=0 但 Y 仅 1 次（仅列表页单佐证）→ 不替换、need_review=True；
  ⑧ 负例：标点差异、超短串、空 title、3 个单字差异 → 不动作；
  ⑨ 端到端：parse_detail poster_only + VLM 错字桩 → topic 修正且
     vlmTopicCorrected=True（接线回归锁——漏接本项即红）。

B（缓存归因，_vlm_extract_fields）：
  ⑩ 成功路径把产出 provider 名写入缓存 value（_provider）；
  ⑪ _provider 不在 _apply_vlm_to_result 的 _MAP 白名单，不漏进 result；
  ⑫ 多场 list 保持结构不变（不挂 _provider）。

运行：python tests/test_vlm_topic_reconcile_20261025.py
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

# 环境开关须在 import parsers 之前设置（模块顶层读取）
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers as P  # noqa: E402

REC = P._reconcile_vlm_topic_with_title

# mks/20 实测值：title（列表锚文本，尾部省略）+ topic（VLM 错字版）
_MKS_TITLE = '第十九届“勷勤论坛”主论坛名校长讲座板块之周建国校长...'
_MKS_TOPIC_BAD = '第十九届“勠勤论坛”主论坛名校长讲座板块之周建国校长专场讲座'
_MKS_TOPIC_OK = '第十九届“勷勤论坛”主论坛名校长讲座板块之周建国校长专场讲座'
# 源页整页文本：h3 里「勷勤」出现 1 次（get_text 后 title/meta 属性不计）
_MKS_PAGE = '首页 新闻 通知 第十九届“勷勤论坛”主论坛名校长讲座板块之周建国校长专场讲座 发布时间：2026-10-20 浏览次数 100'


class TestReconcilePositive(unittest.TestCase):
    """① - ③ 正例：三种抄写形态均修正。"""

    def test_prefix_internal_typo_corrected(self):
        """① 错字在前缀内部（startswith 失效场景，必须 difflib 对齐）。"""
        nt, c, r = REC(_MKS_TOPIC_BAD, _MKS_TITLE, _MKS_PAGE)
        self.assertTrue(c, '前缀内错字必须触发修正')
        self.assertFalse(r)
        self.assertEqual(nt, _MKS_TOPIC_OK)

    def test_same_length_typo_corrected(self):
        """② 同长一字差（lswh/84「国家研究」→「国别研究」）。"""
        page = '关于区域与国别研究的理论与方法 讲座 黄民兴 国别研究 方法'
        nt, c, r = REC('关于区域与国家研究的理论与方法',
                       '关于区域与国别研究的理论与方法', page)
        self.assertTrue(c)
        self.assertEqual(nt, '关于区域与国别研究的理论与方法')

    def test_head_indent_with_typo_corrected(self):
        """③ 头部增删（两端增删是标题省略形态）+ 1 个错字 → 仍修正。"""
        page = '专从理念到形式——设计的逻辑与实存的关联 设计 逻辑 关联'
        nt, c, r = REC('专从理念到形式——设计的逻缉与实存的关联',
                       '从理念到形式——设计的逻辑与实存的关联', page)
        self.assertTrue(c)
        self.assertEqual(nt, '专从理念到形式——设计的逻辑与实存的关联')


class TestReconcileNegative(unittest.TestCase):
    """④ - ⑧ 负例与保守路径。"""

    def test_mid_indent_rejected(self):
        """④ swc/21 实测假阳性：中段增删（逐字错位源）→ 不动作。"""
        nt, c, r = REC('国家自然科学基金申请与经验交流报告会',
                       '国家自然科学基金申请与经验交流会',
                       '国家自然科学基金申请与经验交流会 报告会')
        self.assertFalse(c, '中段增删不得触发修正')
        self.assertFalse(r)
        self.assertEqual(nt, '国家自然科学基金申请与经验交流报告会')

    def test_large_diff_rejected(self):
        """⑤ swc/2025/0415/91 实测假阳性：两个串大段不同。

        page 只含 title 侧文字、不含 topic 侧任何差异字（贴合 poster_only
        真实形态：题目在海报里、正文没有题目字）——否则 X 被「源页已有」
        路径兜住，本例测不到「replace 总差异 ≤2」判据（2026-10-25 破坏验证
        实测：page 混入「从理念到形式」时该判据回退不红）。
        """
        page = '华南师范大学第30期工作会'
        nt, c, r = REC('从理念到形式——设计的逻辑与实存的关联',
                       '华南师范大学——设区第逻辑期实学工作联', page)
        self.assertFalse(c, '大段不同不得触发修正（difflib 段数少≠抄写关系）')
        self.assertEqual(nt, '从理念到形式——设计的逻辑与实存的关联')

    def test_two_segments_total_diff_over_limit_rejected(self):
        """⑤-b 专测「replace 总差异 ≤2」判据：2 个等长段共 4 个差异字。

        swc/91 原例是 4 个 replace 段（被段数判据拒），测不到总差异上限；
        本构造 = 单段 4 差异（段数 1 ≤2、等长、无 indel，只有总差异判据能拦）。
        """
        page = '赵钱孙李戊己庚辛讲座报告 学术活动'
        nt, c, r = REC('甲乙丙丁戊己庚辛讲座报告',
                       '赵钱孙李戊己庚辛讲座报告', page)
        self.assertFalse(c, '单段 4 个差异字超过单字互校上限')
        self.assertEqual(nt, '甲乙丙丁戊己庚辛讲座报告')

    def test_x_present_in_source_rejected(self):
        """⑥ X 在源页有出现 → 源页本身这么写，绝不动。"""
        nt, c, r = REC(_MKS_TOPIC_BAD, _MKS_TITLE,
                       _MKS_PAGE + ' 勠勤论坛历史 勠')
        self.assertFalse(c)
        self.assertFalse(r)
        self.assertEqual(nt, _MKS_TOPIC_BAD)

    def test_single_evidence_marks_review(self):
        """⑦ X=0 但 Y 仅 1 次（仅列表页单佐证）→ 不替换、标记复核。"""
        # page 里不含「勷」，title 提供唯一 1 次
        nt, c, r = REC(_MKS_TOPIC_BAD, _MKS_TITLE, '发布时间：2026-10-20 浏览次数 100')
        self.assertFalse(c, '单佐证不得自动替换')
        self.assertTrue(r, '单佐证须标记 needsHumanReview')
        self.assertEqual(nt, _MKS_TOPIC_BAD)

    def test_punct_diff_ignored(self):
        """⑧ 标点差异（非 CJK 对）→ 不动 topic。"""
        nt, c, r = REC('第十九届"勷勤论坛"主论坛名校长讲座板块之周建国校长专场讲座',
                       '第十九届“勷勤论坛”主论坛名校长讲座板块之周建国校长...',
                       _MKS_PAGE)
        self.assertFalse(c)
        self.assertFalse(r)

    def test_three_diffs_rejected(self):
        """⑧ 3 个单字差异 → 超过「单字互校」上限，不动作。"""
        page = '甲乙丙丁戊己庚辛壬癸的讲座报告通知 甲乙丙'
        nt, c, r = REC('甲乙丙丁戊己庚辛壬癸的讲座报告', '甲X丙X戊X庚辛壬癸的讲座报告', page)
        self.assertFalse(c, '3 个差异字超过单字互校上限')

    def test_short_and_empty(self):
        """⑧ 超短串 / 空 title → 不动作；空 page 落入单佐证路径（⑦ 语义）。"""
        self.assertEqual(REC('短题目', '短题目', '短题目'), ('短题目', False, False))
        self.assertEqual(REC(_MKS_TOPIC_BAD, '', _MKS_PAGE), (_MKS_TOPIC_BAD, False, False))
        # 空 page：evid 只剩 title，Y 仅 1 次 → 不替换、标记复核（同 test_single_evidence）
        nt, c, r = REC(_MKS_TOPIC_BAD, _MKS_TITLE, '')
        self.assertEqual((nt, c, r), (_MKS_TOPIC_BAD, False, True))

    def test_identical_no_action(self):
        """⑧ topic 与 title 完全一致 → 无 replace 段，不动作。"""
        nt, c, r = REC(_MKS_TOPIC_OK, _MKS_TITLE, _MKS_PAGE)
        self.assertFalse(c)
        self.assertFalse(r)
        self.assertIs(nt, _MKS_TOPIC_OK)


_URL = 'http://mks.scnu.edu.cn/a/20261020/20.html'
_IMG = 'http://statics.scnu.edu.cn/pics/mks/2026/1020/1792466877886204.jpg'

# poster_only 正文复刻 mks/20 形态：无字段标签（否则进文本路）+ h3 题目 + 内容图。
# ⚠ 「发布时间：」会命中 poster_only 判据的 _LECLABEL（时间[：:]）导致进文本路，
# 元信息必须用不含「时间：」的写法（真实 mks/20 正文元信息即不带该标签）。
_POSTER_HTML = (
    '<div class="content">'
    '<h3>第十九届“勷勤论坛”主论坛名校长讲座板块之周建国校长专场讲座</h3>'
    '<p>发布于2026年10月20日　点击数：100</p>'
    '<img src="%s">' % _IMG +
    '</div>'
)

# VLM 错字桩（glm-4v-flash 生产实录：勷→勠）
_VLM_BAD = {
    'topic': _MKS_TOPIC_BAD,
    'speaker': '周建国',
    'speakerAffiliation': '广大附中美林湖学校',
    'lectureStart': '2026-10-28 18:30',
    'location': '桃李园三楼高明厅（石牌校园）',
    'speakerBio': '广东省骨干教师。',
}


class TestE2EPosterOnly(unittest.TestCase):
    """⑨ 端到端：parse_detail poster_only 路径接线回归锁。"""

    def test_typo_corrected_in_parse_detail(self):
        with mock.patch.object(P, '_img_to_text', lambda img: ''), \
             mock.patch.object(P, '_vlm_extract_fields', lambda urls, cfgs: dict(_VLM_BAD)):
            out = P.parse_detail(_POSTER_HTML, _URL, '马克思主义学院', '石牌', 2026,
                                 list_title=_MKS_TITLE, skip_news_filter=True)
        rec = out[0] if isinstance(out, list) else out
        self.assertEqual(rec.get('topic'), _MKS_TOPIC_OK,
                         'poster_only 路径必须完成单字互校修正')
        self.assertTrue(rec.get('vlmTopicCorrected'),
                        '修正后须打 vlmTopicCorrected 标记（审计用）')
        # title 恒为源页列表锚文本，互校不得改写 title
        self.assertEqual(rec.get('title'), _MKS_TITLE)


class TestProviderAttribution(unittest.TestCase):
    """⑩ - ⑫ 方案B：缓存归因。"""

    _CFG = {'name': 'zhipu', 'model': 'glm-4v-flash'}

    def _run_extract(self, fields, captured):
        with mock.patch.object(P, '_vlm_img_b64', lambda u: 'b64stub'), \
             mock.patch.object(P, '_vlm_cache_get', lambda key: None), \
             mock.patch.object(P, '_vlm_cache_set',
                               lambda key, val: captured.__setitem__(key, val)), \
             mock.patch.object(P, '_vlm_try_one_provider',
                               lambda message, cfg, proxies, rpm=None: (fields, False)):
            return P._vlm_extract_fields([_IMG], [self._CFG])

    def test_provider_written_into_cache(self):
        """⑩ 成功路径把产出 provider 名写入缓存 value。"""
        captured = {}
        got = self._run_extract(dict(_VLM_BAD), captured)
        self.assertEqual(len(captured), 1)
        key, val = next(iter(captured.items()))
        self.assertIn('glm-4v-flash', key, 'key 仍须含模型名（换模型全量失效）')
        self.assertEqual(val.get('_provider'), 'zhipu')
        self.assertEqual(got.get('_provider'), 'zhipu', '返回值同样带归因')

    def test_provider_not_leaked_into_result(self):
        """⑪ _provider 不在 _apply_vlm_to_result 的 _MAP 白名单，不漏进 result。"""
        result = {'sourceUrl': _URL, 'title': _MKS_TITLE, 'topic': '',
                  'speaker': '', 'speakerTitle': '', 'speakerAffiliation': '',
                  'lectureStart': None, 'lectureEnd': None, 'location': '',
                  'speakerBio': '', 'organizer': '马克思主义学院'}
        f = dict(_VLM_BAD)
        f['_provider'] = 'zhipu'
        P._apply_vlm_to_result(result, f, 2026, None, None, None, poster_only=True)
        self.assertNotIn('_provider', result, '_provider 不得漏进 lectures 记录')

    def test_list_fields_structure_unchanged(self):
        """⑫ 多场 list 保持结构不变（不挂 _provider）。"""
        captured = {}
        got = self._run_extract([dict(_VLM_BAD), dict(_VLM_BAD)], captured)
        self.assertIsInstance(got, list)
        val = next(iter(captured.values()))
        self.assertIsInstance(val, list)
        for item in val:
            self.assertNotIn('_provider', item)


if __name__ == '__main__':
    unittest.main(verbosity=2)
