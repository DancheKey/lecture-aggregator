#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""拉丁主讲人姓名重音字母回归（2026-11-16，life/6848 实测）。

背景：用户报告公网卡片无「主讲人」行。根因链（三环相扣，任一即整体落空）：
  ① life 站 CMS 把重音字母夹在 span 标签之间：Rodr</span>í<span>guez…
     get_text(' ') 在标签边界插空格 →「Rodr í guez」；í 非 CJK，
     N1a 空格折叠不合并 → 名字碎片化。
  ② _split_english_speaker 的姓名 token 与 _EN_NAME_OK_RE 只认 [A-Za-z]，
     碎片化的「í」独词使姓名匹配断裂；且「姓名, 单位」合法标签值 62 字
     超过 60 字长度守卫 → 拉丁路径整体返回空。
  ③ 残值（含单位整串）进 F3-WIPE：逗号切段后 'Spanish National Research
     Council' 非人名 → 整体清空。而 VLM 在阶段 4 已填入干净名字
     'Pedro L. Rodríguez'（海报可读），被规则路径的垃圾值**反向覆盖**——
     违反「坏值不得顶掉好值」的直觉，但符合「规则层权威」的既有顺序。

修复（parsers.py 同 commit）：
  1. _split_english_speaker 入口重连「两侧均为拉丁字母的单个 Latin-1 重音
     字母」（Rodr í guez → Rodríguez；×/÷ 不在字母类内不会误触）；
  2. 姓名 token 与 _EN_NAME_OK_RE 字符类扩到 Latin-1 重音区
     （_LATIN1_LETTER 单一事实源，排除 ×/÷）；
  3. 长度守卫 60 → 90（简历护栏由特征词承担，em 6966 实测值 90+ 字且必含
     「，男，/研究方向」特征）。

修复后（真实夹具解析）：speaker='Pedro L. Rodríguez'、
speakerAffiliation='Spanish National Research Council'、VLM 值与规则值一致。

运行：python tests/test_speaker_latin_accents_20261116.py
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 与 golden 同纪律：import parsers 前锁纯规则基线，避免本机 .env 触发真实大模型
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
import parsers as P  # noqa: E402

FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'html', 'life6848.html')
VLM_FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'vlm_life6848.json')


class LatinAccentSplitTest(unittest.TestCase):
    """入口重连 + 字符类扩展：碎裂/完整体都能抽出姓名与单位。"""

    def test_mangled_accent_name_split(self):
        # life/6848 原值（get_text(' ') 碎裂形态），修复前返回 ('', '', '')
        self.assertEqual(
            P._split_english_speaker(
                'Pedro L. Rodr í guez, Spanish National Research Council (CSIC),'),
            ('Pedro L. Rodríguez', 'Spanish National Research Council', ''))

    def test_clean_accent_name_split(self):
        # 无标签碎裂的干净重音名（常见源页形态）同样命中
        self.assertEqual(
            P._split_english_speaker('José García, Universidad de Granada'),
            ('José García', 'Universidad de Granada', ''))

    def test_en_name_ok_accepts_accents(self):
        self.assertTrue(P._EN_NAME_OK_RE.match('Pedro L. Rodríguez'))
        self.assertTrue(P._EN_NAME_OK_RE.match('José García'))
        self.assertTrue(P._EN_NAME_OK_RE.match('Yan Zhang'))          # ASCII 不回退
        self.assertFalse(P._EN_NAME_OK_RE.match('a b c d e'))         # >4 词形态守卫不变
        self.assertFalse(P._EN_NAME_OK_RE.match('1 2'))               # 非字母不变

    def test_short_ascii_value_unaffected(self):
        # 既有 ASCII 主形态不回退（cs 5294 锚点）
        self.assertEqual(
            P._split_english_speaker('Yan Zhang, University of Oslo'),
            ('Yan Zhang', 'University of Oslo', ''))

    def test_len_guard_still_blocks_overlong(self):
        # 长度守卫放宽到 90 后，>90 的值仍被拦（防误喂整段简介）
        long_val = 'Pedro L. Rodríguez, ' + 'some affiliation words ' * 5 + 'end'
        self.assertGreater(len(long_val), 90)
        self.assertEqual(P._split_english_speaker(long_val), ('', '', ''))

    def test_bio_feature_guard_still_blocks(self):
        # em 6966 护栏：含「，男，/研究方向」特征的履历段即使长度合规也拒绝
        bio = '谭春桥，男，中南大学商学院教授，主要研究方向是博弈论。'
        self.assertLessEqual(len(bio), 90)
        self.assertEqual(P._split_english_speaker(bio), ('', '', ''))


class Life6848EndToEndTest(unittest.TestCase):
    """真实夹具端到端：公网卡片恢复「主讲人」行。"""

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding='utf-8') as f:
            cls._html = f.read()
        with open(VLM_FIXTURE, encoding='utf-8') as f:
            cls._vlm = json.load(f)
        # ⚠ 离线确定性三桩（缺一 CI/纯净检出即假红或假差异）：
        # ① _img_to_text 桩空——test_parser_golden.py 在 pytest 收集阶段把它全局换成
        #    「录制回放」，本页海报图不在 ocr_cache.json → 全套跑直接 AssertionError。
        # ② _load_vlm_configs 桩非空假配置——golden 导入阶段还会把它全局换成
        #    golden-replay 回放配置（缓存键随之改变必 miss）；CI 无密钥时返回空，
        #    海报富集腿整体短路 → 本页地点（VLM 从海报读出，规则路被源页
        #    「地一点」笔误挡住）断言必红。假配置只透传、不发请求。
        # ③ _vlm_extract_fields 回放固化夹具——真实 VLM 结果存
        #    data/.vlm_cache.json（gitignore，CI 拿不到），固化为入库夹具后
        #    「规则路 + VLM 合并」全程离线可复现；网络调用本身由 golden/其它
        #    套件覆盖，本套件验证的是合并与守卫逻辑。
        with patch.object(P, '_img_to_text', return_value=''), \
             patch.object(P, '_load_vlm_configs',
                          return_value=[{'name': 'fake', 'model': 'fake-vlm'}]), \
             patch.object(P, '_vlm_extract_fields',
                          side_effect=lambda urls, cfgs: dict(cls._vlm)), \
             patch.object(P, '_safe_fetch', return_value=None):
            out = P.parse_detail(cls._html,
                                 'https://life.scnu.edu.cn/a/20261115/6848.html',
                                 college='生命科学学院', campus='石牌',
                                 list_title='学术讲座')
        cls.recs = out if isinstance(out, list) else [out]
        cls.rec = cls.recs[0]

    def test_speaker_filled(self):
        self.assertEqual(self.rec.get('speaker'), 'Pedro L. Rodríguez')

    def test_affiliation_filled_without_speaker_leak(self):
        aff = self.rec.get('speakerAffiliation') or ''
        self.assertIn('Spanish National Research Council', aff)
        self.assertNotIn('Rodríguez', aff)

    def test_time_location_unregressed(self):
        # 源页「时一间/地一点」笔误（meta+正文均如此），既有兜底已正确解析，锁住不回退
        self.assertEqual(self.rec.get('lectureStart'), '2026-11-16 10:00:00')
        self.assertIn('138会议室', self.rec.get('location') or '')

    def test_speaker_passes_f3(self):
        # F3 终检口径：修复后值必须是合法人名（否则上游仍有碎裂未修）
        self.assertTrue(P._looks_like_real_name(self.rec.get('speaker') or ''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
