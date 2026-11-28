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
from datetime import datetime, timezone

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

# 分享封面文件名。带 -2 后缀是有意的：换图时**必须换文件名**，否则微信/CDN
# 按旧 URL 返回缓存图（GitHub Pages 的 Cache-Control 是 600s，微信缓存更久）。
COVER_NAME = 'og-cover-2.jpg'
# 微信分享缩略图上限（JS-SDK 文档：imgUrl 不超过 32kb，抓取器沿用同一套限制）。
COVER_MAX_KB = 32


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


def _jpeg_sof_marker(path):
    """返回首个 SOF 段标记：0xC0 = 基线，0xC2 = 渐进。

    不依赖图像库——CI 上只有标准库，装 Pillow 会让门禁多一层环境依赖。
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
            return marker
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


def _opening_tags(html, name):
    """取真实起始标签；先去注释，避免把注释里的示例算进去。"""
    clean = re.sub(r'<!--.*?-->', '', html, flags=re.S)
    return re.findall(r'<%s\b[^>]*>' % re.escape(name), clean, flags=re.I)


class TestAccessibility(unittest.TestCase):
    """#20 无障碍修复的静态回归门禁（不替代人工键盘/读屏验收）。"""

    def test_三页都有可见跳转链接与主内容锚点(self):
        targets = {
            'index.html': 'main-content',
            'stats.html': 'stats-main',
            'visits-trend.html': 'trend-main',
        }
        for page, target in targets.items():
            html = _read(page)
            with self.subTest(page=page):
                self.assertRegex(
                    html,
                    r'<a\b(?=[^>]*class="skip-link")(?=[^>]*href="#%s")[^>]*>'
                    % re.escape(target),
                    f'{page} 缺 skip link 或 href 不对')
                self.assertRegex(
                    html,
                    r'<main\b(?=[^>]*id="%s")(?=[^>]*tabindex="-1")[^>]*>'
                    % re.escape(target),
                    f'{page} 缺可聚焦的 main 锚点')
        index_css = '\n'.join(
            re.findall(r'<style[^>]*>(.*?)</style>', _read('index.html'), re.S))
        stats_css = '\n'.join(
            re.findall(r'<style[^>]*>(.*?)</style>', _read('stats.html'), re.S))
        for css in (index_css, stats_css, _read('visits-trend.css')):
            self.assertIn('.skip-link:focus', css, 'skip link 聚焦时必须有可见样式')

    def test_三页都有常驻加载与结果状态区(self):
        for page in PAGES:
            html = _read(page)
            with self.subTest(page=page):
                self.assertIn('role="status"', html, f'{page} 缺常驻 status')
                self.assertIn('aria-live="polite"', html, f'{page} 状态区未启用 polite live')
                self.assertIn('aria-atomic="true"', html, f'{page} 状态区未整段播报')
        self.assertIn('resultsStatus()', _read('app.computed.js'))
        self.assertIn('liveStatus()', _read('stats.js'))
        self.assertIn("getElementById('trend-status')", _read('visits-trend.js'))

    def test_首页筛选菜单与搜索暴露选中态和名称(self):
        html = _read('index.html')
        self.assertGreaterEqual(html.count(':aria-pressed="campus === c"'), 2,
                                '桌面/移动校区筛选都要暴露 aria-pressed')
        for attr in (':aria-pressed="showLikedOnly"', ':aria-pressed="showWantedOnly"',
                     ':aria-pressed="ongoingOnly"', ':aria-pressed="upcomingOnly"'):
            self.assertIn(attr, html, f'首页缺 {attr}')
        self.assertGreaterEqual(html.count(':aria-pressed="hasWanted('), 2,
                                '普通卡与论坛卡的想听按钮都要暴露选中态')
        self.assertGreaterEqual(html.count(':aria-pressed="hasLiked('), 2,
                                '普通卡与论坛卡的点赞按钮都要暴露选中态')
        for needle in ("(hasCalAdded(l.ck || l.sourceUrl) ? '已加入日历：' : '加入日历：')",
                       "(hasGroupCalAdded(u) ? '整组已加入日历：' : '整组加入日历：')",
                       "(hasWanted(u.ck) ? '取消想听：' : '想听：')",
                       "(hasWanted(l.ck) ? '取消想听：' : '想听：')",
                       "(hasLiked(u.ck) ? '取消点赞：' : '点赞：')",
                       "(hasLiked(l.ck) ? '取消点赞：' : '点赞：')"):
            self.assertIn(needle, html, f'图标操作按钮缺动态 aria-label：{needle}')
        self.assertIn(':aria-expanded="showMenu"', html)
        self.assertIn('aria-controls="more-menu"', html)
        self.assertIn('id="more-menu"', html)
        self.assertIn('@keydown.esc="closeMenu(true)"', html)
        self.assertRegex(html, r'<input\b(?=[^>]*aria-label="搜索讲座")[^>]*>',
                         '搜索框缺可访问名称')
        self.assertIn('ref="menuButton"', html)

    def test_首页折叠控件与讲者按钮语义完整(self):
        html = _read('index.html')
        self.assertRegex(html, r'<button\b(?=[^>]*@click="pickSpeaker\(l\)")[^>]*>',
                         '讲者筛选仍是可点击 span/div，须用原生 button')
        self.assertIn('speaker-filter-btn', html)
        for needle in (
            ':aria-controls="\'forum-list-\' + u.key"',
            ':aria-controls="\'bio-\' + expKey(l, \'bio\')"',
            ':aria-controls="\'abstract-\' + expKey(l, \'abs\')"',
            ':id="\'sources-\' + l.ck"',
        ):
            self.assertIn(needle, html, f'首页折叠区缺 {needle}')
        self.assertIn('v-show="isForumOpen(u)"', html,
                      '折叠时 aria-controls 的目标必须仍存在于 DOM')
        self.assertIn('v-show="expanded[l.ck]"', html)

    def test_首页装饰图与报告弹窗(self):
        html = _read('index.html')
        self.assertGreaterEqual(len(re.findall(
            r'<img\b[^>]*src="motto\.webp(?:\?[^"]*)?"[^>]*alt=""', html)), 2,
                                '两侧校训水印必须作为装饰图使用空 alt')
        for svg in _opening_tags(html, 'svg'):
            self.assertIn('aria-hidden="true"', svg, '首页装饰 SVG 应对读屏隐藏')
            self.assertIn('focusable="false"', svg, '首页装饰 SVG 不应进入 Tab 顺序')
        dialog = next((tag for tag in _opening_tags(html, 'div')
                       if 'ref="reportModal"' in tag), '')
        for needle in ('role="dialog"', 'aria-modal="true"',
                       'aria-labelledby="src-modal-title"',
                       'aria-describedby="src-modal-desc"', '@keydown="onReportKeydown"'):
            self.assertIn(needle, dialog, f'报告弹窗缺 {needle}')
        self.assertIn('id="src-modal-title"', html)
        self.assertIn('id="src-modal-desc"', html)
        self.assertIn('ref="reportUnit"', html)

    def test_统计页表格与滚动区语义(self):
        html = _read('stats.html')
        self.assertRegex(html, r'<label\b(?=[^>]*for="college-filter")[^>]*>')
        self.assertRegex(html, r'<input\b(?=[^>]*id="college-filter")[^>]*>')
        regions = [tag for tag in _opening_tags(html, 'div') if 'scroll-region' in tag]
        self.assertGreaterEqual(len(regions), 3, '桌面表格与移动双滚动区都必须可键盘访问')
        self.assertEqual(sum('role="region"' in tag for tag in regions), 1)
        self.assertEqual(sum('role="rowgroup"' in tag for tag in regions), 2)
        for tag in regions:
            self.assertIn('tabindex="0"', tag)
            self.assertRegex(tag, r'role="(?:region|rowgroup)"')
            self.assertIn('aria-label=', tag)
        mobile_header = re.search(r'<div id="m-thead-scroll"[^>]*>(.*?)<!-- 数据独立横滑容器 -->',
                                  html, re.S).group(1)
        self.assertRegex(mobile_header,
                         r'<div[^>]*role="row"[^>]*>\s*<div[^>]*role="columnheader"',
                         '表头行必须直接拥有列标题，不能嵌 rowgroup')
        self.assertIn('class="flex" role="presentation"', mobile_header)
        self.assertGreaterEqual(len([tag for tag in _opening_tags(html, 'div')
                                     if 'role="rowheader"' in tag]), 2)
        self.assertGreaterEqual(len([tag for tag in _opening_tags(html, 'div')
                                     if 'role="cell"' in tag]), 8)
        self.assertIn('<caption', html, '统计表缺 caption')
        self.assertIn('<tfoot>', html, '合计行应放入 tfoot')
        self.assertGreaterEqual(len(_opening_tags(html, 'th')), 7)
        for th in _opening_tags(html, 'th'):
            self.assertRegex(th, r'scope="(?:col|row)"', f'表头缺 scope：{th}')
        self.assertGreaterEqual(len([tag for tag in _opening_tags(html, 'button')
                                     if 'sort-button' in tag]), 10,
                                '桌面与移动排序控件都应为原生 button')
        self.assertGreaterEqual(html.count(':aria-sort='), 5,
                                '桌面五个可排序列表头都要动态暴露 aria-sort')

    def test_趋势页模板与产物同步且交互状态可播报(self):
        import importlib.util
        path = os.path.join(_ROOT, 'scripts', 'gen_visits_trend.py')
        spec = importlib.util.spec_from_file_location('_trend_a11y_test', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(_read('visits-trend.js'), mod.JS, '趋势 JS 产物与生成器模板不同步')
        self.assertEqual(_read('visits-trend.css'), mod.CSS, '趋势 CSS 产物与生成器模板不同步')
        html = _read('visits-trend.html')
        ranges = [tag for tag in _opening_tags(html, 'button') if 'data-range=' in tag]
        self.assertEqual(len(ranges), 3)
        for tag in ranges:
            self.assertIn('aria-pressed=', tag)
        self.assertRegex(html, r'<div\b(?=[^>]*class="tbl scroll-region")'
                              r'(?=[^>]*tabindex="0")(?=[^>]*role="region")[^>]*>')
        self.assertIn('id="trend-status"', html)
        self.assertIn('<caption', html)
        ths = _opening_tags(html, 'th')
        self.assertEqual(len(ths), 3)
        for th in ths:
            self.assertIn('scope="col"', th)
        toggle = next((tag for tag in _opening_tags(html, 'button')
                       if 'id="toggleRows"' in tag), '')
        for needle in ('aria-expanded="false"', 'aria-controls="tbody"', 'aria-pressed="false"'):
            self.assertIn(needle, toggle)
        js = _read('visits-trend.js')
        for needle in ("setAttribute('aria-pressed'", "setAttribute('aria-expanded'",
                       "trendStatus('访问量数据加载失败"):
            self.assertIn(needle, js, f'趋势页交互/错误播报缺 {needle}')
        self.assertIn('role="alert"', html, '加载错误应有独立 alert 播报')

    def test_本轮修改资产保持LF(self):
        names = ('index.html', 'stats.html', 'visits-trend.html', 'visits-trend.js',
                 'visits-trend.css', 'stats.js', 'app.admin.js', 'app.computed.js')
        for name in names:
            with self.subTest(file=name):
                with open(os.path.join(_SITE, name), 'rb') as f:
                    raw = f.read()
                self.assertNotIn(b'\r\n', raw, f'site/{name} 混入 CRLF')


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
            self.assertEqual(img, base + COVER_NAME,
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

    def test_封面图是方形基线JPEG且小于32KB(self):
        """三条硬约束，每条都对应一次实测踩坑。

        ① **方形**：微信缩略图是方形取景框，4:1 横幅会被裁成中间一条。
        ② **< 32KB**：微信 JS-SDK 文档明写 imgUrl 不超过 32kb，抓取器沿用同一套
           限制。2026-10-20 实测 800×800 / 48KB 的表现是「有标题、没图」——
           标题来自客户端本地 DOM，图片要服务端下载，超限就被丢掉。
        ③ **基线 JPEG**：progressive JPEG 的 SOF 是 0xC2，部分老解码器不支持；
           基线是 0xC0。用段标记判断，不依赖图像库（CI 上没装 Pillow）。
        """
        path = os.path.join(_SITE, COVER_NAME)
        self.assertTrue(os.path.exists(path), f'site/{COVER_NAME} 不存在')
        w, h = _jpeg_size(path)
        self.assertEqual(w, h, f'封面应为方形（微信缩略图是方形取景框），实测 {w}x{h}')
        self.assertGreaterEqual(w, 600, f'封面边长至少 600px，实测 {w}')
        size_kb = os.path.getsize(path) / 1024
        self.assertLess(size_kb, COVER_MAX_KB,
                        f'封面必须小于 {COVER_MAX_KB}KB（微信上限），实测 {size_kb:.1f}KB')
        self.assertEqual(_jpeg_sof_marker(path), 0xC0,
                         '封面必须是基线 JPEG（SOF0）；渐进式（SOF2）微信不一定支持')
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


class TestFeeds(unittest.TestCase):
    """订阅源（2026-10-20 批次 B）：feed.xml（RSS 2.0）+ feed.ics（iCalendar）。

    两个文件都是每日重生成的产物，格式错一次就整站订阅失效，而浏览器里
    看不出任何异常——RSS 阅读器只会显示「无条目」，日历客户端只会拒绝导入。
    """

    # RFC 822 的星期/月份必须是固定的英文三字母缩写。用 strftime('%a %b') 取的是
    # **进程 locale**，在中文 locale 下会输出「二 10月」这种非法值——多数阅读器
    # 会直接丢弃整条 pubDate，条目按「无日期」排到最后。
    _DAY = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
    _MON = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')

    def _read(self, name):
        path = os.path.join(_SITE, name)
        self.assertTrue(os.path.exists(path), f'site/{name} 不存在')
        with open(path, encoding='utf-8', newline='') as f:
            return f.read()

    # ---------------------------------------------------------------- RSS
    def test_rss可被解析且条目结构完整(self):
        import xml.etree.ElementTree as ET
        xml = self._read('feed.xml')
        root = ET.fromstring(xml)           # 解析失败会直接抛异常 → 门禁变红
        self.assertEqual(root.tag, 'rss')
        self.assertEqual(root.get('version'), '2.0')
        chan = root.find('channel')
        self.assertIsNotNone(chan, 'feed.xml 缺 channel')
        for key in ('title', 'link', 'description'):
            node = chan.find(key)
            self.assertIsNotNone(node, f'channel 缺 {key}')
            self.assertTrue((node.text or '').strip(), f'channel/{key} 为空')
        items = chan.findall('item')
        self.assertGreater(len(items), 0, 'feed.xml 没有任何 item')
        base = _site_base_url()
        for it in items:
            for key in ('title', 'link', 'guid', 'pubDate'):
                node = it.find(key)
                self.assertIsNotNone(node, f'item 缺 {key}')
                self.assertTrue((node.text or '').strip(), f'item/{key} 为空')
            # link 有意指向**原始来源页**而非本站：订阅者点开一条讲座，想看的是
            # 学院原公告（含海报、报名方式、可能改期后的最新说明），本站只是索引。
            # 因此这里不能断言等于站点 base，但必须断言是绝对 URL——RSS 阅读器
            # 不解析相对路径，相对 link 会让整条显示为不可点击。
            link = it.find('link').text
            self.assertTrue(link.startswith(('http://', 'https://')),
                            f'item link 必须是绝对 URL（RSS 阅读器不解析相对路径），实际 {link}')
        # channel/link 才是本站入口，必须是站点绝对地址
        self.assertEqual(chan.find('link').text, base,
                         'channel/link 应指向站点首页')
        # 自引用 atom:link 让阅读器/聚合服务知道 feed 的真实地址
        atom = chan.find('{http://www.w3.org/2005/Atom}link')
        self.assertIsNotNone(atom, 'channel 缺 atom:link 自引用（订阅器靠它发现更新）')
        self.assertEqual(atom.get('href'), base + 'feed.xml')

    def test_rss日期用英文缩写而非本地化名称(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self._read('feed.xml'))
        stamps = [it.find('pubDate').text for it in root.find('channel').findall('item')]
        self.assertGreater(len(stamps), 0, '没有可校验的 pubDate')
        pat = re.compile(r'^(%s), (\d{2}) (%s) (\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-]\d{4})$'
                         % ('|'.join(self._DAY), '|'.join(self._MON)))
        for s in stamps:
            m = pat.match(s)
            self.assertIsNotNone(m, f'pubDate 不是合法 RFC 822（疑似用了本地化 strftime）：{s}')
            self.assertIn(m.group(8), ('+0800', '+0000'),
                          f'pubDate 时区必须是 +0800 或 +0000，实际 {s}')

    # ---------------------------------------------------------------- ICS
    def test_ics结构完整且行宽不超过75字节(self):
        raw = self._read('feed.ics')
        # RFC 5545 §3.1：内容行必须以 CRLF 分隔
        self.assertIn('\r\n', raw, 'feed.ics 必须用 CRLF 换行（RFC 5545）')
        bare_lf = raw.replace('\r\n', '').count('\n')
        self.assertEqual(bare_lf, 0, 'feed.ics 混入了裸 LF 换行')
        self.assertTrue(raw.startswith('BEGIN:VCALENDAR\r\n'), 'feed.ics 必须以 BEGIN:VCALENDAR 开头')
        self.assertTrue(raw.rstrip('\r\n').endswith('END:VCALENDAR'), 'feed.ics 必须以 END:VCALENDAR 结尾')
        # 75 字节是**字节**不是字符：中文标题按字符折行会超限，
        # Outlook/Apple 日历会判文件损坏而整份拒绝导入。
        for i, line in enumerate(raw.split('\r\n'), 1):
            self.assertLessEqual(len(line.encode('utf-8')), 75,
                                 f'feed.ics 第 {i} 行 {len(line.encode("utf-8"))} 字节，超过 RFC 5545 的 75 字节上限')
        n_begin = raw.count('BEGIN:VEVENT')
        n_end = raw.count('END:VEVENT')
        self.assertEqual(n_begin, n_end, f'VEVENT 未配平：BEGIN={n_begin} END={n_end}')
        self.assertGreater(n_begin, 0, 'feed.ics 没有任何 VEVENT')

    def test_ics时间是UTC且带结束时间(self):
        raw = self._read('feed.ics')
        # DTSTART 必须带 Z 后缀（UTC）。不带 Z 会被按「本地时间」解释，
        # 而记录里是北京时间字面量 → 境外用户导入后整体偏移数小时。
        starts = re.findall(r'DTSTART:(\S+)', raw)
        self.assertGreater(len(starts), 0, 'feed.ics 没有 DTSTART')
        pat = re.compile(r'^\d{8}T\d{6}Z$')
        for s in starts:
            self.assertIsNotNone(pat.match(s), f'DTSTART 必须是 UTC 形式 YYYYMMDDTHHMMSSZ，实际 {s}')
        self.assertEqual(len(re.findall(r'DTEND:\S+', raw)), len(starts),
                         '每个 VEVENT 都必须有 DTEND（缺结束时间会让部分客户端把日程画成 0 分钟）')

    def test_标题不与题目重复(self):
        """title 已内嵌题目时不能再追加 topic。

        全库 483 条（12.6%）的 title 本身就包含 topic（「学术讲座：早期胚胎发育…」
        「黄中杰——Spinning Mellin Bootstrap」），旧逻辑只判 `topic != title`
        会拼成「…题目｜题目」——RSS 阅读器和日历里都是明显的重复，
        且随每日新增持续出现。这里锁住三条分支：已包含→不拼、真不同→拼、空值→兜底。
        """
        import importlib.util
        path = os.path.join(_ROOT, 'scripts', 'generate_frontend_data.py')
        spec = importlib.util.spec_from_file_location('_gfd_for_test', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cases = [
            ({'title': '学术讲座：早期胚胎发育和体轴建立',
              'topic': '早期胚胎发育和体轴建立'},
             '学术讲座：早期胚胎发育和体轴建立'),
            ({'title': '黄中杰——Spinning Mellin Bootstrap',
              'topic': 'Spinning Mellin Bootstrap'},
             '黄中杰——Spinning Mellin Bootstrap'),
            ({'title': '系列讲座第3讲', 'topic': '人工智能前沿'},
             '系列讲座第3讲｜人工智能前沿'),
            ({'title': '完全相同的标题', 'topic': '完全相同的标题'}, '完全相同的标题'),
            ({'title': '', 'topic': '只有题目'}, '只有题目'),
            ({}, '学术讲座'),
        ]
        for rec, want in cases:
            self.assertEqual(mod._feed_title(rec), want,
                             f'_feed_title({rec}) 应为 {want!r}')

    def test_ics的DTSTAMP取自数据更新时间而非生成时刻(self):
        """DTSTAMP 若用 datetime.now()，每次生成都会改动 feed.ics 的字节。

        后果有两层：订阅端据此认为事件被修改（部分客户端会触发不必要的刷新），
        以及每次生成（每日 CI + 任何手动重跑）都产生无意义的 git diff。
        这里用「DTSTAMP 必须等于 RSS 的 lastBuildDate」来锁——两者都取自
        data/lectures.json 的 updatedAt，一旦有人改回 now()，这条立刻变红。
        """
        import xml.etree.ElementTree as ET
        from email.utils import parsedate_to_datetime

        ics = self._read('feed.ics')
        m = re.search(r'DTSTAMP:(\d{8}T\d{6}Z)', ics)
        self.assertIsNotNone(m, 'feed.ics 缺 DTSTAMP')
        dt_stamp = datetime.strptime(m.group(1), '%Y%m%dT%H%M%SZ').replace(
            tzinfo=timezone.utc)

        root = ET.fromstring(self._read('feed.xml'))
        lbd = root.find('channel').find('lastBuildDate').text
        dt_build = parsedate_to_datetime(lbd).astimezone(timezone.utc)

        self.assertEqual(
            dt_stamp, dt_build,
            'DTSTAMP 应等于 RSS 的 lastBuildDate（两者都取自 updatedAt）；'
            f'实际 DTSTAMP={dt_stamp} / lastBuildDate={dt_build}，'
            '不等说明有一方用了生成时刻 now()')

    @staticmethod
    def _generator():
        """按路径加载生成器（CI 纯净检出同样可用；不写任何文件）。"""
        import importlib.util
        path = os.path.join(_ROOT, 'scripts', 'generate_frontend_data.py')
        spec = importlib.util.spec_from_file_location('_gfd_for_feeds_test', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_xml与ics转义保留合法换行并移除非法控制字符(self):
        mod = self._generator()
        self.assertEqual(
            mod._xml_esc('A\x00B\tC\nD\rE\x0bF\x1fG\ufffe\uffff\ud800<&'),
            'AB\tC\nD\rEFG&lt;&amp;')
        self.assertEqual(
            mod._ics_esc('line1\r\nline2\nline3\r'),
            'line1\\nline2\\nline3\\n')
        self.assertEqual(
            mod._ics_esc('a;b,c\\d\x00\x0b\x7f\ud800'),
            'a\\;b\\,c\\\\d')
        self.assertEqual(mod._ics_esc('t\tab'), 't\tab')

    def test_空订阅源覆盖旧文件且格式合法(self):
        import tempfile
        import xml.etree.ElementTree as ET
        from unittest import mock

        mod = self._generator()
        with tempfile.TemporaryDirectory(prefix='test_empty_feeds_') as tmp:
            site = os.path.join(tmp, 'site')
            os.makedirs(site)
            for name in ('feed.xml', 'feed.ics'):
                with open(os.path.join(site, name), 'w', encoding='utf-8') as f:
                    f.write('STALE')
            with mock.patch.object(mod, 'ROOT', tmp):
                mod.write_feeds([], '2026-11-26T08:00:00+08:00')
            with open(os.path.join(site, 'feed.xml'), encoding='utf-8') as f:
                root = ET.fromstring(f.read())
            self.assertEqual(root.tag, 'rss')
            self.assertEqual(root.find('channel').findall('item'), [])
            with open(os.path.join(site, 'feed.ics'), encoding='utf-8', newline='') as f:
                ics = f.read()
            self.assertTrue(ics.startswith('BEGIN:VCALENDAR\r\n'))
            self.assertTrue(ics.rstrip('\r\n').endswith('END:VCALENDAR'))
            self.assertNotIn('BEGIN:VEVENT', ics)
            self.assertNotIn('STALE', ics)

    def test_main空数据仍清空订阅源(self):
        from unittest import mock

        mod = self._generator()
        with mock.patch.object(mod, 'load_lectures', return_value=([], '2026-11-26T08:00:00+08:00')), \
                mock.patch.object(mod, 'write_visits_snapshot'), \
                mock.patch.object(mod, 'warn_uncommitted_scripts'), \
                mock.patch.object(mod, 'warn_stale_stamp_inputs'), \
                mock.patch.object(mod, 'write_feeds') as feeds:
            mod.main()
        feeds.assert_called_once_with([], '2026-11-26T08:00:00+08:00')

    def test_main读取失败时中止而不伪造空订阅源(self):
        from unittest import mock

        mod = self._generator()
        with mock.patch.object(mod, 'write_visits_snapshot'), \
                mock.patch.object(mod, 'warn_uncommitted_scripts'), \
                mock.patch.object(mod, 'warn_stale_stamp_inputs'), \
                mock.patch.object(mod, 'load_lectures', side_effect=ValueError('bad json')), \
                mock.patch.object(mod, 'write_feeds') as feeds:
            with self.assertRaises(ValueError):
                mod.main()
        feeds.assert_not_called()

    def test_main把派生数据交给订阅源(self):
        """评审 #10 行为锁：main() 交给 write_feeds 的必须是**已派生 ck** 的 full_data。

        原先传 data（未派生）时，同页多场的 N 条订阅项 guid / ICS UID 都回退成
        同一个 sourceUrl，日历客户端按相同 UID 互相覆盖；这类错零报错、只有订阅
        端才看得见。这里不靠「源码字符串长什么样」，而是桩掉所有落盘动作后真跑
        一次 main()，把它实际交出去的记录接住验证。

        同时锁住 GLM 复核提的过滤口径：换传参后 feed 条数与逐条 title/desc/link
        必须与「传 raw data」一致（with_unit 是 1:1 复制、排除名单在派生前过滤）。
        ⚠ pubDate 不在此列：#11 已把回退改成按 ck 稳定散开，它本来就该变。
        """
        import xml.etree.ElementTree as ET
        from datetime import timedelta
        from unittest import mock

        mod = self._generator()
        now = datetime.now(mod.CST)
        start = (now + timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
        end = (now + timedelta(days=2, hours=2)).strftime('%Y-%m-%d %H:%M:%S')
        url_multi = 'https://maths.scnu.edu.cn/a/20261106/1.html'
        url_one = 'https://maths.scnu.edu.cn/a/20261106/2.html'
        rows = [
            {'sourceUrl': url_multi, 'lectureIndex': 1, 'title': '系列讲座第一场',
             'topic': '第一场题目', 'lectureStart': start, 'lectureEnd': end},
            {'sourceUrl': url_multi, 'lectureIndex': 2, 'title': '系列讲座第二场',
             'topic': '第二场题目', 'lectureStart': start, 'lectureEnd': end},
            {'sourceUrl': url_one, 'title': '独立一场',
             'topic': '独立题目', 'lectureStart': start, 'lectureEnd': end},
        ]

        captured, raw_cap, delivered = {}, {}, {}

        def spy(data, updated_at):
            delivered['data'] = data
            return orig_feeds(data, updated_at)

        def sink(target):
            def _write(path, text):
                target[os.path.basename(path)] = text
            return _write

        orig_feeds = mod.write_feeds
        noop = lambda *a, **k: None
        with mock.patch.object(mod, 'warn_uncommitted_scripts', noop), \
             mock.patch.object(mod, 'warn_stale_stamp_inputs', noop), \
             mock.patch.object(mod, 'write_visits_snapshot', noop), \
             mock.patch.object(mod, 'load_lectures', lambda: (rows, '2026-11-06T08:00:00+08:00')), \
             mock.patch.object(mod, 'load_excluded', lambda: set()), \
             mock.patch.object(mod, 'atomic_write_json', noop), \
             mock.patch.object(mod, 'atomic_write_text', sink(captured)), \
             mock.patch.object(mod, 'write_chunks', noop), \
             mock.patch.object(mod, 'write_detail_chunks', noop), \
             mock.patch.object(mod, 'stamp_script_version', noop), \
             mock.patch.object(mod, 'write_sitemap', noop), \
             mock.patch.object(mod, 'write_feeds', spy):
            mod.main()

        data = delivered.get('data')
        self.assertIsNotNone(data, 'main() 没有调用 write_feeds')
        self.assertEqual(len(data), len(rows), '派生链不得增删条目（feed 过滤口径会跟着变）')
        self.assertTrue(all(r.get('ck') for r in data),
                        'write_feeds 收到的记录没有 ck —— 又被传成未派生的 data 了')
        self.assertEqual(len({r['ck'] for r in data}), len(rows),
                         '同页多场的 ck 必须各自唯一，否则 guid/UID 仍会撞车')

        xml, ics = captured.get('feed.xml'), captured.get('feed.ics')
        self.assertIsNotNone(xml, '未产出 feed.xml')
        self.assertIsNotNone(ics, '未产出 feed.ics')
        guids = re.findall(r'<guid[^>]*>([^<]+)</guid>', xml)
        uids = re.findall(r'UID:([^\r\n]+)', ics)
        self.assertEqual(len(guids), len(rows), 'feed.xml 条目数应与可订阅讲座数一致')
        self.assertEqual(len(set(guids)), len(rows), 'RSS guid 有重复，阅读器会合并条目')
        self.assertEqual(len(set(uids)), len(rows), 'ICS UID 有重复，日历客户端会互相覆盖')

        # 与「传 raw data」对比：条数、逐条 title/description/link 必须逐字一致。
        with mock.patch.object(mod, 'atomic_write_text', sink(raw_cap)):
            orig_feeds(rows, '2026-11-06T08:00:00+08:00')
        before = ET.fromstring(raw_cap['feed.xml']).find('channel').findall('item')
        after = ET.fromstring(xml).find('channel').findall('item')
        self.assertEqual(len(before), len(after), '换传参后 feed 条数变了（派生链可能增删记录）')
        for i, (a, b) in enumerate(zip(before, after)):
            for key in ('title', 'link', 'description'):
                self.assertEqual(a.find(key).text, b.find(key).text,
                                 f'第 {i} 条 {key} 因换传参而改变')

    def test_pubDate回退按ck稳定散开(self):
        """评审 #11：publishTime 缺失/晚于 built 时不得全部塌缩成同一个时刻。

        三条硬约束：≤ built（RSS 不许有未来时间）、同一 ck 每次结果相同（数据不变
        产物字节就不变）、不同 ck（含一页多场的 ~序号）能散开可供阅读器排序。
        """
        from datetime import timedelta
        mod = self._generator()
        built = mod._parse_cst('2026-11-06 08:00:00')
        self.assertIsNotNone(built)
        recs = [{'ck': f'https://x.scnu.edu.cn/a/{i}.html', 'title': f'讲座{i}'}
                for i in range(15)]
        dts = [mod._feed_pub_dt(r, built) for r in recs]
        self.assertTrue(all(d <= built for d in dts), 'pubDate 不得晚于 lastBuildDate')
        self.assertGreaterEqual(len(set(dts)), 12,
                                '15 条回退时间仍几乎全相同，阅读器排不了序')
        self.assertEqual(dts, [mod._feed_pub_dt(r, built) for r in recs],
                         '同一记录两次生成必须得到同一 pubDate（禁止内置 hash 随机化）')
        self.assertEqual(
            mod._feed_pub_dt({'ck': 'u~1', 'sourceUrl': 'u'}, built),
            mod._feed_pub_dt({'ck': 'u~1', 'sourceUrl': 'u'}, built))
        self.assertNotEqual(
            mod._feed_pub_dt({'ck': 'u~1', 'sourceUrl': 'u'}, built),
            mod._feed_pub_dt({'ck': 'u~2', 'sourceUrl': 'u'}, built),
            '同一源页拆出的两场也应拿到不同 pubDate')
        self.assertLessEqual(
            mod._feed_pub_dt({'ck': 'u~f', 'publishTime': '2027-01-01 10:00:00'}, built),
            built, '晚于 built 的源站时间必须回退，不能让 feed 出现未来时间')
        good = {'ck': 'u~g', 'publishTime': '2026-11-01 09:00:00'}
        self.assertEqual(mod._feed_pub_dt(good, built),
                         mod._parse_cst('2026-11-01 09:00:00'),
                         '合法且早于 built 的源站时间必须原样保留')
        self.assertGreater(built - max(dts), timedelta(0), '回退偏移不得为 0 或负')

    def test_订阅源由生成器产出(self):
        import inspect
        mod = self._generator()
        self.assertTrue(hasattr(mod, 'write_feeds'), 'generate_frontend_data.py 缺 write_feeds')
        # 只在 main() 函数体内查（自带信息边界），别对整份源码做 assertIn。
        main_src = inspect.getsource(mod.main)
        self.assertIn('write_feeds(full_data, updated_at)', main_src,
                      'main() 里订阅源必须取派生后的 full_data')

    def test_ics在gitattributes里豁免换行归一(self):
        """`.gitattributes` 的 `site/** text eol=lf` 会把 feed.ics 的 CRLF 洗成 LF。

        RFC 5545 强制 CRLF，且这个转换**只在入库/检出时发生**——本地文件看着
        好好的（CRLF），CI checkout 出来是 LF，上面那条行宽/换行断言才会突然变红，
        而本地复现不出来。必须在 .gitattributes 里显式 `-text` 豁免。
        """
        with open(os.path.join(_ROOT, '.gitattributes'), encoding='utf-8') as f:
            lines = [ln.strip() for ln in f]
        hit = [ln for ln in lines
               if ln and not ln.startswith('#') and ln.split()[:2] == ['site/feed.ics', '-text']]
        self.assertTrue(hit,
                        '.gitattributes 缺少 `site/feed.ics -text`：CRLF 会被 '
                        '`site/** text eol=lf` 归一成 LF，违反 RFC 5545')


if __name__ == '__main__':
    unittest.main()
