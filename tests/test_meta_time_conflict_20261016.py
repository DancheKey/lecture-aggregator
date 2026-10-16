# -*- coding: utf-8 -*-
"""meta 摘要旧时刻压过正文正确值的回归（2026-10-16，life/6829）。

## 根因链（生产实录）

公网记录 http://life.scnu.edu.cn/a/20261015/6829.html 落库
`lectureStart=2026-10-16 02:30:00`，而页面正文明确写「上午10:00」。三因叠加：

1. **正文标签被白色 span 混淆**：编辑用
   `<span>时</span><span style="color:white">一</span><span>间：</span>`
   排版（视觉「时间：」，字面「时一间：」），R1 权威标签正则不中，
   正文时间只能走 R2 通用解析（值恰好正确）；「地点」同理变「地一点」。
2. **meta description 残留旧值**：`时间：2026年10月16日（星期五）上午2:30`
   （编辑后改正文为 10:00，meta 未同步）。
3. **meta 追加去重守卫失效**：守卫拿 meta 前 30 字对正文比对，正文同位置
   字面是「时一间」≠「时间」→ 判「正文不含 meta」→ meta 被拼进 body_text，
   其干净的「时间：」标签以 R1 authoritative-label 身份压过正文 R2 结果。

## 本套件锁定

  ① 冲突场景：落库值 = 正文新时刻，meta 旧值不得泄漏（A：冲突抑制）；
  ② 白色不可见节点被剔除，标签字面恢复（B：剔除不可见样式节点）；
  ③ 保护面：正文无时刻、meta 补时刻的页面（美术学院类）**不受影响**——
     追加功能保留；
  ④ 保护面：两侧时间相等时行为不变；
  ⑤ `_meta_time_conflicts_with_body` 的裁决口径（对称、真时刻、非占位）。

运行：python tests/test_meta_time_conflict_20261016.py
"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

# 环境开关须在 import parsers 之前设置（模块顶层读取）
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers as P  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

URL = 'http://life.scnu.edu.cn/a/20261015/6829.html'

# 正文时间行：白色 span 在「时」「间」之间夹不可见「一」字（复刻生产页形态）
_WHITE_TIME_LINE = (
    '<p><span>时</span><span style="font-size: 17px; color: white;">一</span>'
    '<span>间：</span><span>2026</span><span>年</span><span>10</span>'
    '<span>月</span><span>16</span><span>日</span><span>（星期五）</span>'
    '<span>上午</span><span>10</span><span>:</span><span>00</span></p>'
)
# 正文时间行：干净标签版
_CLEAN_TIME_LINE = '<p>时间：2026年10月16日（星期五）上午10:00</p>'
# 正文地点行：白色 span 夹「一」→ 字面「地一点」
_WHITE_LOC_LINE = (
    '<p><span>地</span><span style="color: white;">一</span><span>点：</span>'
    '<span>昆虫所110会议室</span></p>'
)
_CLEAN_LOC_LINE = '<p>地点：昆虫所110会议室</p>'
_FILLER = (
    '<p>本讲座由生命科学学院主办，欢迎广大师生参加。讲座将围绕蛋白酶系统展开'
    '深入探讨，结合烟草天蚕、果蝇与刚比亚按蚊等模式昆虫的最新研究进展，解析'
    '免疫调控的分子机制与信号通路。</p>'
)


def _page(time_line, meta_desc, loc_line=_CLEAN_LOC_LINE):
    return (
        '<html><head><title>学术讲座通知</title>'
        '<meta name="description" content="%s">'
        '</head><body><div class="wp_articlecontent">'
        '<p>报告人：蒋浩波教授，美国俄克拉荷马州立大学</p>'
        '<p>主持人：余小强</p>'
        + time_line + loc_line + _FILLER +
        '</div></body></html>'
    ) % meta_desc


# 生产页 meta（残旧 2:30）——前缀与正文不同，去重守卫必失效（须靠冲突抑制兜底）
_META_STALE = ('报告人：蒋浩波教授，美国俄克拉荷马州立大学 主持人：余小强 '
               '时间：2026年10月16日（星期五）上午2:30  地点：昆虫所110会议室')
# 守卫失效专用前缀（【主题】不在正文，规范化后前 30 字仍不在正文）
_META_STALE_DIRTY_HEAD = ('【主题】蛋白酶系统对昆虫免疫的调控 '
                          '时间：2026年10月16日上午2:30 地点：昆虫所110会议室')
_META_FRESH_DIRTY_HEAD = ('【主题】蛋白酶系统对昆虫免疫的调控 '
                          '时间：2026年10月16日上午10:00 地点：昆虫所110会议室')
_META_DATE_PLUS_TIME = ('【主题】蛋白酶系统对昆虫免疫的调控 '
                        '时间：2026年10月16日上午10:00 地点：昆虫所110会议室')


def parse(html):
    out = P.parse_detail(html, URL, '生命科学学院', '石牌', default_year=2026)
    rec = out[0] if isinstance(out, list) and out else out
    return rec if isinstance(rec, dict) else {}


class MetaTimeConflictTest(unittest.TestCase):
    """A：meta 旧时刻不得压过正文正确值。"""

    def test_01_白色span混淆标签_正文时刻为准(self):
        """核心回归：复刻 life/6829 生产页形态。

        正文（白色 span 混淆「时间：」）写 10:00、meta 残旧 2:30 →
        必须落库 10:00；2:30 不得泄漏到任何时间字段。
        """
        r = parse(_page(_WHITE_TIME_LINE, _META_STALE))
        self.assertIn('lectureStart', r, '未解析出时间，本用例失去意义')
        self.assertEqual(str(r['lectureStart']), '2026-10-16 10:00:00',
                         '正文 10:00 被 meta 旧值 2:30 压过（根因链复发）')
        self.assertNotIn('02:30', str(r.get('lectureStart'))
                         + str(r.get('lectureEnd')) + str(r.get('timeNote')),
                         'meta 旧时刻泄漏')

    def test_02_干净标签加脏前缀meta_仍以正文为准(self):
        """守卫失效但正文标签干净：R1 正文命中 + 冲突抑制，双保险下取 10:00。"""
        r = parse(_page(_CLEAN_TIME_LINE, _META_STALE_DIRTY_HEAD))
        self.assertEqual(str(r['lectureStart']), '2026-10-16 10:00:00')

    def test_03_白色span混淆标签加脏前缀meta(self):
        r = parse(_page(_WHITE_TIME_LINE, _META_STALE_DIRTY_HEAD))
        self.assertEqual(str(r['lectureStart']), '2026-10-16 10:00:00')


class MetaAppendPreservedTest(unittest.TestCase):
    """保护面：meta 追加功能不得被冲突抑制误伤。"""

    def test_10_正文无时间标签_meta补日期时刻_仍追加(self):
        """美术学院类页面：正文完全无时间标签（图/骨架页），时刻在 meta 摘要里
        → 必须照旧追加，meta 的日期+时刻成为唯一时间来源。

        （parsers.py 3814 注释场景。）冲突抑制只在「两侧都有真时刻且不等」
        时触发——此场景正文侧解析为空，不触发。
        """
        html = ('<html><head><title>学术讲座通知</title>'
                '<meta name="description" content="%s">'
                '</head><body><div class="wp_articlecontent">'
                '<p>报告人：蒋浩波教授，美国俄克拉荷马州立大学</p>'
                '<p>主持人：余小强</p>'
                '<p>地点：昆虫所110会议室</p>'
                '<p>本讲座由生命科学学院主办，欢迎广大师生参加。讲座将围绕蛋白酶'
                '系统展开深入探讨，结合模式昆虫的最新研究进展，解析免疫调控的'
                '分子机制与信号通路。</p>'
                '<p>本讲座由生命科学学院主办，欢迎广大师生参加。讲座将围绕蛋白酶'
                '系统展开深入探讨，结合模式昆虫的最新研究进展，解析免疫调控的'
                '分子机制与信号通路。</p>'
                '</div></body></html>'
                ) % _META_DATE_PLUS_TIME
        r = parse(html)
        self.assertEqual(str(r.get('lectureStart')), '2026-10-16 10:00:00',
                         '正文无时间标签时，meta 的日期+时刻应经追加补入'
                         '（追加功能被误伤）')

    def test_11_两侧时间相等_行为不变(self):
        r = parse(_page(_CLEAN_TIME_LINE, _META_FRESH_DIRTY_HEAD))
        self.assertEqual(str(r['lectureStart']), '2026-10-16 10:00:00')


class HiddenStyleStripTest(unittest.TestCase):
    """B：不可见样式节点剔除，标签字面恢复。"""

    def _prep(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        st = types.SimpleNamespace(text='', body_text='', body_text_llm='',
                                   body_text_folded='', ocr_text='')
        P._prep_doc_text(soup, '', st)
        return st

    def test_20_白色一字被剔除_时间地点字面恢复(self):
        st = self._prep(_page(_WHITE_TIME_LINE, _META_STALE,
                              loc_line=_WHITE_LOC_LINE))
        flat = st.body_text.replace(' ', '')
        self.assertNotIn('时一间', flat, '白色「一」未被剔除（时一间 残留）')
        self.assertNotIn('地一点', flat, '白色「一」未被剔除（地一点 残留）')
        # 注：N1 规范化会把全角「：」折为半角「:」，此处按规范化后的字面断言
        self.assertIn('时间:2026年10月16日', flat, '「时间:」字面未恢复')
        self.assertIn('地点:昆虫所110会议室', flat, '「地点:」字面未恢复')

    def test_21_display_none节点同样剔除(self):
        html = ('<html><body><div class="wp_articlecontent">'
                '<p>时间：2026年10月16日上午10:00</p>'
                '<p style="display: none;">内部备注请勿显示2:30</p>'
                + _FILLER + '</div></body></html>')
        st = self._prep(html)
        self.assertNotIn('2:30', st.body_text, 'display:none 节点文本泄漏')

    def test_22_普通样式节点不受影响(self):
        st = self._prep(_page(_CLEAN_TIME_LINE, _META_STALE))
        self.assertIn('时间:2026年10月16日', st.body_text.replace(' ', ''))
        self.assertIn('蒋浩波', st.body_text)


class MetaTimeConflictJudgeTest(unittest.TestCase):
    """`_meta_time_conflicts_with_body` 裁决口径直测。"""

    def test_30_真时刻冲突为真(self):
        self.assertTrue(P._meta_time_conflicts_with_body(
            '时间：2026年10月16日上午10:00',
            '时间：2026年10月16日上午2:30'))

    def test_31_相等为假(self):
        self.assertFalse(P._meta_time_conflicts_with_body(
            '时间：2026年10月16日上午10:00',
            '时间：2026年10月16日上午10:00'))

    def test_32_一侧无时刻为假(self):
        """正文只有日期、meta 有时刻 → 非冲突（追加即功能）。"""
        self.assertFalse(P._meta_time_conflicts_with_body(
            '时间：2026年10月16日',
            '时间：2026年10月16日上午10:00'))

    def test_33_一侧无日期为假(self):
        self.assertFalse(P._meta_time_conflicts_with_body(
            '', '时间：2026年10月16日上午10:00'))
        self.assertFalse(P._meta_time_conflicts_with_body(
            '欢迎参加', '欢迎参加'))

    def test_34_日期不同时刻均真_为真(self):
        self.assertTrue(P._meta_time_conflicts_with_body(
            '时间：2026年10月16日上午10:00',
            '时间：2026年10月15日上午10:00'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
