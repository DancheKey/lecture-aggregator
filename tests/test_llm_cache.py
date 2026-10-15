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
import contextlib
import io
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


class LlmFailureWarnTest(unittest.TestCase):
    """P2-8：LLM 通道失败必须留痕（2026-10-14）。

    原实现 3 次重试耗尽/鉴权失败后静默返回 None：key 过期、限费、网络中断
    在服务端零痕迹，表现为「今天没抓到什么」，极易误判为源站无更新。
    本类断言：失败路径写 WARN 到 stderr，且返回值语义不变（仍为 None）。
    """

    def _run_post(self, fake_post):
        import io
        import contextlib
        import llm_provider
        p = llm_provider.AgnesProvider(api_key='test-key', model='test-model')
        llm_provider.time.sleep = lambda *a: None  # 跳过重试退避
        orig = llm_provider.requests.post
        llm_provider.requests.post = fake_post
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                result = p._post([{'role': 'user', 'content': 'x'}], 0.0)
        finally:
            llm_provider.requests.post = orig
        return result, buf.getvalue()

    def test_59_鉴权失败告警且返回None(self):
        class _R:
            status_code = 401

            def raise_for_status(self):
                pass
        result, err = self._run_post(lambda *a, **k: _R())
        self.assertIsNone(result)
        self.assertIn('401', err)
        self.assertIn('llm[agnes]', err)

    def test_60_重试耗尽告警且返回None(self):
        calls = {'n': 0}

        def _post(*a, **k):
            calls['n'] += 1
            raise ConnectionError('network unreachable')
        result, err = self._run_post(_post)
        self.assertIsNone(result)
        self.assertEqual(calls['n'], 3, '应重试满 3 次')
        self.assertIn('3 次重试均失败', err)
        self.assertIn('network unreachable', err)


class TransientWriteFailureTest(unittest.TestCase):
    """写侧瞬时占用不得静默丢条目（2026-10-14，test_53 本机偶红根因）。

    根因实测（tmp/repro_cache_race.py，200 线程 × 8 轮）：Windows 上
    `os.replace(tmp, p)` 在目标文件被 AV/索引器瞬时占用时抛
    `PermissionError: WinError 5`，原实现 `except Exception: pass` 静默吞掉 →
    该条缓存消失（表现为缓存命中率莫名下降、重复烧额度）。
    本类锁三条：① 瞬时失败重试后条目仍在且不告警；② 重试耗尽必须留痕且
    不破坏已有条目；③ 瞬时「读」失败不得被当成损坏去重建（那会把一次瞬时
    故障放大成整库清空）。
    """

    def setUp(self):
        self.tmpdir = _mk_tmpdir('llm_cache_tx_')
        self.path = os.path.join(self.tmpdir, '.vlm_cache.json')
        self._orig = llm_cache.cache_path
        llm_cache.cache_path = lambda: self.path

    def tearDown(self):
        llm_cache.cache_path = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _read_all(self):
        import builtins
        if not os.path.exists(self.path):
            return {}
        with builtins.open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def test_61_瞬时替换失败重试后条目保留(self):
        import unittest.mock as mock
        real = os.replace
        calls = {'n': 0}

        def flaky(a, b):
            calls['n'] += 1
            if calls['n'] <= 2:                      # 前两次瞬时拒绝
                raise PermissionError(5, 'Denied (transient)')
            return real(a, b)

        buf = io.StringIO()
        with mock.patch('os.replace', flaky):
            with contextlib.redirect_stderr(buf):
                llm_cache.cache_set('k1', {'v': 1})
        self.assertEqual(self._read_all().get('k1'), {'v': 1},
                         '瞬时 replace 失败后条目丢失（重试未生效）')
        self.assertEqual(calls['n'], 3, '应重试到第 3 次成功')
        self.assertNotIn('WARN', buf.getvalue(), '重试成功不应告警')

    def test_62_重试耗尽留痕且不破坏已有条目(self):
        import unittest.mock as mock
        llm_cache.cache_set('keep', 1)                # 先落一条已有条目

        def always_fail(a, b):
            raise PermissionError(5, 'Denied (persistent)')

        buf = io.StringIO()
        with mock.patch('os.replace', always_fail):
            with contextlib.redirect_stderr(buf):
                llm_cache.cache_set('new', 2)         # 不得抛
        data = self._read_all()
        self.assertEqual(data.get('keep'), 1, '写失败不得破坏已有条目')
        self.assertNotIn('new', data)
        self.assertIn('WARN', buf.getvalue(), '重试耗尽必须留痕（不得静默）')
        self.assertIn('PermissionError', buf.getvalue())
        self.assertFalse(os.path.exists(self.path + '.tmp'),
                         '重试耗尽后应清理残片 .tmp')

    def test_63_瞬时读失败不重建(self):
        """读 OSError（瞬时不可读）≠ 文件损坏：必须放弃写入，不得用 {} 重建。

        ⚠ 桩必须**只挡读、放行写**：首版桩对所有路径/permission 一律抛，
        结果「读被挡 → 返空 → 写也被挡 → 兜底保住旧文件」使断言在
        「无脑重建」的错误实现下依然绿（破坏验证抓到假绿）。桩的粒度必须
        精确对应被测语义。
        """
        import builtins
        llm_cache.cache_set('keep', 1)
        real_open = builtins.open
        cache_abs = os.path.abspath(self.path)

        def deny_read(path, *a, **kw):
            mode = a[0] if a else kw.get('mode', 'r')
            if (os.path.abspath(str(path)) == cache_abs and 'r' in mode
                    and '+' not in mode):
                raise PermissionError(13, 'in use by another process')
            return real_open(path, *a, **kw)

        llm_cache.open = deny_read                    # 覆盖模块内 open 查找
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                llm_cache.cache_set('new', 2)
        finally:
            del llm_cache.open
        data = self._read_all()
        self.assertEqual(data, {'keep': 1},
                         '瞬时读失败被当成损坏重建 → 整份缓存被单键覆盖')
        self.assertIn('WARN', buf.getvalue())


class VlmCacheKeyTest(unittest.TestCase):
    """VLM 缓存键纳入模型名（2026-10-14）。

    背景：文本通道缓存键早已含模型名（llm_provider.py:468/:593），换模型自动
    全量失效重抽；VLM 通道键不含模型名，模型升级（如 glm-4v-flash→glm-4v-plus）
    后旧结果仍被命中、锁死，表现为「改了模型没效果」。
    本类锁：① 换模型产生不同键（动态，走真实 _vlm_extract_fields）；
    ② 同模型同 URL 键稳定（幂等）；③ 多 provider 时键含全部模型名。
    """

    def _capture_keys(self, img_urls, cfgs):
        """调用真实 _vlm_extract_fields，捕获其计算的缓存键。

        桩掉缓存读写与网络调用（_vlm_img_b64 / _vlm_try_one_provider），
        仅让键计算路径执行——锁的是生产代码本身，不是复刻副本。
        """
        import parsers as P
        captured = []
        orig = (P._vlm_cache_get, P._vlm_cache_set,
                P._vlm_img_b64, P._vlm_try_one_provider)
        P._vlm_cache_get = lambda key: captured.append(key) or None
        P._vlm_cache_set = lambda key, val: None
        P._vlm_img_b64 = lambda u: None          # 图片下载失败 → 提前返 None，不触网
        P._vlm_try_one_provider = lambda *a, **k: (None, False)
        try:
            P._vlm_extract_fields(img_urls, cfgs)
        finally:
            (P._vlm_cache_get, P._vlm_cache_set,
             P._vlm_img_b64, P._vlm_try_one_provider) = orig
        return captured

    def test_70_换模型产生不同键(self):
        """换模型必须产生不同缓存键（否则旧结果锁死、改模型没效果）。"""
        keys = self._capture_keys(['http://x/a.jpg'], [{'model': 'm1'}])
        keys2 = self._capture_keys(['http://x/a.jpg'], [{'model': 'm2'}])
        self.assertEqual(len(keys), 1)
        self.assertEqual(len(keys2), 1)
        self.assertNotEqual(keys[0], keys2[0],
                            '换模型必须产生不同缓存键')
        self.assertTrue(keys[0].startswith('vlm:m1:'), keys[0])
        self.assertTrue(keys2[0].startswith('vlm:m2:'), keys2[0])

    def test_71_同模型同URL键稳定(self):
        """URL 顺序无关（sorted），同模型同 URL 键必须稳定（重跑幂等）。"""
        k1 = self._capture_keys(['http://x/b.jpg', 'http://x/a.jpg'],
                                [{'model': 'm'}])
        k2 = self._capture_keys(['http://x/a.jpg', 'http://x/b.jpg'],
                                [{'model': 'm'}])
        self.assertEqual(k1, k2, 'URL 顺序不得影响缓存键')

    def test_72_多provider键含全部模型(self):
        """agnes+zhipu 双通道时，键须含两个模型名（任一换模型都应失效）。"""
        keys = self._capture_keys(
            ['http://x/c.jpg'],
            [{'model': 'agnes-3.0-flash'}, {'model': 'glm-4v-flash'}])
        self.assertEqual(len(keys), 1)
        self.assertIn('agnes-3.0-flash', keys[0])
        self.assertIn('glm-4v-flash', keys[0])
        self.assertTrue(keys[0].startswith('vlm:agnes-3.0-flash|glm-4v-flash:'),
                        keys[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)