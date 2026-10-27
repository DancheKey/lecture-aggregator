#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""bio/abstract 交叉卫生守卫回归：锁住 em/9551 摘要散文误作讲者简介的修复（2026-10-27）。

背景（em.scnu.edu.cn/a/20230522/9551.html 实测病根，两层叠加）：
  1. bio_pat 的裸「简历」起点锚点命中摘要散文——9551 摘要正文
     「…13101位精英简历数据，涉及科研教育…」中「简历」出现位置早于真正的
     「主讲人简介：」标签，re.search 取首个匹配即把摘要后半段误当 bio 起点。
  2. 旧 BIO_STOP 终止词表只有括号形态（[主讲人简介]），而 body_text 构建时【】
     括号已被剥离，「主讲人简介：李嘉楠…」是裸词形态 → bio 吞掉标签与真简介
     （517 字），下游 _trim_bio_to_first_speaker 截断后只剩摘要后半段（232 字）。

修复（同一 commit）：
  1. BIO_STOP 补全裸词形态（主讲人简介|报告人简介|…），与 abstract 终止边界同词表；
     括号词表同步补全复合标签（根修：bio 不再吞「主讲人简介：」标签）。
  2. abstract 提取完成后新增交叉卫生检查：归一化（去空白+全半角标点统一）后
     bio 是 abstract 的子串 → 回退 re.finditer 遍历全部锚点候选，取第一个不与
     abstract 重叠的；无合格候选则清空 bio（宁缺勿脏）。

本测试相对既有门禁多锁两点（test_p0_abstract_fix 锁简介-摘要标签混淆；
test_bio_stop_words 只锁 BIO_STOP 裸「报名/咨询」）：摘要散文「简历」字样
误触发起点锚点时的产出卫生 + bio ⊂ abstract 子串形态的回退重试语义。
"""
import os
import sys
import unittest

# ⚠ 必须在 import parsers 前关闭 LLM 文本增强/富化，否则会触发外部调用
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402


_HEAD = """<html><head><title>讲座预告</title></head><body>
<div class="content">
<p>讲座题目：{title}</p>
<p>主讲人：{speaker}</p>
<p>讲座时间：2026年7月2日 10:00</p>
<p>讲座地点：文二栋301会议室</p>
"""


def _parse(html, url='http://example.com/a/1.html'):
    out = P.parse_detail(html, url, '测试学院', '石牌', skip_news_filter=True)
    recs = out if isinstance(out, list) else [out]
    return recs[0] if recs else {}


class BioAbsCrossGuardTest(unittest.TestCase):
    """摘要散文含「简历」字样时，speakerBio 不得装入摘要片段（em/9551 回归）。"""

    def test_摘要散文简历字样不误作bio起点(self):
        # 复刻 9551 形态：摘要散文含「简历」（早于真简介标签出现）
        html = (_HEAD.format(title='科举废除与人才配置', speaker='李嘉楠')
                + '<p>【摘要】</p>'
                + '<p>近代科举废除之后，传统功名被西式教育文凭替代。研究者手动整理并'
                + '分析262个府1865至1900年出生的13101位精英简历数据，涉及科研教育、'
                + '司法、工商业等多个领域，研究发现科举废除后产生了更多现代化职业精英，'
                + '为理解人才配置制度提供了新的历史证据。</p>'
                + '<p>主讲人简介：李嘉楠，厦门大学经济学院副教授、博士生导师，'
                + '英国诺丁汉大学经济学博士，研究领域为经济史与国际贸易。</p>'
                + '</div></body></html>')
        r = _parse(html)
        bio = r.get('speakerBio') or ''
        ab = r.get('abstract') or ''
        # 真简介应被采纳，而非摘要后半段
        self.assertIn('厦门大学经济学院副教授', bio, '主讲人简介内容应进 speakerBio')
        self.assertNotIn('13101位精英简历数据', bio, '摘要散文片段不得装入 speakerBio')
        self.assertIn('13101位精英简历数据', ab, '摘要应完整保留在 abstract')

    def test_bio不得吞掉主讲人简介标签(self):
        # BIO_STOP 根修锁定：裸词「主讲人简介：」必须终止 bio 提取
        html = (_HEAD.format(title='经济学讲座', speaker='李嘉楠')
                + '<p>【摘要】</p>'
                + '<p>本次讲座讨论人才配置制度的经济学含义，并检验相关机制假说，'
                + '得出稳健结论后讨论政策含义与后续研究方向。</p>'
                + '<p>主讲人简介：李嘉楠，厦门大学经济学院副教授。</p>'
                + '</div></body></html>')
        r = _parse(html)
        bio = r.get('speakerBio') or ''
        self.assertNotIn('主讲人简介', bio, 'speakerBio 不得吞掉「主讲人简介」标签')

    def test_正常bio与摘要独立时不受影响(self):
        html = (_HEAD.format(title='机器学习讲座', speaker='张三')
                + '<p>【摘要】</p>'
                + '<p>本报告介绍深度学习在图像识别中的最新进展与挑战。</p>'
                + '<p>主讲人简介：张三，计算机学院教授，主要研究方向为计算机视觉。</p>'
                + '</div></body></html>')
        r = _parse(html)
        bio = r.get('speakerBio') or ''
        self.assertIn('计算机学院教授', bio, '正常讲者简介应完整保留')
        self.assertNotIn('图像识别中的最新进展', bio)

    def test_姓名职衔简介标签动态截断(self):
        # 复刻 psy/203 马宁形态：简介段以「马宁博士简介:」起头（静态词表无此形态）
        html = (_HEAD.format(title='特质推断的神经机制', speaker='马宁')
                + '<p>【摘要】</p>'
                + '<p>特质推断一直是社会认知研究的重要主题。近年来随着认知神经科学的'
                + '快速发展，很多研究开始探讨特质推断的神经机制。</p>'
                + '<p>马宁博士简介: 马宁，博士毕业于比利时布鲁塞尔自由大学，'
                + '主要从事社会推断的神经机制研究。</p>'
                + '</div></body></html>')
        r = _parse(html)
        bio = r.get('speakerBio') or ''
        self.assertIn('布鲁塞尔自由大学', bio, '「姓名+职衔+简介」标签后的真简介应被提取')
        self.assertNotIn('特质推断一直是社会认知研究', bio, '摘要不得混入 bio')

    def test_复合姓名speaker提取CJK段注入(self):
        # 复刻 psy/206 李平形态：speaker='Ping Li ( 李平'，简介标签「李平教授简介:」
        html = (_HEAD.format(title='语言认知研究', speaker='Ping Li ( 李平')
                + '<p>报告内容简介: A great deal of research has examined '
                + 'behavioral performance changes associated with learning.</p>'
                + '<p>李平教授简介: 李平教授1983年在北京大学获得语言学学士学位，'
                + '主要研究语言的神经和计算基础。</p>'
                + '</div></body></html>')
        r = _parse(html)
        bio = r.get('speakerBio') or ''
        self.assertIn('语言学学士', bio, '复合名 speaker 应提取 CJK 段动态注入锚点')
        self.assertNotIn('A great deal', bio, '英文摘要不得混入 bio')
        self.assertIn('behavioral performance', (r.get('abstract') or ''),
                      '英文摘要应归位 abstract')

    def test_英文摘要与裸简介标签分离(self):
        # 复刻 psy/1724 丁晓伟形态：Abstract 英文摘要 + 裸「简介:」中文简介
        html = (_HEAD.format(title='生物运动感知', speaker='丁晓伟')
                + '<p>Abstract: Biological motion broadly refers to the '
                + 'movements of animate entities and carries social signals.</p>'
                + '<p>简介: 丁晓伟博士，中山大学心理学系副教授，主要研究注意和工作记忆。</p>'
                + '</div></body></html>')
        r = _parse(html)
        bio = r.get('speakerBio') or ''
        self.assertIn('中山大学心理学系副教授', bio, '裸「简介:」后的真简介应被提取')
        self.assertNotIn('Biological motion', bio, '英文摘要不得混入 bio')
        self.assertIn('Biological motion', (r.get('abstract') or ''),
                      '英文摘要应归位 abstract')

    def test_真简介存在时优先采纳且不与摘要重叠(self):
        # 交叉检查回退语义锁定：首个锚点候选（摘要后半段）被拒后，
        # finditer 必须继续找下一个候选（真简介），而不是直接放弃。
        html = (_HEAD.format(title='科举废除与人才配置', speaker='李嘉楠')
                + '<p>【摘要】</p>'
                + '<p>近代科举废除之后，传统功名被西式教育文凭替代。研究者手动整理并'
                + '分析262个府1865至1900年出生的13101位精英简历数据，涉及科研教育、'
                + '司法、工商业等多个领域，研究发现科举废除后产生了更多现代化职业精英。</p>'
                + '<p>主讲人简介：李嘉楠，厦门大学经济学院副教授、博士生导师。</p>'
                + '</div></body></html>')
        r = _parse(html)
        bio = r.get('speakerBio') or ''
        self.assertIn('厦门大学经济学院副教授', bio,
                      '回退重试必须采纳真简介候选')


if __name__ == '__main__':
    unittest.main()
