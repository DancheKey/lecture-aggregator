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
LECTURE_STATS_PATH = os.path.join(DATA_DIR, 'lecture_stats.json')  # 每条讲座的访问/点赞：{url:{visits,likes}}

_scrape_lock = threading.Lock()
_stat_lock = threading.Lock()

# ---- 访问量 / 点赞统计的运行时状态（文件持久化 + 内存防刷窗口） ----
_site_visits = {'total': 0}            # 站点总访问量
_lecture_stats = {}                     # url -> {"visits": N, "likes": M}
_recent_site_ip = {}                   # ip -> 最近一次计数的时间戳（站点访问防刷）
_recent_lecture = {}                   # (ip, url) -> 时间戳（单讲座访问防刷）
VISIT_THROTTLE = 180                   # 同一 IP / 同一讲座 3 分钟内只计 1 次


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


def _save_visits():
    try:
        with open(VISITS_PATH, 'w', encoding='utf-8') as f:
            json.dump(_site_visits, f, ensure_ascii=False)
    except Exception:
        pass


def _save_lecture_stats():
    try:
        with open(LECTURE_STATS_PATH, 'w', encoding='utf-8') as f:
            json.dump(_lecture_stats, f, ensure_ascii=False)
    except Exception:
        pass


_load_stat_files()


def _find_scraper_python():
    """选择一个能 import 爬虫依赖（requests/bs4）的 Python 解释器。

    server.py 自身可能用没装这些依赖的解释器启动（例如某些环境默认的 3.13），
    直接用它跑 scraper 会 ImportError -> 抓取失败。这里自动探测一个可用解释器：
    优先尝试 sys.executable，再回退到本机已知装齐依赖的路径与 PATH 中的 python。
    """
    candidates = [sys.executable, r'D:\Tools\Python 312\python.exe']
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
        with open(SOURCES_PATH, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

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
        """尽量还原真实客户端 IP（兼容反向代理透传）。"""
        xff = self.headers.get('X-Forwarded-For')
        if xff:
            return xff.split(',')[0].strip()
        return self.client_address[0]

    # ---- 访问量 / 点赞统计 ----

    def _api_visits_get(self):
        """站点总访问量与按日明细：同一 IP 3 分钟内重复刷新只计 1 次。

        返回 {"ok": true, "total": N, "by_day": {"YYYY-MM-DD": count, ...}}。
        by_day 按本地日期累计，供生成「每年每月访问量」报告；
        完全本地（data/visits.json），不依赖任何外部计数服务（busuanzi / countapi 等）。
        """
        ip = self._client_ip()
        now = time.time()
        with _stat_lock:
            last = _recent_site_ip.get(ip, 0)
            if now - last >= VISIT_THROTTLE:
                _site_visits['total'] = _site_visits.get('total', 0) + 1
                today = time.strftime('%Y-%m-%d', time.localtime(now))
                bd = _site_visits.setdefault('by_day', {})
                bd[today] = bd.get(today, 0) + 1
                _recent_site_ip[ip] = now
                _save_visits()
            return self._send_json({'ok': True, 'total': _site_visits.get('total', 0), 'by_day': _site_visits.get('by_day', {})})

    def _api_lecture_stats_get(self):
        """返回每条讲座的访问/点赞统计：{url: {visits, likes}}。"""
        with _stat_lock:
            return self._send_json({'ok': True, 'stats': _lecture_stats})

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
            return self._send_json({'ok': True, 'visits': cur.get('visits', 0)})

    def _api_lecture_like_post(self):
        """记录一次点赞：前端已做本机 toggle（奇数次赞、偶数次取消），这里直接累加。"""
        body = self._read_body_json()
        url = (body.get('url') or '').strip()
        if not url:
            return self._send_json({'ok': False, 'message': 'url 必填'}, 400)
        with _stat_lock:
            st = _lecture_stats.setdefault(url, {'visits': 0, 'likes': 0})
            st['likes'] = st.get('likes', 0) + 1
            _save_lecture_stats()
            return self._send_json({'ok': True, 'likes': st.get('likes', 0)})

    def _api_lecture_unlike_post(self):
        """取消一次点赞：前端偶数次点击触发，这里累减（最小 0）。"""
        body = self._read_body_json()
        url = (body.get('url') or '').strip()
        if not url:
            return self._send_json({'ok': False, 'message': 'url 必填'}, 400)
        with _stat_lock:
            st = _lecture_stats.setdefault(url, {'visits': 0, 'likes': 0})
            st['likes'] = max(0, st.get('likes', 0) - 1)
            _save_lecture_stats()
            return self._send_json({'ok': True, 'likes': st.get('likes', 0)})

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
                with open(path, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                # 兼容包裹格式 {updatedAt, data} 与旧版纯数组
                if isinstance(raw, dict) and 'data' in raw:
                    data = raw.get('data', []) or []
                    updated_at = raw.get('updatedAt', '') or ''
                else:
                    data = raw if isinstance(raw, list) else []
            self._send_json({'data': data, 'mtime': cur_mtime, 'updatedAt': updated_at, 'unchanged': False})
            return
        if self.path.split('?')[0] == '/api/sources':
            return self._api_sources_get()
        super().do_GET()

    def do_POST(self):
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
                    self._send_json({'ok': False, 'message': '采集失败（请确认运行 server.py 的 Python 已安装 requests/bs4/easyocr 等依赖）：' + tail}, 500)
                    return
                path = os.path.join(DATA_DIR, 'lectures.json')
                count = 0
                mtime = os.path.getmtime(path) if os.path.exists(path) else 0
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        count = len(json.load(f))
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
        self.send_error(404)

    def do_PUT(self):
        base = self.path.split('?')[0]
        m = self._match_sources_index(base)
        if isinstance(m, int) and m >= 0:
            return self._api_sources_put(m)
        self.send_error(404)

    def do_DELETE(self):
        base = self.path.split('?')[0]
        m = self._match_sources_index(base)
        if isinstance(m, int) and m >= 0:
            return self._api_sources_delete(m)
        self.send_error(404)


def main():
    port = int(os.environ.get('PORT', '8000'))
    # 安全默认：仅绑定本机回环地址，避免把带写操作（/api/scrape、/api/sources 增删改）
    # 的后台意外暴露到局域网/公网。如确需局域网访问，显式设置 HOST=0.0.0.0（自担风险）。
    host = os.environ.get('HOST', '127.0.0.1')
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f'[server] 华师讲座聚合已启动：http://localhost:{port}  （Ctrl+C 退出）')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == '__main__':
    main()
