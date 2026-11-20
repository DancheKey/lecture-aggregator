#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VLM「拆标题 / 两段合并摘要」两条守卫的回归门禁（2026-11-20，xz/377、368、378）。

背景（用户截图反馈「题目不准确 / 简介变成摘要」）：
  行知书院 377/368/378 三条讲座源页正文一个字都没有，信息全在海报图上，字段由
  VLM 读图填写。VLM 的两处行为与规则层假设不符，各自踩出一条错：

  ① 题目半截（377、368）：海报标题两行排版、中间没有冒号时，VLM 把同一条标题
     拆进 title/topic 两个字段，而且谁进哪个字段随机——377 连跑两次结果正好颠倒，
     368 方向相反。title 恒取源页列表标题（方案A）不受影响，但 topic 空位被半截
     值抢占（fill-only），规则层后置提取发现非空便不再覆盖。
  ② 简介变摘要（378）：海报上「演出团体介绍」与「讲座内容介绍」是两段，VLM 合并
     塞进 abstract、speakerBio 留空。原 C1-UNIVERSAL 整段迁移守卫要求摘要「含履历
     词且不含任何讲座特征词」，两段混杂时两个条件都不满足 → 守卫拒动。

两条守卫的判据都刻意收窄（宁可不修也不误改），本套件同时锁正例与反例：
  · 拆标题：源标题含标签前缀/分隔符（「学术讲座：」「XX学院— 」「系列讲座一」）
    时一律放行——这类页面 topic 只承载标题里的题目段落是正常设计；
  · 段落拆分：只认「前段主体介绍 + 后段讲座内容」且段落边界清晰，摘要背景段
    （「XX 是重要的研究方向之一」）不得被拆进主讲人简介。

全离线：源页 HTML 固化为 tests/fixtures/html/xz3*.html，VLM 返回在本文件内
patch 固定（不用缓存回放——这几个形态恰恰是 VLM 每次返回都不一样的）。
"""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
FIXDIR = os.path.join(ROOT, 'tests', 'fixtures', 'html')

# 与 golden 同纪律：import parsers 前锁纯规则基线，避免本机 .env 触发真实大模型
os.environ.setdefault('SCNU_LLM_TEXT', '0')
os.environ.setdefault('SCNU_LLM_RICH', '0')

import parsers as P  # noqa: E402

URL_377 = 'http://xz.scnu.edu.cn/a/20261103/377.html'
URL_368 = 'http://xz.scnu.edu.cn/a/20261019/368.html'
URL_378 = 'http://xz.scnu.edu.cn/a/20261102/378.html'
URL_376 = 'http://xz.scnu.edu.cn/a/20261111/376.html'

TITLE_377 = '土地与歌 中国民歌的地域风格'
TITLE_368 = '科学提分安全护航 大学生体质健康测试提升与急救知识专题讲座'

BIO_378 = ('凌晨海岸乐队，由汕尾本地音乐人共同组建，擅长R&B等流行音乐创作与演出，'
           '关注沿海青年生活，以音乐展现汕尾滨海城市文化气息。乐队于2026年参与'
           '《“山海升明月”2026粤港澳大湾区中秋晚会》音乐监制与节目演出，同年受邀参加'
           '海丰“致青春·逐梦前行”五四青年节音乐会。')
LEC_378 = ('本次讲座采用音乐交流沙龙形式，结合创作分享、现场演绎和师生互动，'
           '介绍音乐创作实践经历与青年成长体验，分享汕尾滨海生活、乡土人情与地方文化符号'
           '在当代音乐中的表达，展现汕尾本土音乐的活力与地域文化魅力。')


def _vlm(title='', topic='', speaker='', abstract='', bio='', aff='', loc=''):
    return [{'sessionNumber': '', 'title': title, 'topic': topic, 'speaker': speaker,
             'speakerTitle': '', 'speakerAffiliation': aff,
             'lectureStart': '', 'lectureEnd': '', 'location': loc,
             'abstract': abstract, 'speakerBio': bio}]


def _parse(fixture, url, list_title, vlm_fields, college='行知书院', campus='汕尾校区'):
    with open(os.path.join(FIXDIR, fixture), encoding='utf-8') as f:
        html = f.read()
    # 同时桩掉 OCR 文本产出（图片不必下载）与 VLM 返回（形态固定，不依赖缓存）。
    with patch.object(P, '_img_to_text', return_value=''), \
            patch.object(P, '_vlm_extract_fields', return_value=vlm_fields):
        out = P.parse_detail(html, url, college, campus, default_year=2026,
                             list_title=list_title)
    recs = out if isinstance(out, list) else [out]
    return [r for r in recs if r]


class SplitTitleJudgeTest(unittest.TestCase):
    """判据纯函数：正例命中、反例一律放行。"""

    def test_11_complement_two_lines(self):
        """377：两行标题，VLM 名次颠倒也认。"""
        for vt, vp in (('中国民歌的地域风格', '土地与歌'),
                       ('土地与歌', '中国民歌的地域风格')):
            self.assertEqual(
                P._vlm_topic_is_split_title(TITLE_377, vt, vp), 'complement',
                '两段互补拼成整条标题应判拆标题：%r/%r' % (vt, vp))

    def test_12_full_title_plus_fragment(self):
        """368：VLM 把完整题目放 title、主标题片段放 topic，且两行之间补了空格。"""
        self.assertEqual(
            P._vlm_topic_is_split_title(
                TITLE_368, '科学提分 安全护航',
                '大学生体质健康测试提升与急救知识专题讲座'), 'complement')

    def test_13_full_title_branch(self):
        self.assertEqual(
            P._vlm_topic_is_split_title(
                TITLE_368, TITLE_368, '大学生体质健康测试提升与急救知识专题讲座'),
            'full-title')

    def test_20_marked_title_not_touched(self):
        """源标题带标签前缀/分隔符时是正常设计，一律放行。"""
        cases = [
            # 376 标题用半角冒号分隔主副标题
            ('弹指千年:走进琵琶的艺术世界', '弹指千年', '走进琵琶的艺术世界'),
            # 学院名 + 题目
            ('华南师范大学物理学院— 量子计算前沿', '物理学院', '量子计算前沿'),
            # 系列名 + 场次名
            ('《人文精神概论》系列讲座一 导论', '《人文精神概论》系列讲座一', '导论'),
        ]
        for src, vt, vp in cases:
            self.assertEqual(P._vlm_topic_is_split_title(src, vt, vp), '',
                             '带标签/分隔符的标题不得拦：%r' % src)

    def test_21_same_value_not_touched(self):
        self.assertEqual(
            P._vlm_topic_is_split_title('学术讲座 人工智能前沿', '人工智能前沿', '人工智能前沿'), '')

    def test_22_partial_overlap_not_touched(self):
        """两段有重叠（不是互补拼成整条）时不是拆标题。"""
        self.assertEqual(
            P._vlm_topic_is_split_title('人工智能 前沿 技术', '人工智能', '人工智能技术'), '')


class MergedAbstractJudgeTest(unittest.TestCase):
    def test_11_split_intro_and_lecture(self):
        got = P._split_merged_abstract(BIO_378 + '\n' + LEC_378)
        self.assertIsNotNone(got)
        self.assertEqual(got[0], BIO_378)
        self.assertEqual(got[1], LEC_378)

    def test_20_background_para_not_split(self):
        """摘要背景段不是人物/团体介绍，不得拆进主讲人简介。"""
        ab = ('希格斯物理是高能物理最重要的研究方向之一。大型强子对撞机上的'
              'ATLAS 和 CMS 实验在 2012 年发现了希格斯玻色子。\n'
              '本报告将介绍希格斯玻色子性质的研究进展与未来方向。')
        self.assertIsNone(P._split_merged_abstract(ab))

    def test_21_single_para_not_split(self):
        self.assertIsNone(P._split_merged_abstract(BIO_378 + LEC_378))

    def test_22_lecture_word_in_head_not_split(self):
        """前段已出现讲座引导词时边界不清，整体不动。"""
        ab = ('本次讲座分两部分。\n' + BIO_378)
        self.assertIsNone(P._split_merged_abstract(ab))


class EndToEndTest(unittest.TestCase):
    def test_11_377_topic_complete(self):
        recs = _parse('xz377.html', URL_377, TITLE_377,
                      _vlm(title='中国民歌的地域风格', topic='土地与歌',
                           speaker='万钟如', aff='华师音乐学院'))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['title'], TITLE_377)
        self.assertEqual(recs[0]['topic'], TITLE_377,
                         'VLM 把标题拆两半时，题目必须回到完整源标题')

    def test_12_368_topic_complete(self):
        recs = _parse('xz368.html', URL_368, TITLE_368,
                      _vlm(title='科学提分 安全护航',
                           topic='大学生体质健康测试提升与急救知识专题讲座',
                           speaker='邓万金、陆俊', aff='深圳大学体育学院'))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['topic'], TITLE_368,
                         '题目必须完整，不得被「去掉尾部通用词」削成「…专题」')

    def test_20_376_keeps_vlm_topic(self):
        """反例：376 源标题含冒号，VLM topic 虽只是其中一段也不得被替换。"""
        recs = _parse('xz376.html', URL_376, '弹指千年:走进琵琶的艺术世界',
                      _vlm(title='弹指千年', topic='走进琵琶的艺术世界',
                           speaker='张婷婷'))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['topic'], '走进琵琶的艺术世界')

    def test_30_378_bio_promoted(self):
        recs = _parse('xz378.html', URL_378, '从海岸出发:汕尾青年音乐创作分享会',
                      _vlm(title='从海岸出发：汕尾青年音乐创作分享会', speaker='曾粤特',
                           aff='凌晨海岸乐队', abstract=BIO_378 + '\n' + LEC_378))
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r['speakerBio'], BIO_378, '团体介绍段应归简介')
        self.assertNotIn('凌晨海岸乐队', r['abstract'], '摘要不得含履历/团体介绍段')
        self.assertIn('本次讲座', r['abstract'], '摘要应保留讲座内容段')

    def test_31_vlm_bio_not_overwritten(self):
        """VLM 已把两段分开时，拆分守卫不得重复搬运（幂等）。"""
        recs = _parse('xz378.html', URL_378, '从海岸出发:汕尾青年音乐创作分享会',
                      _vlm(title='从海岸出发：汕尾青年音乐创作分享会', speaker='曾粤特',
                           aff='凌晨海岸乐队', abstract=LEC_378, bio=BIO_378))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['speakerBio'], BIO_378)
        self.assertEqual(recs[0]['abstract'], LEC_378)


if __name__ == '__main__':
    unittest.main(verbosity=2)
