"""华师讲座聚合 —— 本地演示服务器（方案 A 增强版）。

- 静态托管 site/（所有响应禁用缓存，刷新即见最新）
- GET  /api/lectures?since=<mtime>  读取最新 data/lectures.json；若文件未变则返回空数组
- POST /api/scrape    以子进程触发采集器重新抓取，返回最新条数与文件时间戳
- GET    /api/sources          返回信息源列表（来自 scraper/sources.yaml）
- POST   /api/sources          新增信息源
- PUT    /api/sources/<index>  更新指定信息源
- DELETE /api/sources/<index>  删除指定信息源

运行：python server.py  （默认端口 8000，可用 PORT 环境变量覆盖）
"""
import os
import re
import sys
import json
import time
import threading
import subprocess
import yaml
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(ROOT, 'site')
DATA_DIR = os.path.join(ROOT, 'data')
SCRAPER = os.path.join(ROOT, 'scraper', 'scraper.py')
SOURCES_PATH = os.path.join(ROOT, 'scraper', 'sources.yaml')

VISITS_PATH = os.path.join(DATA_DIR, 'visits.json')          # 站点访问量：{"total": N}
LECTURE_STATS_PATH = os.path.join(DATA_DIR, 'lecture_stats.json')  # 每条讲座的访问/点赞/想听：{url:{visits,likes,wants}}

_scrape_lock = threading.Lock()
_stat_lock = threading.Lock()

# ---- 访问量 / 点赞统计的运行时状态（文件持久化 + 内存防刷窗口） ----
_site_visits = {'total': 0}            # 站点总访问量
_lecture_stats = {}                     # url -> {"visits": N, "likes": M, "wants": W}
_recent_site_ip = {}                   # ip -> 最近一次计数的时间戳（站点访问防刷）
_recent_lecture = {}                   # (ip, url) -> 时间戳（单讲座访问防刷）
_recent_like_action = {}               # (ip, url) -> (时间戳, 'like'|'unlike')（点赞防刷，区分动作）
_recent_want_action = {}               # (ip, url) -> (时间戳, 'want'|'unwant')（想听防刷，区分动作）
VISIT_THROTTLE = 180                   # 同一 IP / 同一讲座 3 分钟内只计 1 次
LIKE_THROTTLE = 3                      # 同一 IP / 同一讲座 3 秒内相同点赞动作只接受一次（允许 like↔unlike 交替）
WANT_THROTTLE = 3                      # 同一 IP / 同一讲座 3 秒内相同想听动作只接受一次（允许 want↔unwant 交替）


def _load_excluded():
    """读取全局排除名单 data/excluded_urls.json。

    该名单既被爬虫用于增量抓取时跳过，也必须在展示端过滤——凡是列入的 URL
    不应出现在聚合页面 / 统计中（否则 excluded 形同虚设，非讲座会反复回潮）。
    """
    p = os.path.join(DATA_DIR, 'excluded_urls.json')
    try:
        with open(p, 'r', encoding='utf-8') as f:
            lst = json.load(f)
        return set(lst) if isinstance(lst, list) else set()
    except Exception:
        return set()


def _attach_unit_types(data):
    """为单页多讲座拆分记录标注 unitType（场/期），逻辑须与 scripts/generate_frontend_data.py
    的 with_unit() 严格一致，确保本地开发服务器下发的 /api/lectures 与公网静态切片行为相同：

    - 同一 sourceUrl 组内所有讲座日期相同（同一天多场次）-> 'session'（第x场）
    - 跨了不同日期（系列讲座分期）-> 'issue'（第x期）

    仅对含 lectureIndex 的记录附加该字段，其余原样透传，不污染主数据。
    """
    url_dates = {}
    for item in data:
        u = item.get('sourceUrl') or ''
        d = (item.get('lectureStart') or '')[:10]
        url_dates.setdefault(u, set())
        if d:
            url_dates[u].add(d)
    out = []
    for item in data:
        if item.get('lectureIndex') is not None:
            dates = url_dates.get(item.get('sourceUrl') or '', set())
            it = dict(item)
            it['unitType'] = 'session' if len(dates) == 1 else 'issue'
            out.append(it)
        else:
            out.append(item)
    return out


def _load_stat_files():
    """启动时把磁盘上的统计状态读入内存（若不存在则用默认值）。"""
    global _site_visits, _lecture_stats
    try:
        if os.path.exists(VISITS_PATH):
            _site_visits = json.load(open(VISITS_PATH, encoding='utf-8')) or {'total': 0}
    except Exception:
        _site_visits = {'total': 0}
    # 兼容旧格式（仅有 total，无 by_day 按日明细）；旧值仍保留为「历史遗留总数」
    if not isinstance(_site_visits.get('by_day'), dict):
        _site_visits['by_day'] = {}
    try:
        if os.path.exists(LECTURE_STATS_PATH):
            _lecture_stats = json.load(open(LECTURE_STATS_PATH, encoding='utf-8')) or {}
    except Exception:
        _lecture_stats = {}


def _atomic_write_json(path, obj):
    """原子写 JSON：先写 .tmp 再 os.replace，避免中途崩溃留下半份文件。"""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _save_visits():
    try:
        _atomic_write_json(VISITS_PATH, _site_visits)
    except Exception:
        pass


def _save_lecture_stats():
    try:
        _atomic_write_json(LECTURE_STATS_PATH, _lecture_stats)
    except Exception:
        pass


# 讲座 sourceUrl 白名单（按 lectures.json mtime 缓存）：写统计接口仅接受已知讲座，
# 防止任意 url 撑大 _lecture_stats / 伪造访问与点赞。
_lecture_urls_cache = {'mtime': 0.0, 'urls': frozenset()}


def _known_lecture_urls():
    path = os.path.join(DATA_DIR, 'lectures.json')
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return _lecture_urls_cache['urls']
    if mt != _lecture_urls_cache['mtime']:
        urls = set()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            rows = raw.get('data', []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
            for r in rows:
                u = r.get('sourceUrl')
                if u:
                    urls.add(str(u).rstrip('/'))
        except Exception:
            pass
        _lecture_urls_cache['mtime'] = mt
        _lecture_urls_cache['urls'] = frozenset(urls)
    return _lecture_urls_cache['urls']


_load_stat_files()


def _find_scraper_python():
    """选择一个能 import 爬虫依赖（requests/bs4）的 Python 解释器。

    server.py 自身可能用没装这些依赖的解释器启动（例如某些环境默认的 3.13），
    直接用它跑 scraper 会 ImportError -> 抓取失败。这里按可移植的顺序自动探测：
      1) 环境变量 SCRAPER_PYTHON（显式指定，便于在不同机器 / CI 部署）
      2) 当前解释器 sys.executable
      3) PATH 中的 python3 / python
    不再硬编码本机绝对路径，避免换环境即崩溃。
    """
    candidates = []
    env_py = os.environ.get('SCRAPER_PYTHON')
    if env_py:
        candidates.append(env_py)
    candidates.append(sys.executable)
    try:
        import shutil
        for w in ('python3', 'python'):
            p = shutil.which(w)
            if p:
                candidates.append(p)
    except Exception:
        pass
    seen = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        try:
            out = subprocess.run(
                [c, '-c', 'import requests, bs4'],
                capture_output=True, text=True, timeout=30,
            )
            if out.returncode == 0:
                return c
        except Exception:
            continue
    return sys.executable  # 兜底：实在找不到就沿用当前解释器（会如实报错）


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SITE_DIR, **kwargs)

    def end_headers(self):
        # 禁用缓存：每次刷新都拿到最新数据
        self.send_header('Cache-Control', 'no-store')
        # gzip 协商：若浏览器声明支持，则对响应体做 gzip 压缩
        if getattr(self, '_gz', False):
            self.send_header('Content-Encoding', 'gzip')
        super().end_headers()

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        # 协商 gzip：仅当客户端声明支持时压缩，否则原样发送（兼容简易客户端）
        accept = self.headers.get('Accept-Encoding', '') or ''
        if 'gzip' in accept.lower() and len(body) > 1024:
            import gzip as _gzip
            body = _gzip.compress(body, 6)
            self._gz = True
        else:
            self._gz = False
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- 信息源 CRUD ----

    def _load_sources(self):
        if not os.path.exists(SOURCES_PATH):
            return {'sources': []}
        with open(SOURCES_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {'sources': []}

    def _save_sources(self, data):
        tmp = SOURCES_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        os.replace(tmp, SOURCES_PATH)

    def _api_sources_get(self):
        data = self._load_sources()
        self._send_json({'ok': True, 'sources': data.get('sources', [])})

    def _api_sources_post(self):
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._send_json({'ok': False, 'message': '无效的 JSON'}, 400)
            return
        name = (body.get('name') or '').strip()
        campus = (body.get('campus') or '').strip()
        base = (body.get('base') or '').strip()
        list_urls = body.get('list_urls') or []
        if not name or not base:
            self._send_json({'ok': False, 'message': 'name 和 base 为必填项'}, 400)
            return
        data = self._load_sources()
        new_src = {'name': name, 'campus': campus or '', 'base': base, 'list_urls': list_urls}
        data['sources'].append(new_src)
        self._save_sources(data)
        self._send_json({'ok': True, 'index': len(data['sources']) - 1, 'source': new_src})

    def _api_sources_put(self, idx):
        data = self._load_sources()
        if idx < 0 or idx >= len(data['sources']):
            self._send_json({'ok': False, 'message': f'索引 {idx} 超出范围（共 {len(data["sources"])} 条）'}, 404)
            return
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._send_json({'ok': False, 'message': '无效的 JSON'}, 400)
            return
        src = data['sources'][idx]
        for k in ('name', 'campus', 'base', 'list_urls'):
            if k in body:
                src[k] = body[k]
        self._save_sources(data)
        self._send_json({'ok': True, 'source': src})

    def _api_sources_delete(self, idx):
        data = self._load_sources()
        if idx < 0 or idx >= len(data['sources']):
            self._send_json({'ok': False, 'message': f'索引 {idx} 超出范围（共 {len(data["sources"])} 条）'}, 404)
            return
        removed = data['sources'].pop(idx)
        self._save_sources(data)
        self._send_json({'ok': True, 'removed': removed})

    def _match_sources_index(self, path):
        """从 /api/sources/3 之类的路径中提取整数索引；不匹配返回 None。"""
        if path == '/api/sources':
            return -1  # 集合端点，非单条
        if path.startswith('/api/sources/'):
            try:
                return int(path[len('/api/sources/'):])
            except ValueError:
                return None
        return None

    def _client_ip(self):
        """客户端 IP。本机直连无反代，直接用连接地址；不信任可由客户端伪造的 X-Forwarded-For。"""
        return self.client_address[0]

    def _is_local_origin(self):
        """写接口 CSRF 防护：Origin/Referer 缺失放行（curl/本机脚本）；
        存在时必须指向 127.0.0.1/localhost，拒绝跨站表单/fetch 触发本机写接口。"""
        for h in ('Origin', 'Referer'):
            v = (self.headers.get(h) or '').strip()
            if not v:
                continue
            if re.match(r'^https?://(127\.0\.0\.1|localhost)(:\d+)?(/|$)', v, re.I):
                return True
            return False
        return True

    # ---- 访问量 / 点赞统计 ----

    def _api_visits_get(self):
        """站点总访问量与按日明细：同一 IP 3 分钟内重复刷新只计 1 次。

        返回 {"ok": true, "total": N, "by_day": {"YYYY-MM-DD": count, ...}}。
        by_day 按本地日期累计，供生成「每年每月访问量」报告；
        完全本地（data/visits.json），不依赖任何外部计数服务（busuanzi / countapi 等）。
        """
        ip = self._client_ip()
        now = time.time()
        # 2026-08-05 体检修正（中等-16）：锁内只改状态，锁外发响应。
        # 此前在 with _stat_lock 内直接 _send_json，慢客户端写响应期间
        # 会持锁阻塞所有其它统计请求。
        with _stat_lock:
            last = _recent_site_ip.get(ip, 0)
            if now - last >= VISIT_THROTTLE:
                _site_visits['total'] = _site_visits.get('total', 0) + 1
                today = time.strftime('%Y-%m-%d', time.localtime(now))
                bd = _site_visits.setdefault('by_day', {})
                bd[today] = bd.get(today, 0) + 1
                _recent_site_ip[ip] = now
                _save_visits()
            payload = {'ok': True, 'total': _site_visits.get('total', 0), 'by_day': dict(_site_visits.get('by_day', {}))}
        return self._send_json(payload)

    def _api_lecture_stats_get(self):
        """返回每条讲座的访问/点赞统计：{url: {visits, likes}}。"""
        # 锁内浅拷贝快照、锁外发响应（同 中等-16 修正；避免慢客户端持锁）。
        with _stat_lock:
            snapshot = {u: dict(st) for u, st in _lecture_stats.items()}
        return self._send_json({'ok': True, 'stats': snapshot})

    def _read_body_json(self):
        length = int(self.headers.get('Content-Length', 0) or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            return {}

    def _api_lecture_visit_post(self):
        """记录一次讲座访问：同一 (IP, url) 3 分钟内只计 1 次。"""
        body = self._read_body_json()
        url = (body.get('url') or '').strip()
        if not url:
            return self._send_json({'ok': False, 'message': 'url 必填'}, 400)
        if url.rstrip('/') not in _known_lecture_urls():
            return self._send_json({'ok': False, 'message': '未知讲座'}, 400)
        ip = self._client_ip()
        now = time.time()
        with _stat_lock:
            key = (ip, url)
            last = _recent_lecture.get(key, 0)
            if now - last >= VISIT_THROTTLE:
                st = _lecture_stats.setdefault(url, {'visits': 0, 'likes': 0})
                st['visits'] = st.get('visits', 0) + 1
                _recent_lecture[key] = now
                _save_lecture_stats()
            cur = _lecture_stats.get(url, {'visits': 0, 'likes': 0})
            payload = {'ok': True, 'visits': cur.get('visits', 0)}
        return self._send_json(payload)  # 锁外发响应（中等-16）

    def _api_lecture_like_post(self):
        """记录一次点赞：前端已做本机 toggle（奇数次赞、偶数次取消）。

        防刷：同一 IP 对同一讲座在 LIKE_THROTTLE 秒内重复「点赞」动作只计一次，
        防止脚本无限刷赞；允许 like↔unlike 交替（即正常用户切换点赞状态）。
        """
        body = self._read_body_json()
        url = (body.get('url') or '').strip()
        if not url:
            return self._send_json({'ok': False, 'message': 'url 必填'}, 400)
        if url.rstrip('/') not in _known_lecture_urls():
            return self._send_json({'ok': False, 'message': '未知讲座'}, 400)
        ip = self._client_ip()
        now = time.time()
        with _stat_lock:
            key = (ip, url)
            last = _recent_like_action.get(key)
            if last and last[1] == 'like' and now - last[0] < LIKE_THROTTLE:
                # 短时间内重复点赞：视为刷量，直接返回当前值，不累加
                cur = _lecture_stats.get(url, {'visits': 0, 'likes': 0})
                payload = {'ok': True, 'likes': cur.get('likes', 0), 'throttled': True}
            else:
                st = _lecture_stats.setdefault(url, {'visits': 0, 'likes': 0})
                st['likes'] = st.get('likes', 0) + 1
                _recent_like_action[key] = (now, 'like')
                _save_lecture_stats()
                payload = {'ok': True, 'likes': st.get('likes', 0)}
        return self._send_json(payload)  # 锁外发响应（中等-16）

    def _api_lecture_unlike_post(self):
        """取消一次点赞：前端偶数次点击触发，这里累减（最小 0）。

        防刷：同一 IP 对同一讲座在 LIKE_THROTTLE 秒内重复「取消」动作只计一次。
        """
        body = self._read_body_json()
        url = (body.get('url') or '').strip()
        if not url:
            return self._send_json({'ok': False, 'message': 'url 必填'}, 400)
        if url.rstrip('/') not in _known_lecture_urls():
            return self._send_json({'ok': False, 'message': '未知讲座'}, 400)
        ip = self._client_ip()
        now = time.time()
        with _stat_lock:
            key = (ip, url)
            last = _recent_like_action.get(key)
            if last and last[1] == 'unlike' and now - last[0] < LIKE_THROTTLE:
                cur = _lecture_stats.get(url, {'visits': 0, 'likes': 0})
                payload = {'ok': True, 'likes': cur.get('likes', 0), 'throttled': True}
            else:
                st = _lecture_stats.setdefault(url, {'visits': 0, 'likes': 0})
                st['likes'] = max(0, st.get('likes', 0) - 1)
                _recent_like_action[key] = (now, 'unlike')
                _save_lecture_stats()
                payload = {'ok': True, 'likes': st.get('likes', 0)}
        return self._send_json(payload)  # 锁外发响应（中等-16）

    def _api_lecture_want_post(self):
        """记录一次「想听」：前端已做本机 toggle（奇数次想听、偶数次取消）。

        防刷：同一 IP 对同一讲座在 WANT_THROTTLE 秒内重复「想听」动作只计一次。
        """
        body = self._read_body_json()
        url = (body.get('url') or '').strip()
        if not url:
            return self._send_json({'ok': False, 'message': 'url 必填'}, 400)
        if url.rstrip('/') not in _known_lecture_urls():
            return self._send_json({'ok': False, 'message': '未知讲座'}, 400)
        ip = self._client_ip()
        now = time.time()
        with _stat_lock:
            key = (ip, url)
            last = _recent_want_action.get(key)
            if last and last[1] == 'want' and now - last[0] < WANT_THROTTLE:
                cur = _lecture_stats.get(url, {'visits': 0, 'likes': 0, 'wants': 0})
                payload = {'ok': True, 'wants': cur.get('wants', 0), 'throttled': True}
            else:
                st = _lecture_stats.setdefault(url, {'visits': 0, 'likes': 0, 'wants': 0})
                st['wants'] = st.get('wants', 0) + 1
                _recent_want_action[key] = (now, 'want')
                _save_lecture_stats()
                payload = {'ok': True, 'wants': st.get('wants', 0)}
        return self._send_json(payload)  # 锁外发响应（中等-16）

    def _api_lecture_unwant_post(self):
        """取消一次「想听」：前端偶数次点击触发，这里累减（最小 0）。

        防刷：同一 IP 对同一讲座在 WANT_THROTTLE 秒内重复「取消」动作只计一次。
        """
        body = self._read_body_json()
        url = (body.get('url') or '').strip()
        if not url:
            return self._send_json({'ok': False, 'message': 'url 必填'}, 400)
        if url.rstrip('/') not in _known_lecture_urls():
            return self._send_json({'ok': False, 'message': '未知讲座'}, 400)
        ip = self._client_ip()
        now = time.time()
        with _stat_lock:
            key = (ip, url)
            last = _recent_want_action.get(key)
            if last and last[1] == 'unwant' and now - last[0] < WANT_THROTTLE:
                cur = _lecture_stats.get(url, {'visits': 0, 'likes': 0, 'wants': 0})
                payload = {'ok': True, 'wants': cur.get('wants', 0), 'throttled': True}
            else:
                st = _lecture_stats.setdefault(url, {'visits': 0, 'likes': 0, 'wants': 0})
                st['wants'] = max(0, st.get('wants', 0) - 1)
                _recent_want_action[key] = (now, 'unwant')
                _save_lecture_stats()
                payload = {'ok': True, 'wants': st.get('wants', 0)}
        return self._send_json(payload)  # 锁外发响应（中等-16）

    def do_GET(self):
        if self.path.split('?')[0] == '/api/visits':
            return self._api_visits_get()
        if self.path.split('?')[0] == '/api/lecture/stats':
            return self._api_lecture_stats_get()
        if self.path.split('?')[0] == '/api/lectures':
            path = os.path.join(DATA_DIR, 'lectures.json')
            # 解析 since 参数（文件 mtime，秒级浮点）
            qs = self.path.partition('?')[2]
            since = None
            for p in qs.split('&'):
                if p.startswith('since='):
                    try:
                        since = float(p[6:])
                    except ValueError:
                        pass
                    break
            cur_mtime = os.path.getmtime(path) if os.path.exists(path) else 0
            if since is not None and abs(cur_mtime - since) < 1.0:
                self._send_json({'data': [], 'mtime': cur_mtime, 'unchanged': True})
                return
            data = []
            updated_at = ''
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        raw = json.load(f)
                except (json.JSONDecodeError, ValueError, OSError) as e:
                    # 2026-08-05 体检修正（中等-17）：数据文件损坏/读取失败时
                    # 返回明确的 500 与原因，而不是未捕获异常导致裸 traceback。
                    return self._send_json({'ok': False, 'message': f'lectures.json 读取失败：{e}'}, 500)
                # 兼容包裹格式 {updatedAt, data} 与旧版纯数组
                if isinstance(raw, dict) and 'data' in raw:
                    data = raw.get('data', []) or []
                    updated_at = raw.get('updatedAt', '') or ''
                else:
                    data = raw if isinstance(raw, list) else []
            # 全局排除名单过滤：凡是列入的 URL 不应展示（与公网静态切片一致）。
            excluded = _load_excluded()
            if excluded:
                data = [r for r in data if (r.get('sourceUrl') or '') not in excluded]
            # 本地下发的 /api/lectures 须与公网静态切片一致地补上 unitType（场/期），
            # 否则 app.js 拿不到该字段会全部回退显示「期」。
            data = _attach_unit_types(data)
            self._send_json({'data': data, 'mtime': cur_mtime, 'updatedAt': updated_at, 'unchanged': False})
            return
        if self.path.split('?')[0] == '/api/sources':
            return self._api_sources_get()
        # 屏蔽切片原子写留下的 *.tmp（写入窗口内可被读到半份 JSON）
        if self.path.split('?')[0].endswith('.tmp'):
            self.send_error(404, '临时文件不可访问')
            return
        super().do_GET()

    def do_POST(self):
        if not self._is_local_origin():
            return self._send_json({'ok': False, 'message': '跨站请求被拒绝'}, 403)
        base = self.path.split('?')[0]
        if base == '/api/scrape':
            if not _scrape_lock.acquire(blocking=False):
                self._send_json({'ok': False, 'message': '已有抓取任务在运行中，请稍候'}, 409)
                return
            try:
                cmd = [_find_scraper_python(), SCRAPER]
                # 若存在上次抓取记录，则以增量模式运行（仅抓取之后发布的新信息）
                last_path = os.path.join(DATA_DIR, 'last_scrape.json')
                if os.path.exists(last_path):
                    try:
                        _since = json.load(open(last_path, encoding='utf-8')).get('last_scrape')
                        if _since:
                            cmd += ['--since', _since]
                    except Exception:
                        pass
                proc = subprocess.run(
                    cmd,
                    cwd=os.path.dirname(SCRAPER),
                    capture_output=True, text=True, timeout=600,
                )
                if proc.returncode != 0:
                    tail = (proc.stderr or proc.stdout or '')[-400:]
                    self._send_json({'ok': False, 'message': '采集失败（请确认运行 server.py 的 Python 已安装 requests/bs4/rapidocr 等依赖）：' + tail}, 500)
                    return
                path = os.path.join(DATA_DIR, 'lectures.json')
                count = 0
                mtime = os.path.getmtime(path) if os.path.exists(path) else 0
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        raw = json.load(f)
                    count = len(raw.get('data', [])) if isinstance(raw, dict) else len(raw)
                self._send_json({'ok': True, 'count': count, 'mtime': mtime, 'message': '抓取完成'})
            except subprocess.TimeoutExpired:
                self._send_json({'ok': False, 'message': '抓取超时（>10 分钟）'}, 500)
            except Exception as e:
                self._send_json({'ok': False, 'message': str(e)}, 500)
            finally:
                _scrape_lock.release()
            return
        if base == '/api/sources':
            return self._api_sources_post()
        if base == '/api/lecture/visit':
            return self._api_lecture_visit_post()
        if base == '/api/lecture/like':
            return self._api_lecture_like_post()
        if base == '/api/lecture/unlike':
            return self._api_lecture_unlike_post()
        if base == '/api/lecture/want':
            return self._api_lecture_want_post()
        if base == '/api/lecture/unwant':
            return self._api_lecture_unwant_post()
        self.send_error(404)

    def do_PUT(self):
        if not self._is_local_origin():
            return self._send_json({'ok': False, 'message': '跨站请求被拒绝'}, 403)
        base = self.path.split('?')[0]
        m = self._match_sources_index(base)
        if isinstance(m, int) and m >= 0:
            return self._api_sources_put(m)
        self.send_error(404)

    def do_DELETE(self):
        if not self._is_local_origin():
            return self._send_json({'ok': False, 'message': '跨站请求被拒绝'}, 403)
        base = self.path.split('?')[0]
        m = self._match_sources_index(base)
        if isinstance(m, int) and m >= 0:
            return self._api_sources_delete(m)
        self.send_error(404)


def _prune_throttles():
    """定期清理防刷字典中超出窗口的旧条目，避免内存无限增长（内存泄漏）。

    四个防刷字典只增不减：_recent_site_ip / _recent_lecture / _recent_like_action
    / _recent_want_action（2026-08-05 体检修正文案：此前 docstring 误写「三个」）。
    每 60 秒惰性删除已超过对应节流窗口的条目。
    """
    while True:
        time.sleep(60)
        now = time.time()
        try:
            with _stat_lock:
                for k, t in list(_recent_site_ip.items()):
                    if now - t >= VISIT_THROTTLE:
                        _recent_site_ip.pop(k, None)
                for k, t in list(_recent_lecture.items()):
                    if now - t >= VISIT_THROTTLE:
                        _recent_lecture.pop(k, None)
                for k, v in list(_recent_like_action.items()):
                    if now - v[0] >= LIKE_THROTTLE:
                        _recent_like_action.pop(k, None)
                for k, v in list(_recent_want_action.items()):
                    if now - v[0] >= WANT_THROTTLE:
                        _recent_want_action.pop(k, None)
        except Exception:
            pass


def main():
    port = int(os.environ.get('PORT', '8000'))
    # 安全默认：仅绑定本机回环地址，避免把带写操作（/api/scrape、/api/sources 增删改）
    # 的后台意外暴露到局域网/公网。如确需局域网访问，显式设置 HOST=0.0.0.0（自担风险）。
    host = os.environ.get('HOST', '127.0.0.1')
    # 启动防刷字典清理线程（守护线程，随主进程退出）
    threading.Thread(target=_prune_throttles, daemon=True).start()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f'[server] 华师讲座聚合已启动：http://localhost:{port}  （Ctrl+C 退出）')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == '__main__':
    main()
