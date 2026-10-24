# -*- coding: utf-8 -*-
"""字段列表型多场拆分修复 + 学位单位提取 + 块内简介防误伤（2026-10-24）。

cs/5400 实测三连修（+ ctld4391/4299 回归锁）：
① 4 场只出 3 场且 start 集体错位——字段列表型页面每场字段顺序为
   「报告时间:X → 报告题目:A → 摘要 → 报告人」，候选1 按题目标签分块后块内
   首个时钟是**下一场**的（末块无时钟整场被丢）。修法：时间解析文本前移到
   本场「报告时间:」标签起点（_dt_text），窄口径回填判据同步改查 _dt_text。
② 陈莉单位串场暨南大学——cand_core 自首个职称 token（博士）起删到尾，把
   「工学博士，西北大学…」的单位连带删掉（rest='中共党员,工学'）。修法：
   sp_m 路径 rest 从**原始 cand** 计算，_extract_affiliation 自行清理。
③ 「现为CCF杰出会员」（会员身份而非单位）把 _scope 推到单位之后 → 回退全文。
   兼任守卫（_status + 特约/客座…）仅在原路径生效，回退路径跳过防下标错位。
④ ctld4391/4299 回归锁：块内「报告人：/主讲人：」连写兜底曾把「时间/地点」
   等元数据段落覆盖进 bio（遭 gate 置空）。修法双重：简介类标签已命中
   （_bio_hit）不再兜底 + lookahead 补「时间：/地点：/简介：」等字段标签。

离线单测：不发网络请求、不走 LLM（SCNU_LLM_*=0）。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scraper'))
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers  # noqa: E402

# cs/5400 形态：页眉总览时段 + 4 场字段列表（本场「报告时间」排在「报告题目」
# 之前——正是候选1 时钟错位的触发形态）。bio 逐场各异，末场同样带完整时段。
_HTML_5400 = '''<html><head><title>学术报告会</title></head><body>
<div class="article-content">
<p>地点：华南师范大学计算机学院学术报告厅 时间：3月18日上午9:00-12:00</p>
<p>报告时间:9:20-10:00</p>
<p>报告题目:人工智能视野下的科技伦理教育</p>
<p>报告摘要: 人工智能技术的颠覆性、复杂性以及社会关联性,对科技伦理教育提出新要求。</p>
<p>报告人:古天龙,暨南大学教授,博士生导师,可信人工智能教育部工程研究中心主任。兼任教育部高等学校计算机类专业教学指导委员会副主任委员。主要研究方向为可信人工智能与知识工程。</p>
<p>报告时间: 10:00-10:40</p>
<p>报告题目:资源分配问题的模型与算法</p>
<p>报告摘要: 在经济学中,资源分配是指将可用资源通过各种手段进行优化配置。</p>
<p>报告人:曹永知,北京大学计算机学院副教授。研究方向为算法博弈论与机制设计,主持多项国家自然科学基金项目,发表论文三十余篇。</p>
<p>报告时间: 10:40-11:20</p>
<p>报告题目:从教学学术视角谈一流课程建设与实践</p>
<p>报告摘要: 一流本科教育、一流人才培养是高等学校的内涵建设。</p>
<p>报告人:陈莉,中共党员,工学博士,西北大学二级教授、博士生导师,陕西省教学名师。现为CCF杰出会员,CAAI高级会员。主要研究方向为智能信息处理与数据挖掘。</p>
<p>报告时间: 11:20-12:00</p>
<p>报告题目:智能交通中的移动协同计算</p>
<p>报告摘要: 智能交通系统是智慧城市建设的重要组成部分,移动协同计算是关键支撑。</p>
<p>报告人:武继刚,广东工业大学教授,博士生导师。研究方向为移动计算与边缘智能,在国内外高水平期刊发表多篇学术论文。</p>
</div></body></html>'''


def _parse5400():
    out = parsers.parse_detail(_HTML_5400, 'http://cs.scnu.edu.cn/a/20230315/5400.html',
                               '计算机学院', '石牌', default_year=2023,
                               list_title='学术报告会', skip_news_filter=True)
    return out if isinstance(out, list) else ([out] if out else [])


class FieldListOrderSplitTest(unittest.TestCase):
    """本场「报告时间」在题目前时：4 场齐全、start 不错位。"""

    def test_four_sessions_with_correct_starts(self):
        recs = _parse5400()
        self.assertEqual(len(recs), 4)
        starts = [str(r['lectureStart']) for r in recs]
        for st, want in zip(starts, ('2023-03-18 09:20:00', '2023-03-18 10:00:00',
                                     '2023-03-18 10:40:00', '2023-03-18 11:20:00')):
            self.assertTrue(st.startswith(want), '%r 应以 %r 开头' % (st, want))
        topics = [r.get('topic') for r in recs]
        self.assertEqual(topics[0], '人工智能视野下的科技伦理教育')
        self.assertEqual(topics[3], '智能交通中的移动协同计算')

    def test_bio_split_per_session(self):
        """每场 bio 各异（不得共享第一场古天龙简介）。"""
        recs = _parse5400()
        bios = [(r.get('speakerBio') or '') for r in recs]
        for b in bios:
            self.assertTrue(b, 'speakerBio 不应为空')
        self.assertEqual(len(set(bios)), 4, '四条 bio 应互不相同')
        self.assertIn('暨南大学', bios[0])
        self.assertIn('北京大学', bios[1])
        self.assertIn('西北大学', bios[2])
        self.assertIn('广东工业大学', bios[3])

    def test_affiliation_per_session(self):
        """逐场单位：陈莉（学位+单位连写）不得串成古天龙的暨南大学。"""
        recs = _parse5400()
        affs = [r.get('speakerAffiliation') or '' for r in recs]
        self.assertEqual(affs[0], '暨南大学')
        self.assertEqual(affs[2], '西北大学')
        self.assertEqual(affs[3], '广东工业大学')


class ExtractAffiliationDegreeTest(unittest.TestCase):
    """_extract_affiliation：学位词之后的单位不得被连带删除。"""

    def test_degree_then_affiliation(self):
        aff = parsers._extract_affiliation(
            '中共党员,工学博士,西北大学二级教授、博士生导师,陕西省教学名师')
        self.assertEqual(aff, '西北大学')

    def test_party_prefix_not_leaked(self):
        aff = parsers._extract_affiliation('中共党员,工学博士,西北大学二级教授')
        self.assertNotIn('党员', aff)


class LaizhuangFallbackTest(unittest.TestCase):
    """「现为/现任」标记后无单位（会员身份）时回退全文取单位。"""

    def test_status_without_unit_falls_back(self):
        aff = parsers._extract_affiliation(
            '中共党员,工学博士,西北大学二级教授、博士生导师。现为CCF杰出会员,CAAI高级会员')
        self.assertEqual(aff, '西北大学')

    def test_status_with_unit_still_wins(self):
        """「现为X大学教授」仍以标记后单位为准，不回退取更早的学位单位。"""
        aff = parsers._extract_affiliation('湖南师范大学教育学博士,现为北京大学教育学院教授')
        self.assertEqual(aff, '北京大学教育学院')


# CTLD 形态（ctld4391/4299 回归锁）：表格展平后「主讲人：X教授」与「时间/地点」
# 元数据、「主讲人简介：」连写（元数据在简介**之前**——truncate_rich_text 会把
# 元数据及之后整段截掉，若连写兜底误抓则该场 bio 被清空、简介标签的正确值丢失）。
_HTML_CTLD = '''<html><head><title>教学研修活动</title></head><body>
<div class="article-content">
<p>地点：石牌校园教师发展中心三楼303室 时间：11月15日周五下午14:00-17:00</p>
<p>讲座一 题目：教育人工智能研究前沿</p>
<p>主讲人：穆肃教授 时间:11月15日周五下午14:00-17:00 地点:石牌校园教师发展中心(校史文博馆楼)三楼303室 主讲人简介：华南师范大学教授、博士生导师,现任华南师范大学教育人工智能研究院副院长。从事在线深层次学习与知识建构研究。出版多部学术专著。</p>
<p>讲座二 题目：计算机课程教学创新</p>
<p>主讲人：黄卫祖教授 时间:11月20日周五下午14:00-17:00 地点:石牌校园教师发展中心(校史文博馆楼)三楼303室 主讲人简介：教授,广州应用科技学院计算机学院院长,曾任职东北大学、暨南大学。教育部大学计算机课程教学指导委员会委员。全国大学生区块链应用开发大赛组委会委员。</p>
</div></body></html>'''


def _parse_ctld():
    out = parsers.parse_detail(_HTML_CTLD, 'http://ctld.scnu.edu.cn/a/20241111/4391.html',
                               'CTLD教师发展中心', '石牌', default_year=2024,
                               list_title='教学研修活动', skip_news_filter=True)
    return out if isinstance(out, list) else ([out] if out else [])


class BlockBioGuardTest(unittest.TestCase):
    """块内连写兜底不得覆盖简介类标签提取值，也不得跨字段抓元数据。"""

    def test_label_bio_not_overridden(self):
        """简介类标签命中后，「主讲人：X教授 时间:… 地点:… 主讲人简介：…」连写段
        不得覆盖它（旧 lookahead 无字段标签会整段抓取 → truncate 截到元数据前
        → bio 被清空，简介标签的正确值丢失）。"""
        recs = _parse_ctld()
        self.assertGreaterEqual(len(recs), 2)
        b0 = recs[0].get('speakerBio') or ''
        self.assertIn('华南师范大学', b0)
        self.assertNotIn('14:00', b0)
        self.assertNotIn('303室', b0)

    def test_second_session_bio_not_crossed(self):
        recs = _parse_ctld()
        if len(recs) < 2:
            self.skipTest('该形态未拆出多场')
        b1 = recs[1].get('speakerBio') or ''
        self.assertIn('广州应用科技学院', b1)
        self.assertNotIn('华南师范大学', b1)
