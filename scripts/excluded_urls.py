"""全局排除名单的单一读取实现。

供 scraper / generate_frontend_data / server 三点共用，消除三份手抄
（scraper 用 rstrip 归一 + 支持 dict 格式 / generate & server 用精确匹配）
导致的行为分叉——尾斜杠差异或名单格式变化时，爬虫跳过但展示端不过滤，
被排除的非讲座就会静默回潮。
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_excluded():
    """读取全局排除名单 data/excluded_urls.json，返回归一化后的 URL 集合。

    同时支持两种格式：
      - JSON 数组: ["http://...", ...]
      - JSON 对象: {"urls": ["http://...", ...]}

    所有 URL 经 rstrip('/') 归一，确保尾斜杠差异不会导致排除遗漏。
    """
    p = os.path.join(ROOT, 'data', 'excluded_urls.json')
    if not os.path.exists(p):
        return set()
    try:
        with open(p, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return {str(u).rstrip('/') for u in raw}
        if isinstance(raw, dict) and 'urls' in raw:
            return {str(u).rstrip('/') for u in raw['urls']}
    except Exception:
        pass
    return set()
