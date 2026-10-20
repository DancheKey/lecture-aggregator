# -*- coding: utf-8 -*-
"""站点静态资产守卫（2026-10-20 批次 A：分享卡片与可发现性）。

锁住四类「坏了也看不出来」的东西——它们的共同特征是页面照样能打开，
只有转发出去、或者在别人机器上打开时才暴露：

1. **图标三件套**：SVG（矢量清晰）+ 32/16 PNG（Safari 对 SVG favicon 支持
   不全，只留 SVG 会出现「Chrome 有图标、Safari 空白」）+ apple-touch-icon
   （iOS 主屏，必须白底不透明，透明区会被渲染成黑块）。
2. **og / twitter 卡片必须绝对 URL**：爬虫不解析相对路径，写 `og-cover.jpg`
   抓不到图，微信里就退化成一条裸链接。本地打开完全正常，只有转发时才暴露。
3. **og:url 每页指向自己**：从 stats.html 复制 meta 到新页面时最容易忘改，
   结果所有页面转发出去都指回首页。
4. **sitemap 的 loc 必须带 /lecture-aggregator/ 子路径**：GitHub Pages 项目页
   挂在域名子路径下，漏了子路径整份 sitemap 判为无效。

另有一条「口径同源」断言：HTML 里写死的 og:url 前缀必须与生成器常量
`SITE_BASE_URL` 逐字一致。本项目已多次因「同一个口径各写一份」分叉
（计数键三套写法、合并卡阈值四处判定），这里提前锁死。
"""
import os
import re
import struct
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SITE = os.path.join(_ROOT, 'site')

# 三个真实页面：首页、统计页、访问量趋势页（后者是 gen_visits_trend.py 的产物，
# 但模板在脚本里，产物同样要过门禁，否则模板改坏要等次日 CI 才发现）。
PAGES = ('index.html', 'stats.html', 'visits-trend.html')

# 每页期望的 og:url 相对路径（'' = 站点根）
PAGE_PATH = {
    'index.html': '',
    'stats.html': 'stats.html',
    'visits-trend.html': 'visits-trend.html',
}


def _read(rel):
    with open(os.path.join(_SITE, rel), encoding='utf-8') as f:
        return f.read()


def _png_size(path):
    with open(path, 'rb') as f:
        head = f.read(24)
    if head[:8] != b'\x89PNG\r\n\x1a\n':
        raise AssertionError(f'{path} 不是 PNG')
    return struct.unpack('>II', head[16:24])


def _jpeg_size(path):
    """只读基线/渐进 JPEG 的 SOF 段取宽高。

    关键：遇到 SOS(0xDA) 必须停——后面是熵编码数据，随机出现的 0xFFC0 序列
    会被误判成 SOF，读出天文数字（本机实测过 3239×14716 这种假尺寸）。
    """
    with open(path, 'rb') as f:
        data = f.read()
    if data[:2] != b'\xff\xd8':
        raise AssertionError(f'{path} 不是 JPEG')
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF or marker == 0x01 or 0xD0 <= marker <= 0xD7 or marker == 0xD8:
            i += 1 if marker == 0xFF else 2
            continue
        if marker == 0xDA:
            break
        seg_len = struct.unpack('>H', data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack('>HH', data[i + 5:i + 9])
            return w, h
        i += 2 + seg_len
    raise AssertionError(f'{path} 找不到 SOF 段')


def _meta(html, key, attr='property'):
    m = re.search(r'<meta\s+%s="%s"\s+content="([^"]*)"' % (attr, re.escape(key)), html)
    return m.group(1) if m else None


def _site_base_url():
    """从生成器源码里取常量，保证与产物同源（不 import，避免带出副作用）。"""
    with open(os.path.join(_ROOT, 'scripts', 'generate_frontend_data.py'),
              encoding='utf-8') as f:
        src = f.read()
    m = re.search(r"^SITE_BASE_URL\s*=\s*'([^']+)'", src, re.M)
    assert m, 'generate_frontend_data.py 里找不到 SITE_BASE_URL 常量'
    return m.group(1)


class TestIcons(unittest.TestCase):
    """图标三件套：每页都引用，且被引用的文件真实存在、尺寸正确。"""

    def test_每页都有svg与png兜底(self):
        for page in PAGES:
            html = _read(page)
            svg = re.findall(r'<link rel="icon"[^>]*type="image/svg\+xml"', html)
            png = re.findall(r'<link rel="icon"[^>]*type="image/png"', html)
            self.assertEqual(len(svg), 1, f'{page} 缺 SVG favicon')
            self.assertGreaterEqual(len(png), 1, f'{page} 缺 PNG 兜底 favicon（Safari 需要）')
            self.assertIn('apple-touch-icon', html, f'{page} 缺 apple-touch-icon')

    def test_图标文件存在且尺寸正确(self):
        expect = {
            'favicon-32.png': (32, 32),
            'favicon-16.png': (16, 16),
            'apple-touch-icon.png': (180, 180),
        }
        for name, size in expect.items():
            path = os.path.join(_SITE, name)
            self.assertTrue(os.path.exists(path), f'site/{name} 不存在')
            self.assertEqual(_png_size(path), size, f'site/{name} 尺寸应为 {size}')
        # apple-touch-icon 必须是 RGB（无 alpha）：iOS 把透明区渲染成黑块
        with open(os.path.join(_SITE, 'apple-touch-icon.png'), 'rb') as f:
            head = f.read(26)
        color_type = head[25]
        self.assertEqual(color_type, 2, 'apple-touch-icon 必须是 RGB（color type 2），不能带透明通道')

    def test_svg图标文件存在(self):
        path = os.path.join(_SITE, 'scnu-emblem.svg')
        self.assertTrue(os.path.exists(path), 'site/scnu-emblem.svg 不存在')
        with open(path, encoding='utf-8') as f:
            self.assertIn('<svg', f.read(), 'scnu-emblem.svg 不是合法 SVG')


class TestSocialCard(unittest.TestCase):
    """og / twitter 卡片：绝对 URL、每页指向自己、封面图是方形。"""

    def test_每页都有完整og字段(self):
        keys = ('og:title', 'og:description', 'og:url', 'og:image',
                'og:image:width', 'og:image:height')
        for page in PAGES:
            html = _read(page)
            for k in keys:
                self.assertTrue(_meta(html, k), f'{page} 缺 {k}')
            self.assertTrue(_meta(html, 'twitter:card', attr='name'),
                            f'{page} 缺 twitter:card')
            self.assertTrue(_meta(html, 'description', attr='name'),
                            f'{page} 缺 description')

    def test_og图片是绝对url(self):
        base = _site_base_url()
        for page in PAGES:
            img = _meta(_read(page), 'og:image')
            self.assertTrue(img.startswith('https://'),
                            f'{page} 的 og:image 不是绝对 URL：{img}（爬虫抓不到相对路径）')
            self.assertEqual(img, base + 'og-cover.jpg',
                            f'{page} 的 og:image 与 SITE_BASE_URL 不一致：{img}')
            tw = _meta(_read(page), 'twitter:image', attr='name')
            self.assertEqual(tw, img, f'{page} 的 twitter:image 应与 og:image 相同')

    def test_og标签必须在前2KB内(self):
        """抓取器可能只读响应体前 4KB——og 标签被注释/CSP 推到后面 = 静默失效。

        2026-10-20 用户实测：标签齐全（用微信 UA 抓过，逐条都在），但微信里
        仍是裸链接。当时的 og 块位于 2.8~3.5KB 处，前面压着两段长注释——
        这是「写得越认真越容易踩」的坑：注释越详细，标签推得越后。
        修法是把 og 块前置到 head 最前，本条把它固化成断言：将来谁在 head
        里加长注释，这里立刻变红。

        ⚠ 定位必须带 `<meta property="` 前缀：注释里也写着 "og:image" 三个字，
        直接 find(b'og:image') 会命中注释（本机实测偏移量对不上就是这么来的）。
        """
        LIMIT = 2048
        for page in PAGES:
            with open(os.path.join(_SITE, page), 'rb') as f:
                raw = f.read()
            for key in (b'og:title', b'og:image', b'og:url'):
                needle = b'<meta property="' + key + b'"'
                i = raw.find(needle)
                self.assertNotEqual(i, -1, f'{page} 找不到 {needle.decode()}')
                self.assertLess(i, LIMIT,
                                f'{page} 的 {key.decode()} 在第 {i} 字节（上限 {LIMIT}），'
                                f'抓取器可能读不到——把它往前挪，别在 head 顶部堆长注释')

    def test_og_url每页指向自己(self):
        base = _site_base_url()
        for page in PAGES:
            url = _meta(_read(page), 'og:url')
            self.assertEqual(url, base + PAGE_PATH[page],
                             f'{page} 的 og:url 应指向本页：{base}{PAGE_PATH[page]}')

    def test_封面图存在且为方形(self):
        path = os.path.join(_SITE, 'og-cover.jpg')
        self.assertTrue(os.path.exists(path), 'site/og-cover.jpg 不存在')
        w, h = _jpeg_size(path)
        self.assertEqual(w, h, f'og-cover 应为方形（微信缩略图是方形取景框），实测 {w}x{h}')
        self.assertGreaterEqual(w, 600, f'og-cover 边长至少 600px，实测 {w}')
        # 微信/QQ 抓图有超时，体积过大容易抓取失败
        size_kb = os.path.getsize(path) / 1024
        self.assertLess(size_kb, 300, f'og-cover 应小于 300KB，实测 {size_kb:.0f}KB')
        # 尺寸声明必须与真实文件一致，否则部分平台按声明裁剪
        for page in PAGES:
            html = _read(page)
            self.assertEqual(_meta(html, 'og:image:width'), str(w), f'{page} og:image:width 与实际不符')
            self.assertEqual(_meta(html, 'og:image:height'), str(h), f'{page} og:image:height 与实际不符')


class TestDiscoveryFiles(unittest.TestCase):
    """sitemap / robots / 404：静态文件存在且格式正确。"""

    def test_sitemap存在且loc为绝对url(self):
        path = os.path.join(_SITE, 'sitemap.xml')
        self.assertTrue(os.path.exists(path), 'site/sitemap.xml 不存在')
        with open(path, encoding='utf-8') as f:
            xml = f.read()
        self.assertIn('<urlset', xml)
        locs = re.findall(r'<loc>([^<]+)</loc>', xml)
        self.assertEqual(len(locs), len(PAGES), f'sitemap 应有 {len(PAGES)} 条，实际 {len(locs)}')
        base = _site_base_url()
        for loc in locs:
            self.assertTrue(loc.startswith(base),
                            f'sitemap 的 loc 必须以 {base} 开头（GitHub Pages 项目页子路径），实际 {loc}')

    def test_sitemap由生成器产出而不是手写(self):
        """sitemap 必须进 CI 每日生成，否则 lastmod 会永远停在手工那天。"""
        with open(os.path.join(_ROOT, 'scripts', 'generate_frontend_data.py'),
                  encoding='utf-8') as f:
            src = f.read()
        self.assertIn('def write_sitemap(', src, 'generate_frontend_data.py 缺 write_sitemap')
        self.assertIn('write_sitemap(updated_at)', src, 'main() 里没有调用 write_sitemap')

    def test_robots存在且指向sitemap(self):
        path = os.path.join(_SITE, 'robots.txt')
        self.assertTrue(os.path.exists(path), 'site/robots.txt 不存在')
        with open(path, encoding='utf-8') as f:
            txt = f.read()
        self.assertIn('User-agent: *', txt)
        self.assertIn('Sitemap: ' + _site_base_url() + 'sitemap.xml', txt)

    def test_404页面存在且零脚本(self):
        path = os.path.join(_SITE, '404.html')
        self.assertTrue(os.path.exists(path), 'site/404.html 不存在')
        with open(path, encoding='utf-8') as f:
            html = f.read()
        # 走到 404 的请求往往已经失败一次，不再加载脚本/外部样式
        self.assertNotIn('<script', html.lower(), '404 页不应加载任何脚本')
        self.assertNotIn('vendor/tailwind.css', html, '404 页不应加载 tailwind（多 100KB+）')
        self.assertIn('href="./"', html, '404 页必须能返回首页（项目页相对路径为 ./）')
        self.assertIn('noindex', html, '404 页应标 noindex，避免被搜索引擎收录')


if __name__ == '__main__':
    unittest.main()
