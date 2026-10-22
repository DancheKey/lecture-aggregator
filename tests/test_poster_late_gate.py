# -*- coding: utf-8 -*-
"""海报页「事后发布」判据 + 出口终检（2026-10-22）的回归测试。

背景
----
`is_news_record(rec, poster_page=True)` 原先**无条件** return False（2026-07-31
为救文学院「说文论语」系列真海报被误杀）。但「海报页」的判定本身很宽（页面有图
且正文无「时间：」这类结构化标签即算），于是「讲座结束后多日才发布的公告/回顾
页」一并豁免，全靠 is_news_article 的关键词兜底。

实测（2026-10-22，哲社/阿伯丁/政公 三个零更新源全历史回溯）：能过 parse_detail
的 10 个候选 URL 里 9 个是事后发布（+7 天 ~ +741 天），关键词表全部漏网——标题
写成「汕教之旅」「青衿之志——记…」「砺儒茶座」时不含「举办/圆满/通讯」任一词。

修复分三层（本测试逐层覆盖）：
  ① 海报页改**有条件**豁免：发布晚于讲座超过 POSTER_LATE_TOLERANCE_DAYS 天即判
     事后公告；发布早于讲座超过 PUB_BEFORE_LECTURE_MAX_DAYS 天判时间不可信。
  ② is_news_article 新增 title-verbatim 判据（标题自带「通讯稿/新闻稿/活动报道」）。
  ③ parse_detail 出口新增 `_post_publish_final_check`：补判入口覆盖不到的路径
     ——多主讲人连写拆分（_split_by_speakers）/ VLM 多场拆分 / 判定时
     lectureStart 尚为空。

第 ③ 层是本次关键：哲社 2171/2180/2176 在入口判定时 lectureStart=None
（`is_news_record` 遇空时间直接放过），随后才拆出 1~2 场，最终 lectureStart
比 publishTime 早 625/741 天，却已无人再判，全部入库。

口径纪律
--------
出口终检传 `same_day_grace=True`：发布日与讲座日同一天一律放行。因为终检负责的
是**跨日**的事后发布，同日发布多为当天通知/海报的 CMS 录入延迟（swc 32「扬帆
教学论坛」讲座 09:30、页面 09:44 发布，差 14 分钟）。入口不传该参数，原有严格
口径零变化——`SameDayGraceTest` 就是守住这条边界。

运行：python tests/test_poster_late_gate.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import parsers as P  # noqa: E402


def _rec(lec, pub, poster=False, **kw):
    """构造一条最小记录：只带判定需要的字段。"""
    d = {'lectureStart': lec, 'publishTime': pub}
    if poster:
        d['poster_only'] = True
    d.update(kw)
    return d


class PosterLateToleranceTest(unittest.TestCase):
    """① 海报页「讲座结束后才发布」的容差边界（POSTER_LATE_TOLERANCE_DAYS）。"""

    def test_01_讲座前发布_不判(self):
        r = _rec('2025-06-12 14:00', '2025-06-05 09:00', poster=True)
        self.assertFalse(P.is_news_record(r, poster_page=True),
                         '提前一周发的海报是标准预告，不能判事后')

    def test_02_当天发布_不判(self):
        r = _rec('2025-06-12 14:00', '2025-06-12 09:00', poster=True)
        self.assertFalse(P.is_news_record(r, poster_page=True))

    def test_03_次日发布_不判(self):
        """管理员事后补传属合理延迟，+1 天仍在容差内。"""
        r = _rec('2025-06-12 14:00', '2025-06-13 09:00', poster=True)
        self.assertFalse(P.is_news_record(r, poster_page=True))

    def test_04_恰好两天_不判_边界(self):
        """delta == 2.0 天，判据是 > 容差，故不拦。"""
        r = _rec('2025-06-12 14:00', '2025-06-14 14:00', poster=True)
        self.assertFalse(P.is_news_record(r, poster_page=True))

    def test_05_超过两天_判事后(self):
        r = _rec('2025-06-12 14:00', '2025-06-14 20:00', poster=True)
        self.assertTrue(P.is_news_record(r, poster_page=True))

    def test_06_容差常量值(self):
        self.assertEqual(P.POSTER_LATE_TOLERANCE_DAYS, 2,
                         '容差改动会放宽/收紧全库海报页判定，须同步更新本测试与注释依据')

    def test_07_无发布时间_不判(self):
        """无 publishTime 时无法判定，宁可放过（由关键词规则兜底）。"""
        r = _rec('2025-06-12 14:00', '', poster=True)
        self.assertFalse(P.is_news_record(r, poster_page=True))

    def test_08_无讲座时间_不判(self):
        r = _rec('', '2025-06-12 14:00', poster=True)
        self.assertFalse(P.is_news_record(r, poster_page=True))


class PubBeforeLectureTest(unittest.TestCase):
    """① 另一半：发布早于讲座过久 → 发布年份解析错误，时间不可信。"""

    def test_11_提前两个月_不判(self):
        r = _rec('2025-06-12 14:00', '2025-04-12 09:00', poster=True)
        self.assertFalse(P.is_news_record(r, poster_page=True))

    def test_12_提前五年_判不可信(self):
        """哲社 346：讲座 2018-03-15、发布 2013-05-03（URL 年份被当发布年）。"""
        r = _rec('2018-03-15 14:00', '2013-05-03 09:00', poster=True)
        self.assertTrue(P.is_news_record(r, poster_page=True))

    def test_13_提前一年_判不可信(self):
        """哲社 339：讲座 2015-10-21、发布 2014-10-22。"""
        r = _rec('2015-10-21 14:00', '2014-10-22 09:00', poster=True)
        self.assertTrue(P.is_news_record(r, poster_page=True))

    def test_14_提前量常量值(self):
        self.assertEqual(P.PUB_BEFORE_LECTURE_MAX_DAYS, 180,
                         '库内 2929 条「发布早于讲座」记录无一超过 90 天，180 是留一倍余量')


class SameDayGraceTest(unittest.TestCase):
    """出口终检的 same_day_grace：同日放行、跨日照拦。"""

    def test_21_同日晚上发布_入口拦(self):
        """入口（不传 grace）保持严格口径：讲座 15:00、当晚 23:34 发布 → 判事后。"""
        r = _rec('2024-10-30 15:00', '2024-10-30 23:34')
        self.assertTrue(P.is_news_record(r, poster_page=False))

    def test_22_同日晚上发布_终检放行(self):
        """终检（grace=True）放行：这是当天海报的补传，不是回顾稿。"""
        r = _rec('2024-10-30 15:00', '2024-10-30 23:34')
        self.assertFalse(P.is_news_record(r, poster_page=False, same_day_grace=True))

    def test_23_同日差十四分钟_终检放行(self):
        """swc 32「扬帆教学论坛」：讲座 09:30、页面 09:44 发布（CMS 录入延迟）。"""
        r = _rec('2021-06-21 09:30', '2021-06-21 09:44')
        self.assertFalse(P.is_news_record(r, poster_page=False, same_day_grace=True))

    def test_24_隔天_终检照拦(self):
        """grace 只免同日，隔天仍按严格口径判——回顾稿的典型形态。"""
        r = _rec('2021-06-21 09:30', '2021-06-22 09:44')
        self.assertTrue(P.is_news_record(r, poster_page=False, same_day_grace=True))

    def test_25_grace默认关闭(self):
        """默认参数必须保持入口口径，否则全库抓取行为被静默放宽。"""
        r = _rec('2021-06-21 09:30', '2021-06-21 09:44')
        self.assertTrue(P.is_news_record(r, poster_page=False),
                        'same_day_grace 默认 False，入口行为不得变化')


class FinalCheckTest(unittest.TestCase):
    """③ 出口终检 `_post_publish_final_check` 的三种返回形态与豁免。"""

    def test_31_None原样返回(self):
        self.assertIsNone(P._post_publish_final_check(None, url='u'))

    def test_32_单条被拦返回None(self):
        r = _rec('2021-03-15 09:30', '2022-11-30 16:20')
        self.assertIsNone(P._post_publish_final_check(r, url='u'))

    def test_33_单条通过原样返回(self):
        r = _rec('2021-03-15 09:30', '2021-03-01 09:00')
        self.assertIs(P._post_publish_final_check(r, url='u'), r)

    def test_34_列表部分剔除(self):
        ok = _rec('2021-03-15 09:30', '2021-03-01 09:00')
        bad = _rec('2020-12-01 14:30', '2022-12-12 09:31')
        got = P._post_publish_final_check([ok, bad], url='u')
        self.assertEqual(got, [ok], '只剔除事后发布那条，其余保持原对象')

    def test_35_列表全剔除返回None(self):
        bad1 = _rec('2020-12-01 14:30', '2022-12-12 09:31')
        bad2 = _rec('2020-12-01 08:30', '2022-12-12 09:10')
        self.assertIsNone(P._post_publish_final_check([bad1, bad2], url='u'))

    def test_36_无剔除时返回原列表对象(self):
        a = _rec('2021-03-15 09:30', '2021-03-01 09:00')
        b = _rec('2021-04-15 09:30', '2021-04-01 09:00')
        src = [a, b]
        self.assertIs(P._post_publish_final_check(src, url='u'), src,
                      '无剔除时必须原样返回，避免调用方拿到新对象而丢失引用')

    def test_37_skip_news_filter不判(self):
        """经管学院等源显式跳过新闻过滤（发布时间被 CMS 写成讲座时间）。"""
        r = _rec('2026-04-17 15:00', '2026-04-17 16:16')
        self.assertIs(P._post_publish_final_check(r, url='u', skip_news_filter=True), r)

    def test_38_poster_only按整页取任一为真(self):
        """派生记录可能丢 poster_only 键，须按整页判定，否则真海报被严格口径误杀。"""
        a = _rec('2025-06-12 14:00', '2025-06-13 09:00', poster=True)
        b = _rec('2025-06-12 14:00', '2025-06-13 09:00')   # 丢了 poster_only
        got = P._post_publish_final_check([a, b], url='u')
        self.assertEqual(len(got), 2, '+1 天在容差内，两条都应保留')

    def test_39_非字典元素被忽略(self):
        """非 dict 元素不参与判定；无剔除时原样返回原列表（含这些元素）。"""
        ok = _rec('2021-03-15 09:30', '2021-03-01 09:00')
        src = [ok, 'junk', None]
        self.assertIs(P._post_publish_final_check(src, url='u'), src,
                      '无剔除时原样返回，非 dict 元素不参与判定也不被清理')


class RealCaseRegressionTest(unittest.TestCase):
    """真实案例离线复现：2026-10-22 回溯发现的 9 条事后发布，两种口径下都须拦住。"""

    CASES = [
        # (名称, 讲座时间, 发布时间, 发布是否晚于讲座)
        # 前 8 条 pub > lec：普通页口径的「严格时刻比较」就能拦。
        # 后 2 条 pub < lec（发布年份解析错）：只能靠海报页口径的
        # PUB_BEFORE_LECTURE_MAX_DAYS 负差规则拦，普通页口径不拦——这是设计使然，
        # 因为普通页的「发布早于讲座」是正常预告形态，无法区分解析错误。
        ('政公 6468 特朗普冲击波·南粤讲坛129期', '2025-06-12 14:00', '2025-06-29 09:00', True),
        ('阿伯丁 418 学者讲坛第14讲', '2024-07-08 14:00', '2024-07-16 09:00', True),
        ('阿伯丁 419 学者讲坛第15讲', '2024-07-09 14:00', '2024-07-16 09:00', True),
        ('哲社 2276 勷勤论坛陈乔见专场', '2023-11-09 14:00', '2023-11-16 09:00', True),
        ('哲社 2249 师长引航讲座（二）', '2023-09-14 14:00', '2023-10-11 09:00', True),
        ('哲社 2171 砺儒茶座（张伟/陈明）', '2021-03-15 09:30', '2022-11-30 16:20', True),
        ('哲社 2180 青衿之志', '2020-12-01 14:30', '2022-12-12 09:31', True),
        ('哲社 2176 汕教之旅', '2020-12-01 08:30', '2022-12-12 09:10', True),
        ('哲社 346 程恩富讲座通讯稿', '2018-03-15 14:00', '2013-05-03 09:00', False),
        ('哲社 339 吴育林做客理论粤军讲坛', '2015-10-21 14:00', '2014-10-22 09:00', False),
    ]

    def test_41_全部被拦_海报页口径(self):
        for name, lec, pub, _ in self.CASES:
            with self.subTest(name=name):
                r = _rec(lec, pub, poster=True)
                self.assertTrue(P.is_news_record(r, poster_page=True),
                                '%s 应判事后发布' % name)

    def test_42_发布晚于讲座者_普通页口径也拦(self):
        for name, lec, pub, pub_after in self.CASES:
            if not pub_after:
                continue
            with self.subTest(name=name):
                r = _rec(lec, pub, poster=False)
                self.assertTrue(P.is_news_record(r, poster_page=False),
                                '%s 应判事后发布' % name)

    def test_43_出口终检能拦住晚填时间的记录(self):
        """哲社 2171/2180/2176：入口判定时 lectureStart 为空 → 放过；
        场次拆出后时间才定型，靠出口终检兜住。"""
        for name, lec, pub, _ in self.CASES:
            if not name.startswith('哲社 217'):
                continue
            with self.subTest(name=name):
                r = _rec(lec, pub)
                self.assertIsNone(P._post_publish_final_check(r, url='u'),
                                  '%s 应被出口终检拦下' % name)


class TitleVerbatimTest(unittest.TestCase):
    """② 标题自带体裁标记（通讯稿/新闻稿/活动报道）→ 直接判新闻。"""

    def test_51_通讯稿(self):
        self.assertEqual(P.is_news_article('程恩富教授讲座通讯稿', '正文', None),
                         'title-verbatim')

    def test_52_活动报道(self):
        self.assertEqual(P.is_news_article('名师讲座活动报道', '正文', None),
                         'title-verbatim')

    def test_53_正常预告不误伤(self):
        """真预告标题不会出现这些词。"""
        self.assertIsNone(P.is_news_article('第129期南粤讲坛预告', '时间：6月12日', None))


class WiringStaticTest(unittest.TestCase):
    """静态接线：终检必须真的接在 parse_detail 出口，且带同日容差。"""

    def setUp(self):
        with open(os.path.join(ROOT, 'scraper', 'parsers.py'), encoding='utf-8') as f:
            self.src = f.read()

    def test_61_parse_detail调用终检(self):
        key = 'return _post_publish_final_check(out, url=url, skip_news_filter=skip_news_filter)'
        self.assertIn(key, self.src,
                      'parse_detail 出口必须调用终检，否则拆分路径的漏判无人兜底')

    def test_62_终检带同日容差(self):
        key = 'is_news_record(r, poster_page=_poster_page, same_day_grace=True)'
        self.assertIn(key, self.src, '终检必须传 same_day_grace，否则同日记录被误杀')

    def test_63_入口不传同日容差(self):
        """入口两处调用不得带 same_day_grace——保持既有严格口径零变化。"""
        for key in ('is_news_record(result, poster_page=poster_only)',
                    'is_news_record(r, poster_page=poster_only)'):
            self.assertIn(key, self.src, '入口调用形态变化会静默放宽抓取口径')
        self.assertNotIn('is_news_record(result, poster_page=poster_only, same_day_grace',
                         self.src)

    def test_64_终检在mark_time_unknown之后(self):
        """timeUnknown 必须先定型：占位时刻判定依赖它，顺序反了会改变口径。"""
        i_mark = self.src.index('_mark_time_unknown(_r)')
        i_final = self.src.index('return _post_publish_final_check(out, url=url')
        self.assertLess(i_mark, i_final,
                        '_post_publish_final_check 必须在 _mark_time_unknown 之后调用')


if __name__ == '__main__':
    unittest.main(verbosity=2)
