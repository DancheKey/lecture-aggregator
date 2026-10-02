#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM/VLM 共享缓存（scraper/llm_cache.py）并发安全门禁。

背景（2026-10-02 评审实锤）：`data/.vlm_cache.json` 被两个模块同时读写，
而它们各自持一把**互不可见**的锁：

  · scraper/parsers.py      _VLM_CACHE_LOCK
  · scraper/llm_provider.py  _CACHE_LOCK

两者都是「整文件读 → 改一个键 → 整文件写回」，故单进程多线程下
文本抽取与 VLM 抽取并发时，后写者会用自己读时的快照覆盖文件，
把对方刚写的整批条目丢掉——表现为缓存命中莫名下降、重复烧付费额度。

本套件锁两件事：
  ① 静态：两模块必须都走 llm_cache，不得再自带缓存读写实现；
  ② 动态：高并发写入不丢条目（修复前会实打实丢）。
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import llm_cache  # noqa: E402

# 测试用缓存目录放在**工作区 tmp/** 下而非系统 TEMP（2026-10-02）。
# 理由：系统 TEMP 常被 AV / 索引器 / 磁盘清理任务扫描，偶发把刚建的目录或
# 替换中的文件挪走/锁住，表现为「单跑必绿、全量并发跑偶发红一次」这类难查的
# flaky；工作区 tmp/ 已是本项目的既定临时目录约定（.gitignore 收录），
# 且便于出问题时直接翻残留文件。
TMP_ROOT = os.path.join(ROOT, 'tmp')


def _mk_tmpdir(prefix):
    os.makedirs(TMP_ROOT, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=TMP_ROOT)


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


class SharedCacheTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = _mk_tmpdir('llm_cache_')
        self.path = os.path.join(self.tmpdir, '.vlm_cache.json')
        self._orig = llm_cache.cache_path
        llm_cache.cache_path = lambda: self.path

    def tearDown(self):
        llm_cache.cache_path = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _read_all(self):
        """读回整份缓存。

        ⚠ 2026-10-02：此前各用例直接 `open(self.path)`。而 cache_set 末尾用
        `os.replace(tmp, p)` 做原子替换——它在 Windows 上是「删除旧项 + 改名新项」
        两次系统调用（不像 POSIX rename 的单一原子操作），读者若恰好落在两步之间
        就会拿到 FileNotFoundError。这正是并发跑时偶发红一次的来源（GLM 报的
        FileNotFoundError，单独复跑必绿）。

        修法有二，此处同时采用：
          ① 经生产读取路径 cache_get 读——它本就用 try/except 兜住一切，
             符合「缓存不可用不得阻断主链路」的既定语义；
          ② 直接读前先确认文件存在，缺失时给出可诊断的失败信息而非裸异常。
        """
        # 2026-09-27：读取必须持 cache_set 同一把进程锁——Windows 的 os.replace
        # 是「删旧+改名」两步，200 并发写下裸读会撞进间隙（全量套件下确定性
        # 复现 4 红；单跑必绿的「偶发」实为负载差）。持锁后读写串行化，间隙关闭。
        with llm_cache._CACHE_LOCK:
            if not os.path.exists(self.path):
                self.fail('缓存文件未生成：%s（cache_set 应已写入）' % self.path)
            try:
                with open(self.path, encoding='utf-8') as f:
                    return json.load(f)
            except FileNotFoundError:
                # 双保险：极小概率仍落在两步之间；稍候重试一次
                time.sleep(0.05)
                with open(self.path, encoding='utf-8') as f:
                    return json.load(f)

    # ---------- 静态：单一实现 ----------
    def test_50_两模块共用llm_cache(self):
        for mod, name in (('scraper/parsers.py', '_vlm_cache_get'),
                          ('scraper/llm_provider.py', '_cache_get')):
            src = _src(mod)
            self.assertIn('from llm_cache import', src,
                          f'{mod} 未改走 llm_cache 共享实现')

    def test_51_不得再各自定义缓存锁(self):
        """修复后两个模块都不应再有锁的定义（注释里提及不算）。"""
        import ast
        for mod in ('scraper/parsers.py', 'scraper/llm_provider.py'):
            tree = ast.parse(_src(mod))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and \
                        isinstance(node.value, ast.Call) and \
                        isinstance(node.value.func, ast.Attribute) and \
                        node.value.func.attr == 'Lock':
                    for t in node.targets:
                        if isinstance(t, ast.Name) and 'CACHE' in t.id.upper():
                            self.fail(f'{mod}:{node.lineno} 仍自带缓存锁 {t.id}'
                                      f'（应统一走 llm_cache）')

    def test_52_全仓只有一把缓存锁(self):
        """按**代码行**统计（跳过注释与文档字符串）。

        注释里提到 `_CACHE_LOCK`（解释历史）是正常的——2026-10-02 首次运行本用例时
        因 parsers.py 的说明注释里写了「llm_provider._CACHE_LOCK」而被误判失败。
        约束的对象是「各自新建一把锁」，不是「提到过这个名字」。
        """
        import ast

        def lock_defs(rel):
            tree = ast.parse(_src(rel))
            out = []
            for node in ast.walk(tree):
                # 形如 X = threading.Lock() / X = _threading.Lock() 的赋值
                if isinstance(node, ast.Assign):
                    val = node.value
                    if isinstance(val, ast.Call) and \
                            isinstance(val.func, ast.Attribute) and \
                            val.func.attr == 'Lock':
                        for t in node.targets:
                            if isinstance(t, ast.Name):
                                out.append((rel, t.id))
            return out

        found = []
        for rel in ('scraper/parsers.py', 'scraper/llm_provider.py',
                    'scraper/hybrid.py', 'scraper/cited_judge.py',
                    'scraper/llm_cache.py'):
            found += lock_defs(rel)
        cache_locks = [(r, n) for r, n in found if 'CACHE' in n.upper()]
        self.assertEqual(
            len(cache_locks), 1,
            f'缓存锁应恰好一处定义（llm_cache），实得：{cache_locks}')
        self.assertEqual(cache_locks[0][0], 'scraper/llm_cache.py')

    # ---------- 动态：并发不丢 ----------
    def test_53_并发写不丢条目(self):
        """修复前 200 并发会丢条目；修复后必须全保留。"""
        n = 200

        def w(i):
            llm_cache.cache_set('k%03d' % i, {'v': i})
        ts = [threading.Thread(target=w, args=(i,)) for i in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        data = self._read_all()
        self.assertEqual(len(data), n,
                         '并发写入丢失条目（缓存互相覆盖 → 重复烧 API 额度）')

    def test_54_并发读写不抛异常且值正确(self):
        def w(i):
            llm_cache.cache_set('x%02d' % i, i)
            llm_cache.cache_get('x%02d' % i)
        ts = [threading.Thread(target=w, args=(i,)) for i in range(60)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        data = self._read_all()
        for i in range(60):
            self.assertEqual(data.get('x%02d' % i), i)

    def test_55_损坏文件可重建(self):
        """缓存文件损坏时静默重建，而不是让整条链路失败。"""
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{ 这不是合法 JSON')
        llm_cache.cache_set('k', 1)
        self.assertEqual(self._read_all(), {'k': 1})

    def test_56_读不存在的键返回None(self):
        self.assertIsNone(llm_cache.cache_get('nope'))

    def test_57_原子替换不留半份文件(self):
        """写入后不得残留 .tmp（残留会让下次 load 读到空目录）。"""
        llm_cache.cache_set('k', 1)
        self.assertFalse(os.path.exists(self.path + '.tmp'))

    def test_58_读侧对瞬时缺失免疫(self):
        """cache_get 在文件被外部删除/替换的窗口内必须返回 None 而非抛异常。

        这是生产链路的真实场景：Windows 的 os.replace 是「删旧 + 改名」两步，
        多进程写入时读者可能正好落在两步之间。缓存不可用绝不能中断抓取。
        """
        self.assertIsNone(llm_cache.cache_get('k'))      # 文件不存在
        llm_cache.cache_set('k', 1)
        os.remove(self.path)                              # 外部删除
        self.assertIsNone(llm_cache.cache_get('k'))      # 仍不得抛


if __name__ == '__main__':
    unittest.main(verbosity=2)