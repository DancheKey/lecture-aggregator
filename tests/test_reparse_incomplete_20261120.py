#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""残记录重解析机制的离线回归（2026-11-20，行知书院 367~370 实测）。

病根：源站先发无海报/无主讲人的页面，爬虫按残缺信息入库；数日后同一 URL
补上海报与完整字段，但增量抓取对已入库 URL 由 existing_urls 跳过，残值永久固化。
修法在 scraper.py 增量收尾独立重解析残缺记录，只补缺失字段，并用独立台账
data/reparse_ledger.json 做 7 天节流、每轮最多 10 条。

本套件全程 patch fetch/parse_detail/台账路径，零网络、零密钥。
运行：python tests/test_reparse_incomplete_20261120.py
"""
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 与 golden 同纪律：import parsers 前锁纯规则基线，避免本机 .env 触发真实大模型
os.environ.setdefault('SCNU_LLM_TEXT', '0')
os.environ.setdefault('SCNU_LLM_RICH', '0')
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
import scraper as S  # noqa: E402

URL = 'http://xz.scnu.edu.cn/a/20261015/369.html'
CKEY = S._canon_url_key(URL)


class ReparseIncompleteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = os.path.join(self.tmp.name, 'reparse_ledger.json')
        p = patch.object(S, '_REPARSE_FILE', self.ledger_path)
        p.start()
        self.addCleanup(p.stop)

    # ── helper ────────────────────────────────────────────────────────────
    def rec(self, url=URL, **kw):
        r = {
            'sourceUrl': url,
            'title': '测试讲座',
            'listTitle': '测试讲座',
            'college': '行知书院',
            'campus': '汕尾',
            'publishTime': '2026-10-15 15:00:00',
            'lectureStart': '2026-10-20 00:00:00',
            'timeUnknown': True,
            'speaker': '',
            'speakerTitle': '',
            'speakerAffiliation': '',
            'speakerBio': '',
            'location': '',
            'lectureEnd': '',
            'abstract': '',
            'hasPosterImage': True,
            'images': [],
            'imageParseMethod': '',
            'vlmExtracted': None,
        }
        r.update(kw)
        return r

    def write_ledger(self, entries):
        with open(self.ledger_path, 'w', encoding='utf-8') as f:
            json.dump({'entries': entries}, f, ensure_ascii=False)

    def read_ledger(self):
        with open(self.ledger_path, encoding='utf-8') as f:
            return json.load(f)['entries']

    def new_fields(self, **kw):
        d = {
            'speaker': '张三',
            'speakerTitle': '教授',
            'speakerAffiliation': '华南师范大学',
            'speakerBio': '张三简介',
            'location': '汕尾校区南区教学楼102',
            'lectureEnd': '2026-10-20 17:00:00',
            'abstract': '讲座内容',
            'lectureStart': '2026-10-20 15:00:00',
            'timeUnknown': False,
            'images': ['http://xz.scnu.edu.cn/poster.jpg'],
            'imageParseMethod': 'vlm',
            'vlmExtracted': True,
        }
        d.update(kw)
        return d

    # ── 残缺判据 ──────────────────────────────────────────────────────────
    def test_01_残缺判据(self):
        self.assertTrue(S._is_incomplete_record(self.rec()))
        # 时间待定但已有主讲人，也应重解析时间
        self.assertTrue(S._is_incomplete_record(
            self.rec(speaker='张三', timeUnknown=True)))
        # 无海报的纯文本通知页：重解析补不出东西，不参与
        self.assertFalse(S._is_incomplete_record(
            self.rec(hasPosterImage=False, images=[])))
        # 已完整：不参与
        self.assertFalse(S._is_incomplete_record(
            self.rec(speaker='张三', timeUnknown=False)))
        self.assertFalse(S._is_incomplete_record(None))
        self.assertFalse(S._is_incomplete_record('not a dict'))

    # ── 字段补全 ──────────────────────────────────────────────────────────
    def test_02_补全缺失字段并纠正待定时间(self):
        r = self.rec(lectureEnd='2026-10-20 09:00:00',
                     timeUnknownSource='human')
        with patch.object(S, 'fetch', return_value='<html></html>') as f, \
             patch.object(S, 'parse_detail', return_value=self.new_fields()) as p:
            tried, filled = S.reparse_incomplete_records([r])
        self.assertEqual((tried, filled), (1, 1))
        f.assert_called_once_with(URL, allowed_domains=['scnu.edu.cn'])
        p.assert_called_once_with('<html></html>', URL, college='行知书院',
                                  campus='汕尾', list_title='测试讲座')
        self.assertEqual(r['speaker'], '张三')
        self.assertEqual(r['speakerTitle'], '教授')
        self.assertEqual(r['speakerAffiliation'], '华南师范大学')
        self.assertEqual(r['speakerBio'], '张三简介')
        self.assertEqual(r['location'], '汕尾校区南区教学楼102')
        self.assertEqual(r['lectureEnd'], '2026-10-20 17:00:00')
        self.assertEqual(r['abstract'], '讲座内容')
        self.assertEqual(r['lectureStart'], '2026-10-20 15:00:00')
        self.assertFalse(r['timeUnknown'])
        self.assertEqual(r['timeUnknownSource'], 'auto')
        self.assertEqual(r['images'], ['http://xz.scnu.edu.cn/poster.jpg'])
        self.assertEqual(r['imageParseMethod'], 'vlm')
        self.assertTrue(r['vlmExtracted'])
        self.assertEqual(self.read_ledger(),
                         {CKEY: datetime.date.today().isoformat()})

    def test_02b_精确时间缺失结束时清除旧结束时间(self):
        r = self.rec(lectureEnd='2026-10-20 09:00:00',
                     timeUnknownSource='legacy')
        new = self.new_fields(lectureEnd='')
        with patch.object(S, 'fetch', return_value='<html></html>'), \
             patch.object(S, 'parse_detail', return_value=new):
            tried, filled = S.reparse_incomplete_records([r])
        self.assertEqual((tried, filled), (1, 1))
        self.assertEqual(r['lectureStart'], '2026-10-20 15:00:00')
        self.assertIsNone(r['lectureEnd'])
        self.assertIs(r['timeUnknown'], False)
        self.assertEqual(r['timeUnknownSource'], 'auto')

    def test_03_只补缺失字段不覆盖已有精修值(self):
        r = self.rec(speaker='人工李四', speakerTitle='人工职称',
                     location='人工地点',
                     speakerBio='人工简介', speakerAffiliation='人工单位',
                     abstract='人工摘要', lectureEnd='2026-10-20 18:00:00',
                     images=['人工图'], imageParseMethod='人工OCR',
                     vlmExtracted=True)
        new = self.new_fields(speaker='VLM王五', speakerTitle='VLM职称',
                              location='VLM地点',
                              speakerBio='VLM简介', speakerAffiliation='VLM单位',
                              abstract='VLM摘要',
                              lectureEnd='2026-10-20 19:00:00',
                              lectureStart='', timeUnknown=True,
                              images=['VLM图'], imageParseMethod='vlm',
                              vlmExtracted=False)
        with patch.object(S, 'fetch', return_value='<html></html>'), \
             patch.object(S, 'parse_detail', return_value=new):
            tried, filled = S.reparse_incomplete_records([r])
        self.assertEqual((tried, filled), (1, 0))
        self.assertEqual(r['speaker'], '人工李四')
        self.assertEqual(r['speakerTitle'], '人工职称')
        self.assertEqual(r['location'], '人工地点')
        self.assertEqual(r['speakerBio'], '人工简介')
        self.assertEqual(r['speakerAffiliation'], '人工单位')
        self.assertEqual(r['abstract'], '人工摘要')
        self.assertEqual(r['lectureEnd'], '2026-10-20 18:00:00')
        self.assertTrue(r['timeUnknown'])
        self.assertEqual(r['images'], ['人工图'])
        self.assertEqual(r['imageParseMethod'], '人工OCR')
        self.assertTrue(r['vlmExtracted'])
        # title 不在补全白名单内，任何情况都不动
        self.assertEqual(r['title'], '测试讲座')

    def test_03b_旧vlmExtractedFalse翻正(self):
        # False 表示「当时没抽到」，不是人工精修值；新管线抽到后必须翻正，
        # 否则 speakerBio 已补、索引仍说没用 VLM，下次重处理集合会漏掉它。
        r = self.rec(vlmExtracted=False)
        with patch.object(S, 'fetch', return_value='<html></html>'), \
             patch.object(S, 'parse_detail', return_value=self.new_fields()):
            tried, filled = S.reparse_incomplete_records([r])
        self.assertEqual((tried, filled), (1, 1))
        self.assertTrue(r['vlmExtracted'])

    # ── 节流 / 限额 / 开关 ────────────────────────────────────────────────
    def test_04_七天内不重复抓(self):
        self.write_ledger({CKEY: datetime.date.today().isoformat()})
        with patch.object(S, 'fetch') as f:
            tried, filled = S.reparse_incomplete_records([self.rec()])
        self.assertEqual((tried, filled), (0, 0))
        f.assert_not_called()

    def test_05_超过七天后重试且失败也刷新台账日期(self):
        old = (datetime.date.today() - datetime.timedelta(days=8)).isoformat()
        self.write_ledger({CKEY: old})
        with patch.object(S, 'fetch', return_value=None) as f:
            tried, filled = S.reparse_incomplete_records([self.rec()])
        self.assertEqual((tried, filled), (1, 0))
        f.assert_called_once()
        self.assertEqual(self.read_ledger()[CKEY],
                         datetime.date.today().isoformat())

    def test_06_每轮最多十条(self):
        recs = [self.rec(url='http://xz.scnu.edu.cn/a/20261015/%d.html' % i,
                         publishTime='2026-10-15 15:00:00')
                for i in range(100, 112)]
        with patch.object(S, 'fetch', return_value='<html></html>') as f, \
             patch.object(S, 'parse_detail', return_value={}):
            tried, filled = S.reparse_incomplete_records(recs)
        self.assertEqual((tried, filled), (10, 0))
        self.assertEqual(f.call_count, 10)
        self.assertEqual(len(self.read_ledger()), 10)

    def test_07_开关关闭时零副作用(self):
        with patch.dict(os.environ, {'SCNU_REPARSE': '0'}), \
             patch.object(S, 'fetch') as f:
            tried, filled = S.reparse_incomplete_records([self.rec()])
        self.assertEqual((tried, filled), (0, 0))
        f.assert_not_called()
        self.assertFalse(os.path.exists(self.ledger_path))

    # ── 容错 ──────────────────────────────────────────────────────────────
    def test_08_parse_detail抛异常不中断且仍记账(self):
        with patch.object(S, 'fetch', return_value='<html></html>'), \
             patch.object(S, 'parse_detail', side_effect=RuntimeError('VLM boom')):
            tried, filled = S.reparse_incomplete_records([self.rec()])
        self.assertEqual((tried, filled), (1, 0))
        self.assertIn(CKEY, self.read_ledger())

    def test_09_多场次解析结果跳过补全(self):
        # 同 URL 拆出多场的页面不适合“只补一条”，跳过但仍节流记账
        with patch.object(S, 'fetch', return_value='<html></html>'), \
             patch.object(S, 'parse_detail',
                          return_value=[self.new_fields(), self.new_fields()]):
            tried, filled = S.reparse_incomplete_records([self.rec()])
        self.assertEqual((tried, filled), (1, 0))
        self.assertIn(CKEY, self.read_ledger())

    def test_10_无来源URL不抓也不写空台账键(self):
        r = self.rec(url='')
        with patch.object(S, 'fetch') as f:
            tried, filled = S.reparse_incomplete_records([r])
        self.assertEqual((tried, filled), (0, 0))
        f.assert_not_called()
        self.assertFalse(os.path.exists(self.ledger_path))

    def test_11_损坏台账退化为空并重建(self):
        with open(self.ledger_path, 'w', encoding='utf-8') as f:
            f.write('{ broken json')
        with patch.object(S, 'fetch', return_value='<html></html>'), \
             patch.object(S, 'parse_detail', return_value={}):
            tried, filled = S.reparse_incomplete_records([self.rec()])
        self.assertEqual((tried, filled), (1, 0))
        self.assertEqual(self.read_ledger(),
                         {CKEY: datetime.date.today().isoformat()})

    def test_12_台账必须随daily提交且不能被忽略(self):
        # 台账一旦被 .gitignore 忽略，7 天节流就跨不住 CI run，
        # 残缺页会每天重抓——机制看似在跑，实际成本与失败面都失控。
        with open(os.path.join(ROOT, '.github', 'workflows', 'daily.yml'),
                  encoding='utf-8') as f:
            daily = f.read()
        self.assertRegex(daily, r'git\s+add\s+[^\n]*\bdata\b[^\n]*\bsite\b',
                         'daily 必须提交 data/ 下的重解析台账')
        p = subprocess.run(['git', '-C', ROOT, 'check-ignore', '-q',
                            'data/reparse_ledger.json'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertNotEqual(p.returncode, 0,
                            'data/reparse_ledger.json 被 .gitignore 忽略，节流状态无法持久化')


if __name__ == '__main__':
    unittest.main(verbosity=2)
