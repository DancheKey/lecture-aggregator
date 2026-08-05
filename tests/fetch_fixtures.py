#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 golden-case 回归测试所需的外部 HTML，固化为 tests/fixtures/html/*.html。

目的（2026-08-05 体检修复 严重-6）：让 golden 测试密封化——CI 不再直播抓取
外部不可控站点（非密封、耗时、易 flaky、失败即 skip 使门禁空转），
而是读仓库内的静态 fixture；抓取/解析失败应当红而非 skip。

用法：python tests/fetch_fixtures.py     # 刷新全部 fixture（需网络）
"""
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXDIR = os.path.join(ROOT, 'tests', 'fixtures', 'html')

# 与 test_parser_golden.py 的 CASES 一一对应；ctld4409 已下线(404)，无法固化，
# 测试侧保留 skip 语义并在用例中标注 may_404。
FIXTURES = [
    ('ctld4391.html', 'http://ctld.scnu.edu.cn/a/20241111/4391.html'),
    ('skc786.html', 'http://skc.scnu.edu.cn/a/20231116/786.html'),
    ('skc691.html', 'http://skc.scnu.edu.cn/a/20230323/691.html'),
    ('psy899.html', 'http://psy.scnu.edu.cn/a/20151201/899.html'),
    ('ctld4299.html', 'http://ctld.scnu.edu.cn/a/20240408/4299.html'),
    ('ctld4408.html', 'http://ctld.scnu.edu.cn/a/20250303/4408.html'),
    ('ctld4410.html', 'http://ctld.scnu.edu.cn/a/20250317/4410.html'),
    ('xz65.html', 'http://xz.scnu.edu.cn/a/20221026/65.html'),
    ('cs5708.html', 'http://cs.scnu.edu.cn/a/20240516/5708.html'),
    ('ggy5666.html', 'http://ggy.scnu.edu.cn/a/20211116/5666.html'),
    ('physics807.html', 'https://physics.scnu.edu.cn/a/20191118/807.html'),
]


def main():
    os.makedirs(FIXDIR, exist_ok=True)
    failed = []
    for name, url in FIXTURES:
        path = os.path.join(FIXDIR, name)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            with open(path, 'wb') as f:
                f.write(raw)
            print(f'[ok] {name} <- {url} ({len(raw)} bytes)')
        except Exception as e:
            failed.append(name)
            print(f'[FAIL] {name} <- {url}: {e}', file=sys.stderr)
    if failed:
        print(f'\n{len(failed)} 个 fixture 抓取失败：{", ".join(failed)}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
