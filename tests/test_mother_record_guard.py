# -*- coding: utf-8 -*-
"""方案 A 守卫单测（2026-10-27）：incremental_merge 丢弃多场拆分页回退母记录。

背景（jky/5920 CI 实测）：库里某 URL 已拆分为 (url,1)/(url,2) 子记录，增量轮
重抓该页时解析器若未识别出多场拆分，会产出 (url, None) 母记录——复合键
(sourceUrl, None) 与子记录不冲突、直接追加，导致同一页「2 子卡 + 1 母卡」并存。

守卫语义：
  - 基底或本批新记录中同 sourceUrl 已存在带 int lectureIndex 的子记录
    → 丢弃新母记录（解析回退产物）。
  - 反方向（库里单条 None + 新批产出子记录）不触发，保留「补拆漏期」能力。
  - lectureIndex=0 也是合法子记录索引，不得漏判。
"""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_scraper():
    """显式从文件加载 scraper.py（scraper/ 无 __init__.py）。"""
    path = os.path.join(ROOT, 'scraper', 'scraper.py')
    spec = importlib.util.spec_from_file_location('sc_mother_guard', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rec(url, li=None, speaker='张伟', title='学术讲座', date='2026-10-20 15:00'):
    r = {
        'sourceUrl': url,
        'lectureIndex': li,
        'speaker': speaker,
        'title': title,
        'lectureStart': date,
        'college': '测试学院',
        'publishTime': '2026-10-19',
    }
    # 拆分子记录的真实形态：dedup/cross_source_dedup 的复合键靠 isMultiLecture
    # + lectureIndex 区分同 URL 多场，未设则与母记录同键被既有同源去重合并。
    if li is not None:
        r['isMultiLecture'] = True
    return r


class MotherRecordGuardTest(unittest.TestCase):
    """incremental_merge 的母记录回退守卫。"""

    def setUp(self):
        self.s = _load_scraper()

    def _merged_keys(self, existing, new_records):
        out = self.s.incremental_merge(existing, new_records)
        return {(r['sourceUrl'], r.get('lectureIndex')) for r in out}

    def test_01_基底已有子记录_丢弃回退母记录(self):
        """核心场景：库里 (url,1)/(url,2) 在库，增量轮追加 (url,None) 母记录 → 被丢弃。"""
        url = 'https://example.com/jky/5920'
        existing = [_rec(url, 1, speaker='李洪修'), _rec(url, 2, speaker='雷浩')]
        new_records = [_rec(url, None, speaker='李洪修')]
        keys = self._merged_keys(existing, new_records)
        self.assertEqual(keys, {(url, 1), (url, 2)},
                         '回退母记录必须被丢弃，只保留 2 条拆分子记录')

    def test_02_无拆分URL_母记录正常追加(self):
        """普通单场 URL 无子记录，母记录不得被误杀。"""
        base = 'https://example.com/a/1'
        new_url = 'https://example.com/a/2'
        existing = [_rec(base, None, speaker='张伟')]
        new_records = [_rec(new_url, None, speaker='赵强',
                            title='另一场讲座', date='2026-10-21 10:00')]
        keys = self._merged_keys(existing, new_records)
        self.assertIn((new_url, None), keys, '无拆分的新记录必须正常追加')

    def test_03_本批内部子母并存_丢弃母记录(self):
        """同批新记录同时产出子记录与母记录：母记录丢弃、子记录保留。"""
        url = 'https://example.com/bd/66'
        new_records = [
            _rec(url, 1, speaker='王五'),
            _rec(url, 2, speaker='赵六'),
            _rec(url, None, speaker='王五'),
        ]
        keys = self._merged_keys([], new_records)
        self.assertEqual(keys, {(url, 1), (url, 2)},
                         '本批内同 URL 已有拆分子记录时母记录同样丢弃')

    def test_04_反向补拆不触发守卫(self):
        """库里单条 None + 新批解析出子记录：补拆漏期能力必须保留（母子并存为既有行为）。"""
        url = 'https://example.com/cs/1'
        existing = [_rec(url, None, speaker='张伟')]
        new_records = [_rec(url, 1, speaker='张伟'), _rec(url, 2, speaker='李四')]
        keys = self._merged_keys(existing, new_records)
        self.assertIn((url, 1), keys, '补拆出的子记录必须追加')
        self.assertIn((url, 2), keys, '补拆出的子记录必须追加')

    def test_05_索引0也是合法子记录(self):
        """lectureIndex=0 的子记录须被识别为拆分记录，母记录仍须丢弃。"""
        url = 'https://example.com/xx/9'
        existing = [_rec(url, 0, speaker='李洪修')]
        new_records = [_rec(url, None, speaker='李洪修')]
        keys = self._merged_keys(existing, new_records)
        self.assertEqual(keys, {(url, 0)},
                         'li=0 子记录在库时回退母记录必须被丢弃')


if __name__ == '__main__':
    unittest.main(verbosity=2)
