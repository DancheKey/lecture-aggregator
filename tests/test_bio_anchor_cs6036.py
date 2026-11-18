# -*- coding: utf-8 -*-
"""cs/6036 简介锚点优先级冲突：短句抢跑 + 终止符自撞（2026-11-18）。

用户报障：源页有约 500 字完整「报告人简介」，页面只显示 15 字短句。

根因（详见 parsers.py 中 bio_pat 处的 6036 注释）：
同一页先出现「报告人：郑伟诗，中山大学计算机学院教授」（位置 64），
后文又有独立「报告人简介：郑伟诗，…」（位置 434）。re.search 只取首个 →
短句抢到起点；BIO_STOP 含裸词「报告人简介」又把短句截断在 434，完整简介
反倒成了短句的终止符。

修法：仅当首个锚点是「报告人：姓名，」短句、且后文确有「报告人简介」标签、
后者候选更长时才切换起点。不改成泛化的「取最长候选」——psy1179/psy1305/
yjsy10291 实测会被英文片段或错误开头替换（全字段快照已锁零差异）。

离线单测：真实源页固化为夹具、剥离 img/script 密封化（零 OCR/VLM/网络），
SCNU_LLM_TEXT/RICH=0 锁纯规则基线（与 CI 无密钥态一致）。
"""
import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scraper'))
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers  # noqa: E402

_FIXTURE = os.path.join(_ROOT, 'tests', 'fixtures', 'html', 'cs6036.html')
_URL = 'http://cs.scnu.edu.cn/a/20261118/6036.html'
_LIST_TITLE = '2026-11-18第71期学术报告预告'
_EXPECTED_BIO_LEN = 395


def _load_sealed():
    """真实源页 + 剥离 img/script：保留 CMS 排版与双锚点，密封化零网络。"""
    with open(_FIXTURE, encoding='utf-8') as f:
        html = f.read()
    html = re.sub(r'<img\b[^>]*>', '', html, flags=re.I)
    html = re.sub(r'<script\b.*?</script>', '', html, flags=re.S | re.I)
    return html


def _parse():
    out = parsers.parse_detail(_load_sealed(), _URL, '计算机学院', '石牌',
                               default_year=2026, list_title=_LIST_TITLE,
                               skip_news_filter=True)
    return out if isinstance(out, list) else ([out] if out else [])


class BioAnchorCs6036Test(unittest.TestCase):
    """双锚点冲突页必须取完整简介，关键字段与反例行为一并锁定。"""

    def test_01_fixture_keeps_both_anchor_labels(self):
        """样本必须同时保留两个标签，防「把夹具改成正确值」架空门禁。"""
        html = _load_sealed()
        self.assertEqual(html.count('报告人：'), 1)
        self.assertEqual(html.count('报告人简介'), 1)

    def test_02_full_bio_replaces_15_char_stub(self):
        """核心修复：简介从 15 字短句恢复为 395 字完整原文。"""
        recs = _parse()
        self.assertEqual(len(recs), 1)
        bio = recs[0].get('speakerBio') or ''
        self.assertEqual(len(bio), _EXPECTED_BIO_LEN,
                         '简介长度应为完整 395 字，实际 %d：%r' % (len(bio), bio[:40]))
        self.assertNotEqual(bio, '郑伟诗,中山大学计算机学院教授')

    def test_03_bio_content_from_source_not_generated(self):
        """简介逐段取自源页原文（关键履历不得缺），禁 LLM 改写。"""
        bio = _parse()[0].get('speakerBio') or ''
        for frag in ('国家重大人才项目获得者', 'IAPR Fellow', '英国皇家学会牛顿高级学者',
                     '机器智能与先进计算教育部重点实验室主任', 'IEEE T-PAMI',
                     '国家自然科学基金委重点项目', '广东省自然科学奖一等奖'):
            self.assertIn(frag, bio, '简介缺源页片段：%s' % frag)
        self.assertTrue(bio.startswith('郑伟诗,中山大学计算机学院教授、'),
                        '完整简介应从姓名起，第二个词是顿号而非逗号：%r' % bio[:30])

    def test_04_other_fields_intact(self):
        """修法只换 bio 起点，title/topic/speaker/单位/地点/摘要全不变。"""
        r = _parse()[0]
        self.assertEqual(r.get('title'), '第71期学术报告预告')
        self.assertEqual(r.get('topic'), '移动机器人视觉言语自主导航')
        self.assertEqual(r.get('speaker'), '郑伟诗')
        self.assertEqual(r.get('speakerTitle'), '')
        self.assertEqual(r.get('speakerAffiliation'), '中山大学计算机学院')
        self.assertEqual(r.get('location'), '计算机学院东阶梯学术报告厅')
        abstract = r.get('abstract') or ''
        self.assertTrue(abstract.startswith('视觉语言导航致力于赋予智能体'))
        self.assertNotIn('国家重大人才项目获得者', abstract, '简介不得串进摘要')

    def test_05_short_report_person_prefix_without_bio_label_unchanged(self):
        """反例：只有「报告人：姓名，履历」短句、无独立简介标签时不得误触发。"""
        html = '''<html><body><div class="content">
<p>时间：2026年11月20日 10:30</p>
<p>地点：计算机学院学术报告厅</p>
<p>题目：深度模型窃取研究</p>
<p>报告人：张天威博士，新加坡南洋理工大学计算机学院助理教授。主要研究方向是计算机系统安全。</p>
<p>摘要：深度学习因其处理复杂任务和环境的强大能力而受到欢迎。</p>
</div></body></html>'''
        out = parsers.parse_detail(
            html, 'http://cs.scnu.edu.cn/a/20230522/5472.html',
            '计算机学院', '石牌', default_year=2023,
            list_title='第55期学术报告', skip_news_filter=True)
        out = out if isinstance(out, list) else [out]
        bio = out[0].get('speakerBio') or ''
        self.assertIn('新加坡南洋理工大学计算机学院助理教授', bio)
        self.assertNotIn('摘要：', bio)


if __name__ == '__main__':
    unittest.main(verbosity=2)
