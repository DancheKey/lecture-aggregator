"""全局排除名单的单一读取实现。

供scraper / generate_frontend_data / server 三点共用，消除三份手抄
（scraper 用 rstrip 归一 + 支持 dict 格式 / generate & server 用精确匹配）
导致的行为分叉——尾斜杠差异或名单格式变化时，爬虫跳过但展示端不过滤，
被排除的非讲座就会静默回潮。

---

⚠ 语义澄清（2026-10-11，评审后补，防止后人在此处分叉）：

名单与 `sources[]` 是**两种互补的正向证据，不可互相推导**：

  · 名单 = 正向**排除**证据 —— 「这个页面不该出现」，人工维护，
    判据是 `is_excluded(url)`，回答「要不要抓」。
  · `sources[]` = 正向**在库**证据 —— 「这个页面已被合并进某条记录」，
    由 `scraper.py:1975-1990` 在构造 existing_urls 时一并收录，
    判据是 `(canon_key, None) in existing_urls`，回答「抓过没有」。

因此：
  · `∉ sources[]` **推不出任何处置**（不在库也可能只是还没抓到），
    它不是跳过条件，别把它当判据用；
  · `∈ sources[]` **也不等于没被排除**（合并来源也可能被单独拉黑），
    真正要排除时仍须回名单判定 `is_record_excluded`。

增量轮的真实链路是两道**并行**闸，不是二选一：
  :1736 existing_urls 闸（在库，含 sources[]）→ :1739 台账闸
  → :1749 名单闸 → 才 fetch。
故「名单与在库 URL 有交集」并不构成额外浪费——交集当前实测为 0
（322 条名单 vs 3589 条在库 URL，2026-10-11 复测），且即便未来非空，
也是清洗端该处理的事，不要在抓取热路径上加预检。
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


def is_excluded(url, excluded):
    """判断单个 URL 是否在排除名单内（空值安全）。

    2026-10-02 新增：此前三个调用点各自写 `(r.get('sourceUrl') or '').rstrip('/') not in excluded`，
    语义一致但**都只看主 sourceUrl**——跨源合并记录（merged）另有 `sources[].sourceUrl`
    指向各来源页，其中任一被列入排除名单时，主URL 未被排除的合并记录照样上站。
    故把判断收敛到本函数，并新增 `is_record_excluded` 覆盖记录级语义。
    """
    return bool(url) and str(url).rstrip('/') in excluded


def record_urls(rec):
    """记录涉及的全部 URL：主 sourceUrl + 各合并来源的 sourceUrl。

    顺序稳定（主在前、来源按原序），便于测试断言。
    """
    out = []
    u = rec.get('sourceUrl')
    if u:
        out.append(str(u))
    for s in (rec.get('sources') or []):
        su = (s or {}).get('sourceUrl')
        if su and str(su) not in out:
            out.append(str(su))
    return out


def is_record_excluded(rec, excluded):
    """记录级排除判定：**任一**关联 URL（主 URL 或合并来源）命中名单即整条排除。

    语义选择「任一命中即排除」而非「全部命中」：名单是人工维护的「这个页面不该出现」
    清单——只要该讲座的某个来源页被判定为不该展示，整条合并记录就不该展示
    （前端点「多来源」仍能跳到那个已被否决的页面，等于排除失效）。
    """
    return any(is_excluded(u, excluded) for u in record_urls(rec))
