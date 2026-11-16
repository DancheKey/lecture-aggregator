#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""守卫降级不销毁 + 入库不变量绊线回归（2026-11-16，life/6848 三连环）。

背景（用户之问：VLM 能识别、有双模型、还有规则，为什么还是漏）：
VLM 从海报读到干净人名 → 规则层用正文残值**无条件覆盖**（elif sp_clean 分支
不查空位）→ F3 终检判残值非人名**清空**。三步各自按合同正确，组合却把好值
弄丢，公网卡片静默缺「主讲人」行，直到用户肉眼发现。同形状事故：11-11 海报
单人简介盖正文共享简介、陈昉地点串场、「特邀嘉宾」推成第一主讲。

修复两层（parsers.py / audit_data_quality.py 同 commit）：
  ② 守卫降级不销毁：F3 / D-FINAL / D-ORG 清空 speaker 前先回退
     `_apply_vlm_to_result` 暂存的 VLM 候选（`_vlmSpCand`/`_vlmAffCand`，
     逐段过 `_looks_like_real_name`），全部候选失败才置空；`[F3-FALLBACK]`
     日志留证。私有键由 `_gate_record` 出闸统一 pop，不进 lectures.json。
  ① 入库不变量绊线：audit_data_quality.scan 新增高危类——
     「VLM/海报有据（hasPosterImage / imageParseMethod=vlm / vlmExtracted）
     但 speaker 为空或未过姓名校验」。多主讲「、」串逐段校验，不误报。

运行：python tests/test_f3_fallback_tripwire_20261116.py
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import parsers as P  # noqa: E402
import audit_data_quality as A  # noqa: E402

FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'html', 'life6848.html')
VLM_FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'vlm_life6848.json')


class FallbackHelperTest(unittest.TestCase):
    """_f3_fallback_to_vlm：候选有效才恢复，无候选时调用方照旧清空。"""

    def _rec(self, sp, cand='张三', affc=''):
        r = {'speaker': sp, 'sourceUrl': 'http://x/1.html'}
        if cand:
            r['_vlmSpCand'] = cand
        if affc:
            r['_vlmAffCand'] = affc
        return r

    def test_no_candidate_returns_false(self):
        r = {'speaker': '特邀嘉宾'}
        self.assertFalse(P._f3_fallback_to_vlm(r))
        self.assertEqual(r['speaker'], '特邀嘉宾')  # 调用方负责清空，助手不动原值

    def test_valid_single_candidate_restored(self):
        r = self._rec('特邀嘉宾')
        self.assertTrue(P._f3_fallback_to_vlm(r))
        self.assertEqual(r['speaker'], '张三')

    def test_valid_multi_seg_candidate_joined_by_dunhao(self):
        r = self._rec('特邀嘉宾', cand='张三,李四')
        self.assertTrue(P._f3_fallback_to_vlm(r))
        self.assertEqual(r['speaker'], '张三、李四')

    def test_invalid_candidate_rejected(self):
        r = self._rec('特邀嘉宾', cand='特邀嘉宾、某某单位')
        self.assertFalse(P._f3_fallback_to_vlm(r))

    def test_aff_restored_only_when_empty(self):
        r = self._rec('特邀嘉宾', affc='华南师范大学')
        self.assertTrue(P._f3_fallback_to_vlm(r))
        self.assertEqual(r['speakerAffiliation'], '华南师范大学')
        r2 = self._rec('特邀嘉宾', affc='华南师范大学')
        r2['speakerAffiliation'] = '已有单位'
        self.assertTrue(P._f3_fallback_to_vlm(r2))
        self.assertEqual(r2['speakerAffiliation'], '已有单位')  # 只填空不覆盖


class StashWiringTest(unittest.TestCase):
    """_apply_vlm_to_result：VLM 姓名逐段过校验后才暂存候选。"""

    def _apply(self, vlm_fields, result=None):
        result = result if result is not None else {'sourceUrl': 'http://x/1.html'}
        P._apply_vlm_to_result(result, dict(vlm_fields), 2026, None, 2026, 2026)
        return result

    def test_valid_speaker_stashed(self):
        r = self._apply({'speaker': '张三', 'speakerAffiliation': '华南师范大学'})
        self.assertEqual(r.get('_vlmSpCand'), '张三')
        self.assertEqual(r.get('_vlmAffCand'), '华南师范大学')

    def test_multi_seg_valid_stashed_normalized(self):
        r = self._apply({'speaker': '张三, 李四'})
        self.assertEqual(r.get('_vlmSpCand'), '张三、李四')

    def test_invalid_speaker_not_stashed(self):
        r = self._apply({'speaker': '特邀嘉宾'})
        self.assertIsNone(r.get('_vlmSpCand'))

    def test_gate_pops_private_keys(self):
        out = P.apply_exit_gate({'_vlmSpCand': '张三', '_vlmAffCand': '某大学',
                                 'speaker': '', 'sourceUrl': 'http://x/1.html'})
        self.assertNotIn('_vlmSpCand', out)
        self.assertNotIn('_vlmAffCand', out)


class Life6848FallbackEndToEndTest(unittest.TestCase):
    """真实夹具端到端：把 _split_english_speaker 桩回修复前失败形态，
    复现「规则残值覆盖 VLM 好值 → F3 清空」三连环，断言降级回退休复。"""

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding='utf-8') as f:
            cls._html = f.read()
        with open(VLM_FIXTURE, encoding='utf-8') as f:
            cls._vlm = json.load(f)
        # 离线三桩纪律同 test_speaker_latin_accents_20261116（golden 收集阶段
        # 全局换 _img_to_text/_load_vlm_configs + CI 无密钥海报腿短路）。
        # 额外把 _split_english_speaker 桩成失败，人为复活 2026-11-16 修复前的
        # 规则路残值形态，使「覆盖 + 清空」三连环可复现。
        with patch.object(P, '_img_to_text', return_value=''), \
             patch.object(P, '_load_vlm_configs',
                          return_value=[{'name': 'fake', 'model': 'fake-vlm'}]), \
             patch.object(P, '_vlm_extract_fields',
                          side_effect=lambda urls, cfgs: dict(cls._vlm)), \
             patch.object(P, '_safe_fetch', return_value=None), \
             patch.object(P, '_split_english_speaker', return_value=('','','')):
            out = P.parse_detail(cls._html,
                                 'https://life.scnu.edu.cn/a/20261115/6848.html',
                                 college='生命科学学院', campus='石牌',
                                 list_title='学术讲座')
        cls.recs = out if isinstance(out, list) else [out]
        cls.rec = cls.recs[0]

    def test_fallback_restored_vlm_speaker(self):
        self.assertEqual(self.rec.get('speaker'), 'Pedro L. Rodríguez')

    def test_affiliation_restored_from_candidate(self):
        self.assertIn('CSIC', self.rec.get('speakerAffiliation') or '')

    def test_no_private_key_leak(self):
        self.assertEqual([k for k in self.rec if k.startswith('_')], [])

    def test_speaker_passes_f3(self):
        self.assertTrue(P._looks_like_real_name(self.rec.get('speaker') or ''))


class AuditTripwireTest(unittest.TestCase):
    """① 入库不变量绊线：VLM/海报有据却 speaker 空或非法 → 高危标红。"""

    def _scan(self, rec):
        rec.setdefault('sourceUrl', 'http://x/1.html')
        return A.scan([rec], excluded={})

    def _has(self, issues, desc_prefix):
        return any(it[3].startswith(desc_prefix) for it in issues)

    def test_vlm_evidence_empty_speaker_flagged_high(self):
        issues = self._scan({'imageParseMethod': 'vlm', 'vlmExtracted': True})
        self.assertTrue(self._has(issues, 'VLM/海报有据但主讲人为空'))
        it = next(it for it in issues if it[3].startswith('VLM/海报有据但主讲人为空'))
        self.assertEqual(it[2], '高')  # 既有缺失档仍是低，矛盾信号单独标红
        self.assertTrue(any(it[0] == '缺失' and it[2] == '低' for it in issues))

    def test_poster_evidence_empty_speaker_flagged(self):
        issues = self._scan({'hasPosterImage': True})
        self.assertTrue(self._has(issues, 'VLM/海报有据但主讲人为空'))

    def test_invalid_speaker_with_evidence_flagged(self):
        issues = self._scan({'imageParseMethod': 'vlm', 'speaker': '特邀嘉宾'})
        self.assertTrue(self._has(issues, 'VLM/海报有据但主讲人未过姓名校验'))

    def test_multi_speaker_valid_not_flagged(self):
        # 多主讲「、」串逐段校验（yjsy/10291 形态），不得误报
        issues = self._scan({'imageParseMethod': 'vlm',
                             'speaker': '谈子敏、胡扬、曾东林、江云颖'})
        self.assertFalse(self._has(issues, 'VLM/海报有据但主讲人未过姓名校验'))
        self.assertFalse(self._has(issues, 'VLM/海报有据但主讲人为空'))

    def test_no_evidence_no_invariant_issue(self):
        # 无海报/VLM 证据的空主讲只走既有低危缺失档，不触发绊线
        issues = self._scan({})
        self.assertFalse(self._has(issues, 'VLM/海报有据'))
        self.assertTrue(any(it[0] == '缺失' for it in issues))

    def test_translated_dotted_name_not_flagged(self):
        # 外文音译「·」是合法形态（F3 同口径放行），有据也不误报
        issues = self._scan({'imageParseMethod': 'vlm', 'speaker': '克里斯蒂安·盖勒兰'})
        self.assertFalse(self._has(issues, 'VLM/海报有据但主讲人未过姓名校验'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
