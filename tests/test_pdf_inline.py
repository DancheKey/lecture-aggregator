#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF-INLINE 回归门禁（2026-11-06，skc/1124）。

背景：社会科学处「砺儒讲坛第148讲」页面把讲座时间/地点/简介全写在一个嵌在
iframe 里的 PDF（Word 导出）中，HTML 正文只有标题 + 发布时间。线上却抓成
`lectureStart == publishTime`（2026-11-06 11:29）。四道断点：

  ① `div.content` 下并列 `pagecontentright3`（「资讯及通知」侧栏 388 字）把
     body_text 灌到 504 字，越过旧的 `if len(body_text) < 150` 触发条件；
  ② 旧 `<a>` 策略还要求链接文本含「通知」，本页链接文本是纯文件名；
  ③ `_safe_fetch` 用短 UA + 无 Referer，statics CDN 回吐 WAF 拦截页 HTML，
     魔数不匹配就静默 pass，连日志都没有；
  ④ `scraper/requirements.txt` 从未列 PyMuPDF，CI 上 `import fitz` 直接
     ImportError 被吞（本机能跑纯因本地装过）。

修复方向：以「DOM 里检测到 PDF 附件」作为主判据，正文 <150 只作容器选错时的
兜底；侧栏在 DOM 层摘除；下载带完整浏览器 UA + Referer；任何失败都留告警。

本套件**全离线**：真实页面固化为 tests/fixtures/html/skc1124.html，PDF 文本
固化为 tests/fixtures/pdf/skc1124.txt；下载用桩、fitz 用桩，无网络无密钥。
"""
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
FIX_HTML = os.path.join(ROOT, 'tests', 'fixtures', 'html', 'skc1124.html')
FIX_PDF_TEXT = os.path.join(ROOT, 'tests', 'fixtures', 'pdf', 'skc1124.txt')

# 与 golden 同纪律：import parsers 前锁纯规则基线，避免本机 .env 触发真实大模型
os.environ.setdefault('SCNU_LLM_TEXT', '0')
os.environ.setdefault('SCNU_LLM_RICH', '0')

import parsers as P  # noqa: E402
import timeparse  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

URL = 'http://skc.scnu.edu.cn/a/20261106/1124.html'
PDF_URL = '//statics.scnu.edu.cn/pics/skc/2026/1106/1793935936321092.pdf'
PDF_TEXT = open(FIX_PDF_TEXT, encoding='utf-8').read()
REAL_HTML = open(FIX_HTML, encoding='utf-8').read()
PDF_BYTES = b'%PDF-1.7\n' + PDF_TEXT.encode('utf-8')  # 只过魔数，fitz 用桩
WAF_HTML = b'<html><body>WAF blocked</body></html>' * 200


def _fake_fitz(text=PDF_TEXT):
    """最小 fitz 替身：只实现 _collect_assets 用到的 open/get_text/转图接口。"""
    class _Page:
        def get_text(self):
            return text

        def get_pixmap(self, **kw):
            raise RuntimeError('fixture: no raster in offline test')

    class _Doc:
        page_count = 1

        def __iter__(self):
            return iter([_Page()])

        def __getitem__(self, i):
            return _Page()

        def close(self):
            pass

    m = types.ModuleType('fitz')
    m.open = lambda **kw: _Doc()
    m.Matrix = lambda *a, **kw: object()
    return m


def _run_assets(html=REAL_HTML):
    """跑阶段 1+2（正文定位）与阶段 3（资源/PDF 内联），返回共享状态。"""
    soup = BeautifulSoup(html, 'html.parser')
    st = P._OcrSt()
    st.ocr_text = ''
    title, _meta, content_div = P._prep_doc_text(soup, None, st)
    P._collect_assets(soup, content_div, URL, st)
    return st, content_div, title


class _Resp:
    """requests.get 的最小响应替身（供 _safe_fetch 的 headers 断言）。"""

    def __init__(self, code=200, loc=None, body=b'%PDF-1.7'):
        self.status_code = code
        self.headers = {'Location': loc} if loc else {}
        self._body = body

    def iter_content(self, chunk_size=65536):
        return iter([self._body])

    def close(self):
        pass


def _synthetic_html(body_len=320, with_pdf=True, with_sidebar=True):
    """合成页面：正文长（>150，旧判据必漏）+ iframe PDF + 纯文件名下载链接。"""
    pdf_part = ''
    if with_pdf:
        pdf_part = (
            '<iframe src="//statics.scnu.edu.cn/api/viewer/web/viewer2.html#'
            + PDF_URL + '"></iframe>'
            '<a href="' + PDF_URL + '">11月23日第148讲（熊卫）_20261106112256.pdf</a>')
    side = ''
    if with_sidebar:
        items = ''.join(
            '<li>资讯及通知第%d条关于某年度项目申报与评审工作安排的说明文字若干</li>' % i
            for i in range(12))
        side = ('<div class="pagecontentright3"><h3>资讯及通知</h3><ul>'
                + items
                + '</ul><a href="//side.example.com/notice.pdf">侧栏无关附件.pdf</a>'
                  '</div>')
    body = ('<p>本次讲座围绕社会合作规范与信息设计展开讨论，' * 20)[:body_len]
    return ('<html><body><div class="content">'
            '<div class="pagecontentLeft3"><h1>测试讲座</h1>'
            '<p>发布时间: 2026-11-06 11:29:00</p><p>' + body + '</p>'
            + pdf_part + '</div>' + side + '</div></body></html>')


class SidebarDomTest(unittest.TestCase):
    """① 侧栏必须在 DOM 层摘除，而不是靠文本阈值截断。"""

    def test_01_real_page_sidebar_is_identified(self):
        soup = BeautifulSoup(REAL_HTML, 'html.parser')
        content_div = soup.find('div', class_='content')
        self.assertIsNotNone(content_div)
        side = P._sidebar_blocks(content_div)
        self.assertEqual(len(side), 1, 'skc/1124 应命中且只命中一个侧栏块')
        self.assertIn('right', ' '.join(side[0].get('class') or []))
        raw = content_div.get_text(' ')
        self.assertGreater(len(raw.strip()), 400, '原始正文应被侧栏灌水（复现前提）')

    def test_02_body_text_excludes_sidebar(self):
        soup = BeautifulSoup(REAL_HTML, 'html.parser')
        content_div = soup.find('div', class_='content')
        raw = P._content_text_without_sidebar(content_div)
        self.assertNotIn('资讯及通知', raw)
        self.assertLess(len(raw.strip()), 200,
                        '去侧栏后原始文本应从 504 字级明显回落')
        # 触发判据读的是 _prep_doc_text 归一化后的 body_text，才是真正的口径
        st = P._OcrSt()
        st.ocr_text = ''
        _title, _meta, _cd = P._prep_doc_text(soup, None, st)
        self.assertLess(len(st.body_text.strip()), P._PDF_SHORT_BODY,
                        '归一化后正文应回到 115 字量级，不再顶掉 PDF 门槛')
        self.assertNotIn('资讯及通知', st.body_text)

    def test_03_sidebar_pdf_link_is_not_a_candidate(self):
        html = _synthetic_html()
        soup = BeautifulSoup(html, 'html.parser')
        content_div = soup.find('div', class_='content')
        side = P._sidebar_blocks(content_div)
        self.assertTrue(side, '合成侧栏应被识别')
        urls = P._collect_pdf_urls(content_div, side)
        self.assertIn(PDF_URL, urls, '正文 iframe 的 PDF 必须保留')
        self.assertFalse(any('side.example.com' in u for u in urls),
                         '侧栏推荐列表里的 PDF 不得触发下载')


class PdfTriggerTest(unittest.TestCase):
    """② 结构判据为主：正文再长，只要页面里有 PDF 附件就必须尝试。"""

    def test_04_long_body_still_fetches_pdf(self):
        html = _synthetic_html(body_len=320)
        with patch.object(P, '_safe_fetch', return_value=PDF_BYTES) as fetch, \
                patch.dict(sys.modules, {'fitz': _fake_fitz()}):
            st, _cd, _title = _run_assets(html)
        self.assertGreater(len(st.body_text.strip()), P._PDF_SHORT_BODY,
                           '正文未被误截到 150 字以下')
        self.assertTrue(fetch.called, '正文 >=150 时旧判据会整个跳过 PDF，必须回归')
        self.assertEqual(fetch.call_args.kwargs.get('referer'), URL)
        self.assertIn('文科楼二楼会议室', st.body_text.replace(' ', ''))

    def test_05_link_text_without_notice_keyword_is_accepted(self):
        soup = BeautifulSoup(_synthetic_html(), 'html.parser')
        content_div = soup.find('div', class_='content')
        urls = P._collect_pdf_urls(content_div, P._sidebar_blocks(content_div))
        self.assertIn(PDF_URL, urls,
                      '「通知」二字不再作为 .pdf 链接的准入条件')

    def test_06_page_without_pdf_issues_no_request(self):
        html = _synthetic_html(with_pdf=False, with_sidebar=False)
        with patch.object(P, '_fetch_pdf_bytes',
                          return_value=(None, 'stub')) as fetch:
            _run_assets(html)
        self.assertFalse(fetch.called, '无附件页面不得产生任何 PDF 下载请求')


class FetchHardeningTest(unittest.TestCase):
    """③ UA / Referer 与非 PDF 响应显式报错，杜绝静默吞。"""

    def test_07_safe_fetch_sends_browser_ua_and_referer(self):
        with patch.object(P.requests, 'get',
                          return_value=_Resp()) as get:
            data = P._safe_fetch('https://statics.scnu.edu.cn/x.pdf', referer=URL)
        self.assertEqual(data, b'%PDF-1.7')
        headers = get.call_args.kwargs['headers']
        self.assertIn('Chrome/', headers['User-Agent'])
        self.assertEqual(headers['Referer'], URL)

    def test_08_headers_survive_redirect_hop(self):
        with patch.object(P.requests, 'get', side_effect=[
                _Resp(302, '/final.pdf'), _Resp(200)]) as get:
            data = P._safe_fetch('https://statics.scnu.edu.cn/x.pdf', referer=URL)
        self.assertEqual(data, b'%PDF-1.7')
        self.assertEqual(get.call_count, 2)
        for c in get.call_args_list:
            self.assertIn('Chrome/', c.kwargs['headers']['User-Agent'])
            self.assertEqual(c.kwargs['headers']['Referer'], URL)

    def test_09_non_pdf_response_reports_reason(self):
        with patch.object(P, '_safe_fetch', return_value=WAF_HTML) as fetch:
            data, err = P._fetch_pdf_bytes(
                'https://statics.scnu.edu.cn/x.pdf', referer=URL)
        self.assertIsNone(data)
        self.assertIn('非 PDF', err)
        self.assertEqual(fetch.call_args.kwargs.get('referer'), URL)

    def test_10_download_failure_reports_reason(self):
        with patch.object(P, '_safe_fetch', return_value=None):
            data, err = P._fetch_pdf_bytes('https://statics.scnu.edu.cn/x.pdf')
        self.assertIsNone(data)
        self.assertIn('下载失败', err)

    def test_11_pdf_magic_accepts_real_bytes(self):
        with patch.object(P, '_safe_fetch', return_value=PDF_BYTES):
            data, err = P._fetch_pdf_bytes('https://statics.scnu.edu.cn/x.pdf')
        self.assertEqual(data, PDF_BYTES)
        self.assertIsNone(err)

    def test_12_waf_failure_emits_warning(self):
        buf = io.StringIO()
        with patch.object(P, '_safe_fetch', return_value=WAF_HTML), \
                patch.dict(sys.modules, {'fitz': _fake_fitz()}), \
                redirect_stderr(buf):
            _run_assets(REAL_HTML)
        out = buf.getvalue()
        self.assertIn('[PDF-INLINE]', out, '附件丢失必须留下可检索的告警')
        self.assertIn('非 PDF', out)


class EndToEndTimeTest(unittest.TestCase):
    """④ 端到端：PDF 文本进入解析视野后，时间应回到 11 月 23 日上午 9 点。"""

    @staticmethod
    def _resolve(body_text, title, page_text):
        return timeparse.resolve_lecture_time(
            body_text, title, 2026, None,
            '2026-11-06 11:29:00', 'second', 2026, None, page_text)

    def test_13_pdf_text_reaches_body_text(self):
        with patch.object(P, '_safe_fetch', return_value=PDF_BYTES), \
                patch.dict(sys.modules, {'fitz': _fake_fitz()}):
            st, _cd, _title = _run_assets(REAL_HTML)
        fold = st.body_text.replace(' ', '')
        self.assertIn('11月23日上午9:00', fold)
        self.assertIn('文科楼二楼会议室', fold)
        self.assertIn('报告人简介', fold)
        self.assertIn('讲座内容简介', fold)

    def test_14_lecture_start_is_nov23_not_publish_time(self):
        with patch.object(P, '_safe_fetch', return_value=PDF_BYTES), \
                patch.dict(sys.modules, {'fitz': _fake_fitz()}):
            st, _cd, title = _run_assets(REAL_HTML)
        got = self._resolve(st.body_text, title, st.text)
        self.assertIsNotNone(got)
        self.assertEqual(got['start'], '2026-11-23 09:00:00')
        self.assertNotEqual(got['note'], 'general-body-full')

        # 对照：只有 HTML 正文时，解析器只能看到发布时间（旧 bug 的现场）
        soup = BeautifulSoup(REAL_HTML, 'html.parser')
        plain = P._OcrSt()
        plain.ocr_text = ''
        plain_title, _m, _cd = P._prep_doc_text(soup, None, plain)
        before = self._resolve(plain.body_text, plain_title, plain.text)
        self.assertIsNotNone(before)
        self.assertNotEqual(before['start'], '2026-11-23 09:00:00',
                            'PDF 文本必须是决定性证据，否则本用例失去意义')


if __name__ == '__main__':
    unittest.main(verbosity=2)
