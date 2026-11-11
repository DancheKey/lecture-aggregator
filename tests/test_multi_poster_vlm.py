#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多海报周预告页「逐张 VLM」回归门禁（2026-11-11，yjsy/10291）。

背景：研究生院「第十九届勷勤论坛 11月9日-15日活动预告」一页列 12 场活动、
配 12 张各自独立的海报。正文有「活动N：/活动时间：/活动地点：/嘉宾介绍：」标签，
故 poster_only=False，海报 VLM 主链路完全没走；规则层 repeated-label 拆出 10 条，
而「图文页补摘要/简介」分支只试 imgs[:3]、首个有效即 break，把同一份长文本写进
base 再复制给每条 —— 10 条 abstract 全是「教育情怀与育人智慧」、speakerBio 大量
是「连真，泉州风雅颂书局…」、speakerAffiliation 被灌进整段简历、location 尾部串
「嘉宾介绍」，陈昉那场甚至被串成石牌校园计算机学院（实际在大学城国际会议厅）。

修复要点（本套件逐条锁死）：
  · 结构判据「活动N：≥2 + 时间标签 ≥2 + 内容图 ≥2 且与块数相当」；
  · 每张内容图单独调 VLM（不 [:3]、不拼图），失败只跳过该张；
  · 按「日期+时刻」与正文场次对齐，同刻多张按姓名/地点消歧，仍不唯一则放弃；
  · 正文仍是权威：title/topic/时间不动，VLM 把「陈昉」读成「陈妨」不得覆盖；
  · 海报与正文校区冲突时才按海报纠正地点；
  · 时刻读不一致时（Eser 海报 09:00 / 正文 11:00）走保守次级联，须过姓名归属证据；
  · 摘要一律不回填 VLM（铁律：源站没写摘要就不补，AI 生成 = 编造）。

全离线：真实页面 + 12 份 VLM 结果固化为 tests/fixtures/，无网络无密钥。
另锁 VLM_PROMPT 的 SHA256 —— 本次修复刻意不改模型输入一个字，防后续无意识漂移。
"""
import hashlib
import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
FIX = os.path.join(ROOT, 'tests', 'fixtures')

# 与 golden 同纪律：import parsers 前锁纯规则基线，避免本机 .env 触发真实大模型
os.environ.setdefault('SCNU_LLM_TEXT', '0')
os.environ.setdefault('SCNU_LLM_RICH', '0')

import parsers as P  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

with open(os.path.join(FIX, 'html', 'yjsy10291.html'), encoding='utf-8') as _f:
    HTML = _f.read()
with open(os.path.join(FIX, 'vlm_poster_10291.json'), encoding='utf-8') as _f:
    VLM_DOC = json.load(_f)
URL = VLM_DOC['sourceUrl']
VLM_BY_URL = dict(zip(VLM_DOC['urls'], VLM_DOC['fields']))
# 2026-11-11 固化值；改 VLM_PROMPT 任何一字都会让本套件红。
VLM_PROMPT_SHA256 = '24da4af773e2559239d17cf6b0b868988261aa90aa6c0927efe812f93275b451'

CONTENT_DIV = BeautifulSoup(HTML, 'html.parser').find('div', class_='article-content')

# 纯净检出 / CI 没有 .env，_load_vlm_configs() 返回空 → 多海报通道会在
# `if _mp_cfgs:` 处短路，一次 VLM 都不调（本机有密钥全绿、CI 假红 6 例）。
# 显式桩成非空假配置：_vlm_extract_fields 已被下面的 fixture 桩替换，配置只透传、
# 不发任何请求；name 键必需（provider 限速分桶会读它）。
_FAKE_VLM_CFGS = [{'name': 'fixture', 'model': 'fixture-replay',
                   'api_key': 'unused', 'base_url': 'http://fixture.invalid/vlm'}]


class _FakeVLM:
    """按 URL 返回固化海报结果，并记录每次调用（用于验证「逐张」而非 [:3]）。"""

    def __init__(self, mapping=None, fail_all=False, call_log=None):
        self.mapping = mapping if mapping is not None else VLM_BY_URL
        self.fail_all = fail_all
        self.calls = call_log if call_log is not None else []

    def __call__(self, img_urls, cfgs):
        self.calls.append(list(img_urls))
        if self.fail_all or not img_urls:
            return None
        return self.mapping.get(img_urls[0])


def _parse(fake=None, env=None):
    fake = fake or _FakeVLM()
    env = env or {}
    # ⚠ 必须显式桩 OCR：tests/test_parser_golden.py 在 pytest **收集阶段**（模块导入）
    # 就把 P._img_to_text 全局换成「录制回放」，图不在 ocr_cache.json 里会直接抛
    # AssertionError。全套跑时 _extract_speaker 的 _do_ocr 兜底会踩到它（单跑本套件
    # 不复现）；本套件验证的是逐张 VLM 通道，OCR 是无关腿，桩成空串既彻底离线，
    # 也与「_safe_fetch=None → 真 OCR 拿不到图」的实际结果一致。
    # 用 patch.object 上下文自动还原，不会像 golden 那样把桩常驻全进程。
    with patch.object(P, '_vlm_extract_fields', side_effect=fake), \
         patch.object(P, '_load_vlm_configs', return_value=_FAKE_VLM_CFGS), \
         patch.object(P, '_img_to_text', return_value=''), \
         patch.object(P, '_safe_fetch', return_value=None), \
         patch.dict(os.environ, env):
        out = P.parse_detail(HTML, URL, '研究生院', '校级')
    recs = out if isinstance(out, list) else ([out] if out else [])
    return recs, fake


def _by_start(recs, start):
    return [r for r in recs if r.get('lectureStart') == start]


class PromptUnchangedTest(unittest.TestCase):
    def test_vlm_prompt_not_touched(self):
        self.assertEqual(hashlib.sha256(P.VLM_PROMPT.encode('utf-8')).hexdigest(),
                         VLM_PROMPT_SHA256)


class MultiPosterCriterionTest(unittest.TestCase):
    def test_activity_blocks_and_time_labels(self):
        self.assertEqual(P._count_activity_blocks(CONTENT_DIV), (12, 12))

    def test_criterion_hits_real_page(self):
        self.assertTrue(P._multi_poster_page(CONTENT_DIV, VLM_DOC['urls'], False))

    def test_criterion_rejects_poster_only_single_image_and_overflow(self):
        self.assertFalse(P._multi_poster_page(CONTENT_DIV, VLM_DOC['urls'], True))
        self.assertFalse(P._multi_poster_page(CONTENT_DIV, VLM_DOC['urls'][:1], False))
        self.assertFalse(P._multi_poster_page(CONTENT_DIV, ['x'] * 25, False))
        self.assertFalse(P._multi_poster_page(None, VLM_DOC['urls'], False))

    def test_criterion_rejects_plain_page_without_activity_blocks(self):
        soup = BeautifulSoup('<div><p>活动时间：2026-11-11 9:00</p>'
                             '<img src="/a.png"><img src="/b.png"></div>', 'html.parser')
        self.assertFalse(P._multi_poster_page(soup.find('div'), ['a', 'b'], False))

    def test_switch_off_disables_vlm_but_not_page_detection(self):
        calls = []
        recs, fake = _parse(_FakeVLM(call_log=calls),
                            env={'SCNU_MULTI_POSTER_VLM': '0'})
        self.assertEqual(calls, [])
        self.assertEqual(len(recs), 11)
        # 判据仍命中 → 串场清理照常执行
        self.assertTrue(all(not (r.get('abstract') or '').strip() for r in recs))


class VenueHelperTest(unittest.TestCase):
    def test_clean_venue_label_tail(self):
        self.assertEqual(
            P._clean_venue_label_tail('石牌校园计算机学院学术报告厅嘉宾介绍'),
            '石牌校园计算机学院学术报告厅')
        self.assertEqual(P._clean_venue_label_tail('  待定  '), '待定')
        self.assertEqual(P._clean_venue_label_tail(''), '')

    def test_campus_and_normalize(self):
        self.assertEqual(P._vlm_venue_campus('石牌校园教育科学学院125室'), '石牌')
        self.assertEqual(P._vlm_venue_campus('华南师范大学大学城校区国际会议厅'), '大学城')
        self.assertEqual(P._vlm_venue_campus('文三栋201厚德讲学厅'), '')
        self.assertEqual(
            P._normalize_vlm_venue('华南师范大学大学城校区国际会议厅（主会场）'),
            '大学城校园国际会议厅（主会场）')


class CollectSessionsTest(unittest.TestCase):
    def test_one_call_per_image_no_top3_truncation(self):
        fake = _FakeVLM()
        with patch.object(P, '_vlm_extract_fields', side_effect=fake):
            out = P._collect_poster_vlm_sessions(VLM_DOC['urls'], [])
        self.assertEqual(len(fake.calls), 12)
        self.assertTrue(all(len(c) == 1 for c in fake.calls))
        self.assertEqual(len(out), 12)

    def test_failed_image_skipped_and_list_flattened(self):
        urls = VLM_DOC['urls'][:4]
        mapping = dict(VLM_BY_URL)
        mapping[urls[1]] = None
        mapping[urls[3]] = [{'speaker': '甲'}, {'speaker': '乙'}]
        fake = _FakeVLM(mapping=mapping)
        with patch.object(P, '_vlm_extract_fields', side_effect=fake):
            out = P._collect_poster_vlm_sessions(urls, [])
        self.assertEqual(len(fake.calls), 4)
        self.assertEqual(len(out), 4)          # 1 张失败 + 1 张摊平为 2 场
        self.assertEqual(out[-1]['speaker'], '乙')


class MatchGuardTest(unittest.TestCase):
    def test_unique_candidate(self):
        self.assertEqual(P._match_poster_vlm({}, [({'speaker': '甲'}, 0)]),
                         ({'speaker': '甲'}, 0))
        self.assertIsNone(P._match_poster_vlm({}, []))

    def test_speaker_disambiguation_includes_near_homograph(self):
        cands = [({'speaker': '陈妨'}, 0), ({'speaker': '高珂娟'}, 1)]
        rec = {'speaker': '陈昉', 'location': ''}
        self.assertEqual(P._match_poster_vlm(rec, cands)[1], 0)

    def test_venue_disambiguation(self):
        cands = [({'speaker': 'X', 'location': '石牌校园教育科学学院125室'}, 0),
                 ({'speaker': 'Y', 'location': '石牌校园教育信息技术学院103'}, 1)]
        rec = {'speaker': '', 'location': '石牌校园教育信息技术学院103'}
        self.assertEqual(P._match_poster_vlm(rec, cands)[1], 1)

    def test_ambiguous_candidates_abort(self):
        cands = [({'speaker': 'X', 'location': 'A'}, 0),
                 ({'speaker': 'Y', 'location': 'A'}, 1)]
        self.assertIsNone(P._match_poster_vlm({'speaker': '', 'location': 'A'}, cands))


class MergeUnitTest(unittest.TestCase):
    ARGS = (2026, '2026-11-10 15:12:00', None, 2026)

    def test_dup_cleanup_runs_even_without_vlm(self):
        recs = [
            {'lectureStart': '2026-11-11 09:00:00', 'abstract': '同一段串场摘要',
             'speakerBio': '同一段串场简介', 'speakerAffiliation': '某某，整段简历很长很长很长很长',
             'location': '石牌校园学术报告厅嘉宾介绍'},
            {'lectureStart': '2026-11-11 15:00:00', 'abstract': '同一段串场摘要',
             'speakerBio': '同一段串场简介', 'speakerAffiliation': '华南师范大学',
             'location': '大学城校园国际会议厅'},
        ]
        out = P._merge_poster_vlm_into_sessions(recs, [], *self.ARGS)
        self.assertTrue(all(not r['abstract'] for r in out))
        self.assertTrue(all(not r['speakerBio'] for r in out))
        self.assertEqual(out[0]['speakerAffiliation'], '')
        self.assertEqual(out[1]['speakerAffiliation'], '华南师范大学')
        self.assertEqual(out[0]['location'], '石牌校园学术报告厅')
        self.assertFalse(any(r.get('vlmExtracted') for r in out))

    def test_exact_merge_keeps_text_authority_and_fixes_campus(self):
        recs = [{'lectureStart': '2026-11-12 15:00:00',
                 'location': '石牌校园计算机学院学术报告厅嘉宾介绍',
                 'speaker': '陈昉', 'speakerBio': '', 'abstract': '',
                 'speakerAffiliation': ',丹霞山科普顾问、联合国教科文组织评估专家，很长'}]
        vlm = [{'lectureStart': '2026-11-12 15:00', 'speaker': '陈妨',
                'speakerTitle': '老师', 'speakerAffiliation': '',
                'location': '华南师范大学大学城校区国际会议厅（主会场）',
                'speakerBio': '联合国教科文组织世界地质公园评估专家',
                'abstract': '模型改写出来的摘要'}]
        out = P._merge_poster_vlm_into_sessions(recs, vlm, *self.ARGS)
        r = out[0]
        self.assertEqual(r['speaker'], '陈昉')          # 正文权威，拒 VLM 形近字
        self.assertEqual(r['location'], '大学城校园国际会议厅（主会场）')
        self.assertEqual(r['speakerBio'], '联合国教科文组织世界地质公园评估专家')
        self.assertEqual(r['speakerTitle'], '老师')
        self.assertEqual(r['abstract'], '')             # 摘要不回填
        self.assertTrue(r['vlmExtracted'])
        self.assertEqual(r['imageParseMethod'], 'vlm')

    def test_loose_match_requires_name_ownership_evidence(self):
        bio = 'Eser Metin Akinoglu research primarily focuses on nanomaterials'
        recs = [{'lectureStart': '2026-11-11 11:00:00',
                 'location': '大学城校园理5栋二楼报告厅', 'speaker': '卢俊杰',
                 'speakerBio': bio, 'abstract': '', 'speakerAffiliation': '2025 级'}]
        vlm = [{'lectureStart': '2026-11-11 09:00', 'speaker': 'Eser Metin Akinoglu',
                'speakerAffiliation': 'Jinan University',
                'location': '华南师范大学大学城校园环学院5报告厅'}]
        out = P._merge_poster_vlm_into_sessions(recs, vlm, *self.ARGS)
        self.assertEqual(out[0]['speaker'], 'Eser Metin Akinoglu')
        self.assertEqual(out[0]['speakerAffiliation'], 'Jinan University')
        self.assertEqual(out[0]['lectureStart'], '2026-11-11 11:00:00')  # 时间不动
        self.assertEqual(out[0]['location'], '大学城校园理5栋二楼报告厅')  # 地点不动

    def test_loose_match_rejected_without_evidence(self):
        recs = [{'lectureStart': '2026-11-11 11:00:00',
                 'location': '大学城校园理5栋二楼报告厅', 'speaker': '张三',
                 'speakerBio': '一段与海报无关的简介', 'abstract': ''}]
        vlm = [{'lectureStart': '2026-11-11 09:00', 'speaker': '李四',
                'location': '大学城校园某报告厅'}]
        out = P._merge_poster_vlm_into_sessions(recs, vlm, *self.ARGS)
        self.assertEqual(out[0]['speaker'], '张三')
        self.assertFalse(out[0].get('vlmExtracted'))


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = _FakeVLM()
        cls.recs, _ = _parse(cls.fake)

    def test_11_records_with_retro_and_cross_source_dedup_explained(self):
        # 源页 12 块 → MS5 剔除 11-09 已过期 1 场（[SKIP-RETRO-SESSION]）→ 11 条；
        # 线上 10 条是汤庸场被 cs/6029 跨源合并，属 daily 去重层，不在解析层。
        self.assertEqual(len(self.recs), 11)

    def test_every_image_called_once(self):
        self.assertEqual(len(self.fake.calls), 12)
        self.assertTrue(all(len(c) == 1 for c in self.fake.calls))

    def test_all_records_aligned_with_poster_vlm(self):
        self.assertTrue(all(r.get('vlmExtracted') for r in self.recs))
        self.assertTrue(all(r.get('imageParseMethod') == 'vlm' for r in self.recs))

    def test_no_cross_session_field_bleed(self):
        self.assertEqual(len({(r.get('speakerBio') or '').strip()
                              for r in self.recs}), 11)
        self.assertEqual(len({(r.get('speaker') or '').strip()
                              for r in self.recs}), 11)
        self.assertTrue(all(not (r.get('abstract') or '').strip()
                            for r in self.recs))

    def test_text_authority_title_topic_time_unchanged(self):
        gao = _by_start(self.recs, '2026-11-13 09:30:00')
        self.assertEqual(len(gao), 1)
        self.assertIn('定量研究与问卷分析实务', gao[0]['topic'])
        self.assertEqual(gao[0]['speaker'], '高原')
        self.assertEqual(gao[0]['title'],
                         '【培养】第十九届“勷勤论坛”11月9日-11月15日活动预告')

    def test_vlm_homograph_does_not_override_text_speaker(self):
        chen = _by_start(self.recs, '2026-11-12 15:00:00')
        self.assertEqual(len(chen), 1)
        self.assertEqual(chen[0]['speaker'], '陈昉')
        self.assertEqual(chen[0]['location'], '大学城校园国际会议厅（主会场）')

    def test_loose_alignment_fixes_eser(self):
        eser = _by_start(self.recs, '2026-11-11 11:00:00')
        self.assertEqual(len(eser), 1)
        self.assertEqual(eser[0]['speaker'], 'Eser Metin Akinoglu')

    def test_vlm_total_failure_still_cleans_bleed(self):
        recs, fake = _parse(_FakeVLM(fail_all=True))
        self.assertEqual(len(fake.calls), 12)
        self.assertEqual(len(recs), 11)
        self.assertTrue(all(not (r.get('abstract') or '').strip() for r in recs))
        bios = [(r.get('speakerBio') or '').strip() for r in recs]
        self.assertEqual(len([b for b in bios if b]), len(set(b for b in bios if b)))
        self.assertFalse(any(r.get('vlmExtracted') for r in recs))


if __name__ == '__main__':
    unittest.main(verbosity=2)
