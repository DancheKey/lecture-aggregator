#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用附件正文通道回归门禁（2026-11-07）。

背景：2026-11-06 的 [PDF-INLINE] 修复只认 iframe viewer#...pdf 与
a[href].pdf 两种形态，且真实回归样本只有 skc/1124 一个页面。本套件把通道
固化为「静态 DOM 找附件 → 按内容魔数分派提取」，锁住以下行为：

  ① 9 种网页附件形态（含 <embed>/<object>、viewer 的 ?file=、.docx/.doc）；
  ② Word(.docx) 用纯标准库提取，文本进入全部 5 个正文副本并影响时间解析；
  ③ 97-2003 老 .doc(OLE2) 只告警、不假装成功、不污染正文；
  ④ 3 个真实站点页面回放：skc/1125（第二套真实 PDF 数据）、life/6830 与
     ggy/6694（两套不同 DOM 户型、零附件），验证不误下载、不误剔侧栏；
  ⑤ WAF 拦截页 / 非 Word OOXML / 损坏附件仍被内容魔数挡住。

全部离线：真实页面固化在 tests/fixtures/html/（2026-11-07 联网抓取），
Word 附件是 tests/fixtures/attachment/lecture_notice.docx（真实 OOXML 结构，
标准库生成，非桩）；下载一律用桩，零网络零密钥，本套件不经过 fitz 路径。
"""
import io
import json
import os
import re
import sys
import tempfile
import zipfile
import unittest
import importlib.util
from contextlib import redirect_stderr
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
FIX_HTML = os.path.join(ROOT, 'tests', 'fixtures', 'html')
FIX_DOCX = os.path.join(ROOT, 'tests', 'fixtures', 'attachment',
                        'lecture_notice.docx')

# 与 golden 同纪律：import parsers 前锁纯规则基线，避免本机 .env 触发真实大模型。
# 这里直接赋值（非 setdefault）：全量 pytest 下别的套件可能已把开关改成 '1'。
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers as P  # noqa: E402
import timeparse  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

URL = 'http://skc.scnu.edu.cn/a/20261106/1125.html'
DOCX_BYTES = open(FIX_DOCX, 'rb').read()
OLE2_BYTES = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 512
WAF_HTML = b'<html><body>WAF blocked</body></html>' * 200
PDF_BYTES = b'%PDF-1.7\n1 0 obj\n<<>>\nendobj\n'

REAL_PAGES = {
    'skc1125': 'http://skc.scnu.edu.cn/a/20261106/1125.html',
    'life6830': 'http://life.scnu.edu.cn/a/20261019/6830.html',
    'ggy6694': 'http://ggy.scnu.edu.cn/a/20261028/6694.html',
}


def _load_real(name):
    path = os.path.join(FIX_HTML, name + '.html')
    with open(path, encoding='utf-8', errors='replace') as fh:
        return fh.read()


def _soup(html):
    return BeautifulSoup(html, 'html.parser')


def _page(link_html, body='本次讲座围绕社会合作规范与信息设计展开讨论，' * 8):
    return ('<html><body><div class="content"><h1>测试讲座</h1>'
            '<p>发布时间: 2026-11-06 11:29:00</p><p>' + body + '</p>'
            + link_html + '</div></body></html>')


def _sidebar_page():
    items = ''.join(
        '<li>资讯及通知第%d条关于某年度项目申报与评审工作安排的说明文字若干</li>' % i
        for i in range(12))
    return ('<html><body><div class="content">'
            '<div class="pagecontentLeft3"><h1>测试讲座</h1>'
            '<a href="/x/正文附件.docx">讲座通知.docx</a></div>'
            '<div class="pagecontentright3"><h3>资讯及通知</h3><ul>' + items
            + '</ul><a href="//side.example.com/notice.pdf">侧栏无关附件.pdf</a>'
              '</div></div></body></html>')


def _run_assets(html, data, url=URL):
    """跑正文定位 + 资源/附件收集，下载用桩，返回 (st, title, stderr)。"""
    soup = _soup(html)
    st = P._OcrSt()
    st.ocr_text = ''
    title, _meta, cd = P._prep_doc_text(soup, None, st)
    buf = io.StringIO()
    with patch.object(P, '_safe_fetch', return_value=data), redirect_stderr(buf):
        P._collect_assets(soup, cd, url, st)
    return st, title, buf.getvalue()


def _resolve(st, title):
    return timeparse.resolve_lecture_time(
        st.body_text, title, 2026, None,
        '2026-11-06 11:29:00', 'second', 2026, None, st.text)


class ShapeMatrixTest(unittest.TestCase):
    """① 静态 DOM 里 9 种附件形态都该被找到，且 PDF 兼容入口只返回 PDF。"""

    CASES = [
        ('a[href].pdf', '<a href="/x/notice.pdf">附件</a>', '/x/notice.pdf', True),
        ('iframe viewer#pdf',
         '<iframe src="/viewer2.html#//h/x/notice.pdf"></iframe>',
         '//h/x/notice.pdf', True),
        ('iframe viewer?file=pdf',
         '<iframe src="/viewer.html?file=%2Fx%2Fnotice.pdf"></iframe>',
         '/x/notice.pdf', True),
        ('embed pdf', '<embed src="/x/notice.pdf">', '/x/notice.pdf', True),
        ('object data=pdf', '<object data="/x/notice.pdf"></object>',
         '/x/notice.pdf', True),
        ('a[href].docx', '<a href="/x/notice.docx">附件下载</a>',
         '/x/notice.docx', False),
        ('a[href].doc', '<a href="/x/notice.doc">附件下载</a>',
         '/x/notice.doc', False),
        ('iframe viewer#docx',
         '<iframe src="/viewer2.html#//h/x/notice.docx"></iframe>',
         '//h/x/notice.docx', False),
        ('a[href].pdf?query', '<a href="/x/notice.pdf?download=1">附件</a>',
         '/x/notice.pdf?download=1', True),
    ]

    def test_01_all_nine_shapes_are_collected(self):
        for name, frag, expected, _is_pdf in self.CASES:
            with self.subTest(shape=name):
                cd = _soup('<div class="content">' + frag + '</div>').find(
                    'div', class_='content')
                urls = P._collect_attachment_urls(cd)
                self.assertEqual(urls, [expected],
                                 '%s 应被识别为附件链接' % name)

    def test_02_pdf_compat_entry_stays_pdf_only(self):
        for name, frag, expected, is_pdf in self.CASES:
            with self.subTest(shape=name):
                cd = _soup('<div class="content">' + frag + '</div>').find(
                    'div', class_='content')
                pdfs = P._collect_pdf_urls(cd)
                self.assertEqual(pdfs, [expected] if is_pdf else [],
                                 '旧 [PDF-INLINE] 入口不得混入 Word 链接')

    def test_03_viewer_query_is_not_false_positive(self):
        """旧实现会把整个 viewer.html?file=... 当 PDF 下载，必须解出真附件。"""
        cd = _soup('<div class="content">'
                   '<iframe src="/viewer.html?file=%2Fx%2Fnotice.pdf"></iframe>'
                   '</div>').find('div', class_='content')
        urls = P._collect_attachment_urls(cd)
        self.assertEqual(urls, ['/x/notice.pdf'])
        self.assertNotIn('viewer.html', urls[0])

    def test_04_sidebar_attachments_are_excluded(self):
        cd = _soup(_sidebar_page()).find('div', class_='content')
        side = P._sidebar_blocks(cd)
        self.assertEqual(len(side), 1, '侧栏应先被识别')
        urls = P._collect_attachment_urls(cd, side)
        self.assertIn('/x/正文附件.docx', urls, '正文里的 Word 附件必须保留')
        self.assertFalse(any('side.example.com' in u for u in urls),
                         '侧栏推荐列表里的 PDF 不得触发下载')

    def test_05_attachment_tries_are_capped(self):
        links = ''.join('<a href="/x/n%d.pdf">附件%d</a>' % (i, i)
                        for i in range(8))
        html = _page(links)
        soup = _soup(html)
        st = P._OcrSt()
        st.ocr_text = ''
        _t, _m, cd = P._prep_doc_text(soup, None, st)
        with patch.object(P, '_fetch_pdf_bytes',
                          return_value=(None, 'stub')) as fetch, \
                redirect_stderr(io.StringIO()):
            P._collect_assets(soup, cd, URL, st)
        self.assertEqual(fetch.call_count, P._MAX_ATTACHMENT_TRIES,
                         '异常页面不得无限放大附件下载请求')


class ContentTypeTest(unittest.TestCase):
    """② 类型判定只看内容魔数，不信扩展名；异常附件必须显式报错。"""

    def test_06_kind_by_magic(self):
        self.assertEqual(P._attachment_kind(PDF_BYTES), 'pdf')
        self.assertEqual(P._attachment_kind(DOCX_BYTES), 'ooxml')
        self.assertEqual(P._attachment_kind(OLE2_BYTES), 'ole2')
        self.assertIsNone(P._attachment_kind(WAF_HTML))
        # 扩展名骗人也没用：改后缀不改内容
        self.assertEqual(P._attachment_kind(DOCX_BYTES), 'ooxml')

    def test_07_waf_page_is_rejected(self):
        with patch.object(P, '_safe_fetch', return_value=WAF_HTML):
            data, err = P._fetch_pdf_bytes('https://statics.scnu.edu.cn/x.pdf',
                                           referer=URL)
        self.assertIsNone(data)
        self.assertIn('非 PDF', err, '兼容旧告警措辞')

    def test_08_docx_bytes_are_accepted(self):
        with patch.object(P, '_safe_fetch', return_value=DOCX_BYTES):
            data, err = P._fetch_pdf_bytes('https://statics.scnu.edu.cn/x.docx',
                                           referer=URL)
        self.assertEqual(data, DOCX_BYTES)
        self.assertIsNone(err)

    def test_09_docx_text_extraction_from_real_ooxml(self):
        text, err = P._extract_docx_text(DOCX_BYTES)
        self.assertIsNone(err)
        self.assertIn('讲座时间：2026年11月23日 上午9:00', text)
        self.assertIn('文科楼二楼会议室', text)
        self.assertIn('讲座内容简介', text)

    def test_10_non_word_ooxml_reports_reason(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('xl/workbook.xml', '<workbook/>')
        text, err = P._extract_docx_text(buf.getvalue())
        self.assertEqual(text, '')
        self.assertIn('非 Word 附件', err)

    def test_11_broken_zip_reports_reason(self):
        text, err = P._extract_docx_text(b'PK\x03\x04' + b'garbage' * 20)
        self.assertEqual(text, '')
        self.assertIn('DOCX 解析失败', err)


class DocxEndToEndTest(unittest.TestCase):
    """③ Word 附件正文必须进入全部 5 个副本，并真正改变讲座时间。"""

    def test_12_docx_text_reaches_all_five_text_copies(self):
        html = _page('<a href="/x/notice.docx">讲座通知.docx</a>')
        st, _title, log = _run_assets(html, DOCX_BYTES)
        for attr in ('body_text', 'body_text_llm', 'body_text_folded',
                     'text', 'text_folded'):
            with self.subTest(copy=attr):
                self.assertIn('文科楼二楼会议室',
                              (getattr(st, attr, '') or '').replace(' ', ''),
                              '%s 必须同步追加附件正文' % attr)
        self.assertIn('[ATTACH]', log, '成功提取也要留下可检索的记录')

    def test_13_docx_time_is_not_publish_time(self):
        html = _page('<a href="/x/notice.docx">讲座通知.docx</a>')
        st, title, _log = _run_assets(html, DOCX_BYTES)
        got = _resolve(st, title)
        self.assertIsNotNone(got)
        self.assertEqual(got['start'], '2026-11-23 09:00:00')
        # 对照：HTML 正文里根本没有 11 月 23 日的信息（旧 bug 的现场），
        # 没有附件时时间只能退化成发布时间或干脆解析不出，绝不能是 11-23。
        plain_html = _page('')
        self.assertNotIn('11月23日', plain_html)
        pst = P._OcrSt()
        pst.ocr_text = ''
        pt, _m, _cd = P._prep_doc_text(_soup(plain_html), None, pst)
        before = _resolve(pst, pt)
        self.assertTrue(before is None
                        or before['start'] != '2026-11-23 09:00:00',
                        'DOCX 文本必须是决定性证据，否则本用例失去意义')

    def test_14_docx_success_does_not_invoke_fitz(self):
        """Word 不能误走 PDF 分支（PyMuPDF 缺失时也不该报错）。"""
        html = _page('<a href="/x/notice.docx">讲座通知.docx</a>')
        with patch.dict(sys.modules, {'fitz': None}):
            st, _t, log = _run_assets(html, DOCX_BYTES)
        self.assertIn('文科楼二楼会议室', st.body_text.replace(' ', ''))
        self.assertIn('[ATTACH]', log)
        self.assertNotIn('PyMuPDF', log,
                         'Word 附件不得误走 PDF 分支')
        self.assertNotIn('PDF 处理失败', log)


class LegacyDocTest(unittest.TestCase):
    """④ 97-2003 老 .doc：识别到但解析不了，必须留告警且不污染正文。"""

    def test_15_ole2_warns_and_does_not_inject_text(self):
        html = _page('<a href="/x/notice.doc">讲座通知.doc</a>')
        st, _title, log = _run_assets(html, OLE2_BYTES)
        self.assertIn('[ATTACH]', log)
        self.assertIn('OLE2', log)
        self.assertNotIn('文科楼二楼会议室', st.body_text)

    def test_16_failed_attachment_keeps_pdf_inline_alert(self):
        """全部附件下载失败仍要有 [PDF-INLINE] 告警（旧门禁语义不丢）。"""
        html = _page('<a href="/x/notice.docx">讲座通知.docx</a>')
        soup = _soup(html)
        st = P._OcrSt()
        st.ocr_text = ''
        _t, _m, cd = P._prep_doc_text(soup, None, st)
        buf = io.StringIO()
        with patch.object(P, '_safe_fetch', return_value=WAF_HTML), \
                redirect_stderr(buf):
            P._collect_assets(soup, cd, URL, st)
        self.assertIn('[PDF-INLINE]', buf.getvalue())
        self.assertIn('非 PDF', buf.getvalue())


class RealSiteReplayTest(unittest.TestCase):
    """⑤ 真实站点回放：第二套 PDF 数据 + 两套零附件户型。"""

    def test_17_skc1125_second_real_pdf_is_not_hardcoded(self):
        soup = _soup(_load_real('skc1125'))
        st = P._OcrSt()
        st.ocr_text = ''
        _t, _m, cd = P._prep_doc_text(soup, None, st)
        side = P._sidebar_blocks(cd)
        urls = P._collect_attachment_urls(cd, side)
        self.assertTrue(urls, '149 讲与 148 讲同栏目，应同样识别到内嵌 PDF')
        self.assertTrue(any('1793951293245025.pdf' in u for u in urls),
                        '必须是 1125 自己的附件，不能复用了 1124 的硬编码值')
        self.assertFalse(any('1793935936321092' in u for u in urls),
                         '1124 的附件 ID 不该出现在 1125 页面')
        self.assertEqual(len(side), 1, '侧栏仍应被识别')
        self.assertNotIn('资讯及通知', st.body_text)

    def test_18_zero_attachment_pages_do_not_fetch_or_strip(self):
        for name in ('life6830', 'ggy6694'):
            with self.subTest(page=name):
                soup = _soup(_load_real(name))
                st = P._OcrSt()
                st.ocr_text = ''
                _t, _m, cd = P._prep_doc_text(soup, None, st)
                self.assertIsNotNone(cd, '%s 必须命中正文容器' % name)
                side = P._sidebar_blocks(cd)
                self.assertEqual(len(side), 0,
                                 '%s 不含 skc 式侧栏，规则不得误伤' % name)
                urls = P._collect_attachment_urls(cd, side)
                self.assertEqual(urls, [], '%s 无附件，不得产生候选' % name)
                raw = re.sub(r'\s+', ' ', cd.get_text(' ')).strip()
                clean = re.sub(
                    r'\s+', ' ', P._content_text_without_sidebar(cd)).strip()
                self.assertEqual(len(clean), len(raw),
                                 '零命中时正文文本不得被摘除')
                with patch.object(P, '_fetch_pdf_bytes',
                                  return_value=(None, 'stub')) as fetch:
                    P._collect_assets(soup, cd, REAL_PAGES[name], st)
                self.assertFalse(fetch.called,
                                 '%s 不得产生任何附件下载请求' % name)

    def test_19_real_pages_keep_body_text(self):
        for name in ('life6830', 'ggy6694'):
            with self.subTest(page=name):
                soup = _soup(_load_real(name))
                st = P._OcrSt()
                st.ocr_text = ''
                P._prep_doc_text(soup, None, st)
                self.assertGreater(len(st.body_text.strip()), 100,
                                   '%s 正文不得因附件通道改造而被清空' % name)


class AttachmentHealthReportTest(unittest.TestCase):
    """⑥ 告警台账 → 每日体检报告：失败当天可见，且不引入 HTML 注入。"""

    def setUp(self):
        P.clear_attachment_events()

    def tearDown(self):
        P.clear_attachment_events()

    @staticmethod
    def _audit_module():
        spec = importlib.util.spec_from_file_location(
            '_audit_fields_attach',
            os.path.join(ROOT, 'scripts', 'audit_fields.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _render(self, ledger=None):
        audit = self._audit_module()
        fd, out_path = tempfile.mkstemp(suffix='.html', prefix='attach_report_')
        os.close(fd)
        ledger_path = None
        try:
            if ledger is not None:
                lf, ledger_path = tempfile.mkstemp(suffix='.json',
                                                   prefix='attach_ledger_')
                with os.fdopen(lf, 'w', encoding='utf-8') as fh:
                    json.dump(ledger, fh, ensure_ascii=False)
            audit._write_html(out_path, [], [], attachment_path=ledger_path)
            with open(out_path, encoding='utf-8') as fh:
                return fh.read()
        finally:
            os.remove(out_path)
            if ledger_path:
                os.remove(ledger_path)

    def test_20_failed_attachment_is_recorded_for_daily_report(self):
        html = _page('<a href="/x/notice.docx">讲座通知.docx</a>')
        soup = _soup(html)
        st = P._OcrSt()
        st.ocr_text = ''
        _t, _m, cd = P._prep_doc_text(soup, None, st)
        with patch.object(P, '_safe_fetch', return_value=WAF_HTML), \
                redirect_stderr(io.StringIO()):
            P._collect_assets(soup, cd, URL, st)
        events = P.get_attachment_events()
        self.assertTrue(events, '附件全取不到必须留台账，不能只躺在 CI 日志里')
        self.assertEqual(events[0]['sourceUrl'], URL)
        self.assertEqual(events[0]['level'], 'warn')
        self.assertIn('非 PDF', events[0]['message'])

    def test_21_successful_docx_does_not_pollute_ledger(self):
        _st, _t, _log = _run_assets(
            _page('<a href="/x/notice.docx">讲座通知.docx</a>'), DOCX_BYTES)
        self.assertEqual(P.get_attachment_events(), [],
                         '成功提取不该进「需要人处理」的告警台账')

    def test_22_report_shows_clickable_source_link(self):
        ledger = {'updatedAt': '2026-11-07T09:00:00+08:00',
                  'events': [{'level': 'warn',
                              'sourceUrl': 'http://skc.scnu.edu.cn/a/1.html',
                              'message': '附件均未取到'}]}
        html = self._render(ledger)
        self.assertIn('附件提取告警', html)
        self.assertIn('href="http://skc.scnu.edu.cn/a/1.html"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn('附件均未取到', html)

    def test_23_report_escapes_hostile_ledger(self):
        ledger = {'updatedAt': '2026-11-07T09:00:00+08:00',
                  'events': [{'level': 'warn',
                              'sourceUrl': 'javascript:alert(1)',
                              'message': '<script>alert(2)</script>'}]}
        html = self._render(ledger)
        self.assertNotIn('<script>alert(2)</script>', html)
        self.assertNotIn('href="javascript', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<a ', html, '伪协议 URL 不得生成可点击链接')

    def test_24_missing_ledger_never_breaks_report(self):
        html = self._render(None)
        self.assertIn('本轮无台账数据', html)
        self.assertNotIn('<a ', html,
                         '旧调用方不传台账路径时报告结构须保持原样')


if __name__ == '__main__':
    unittest.main(verbosity=2)
