# -*- coding: utf-8 -*-
"""审计报告 HTML 的转义守卫（2026-10-02）。

背景：`scripts/audit_fields.py` 生成的 HTML 报告，数据来自
`data/lectures.json` —— 其中 speaker / location / speakerBio 等是**从外部网站
抓来的任意文本**，desc 里还会回显讲者姓名。此前 `_write_html` 直接把它们拼进
HTML（f-string），意味着：

  · 文本位置遇到 `<script>` / `<img onerror=…>` 即构成注入 —— 报告是 CI 附件，
    维护者下载后双击用浏览器打开就会执行；
  · `href="{u}"` 位置更危险：`javascript:` 伪协议可直接点击执行。

修复：新增 `_esc()`（文本/属性转义）与 `_esc_url()`（额外挡非 http(s) 伪协议）。
本套件用恶意样本验证「注入串绝不会原样进入产物」，并锁住 href 白名单。

运行：python tests/test_audit_html_escape.py
"""
import importlib.util
import io
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))


def _load():
    spec = importlib.util.spec_from_file_location(
        '_audit_fields', os.path.join(ROOT, 'scripts', 'audit_fields.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


A = _load()

XSS = '<script>alert(1)</script>'
IMG = '"><img src=x onerror=alert(3)>'
IFRAME = '<iframe src=evil>'


def _render(rec, desc='污染: ' + XSS):
    issues = [('讲座人', 'speaker', '高', desc, rec)]
    fd, path = tempfile.mkstemp(suffix='.html', prefix='audit_esc_')
    os.close(fd)
    try:
        A._write_html(path, [rec], issues)
        with open(path, encoding='utf-8') as f:
            return f.read()
    finally:
        os.remove(path)


class TestEsc(unittest.TestCase):
    def test_60_五个危险字符全转义(self):
        self.assertEqual(A._esc('<'), '&lt;')
        self.assertEqual(A._esc('>'), '&gt;')
        self.assertEqual(A._esc('"'), '&quot;')
        self.assertEqual(A._esc("'"), '&#39;')
        self.assertEqual(A._esc('&'), '&amp;')

    def test_61_amp须最先处理防二次转义(self):
        """先转 & 会把已转义的 &lt; 再转成 &amp;lt;（双重转义，页面显示乱码）。"""
        self.assertEqual(A._esc('<'), '&lt;')          # 而非 '&amp;lt;'
        self.assertNotIn('&amp;lt;', A._esc('<'))

    def test_62_非字符串输入不崩(self):
        for v in (None, 123, 4.5, True, ['a']):
            self.assertIsInstance(A._esc(v), str)


class TestEscUrl(unittest.TestCase):
    def test_63_允许http与https(self):
        self.assertEqual(A._esc_url('http://a.com/x'), 'http://a.com/x')
        self.assertEqual(A._esc_url('https://b.com/y'), 'https://b.com/y')

    def test_64_挡掉可执行伪协议(self):
        for u in ('javascript:alert(1)', 'JavaScript:alert(2)',
                  '  javascript:x', 'data:text/html,<h1>x',
                  'vbscript:msgbox', 'ftp://a/b', ''):
            self.assertEqual(A._esc_url(u), '',
                             f'{u!r} 不应生成链接（可执行伪协议或非 http(s)）')

    def test_65_伪协议大小写与前导空白都挡掉(self):
        self.assertEqual(A._esc_url('JAVASCRIPT:alert(1)'), '')
        self.assertEqual(A._esc_url('   javascript:alert(1)'), '')


class TestReportNoInjection(unittest.TestCase):
    """端到端：恶意数据进报告，产物里不得出现可执行的注入串。"""

    def _rec(self, **kw):
        base = {'sourceUrl': 'http://x.edu.cn/a.html', 'college': '文学院',
                'speaker': '张三', 'speakerAffiliation': '某某大学',
                'location': '理6栋302', 'lectureStart': '2026-01-01 09:00:00',
                'speakerBio': '简介'}
        base.update(kw)
        return base

    def test_70_script标签被转义(self):
        html = _render(self._rec(speaker=XSS))
        self.assertNotIn(XSS, html, '原始 <script> 串出现在报告里')
        self.assertIn('&lt;script&gt;', html)

    def test_71_img_onerror被转义(self):
        html = _render(self._rec(speaker=IMG))
        self.assertNotIn('<img src=x', html, '可执行的 <img onerror> 出现在报告里')
        self.assertIn('&lt;img', html)

    def test_72_iframe被转义(self):
        html = _render(self._rec(speakerBio=IFRAME))
        self.assertNotIn('<iframe', html)

    def test_73_字段值与描述都转义(self):
        html = _render(self._rec(location='<b>粗体</b>&"引号"',
                                 college='<svg onload=alert(9)>'))
        self.assertNotIn('<svg onload', html)
        self.assertNotIn('<b>粗体</b>', html)
        self.assertIn('&amp;', html)

    def test_74_javascript伪协议不生成链接(self):
        html = _render(self._rec(sourceUrl='javascript:alert(2)'))
        hrefs = re.findall(r'href="([^"]*)"', html)
        self.assertEqual(hrefs, [], f'非 http(s) 的 URL 不应生成 href，实得 {hrefs}')
        self.assertNotIn('<a ', html, 'javascript: URL 竟生成了可点击链接')

    def test_75_正常http链接仍生成且带noopener(self):
        html = _render(self._rec())
        self.assertIn('href="http://x.edu.cn/a.html"', html)
        self.assertIn('rel="noopener noreferrer"', html,
                      'target=_blank 须配 rel=noopener，防 tabnabbing')


class TestNoRawInterpolationInSource(unittest.TestCase):
    """静态锁：_write_html 内不得再出现未转义的 f-string 插值。"""

    def test_80_源码逐个插值点都经转义(self):
        """AST 判定：拼进 HTML 的每个 f-string 插值都必须经 _esc / _esc_url。

        不能用文本查找判断——源码里满是 `_esc(...)` 本身与 `{n}` 这类纯数字
        （str(len(issues)) 已保证安全），文本查找既会漏也会误报。
        """
        import ast
        with open(os.path.join(ROOT, 'scripts', 'audit_fields.py'),
                  encoding='utf-8') as f:
            tree = ast.parse(f.read())

        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == '_write_html')

        # 已先行赋给局部变量的「已转义中间量」不必再查
        safe_names = {'href', 'link', 'summary_rows', 'rid', 'spk', 'aff',
                      'loc', 'ls', 'bio_head', 'u', 'idx', 'rows', 'by_cat'}

        problems = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.JoinedStr):
                continue
            for part in node.values:
                if not isinstance(part, ast.FormattedValue):
                    continue
                # {n} / {href} 这类纯变量名：若是白名单里的中间量或无引号则放过
                code = ast.unparse(part.value) if hasattr(ast, 'unparse') else ''
                if not code:
                    continue
                if code in safe_names or '[' not in code and '(' not in code:
                    continue          # 裸变量/下标，无外部文本
                if '_esc(' in code or '_esc_url(' in code:
                    continue
                if code.startswith('str(len('):
                    continue
                problems.append(code[:60])
        self.assertEqual(problems, [],
                         f'_write_html 内有未转义的插值：{problems}')


if __name__ == '__main__':
    unittest.main(verbosity=2)