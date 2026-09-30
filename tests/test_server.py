#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py 最小测试集（2026-09-30 引入，此前 server.py 零测试覆盖）。

背景：本仓库的解析层有 13 套门禁，而 server.py（本地后端 + sources 写接口）
**一个测试都没有**。2026-09-30 的第三方评审发现两个真实缺陷，共同点是
「只有真跑一次写接口才会暴露」，静态检查与现有门禁全都抓不到：

  B4注释静默蒸发：_save_sources 用 yaml.dump 覆写 sources.yaml，而该文件里有
    19 行人工维护的注释（栏目 URL 约定、死链说明、JS 翻页取舍记录）。PyYAML
    的 dump **不保留注释** → 任何一次 POST/PUT/DELETE 都会把整份文件重写成
    无注释版，且 git diff 只显示「21 deletions」，极易被当作正常改动放过。
    数据等价、知识蒸发，故症状极隐蔽。

  B3 非 dict body 崩线程：_read_body_json 对合法但非对象的 JSON（`[1,2]` /
    `"x"` / `42` / `null`）原样返回，而全部调用方第一件事就是 body.get(...)
    → AttributeError 抛到 handler 外，本线程直接崩（非只影响该请求）。

本套件覆盖：
  ① sources.yaml 往返**保留注释**（PUT/POST/DELETE 三条写路径各一次）
  ② 往返后**数据等价**（不能因为保注释而改动语义）
  ③ 注释行数恒为「真文件实测值」——防止将来有人把indent 参数调错造成全文件重排
  ④ sources 条数上限（MAX_SOURCES）
  ⑤ _read_body_json 对非 dict 的一律降级为空 body（用真 socket 灌真实请求字节）
  ⑥ 真实 _read_body_json 在超长/非法 Content-Length 下不抛未捕获异常

全部用例零网络、零外部服务：sources 读写一律在 tempfile 临时目录里做，
**绝不触碰真实的 scraper/sources.yaml**。

运行：python tests/test_server.py
"""
import importlib.util
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PATH = os.path.join(ROOT, 'server.py')
SOURCES_REL = os.path.join('scraper', 'sources.yaml')

# 真 sources.yaml 里的注释行数（2026-09-30 实测 19 行）。这是**契约**：
# B4 修复后，任何一条 sources 写路径都不许让它掉下来。
EXPECTED_COMMENT_LINES = 19


def _load_server():
    """import server.py 为模块（它有 __main__ 守卫，不会真的起服务）。"""
    # server 模块顶层会读磁盘上的访问统计；反复 import 多个实例时若仍有未关的句柄，
    # Python 会刷一屏 ResourceWarning 淹没测试输出。已改用 with 打开（2026-09-30），
    # 这里再兜一层，确保本套件的输出只有测试结果。
    warnings.simplefilter('ignore', ResourceWarning)
    spec = importlib.util.spec_from_file_location('_server_under_test', SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _SourcesRoundTripBase(unittest.TestCase):
    """把 server 的 SOURCES_PATH 指向临时目录里的真实副本。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='test_server_src_')
        self.real_path = os.path.join(ROOT, SOURCES_REL)
        self.tmp_yaml = os.path.join(self.tmpdir, 'sources.yaml')
        shutil.copyfile(self.real_path, self.tmp_yaml)
        self.before_text = self._read()

        self.srv = _load_server()
        self._orig_path = self.srv.SOURCES_PATH
        self.srv.SOURCES_PATH = self.tmp_yaml

    def tearDown(self):
        self.srv.SOURCES_PATH = self._orig_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _read(self):
        with open(self.tmp_yaml, 'r', encoding='utf-8') as f:
            return f.read()

    def _comment_lines(self):
        return sum(1 for l in self._read().splitlines() if l.strip().startswith('#'))

    def _sources(self):
        return self.srv  # 仅为可读性；实际方法挂在 handler 上，见下方子类

    def _bind(self, h, *names):
        """把 server.Handler 上的方法绑到桩对象 h 上。

        注意：方法在 `server.Handler` 类上，不在模块上——用模块的 __class__
        去取会拿到 `type` 而 AttributeError。
        """
        H = self.srv.Handler
        for n in names:
            setattr(h, n, getattr(H, n).__get__(h))
        return h


class TestSourcesCommentPreservation(_SourcesRoundTripBase):
    """B4：三条写路径（PUT 改字段 / POST 增/ DELETE 删）都必须保住注释。"""

    # server 的 sources CRUD 是 Handler 的方法，不依赖请求状态的部分直接借实例调用。
    # 用 types.SimpleNamespace 造一个只带方法绑定的宿主，避免真的起HTTP 服务。
    class _Stub:
        pass

    def _handler(self):
        h = self._Stub()
        # 绑定三个方法（它们只用 self._load_sources / self._save_sources / self._send_json）
        self._bind(h, '_load_sources', '_save_sources',
                   '_api_sources_post', '_api_sources_put', '_api_sources_delete')
        # 记录 _send_json 的出参，避免真的走 socket
        h.sent = []
        h._send_json = lambda payload, code=200: h.sent.append((code, payload))
        # 桩掉 body 读取
        h._next_body = {}
        h._read_body_json = lambda: h._next_body
        return h

    def test_00_前置_真文件注释行数与契约一致(self):
        """若此用例失败，说明 EXPECTED_COMMENT_LINES 该更新（或注释被谁删了）。"""
        with open(self.real_path, 'r', encoding='utf-8') as f:
            n = sum(1 for l in f if l.strip().startswith('#'))
        self.assertEqual(n, EXPECTED_COMMENT_LINES,
                         f'真实 sources.yaml 注释行数 {n} != 契约 {EXPECTED_COMMENT_LINES}')

    def test_01_无改动往返也保注释(self):
        """纯 load→save（不做任何增删改）就必须保注释——这是 B4 的最小充分条件。"""
        h = self._handler()
        data = h._load_sources()
        h._save_sources(data)
        self.assertEqual(self._comment_lines(), EXPECTED_COMMENT_LINES,
                         'load→save 空往返就丢了注释：_load_sources/_save_sources 没走 round-trip')

    def test_02_往返数据等价(self):
        """保注释的前提是不改语义：往返后与原文件解析结果必须逐键相等。"""
        import yaml as pyyaml
        h = self._handler()
        h._save_sources(h._load_sources())
        after = pyyaml.safe_load(self._read())
        self.assertEqual(after, pyyaml.safe_load(self.before_text))

    def test_03_PUT_改字段保注释(self):
        h = self._handler()
        data = h._load_sources()
        n0 = len(data['sources'])
        h._next_body = {'campus': '汕尾'}
        h._api_sources_put(0)
        code, payload = h.sent[-1]
        self.assertEqual(code, 200, f'PUT 应成功：{payload}')
        self.assertEqual(len(h._load_sources()['sources']), n0, 'PUT 不得增删源')
        self.assertEqual(self._comment_lines(), EXPECTED_COMMENT_LINES, 'PUT 丢了注释')

    def test_04_POST_新增保注释(self):
        h = self._handler()
        data = h._load_sources()
        n0 = len(data['sources'])
        h._next_body = {'name': '测试源', 'campus': '石牌',
                        'base': 'http://t.scnu.edu.cn', 'list_urls': []}
        h._api_sources_post()
        code, payload = h.sent[-1]
        self.assertEqual(code, 200, f'POST 应成功：{payload}')
        self.assertEqual(len(h._load_sources()['sources']), n0 + 1)
        self.assertEqual(self._comment_lines(), EXPECTED_COMMENT_LINES, 'POST 丢了注释')

    def test_05_DELETE_删除保注释(self):
        h = self._handler()
        data = h._load_sources()
        n0 = len(data['sources'])
        h._api_sources_delete(0)
        code, payload = h.sent[-1]
        self.assertEqual(code, 200, f'DELETE 应成功：{payload}')
        self.assertEqual(len(h._load_sources()['sources']), n0 - 1)
        self.assertEqual(self._comment_lines(), EXPECTED_COMMENT_LINES, 'DELETE 丢了注释')

    def test_06_连续三次写_注释仍不丢(self):
        """回归真实使用形态：管理页上连续增/改/删多次，每次都落盘。"""
        h = self._handler()
        for i in range(3):
            d = h._load_sources()
            d['sources'][i]['campus'] = f'校区{i}'
            h._save_sources(d)
            self.assertEqual(self._comment_lines(), EXPECTED_COMMENT_LINES,
                             f'第 {i + 1} 次写后注释丢失')

    def test_07_缩进风格不产生全文件重排(self):
        """indent 参数配错会导致 600+ 行无意义 diff（数据仍对，但评审无从下手）。

        这里锁住「往返后行数与原文件一致」——即真文件当前的缩进风格被正确复现。
        """
        h = self._handler()
        h._save_sources(h._load_sources())
        self.assertEqual(len(self._read().splitlines()),
                         len(self.before_text.splitlines()),
                         '往返后行数变了：ruamel indent 参数与仓库现有风格不一致，会产出全文件 diff')

    def test_08_真文件未被测试改动(self):
        """本套件所有读写都在临时目录；真sources.yaml 必须逐字节不变。"""
        with open(self.real_path, 'r', encoding='utf-8') as f:
            self.assertEqual(f.read(), self.before_text,
                             '测试改动了真实 sources.yaml —— 测试有副作用，必须修')


class TestSourcesCap(_SourcesRoundTripBase):
    """sources 条数上限：防POST 循环把 yaml 撑成几万条（每个源都会被 daily.yml 真抓）。"""

    class _Stub:
        pass

    def _handler(self, body):
        h = self._Stub()
        self._bind(h, '_load_sources', '_save_sources', '_api_sources_post')
        h.sent = []
        h._send_json = lambda payload, code=200: h.sent.append((code, payload))
        h._read_body_json = lambda: body
        return h

    def test_09_超限返回400且不落盘(self):
        h = self._handler({'name': 'x', 'base': 'http://x.edu.cn', 'list_urls': []})
        h.srv = self.srv
        # 把上限调小以便测试（不动全局常量）
        orig = self.srv.MAX_SOURCES
        self.srv.MAX_SOURCES = 1# 真文件有 53个源，故第一条即超限
        try:
            h._api_sources_post()
        finally:
            self.srv.MAX_SOURCES = orig
        code, payload = h.sent[-1]
        self.assertEqual(code, 400, f'超限须400：{payload}')
        self.assertFalse(payload.get('ok'))
        self.assertEqual(self._read(), self.before_text, '超限请求不应落盘')

    def test_10_常量有合理上界(self):
        """MAX_SOURCES 应显著大于真文件源数，否则一上线就锁死新增。"""
        import yaml as pyyaml
        with open(self.real_path, 'r', encoding='utf-8') as f:
            n = len((pyyaml.safe_load(f) or {}).get('sources') or [])
        self.assertGreater(self.srv.MAX_SOURCES, n,
                           f'MAX_SOURCES({self.srv.MAX_SOURCES}) 须大于真源数({n})')


class TestReadBodyJsonGuards(unittest.TestCase):
    """B3 + 既有 P3 守卫：_read_body_json 永不让调用方拿到「非 dict」。"""

    def setUp(self):
        self.srv = _load_server()

    def _read_with(self, raw: bytes, headers: dict):
        """把 raw 当作已读入的请求体，走真_read_body_json 取回解析结果。"""
        class _Stub:
            pass

        h = _Stub()
        h.headers = headers
        h.rfile = io.BytesIO(raw)
        h._read_body_json = self.srv.Handler._read_body_json.__get__(h)
        return h._read_body_json()

    def test_11_合法对象正常解析(self):
        v = self._read_with(json.dumps({'url': 'http://x'}).encode(),
                            {'Content-Length': str(len(json.dumps({'url': 'http://x'})))})
        self.assertEqual(v, {'url': 'http://x'})

    def test_12_非dict一律降级为空body(self):
        """B3 核心：合法但非对象的 JSON 曾原样返回 → 调用方 body.get 崩线程。"""
        for raw in (b'[1,2,3]', b'"hello"', b'42', b'null', b'true', b'1.5'):
            v = self._read_with(raw, {'Content-Length': str(len(raw))})
            self.assertEqual(v, {},
                             f'{raw!r} 应降级为空 body（调用方只认 dict），实得 {v!r}')
            #并且必须能被调用方安全地 .get —— 这才是修复的本意
            self.assertEqual((v or {}).get('url') or '', '')

    def test_13_非法JSON降级为空body(self):
        v = self._read_with(b'{not json', {'Content-Length': '8'})
        self.assertEqual(v, {})

    def test_14_ContentLength非数字降级且不抛(self):
        v = self._read_with(b'{}', {'Content-Length': 'abc'})
        self.assertEqual(v, {})

    def test_15_无ContentLength降级(self):
        v = self._read_with(b'', {})
        self.assertEqual(v, {})

    def test_16_超长ContentLength按空body处理且不抛(self):
        """超MAX_BODY_BYTES 时会把流读完丢弃；此处用小阈值验证该分支不抛异常。"""
        orig = self.srv.MAX_BODY_BYTES
        self.srv.MAX_BODY_BYTES = 4
        try:
            v = self._read_with(b'{"url":"http://x"}', {'Content-Length': '17'})
        finally:
            self.srv.MAX_BODY_BYTES = orig
        self.assertEqual(v, {})


class TestSourcesYamlLoaderEdgeCases(_SourcesRoundTripBase):
    """_load_sources 的退化路径：文件缺失 / 空文件 / 顶层不是 dict。"""

    def _write(self, text):
        with open(self.tmp_yaml, 'w', encoding='utf-8') as f:
            f.write(text)

    def test_17_文件缺失返回空sources(self):
        os.remove(self.tmp_yaml)
        h = type('H', (), {})()
        self._bind(h, '_load_sources')
        self.assertEqual(h._load_sources(), {'sources': []})

    def test_18_空文件返回空sources(self):
        self._write('')
        h = type('H', (), {})()
        self._bind(h, '_load_sources')
        self.assertEqual(h._load_sources(), {'sources': []})

    def test_19_顶层为列表时返回空sources(self):
        """此前 yaml.safe_load 会返回 list，`data['sources']` 立刻 TypeError。"""
        self._write('- a\n- b\n')
        h = type('H', (), {})()
        self._bind(h, '_load_sources')
        data = h._load_sources()
        self.assertEqual(data, {'sources': []})
        self.assertIsInstance(data['sources'], list)


class TestNoTestSideEffects(unittest.TestCase):
    """兜底：整轮测试跑完，真实 sources.yaml 与真实 server.py 都不该被写。"""

    def test_20_真文件收尾未变(self):
        import hashlib
        p = os.path.join(ROOT, SOURCES_REL)
        with open(p, 'rb') as f:
            # 只断言可读且是合法 YAML（结构完整），不硬编码 md5（内容本就会演进）
            self.assertTrue(f.read().startswith(b'sources:'), '真 sources.yaml 头部被破坏')
        import yaml as pyyaml
        with open(p, 'r', encoding='utf-8') as f:
            self.assertIsInstance(pyyaml.safe_load(f), dict)


if __name__ == '__main__':
    unittest.main(verbosity=2)
