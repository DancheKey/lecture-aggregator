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

_scrape_lock = threading.Lock()


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
        super().end_headers()

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
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

    def do_GET(self):
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
