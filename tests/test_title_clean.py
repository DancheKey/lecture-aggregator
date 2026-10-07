# -*- coding: utf-8 -*-
"""标题日期前缀剥离回归（2026-10-07，psy283）。

## 病灶

源页 `<title>`「7月2日-7月4日心理与行为研究的前沿统计方法工作坊 - 通知公告 - …」
清洗后只剩「月4日心理与行为研究的前沿统计方法工作坊」。链条：

  ① `_clean_title` 的单日剥离只吃掉「7月2日」，残留「-7月4日…」；
  ② `_strip_nav_noise` 先 strip 前导横杠、再走「去前导孤立数字」（本为 OCR 页码
     「00」设计），把「7月4日」的「7」当孤立页码吃掉 → 标题剩「月4日…」。

修复：单日剥离正则扩为日期区间形态「N月N日[-至]N月N日 / N月N日-N日」。
运行：python tests/test_title_clean.py
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scraper'))
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers as P  # noqa: E402


class TitleDateRangeStripTest(unittest.TestCase):

    def test_01_日期区间整段剥掉(self):
        self.assertEqual(
            P._clean_title('7月2日-7月4日心理与行为研究的前沿统计方法工作坊'),
            '心理与行为研究的前沿统计方法工作坊')

    def test_02_短区间形态(self):
        self.assertEqual(P._clean_title('7月2日-4日工作坊'), '工作坊')

    def test_03_既有单日形态不回归(self):
        self.assertEqual(P._clean_title('10月29日学术报告'), '学术报告')
        self.assertEqual(P._clean_title('6月6日讲座'), '讲座')

    def test_04_无日期标题不受影响(self):
        t = '心理与行为研究的前沿统计方法工作坊'
        self.assertEqual(P._clean_title(t), t)

    def test_05_带年份前缀仍剥(self):
        self.assertEqual(P._clean_title('2023年12月24日红树林讲座'), '红树林讲座')


if __name__ == '__main__':
    unittest.main(verbosity=2)
