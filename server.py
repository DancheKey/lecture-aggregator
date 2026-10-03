"""华师讲座聚合 —— 本地演示服务器（方案 A 增强版）。

- 静态托管 site/（所有响应禁用缓存，刷新即见最新）
- GET  /api/lectures          读取最新 data/lectures.json（全量；前端不再传 since）
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
import atexit
import hmac
import secrets
import socket
import threading
import subprocess
import yaml           # 仅用于读取其它 yaml（如给 scraper 用的辅助脚本）；sources.yaml 走 ruamel
import ruamel.yaml
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(ROOT, 'site')
DATA_DIR = os.path.join(ROOT, 'data')
SCRAPER = os.path.join(ROOT, 'scraper', 'scraper.py')
SOURCES_PATH = os.path.join(ROOT, 'scraper', 'sources.yaml')
# 确保 scripts/ 与 scraper/ 下的共享模块可被导入（excluded_urls、field_vocab）
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
from excluded_urls import load_excluded, is_record_excluded
from frontend_fields import strip_frontend_fields
import field_vocab as _fv


def _warn(msg):
    """关键路径失败告警：统一打到 stderr（带时间戳），避免 except 静默吞掉问题。

    只用于「数据加载/持久化/校验」等失败会造成数据丢失或静默降级的路径；
    探测类（找解释器、构造命令参数等）保持静默，避免日志噪音。
    """
    print(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] [WARN] {msg}', file=sys.stderr)


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
LIKE_CAP = 999                         # 单条讲座点赞数上限（防慢速刷高；unlike 仍可继续减）
MAX_BODY_BYTES = 1_000_000             # 请求体上限 1MB（本地 API 的 body 都是几十字节的小 JSON）
MAX_SOURCES = 500                      # 信息源条数上限（当前 53 条。防POST 循环写入把yaml 撑成几万条，
                                        # 且每个源都会被 daily.yml 真实抓取——几百个源会让CI 跑一整天。
                                        # 超限直接 400，语义与既有校验一致）

def _yaml_rt():
    """sources.yaml 专用的 ruamel round-trip YAML 实例（**不要**改成 yaml.safe_load/dump）。

    三个参数都是实测选定的，理由见下：
    - round-trip（YAML() 默认）：保留注释与引号风格，这是换掉 PyYAML 的全部意义。
    - indent(mapping=2, sequence=2, offset=0)：与仓库现有 sources.yaml 的缩进风格**逐字节一致**。
      默认（sequence=4, offset=2）会把每行列表都多缩进 2 格，产生 600+ 行无意义 diff。
    - width=4096：避免长 URL 被折行（折行后 YAML 语义不变，但 diff 很难读）。
    """
    y = ruamel.yaml.YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=2, offset=0)
    return y

# ---- 写接口管理凭证（2026-09-26 审计 P1-2）----
# 此前 sources CRUD 与 /api/scrape 的唯一防线是 _is_local_origin——而它对
# 「Origin/Referer 缺失」放行，curl 从局域网直连即可绕过；HOST=0.0.0.0 时即裸奔。
# 现改为：启动时加载/生成随机 token（持久化 data/admin_token.json，不入库），
# 所有写接口须带 X-Admin-Token 头。token 的发放端点 /api/admin/token 仅接受
# 回环地址直连（client IP 为 127.0.0.1/[::1]），局域网/公网拿不到 token 即无法写。
# 本机浏览器（前端「手动抓取」按钮）经 /api/admin/token 自动取 token，无需人工粘贴。
_ADMIN_TOKEN_PATH = os.path.join(DATA_DIR, 'admin_token.json')


def _load_or_create_admin_token():
    try:
        with open(_ADMIN_TOKEN_PATH, 'r', encoding='utf-8') as f:
            tok = (json.load(f) or {}).get('token')
            if tok:
                return tok
    except (OSError, ValueError):
        pass
    tok = secrets.token_urlsafe(24)
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'token': tok}, f)
        os.replace(tmp, _ADMIN_TOKEN_PATH)
    except OSError as e:
        print(f'[WARN] admin_token 写盘失败（token 仅本进程内存有效）: {e!r}', file=sys.stderr)
    return tok


_ADMIN_TOKEN = _load_or_create_admin_token()
_IS_LOOPBACK_RE = re.compile(r'^127\.0\.0\.1$|^::1$|^\[::1\]$')


def _check_admin(self):
    # 写接口凭证校验：X-Admin-Token 必须与启动时生成的 token 一致。
    # 2026-09-27 二轮审计：改常量时间比较（防计时侧信道；编码为 bytes 以
    # 容忍请求头里的非 ASCII 字符而不抛 TypeError）。
    return hmac.compare_digest(
        (self.headers.get('X-Admin-Token') or '').encode('utf-8'),
        _ADMIN_TOKEN.encode('utf-8'))


def _speaker_keys(name):
    """讲者归一化键（2026-09-06）：去空白/职称后缀、全角转半角；英文 lower。
    多主讲人（'A、B'）逐人拆分，返回键数组——同名同人判定为完全一致，
    跨语言（张三/Zhang San）暂不合键（避免同音误并），留作后续。

    实现已收敛到 scraper/field_vocab.speaker_keys()（G4，2026-09-10）——它同时是
    scripts/generate_frontend_data.py 的唯一实现，两端由构造保证一致，
    不再依赖「两处手抄保持同步」。
    """
    return _fv.speaker_keys(name)


def _attach_unit_types(data):
    """为单页多讲座拆分记录标注 unitType（场/期），逻辑须与 scripts/generate_frontend_data.py
    的 with_unit() 严格一致，确保本地开发服务器下发的 /api/lectures 与公网静态切片行为相同：

    - 同一 sourceUrl 组内所有讲座日期相同（同一天多场次）-> 'session'（第x场）
    - 跨了不同日期（系列讲座分期）-> 'issue'（第x期）

    仅对含 lectureIndex 的记录附加 unitType 字段；其余原样透传（但统一复制并补 speakerKeys），
    不污染主数据。speakerKeys 由 _speaker_keys() 生成，须与 generate 端一致。
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
        it = dict(item)
        if item.get('lectureIndex') is not None:
            dates = url_dates.get(item.get('sourceUrl') or '', set())
            it['unitType'] = 'session' if len(dates) == 1 else 'issue'
        # 讲者归一化键（2026-09-06）：前端讲者聚合视图用，多人各一键
        it['speakerKeys'] = _speaker_keys(item.get('speaker'))
        # 按白名单裁掉前端无消费者的内部字段（llmSelfExtract/qaRepaired 等）。
        # 必须与 generate_frontend_data.with_unit() 用同一份白名单，
        # 否则本地下发与公网静态切片字段集分叉，test_frontend_consistency 会失败。
        # ⚠ 这里**有意不调** frontend_fields.split_long_text()：本地开发服务器
        # 直连本机、无带宽成本，长文本内联可少一次请求、调试时直接看到全文。
        # 公网侧剥离长文本改走 lectures/detail/ 分片（2026-09-28 首屏体积优化）。
        # 该刻意差异由 test_public_split_vs_local_inline 锁定，两端都别默默改。
        out.append(strip_frontend_fields(it))
    return out


def _load_stat_files():
    """启动时把磁盘上的统计状态读入内存（若不存在则用默认值）。"""
    global _site_visits, _lecture_stats
    try:
        if os.path.exists(VISITS_PATH):
            with open(VISITS_PATH, encoding='utf-8') as f:
                _site_visits = json.load(f) or {'total': 0}
    except Exception as e:
        _warn(f'站点访问量加载失败，本次从 0 开始（{VISITS_PATH}）：{type(e).__name__}: {e}')
        _site_visits = {'total': 0}
    # 兼容旧格式（仅有 total，无 by_day 按日明细）；旧值仍保留为「历史遗留总数」
    if not isinstance(_site_visits.get('by_day'), dict):
        _site_visits['by_day'] = {}
    try:
        if os.path.exists(LECTURE_STATS_PATH):
            with open(LECTURE_STATS_PATH, encoding='utf-8') as f:
                _lecture_stats = json.load(f) or {}
    except Exception as e:
        _warn(f'讲座统计加载失败，点赞/想听计数将全部归零（{LECTURE_STATS_PATH}）：'
              f'{type(e).__name__}: {e}')
        _lecture_stats = {}


def _atomic_write_json(path, obj):
    """原子写 JSON：先写 .tmp 再 os.replace，避免中途崩溃留下半份文件。"""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---- 统计落盘：脏标记 + 锁外flush（2026-10-02）----
#
# 此前每次计数变更都在 **_stat_lock 内**同步写盘。2026-08-05 体检（中字-16）
# 只把「发响应」移出了锁，写盘仍留在锁内——锁的持有时间仍覆盖整个文件 IO，
# 而历史实测 p50=32ms / p90=516ms（Windows + Defender 扫描 + 小文件很多时尤甚），
# 意味着一次点赞可能把其余所有统计请求堵住半秒。
#
# 改法：锁内只置脏标记（O(1)），锁外由 flush 真正落盘。取舍与正确性：
#   · 丢失窗口：进程被强杀时最多丢「最后一次未落盘的计数」——统计是尽力而为的
#     展示数据（点赞/想听/访问），丢 1~2 次计数不影响正确性语义；原子写保证
#     不会留下半份文件。
#   · 并发安全：flush 用 try-lock，抢不到就跳过本轮（脏标记保持为真，
#     下一次统计请求会再次触发 flush），不阻塞请求线程。
#   · 关停保障：注册 atexit，关停前必落一次，避免正常退出丢数据。
_stats_dirty = {'visits': False, 'lectures': False}


def _mark_dirty(kind):
    """置脏标记。由调用方在持有 _stat_lock 时调用（O(1)，无 IO）。"""
    _stats_dirty[kind] = True


def _flush_stats():
    """锁外落盘：把置脏的统计写回磁盘。可安全并发调用（内部 try-lock）。"""
    if not (_stats_dirty['visits'] or _stats_dirty['lectures']):
        return
    if not _stat_lock.acquire(blocking=False):
        return                      # 别的线程正持锁处理请求，下一轮再落
    try:
        # 快照后再清标记：期间的新变更会再次置脏，不会被本次落盘吞掉
        want_visits, want_lectures = _stats_dirty['visits'], _stats_dirty['lectures']
        _stats_dirty['visits'] = _stats_dirty['lectures'] = False
        if want_visits:
            _save_visits()
        if want_lectures:
            _save_lecture_stats()
    finally:
        _stat_lock.release()


def _save_visits():
    try:
        _atomic_write_json(VISITS_PATH, _site_visits)
    except Exception as e:
        _warn(f'站点访问量写盘失败（{VISITS_PATH}）：{type(e).__name__}: {e}')
        # 落盘失败必须把标记放回去，否则这次变更永远不会被重试
        _stats_dirty['visits'] = True


def _save_lecture_stats():
    try:
        _atomic_write_json(LECTURE_STATS_PATH, _lecture_stats)
    except Exception as e:
        _warn(f'讲座统计写盘失败（{LECTURE_STATS_PATH}）：{type(e).__name__}: {e}')
        _stats_dirty['lectures'] = True


def _flush_stats_atexit():
    """进程退出前尽力落盘（含 Ctrl+C 路径）。失败只告警，不影响退出码。"""
    try:
        _flush_stats()
    except Exception:
        pass


atexit.register(_flush_stats_atexit)


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
        except Exception as e:
            _warn(f'讲座 URL 集合解析失败，「未知讲座」校验会降级：{type(e).__name__}: {e}')
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


# 连接级 socket 超时（秒，2026-10-02）。ThreadingHTTPServer 每连接一线程、
# 基类默认**无任何超时**：客户端连上不发数据、或声明 Content-Length 后慢速发体，
# 线程就永久阻塞在 read()，少量连接即可耗尽线程（本机实测声明 99MB 后只发 2 字节，
# 服务端 3 秒内零响应且不返回）。
# 本地 API 的正常交互都是几十字节的请求 + 立即响应，15 秒绰绰有余；
# 超时后线程自行退出回收，不影响其它请求。
SOCKET_TIMEOUT = 15


class Handler(SimpleHTTPRequestHandler):
    timeout = SOCKET_TIMEOUT          # 供基类 setup() 施加到连接；使 rfile.read 抛超时而非挂死

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SITE_DIR, **kwargs)

    def handle_one_request(self):
        """把「读请求头阶段超时」也收敛掉，不让它冒成未捕获异常刷满 stderr。

        基类实现里 socket.timeout 会走 handle_one_request 的 except 并正常关闭连接，
        但 SimpleHTTPRequestHandler 在读头超时后仍可能继续走 send_error；
        这里统一 catch 成静默关闭——静默超时是正常现象（探活/端口扫描/慢速连接），
        记 WARN 只会让日志噪音掩盖真正的错误。
        """
        try:
            super().handle_one_request()
        except (socket.timeout, TimeoutError, ConnectionError):
            self.close_connection = True

    def end_headers(self):
        # 禁用缓存：每次刷新都拿到最新数据
        self.send_header('Cache-Control', 'no-store')
        # 2026-09-26 审计 P3：基础安全响应头（页面自身已有 CSP meta，此处不重复下发）
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
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
        """读 sources.yaml。**必须**用 ruamel round-trip（不能退回 yaml.safe_load）。

        原因：sources.yaml 里有 19 行人工维护的注释（栏目 URL 约定、死链说明、
        JS 渲染翻页的取舍记录等）。safe_load 丢注释 → 任何一次写接口
        （POST/PUT/DELETE）都会把整份文件重写成无注释版，知识静默蒸发。
        round-trip 的CommentedMap/CommentedSeq 是 dict/list 子类，
        调用方（append / pop / 逐键赋值 / json.dumps）语义不变。
        """
        if not os.path.exists(SOURCES_PATH):
            return {'sources': []}
        with open(SOURCES_PATH, 'r', encoding='utf-8') as f:
            data = _yaml_rt().load(f)
        if not data or not isinstance(data, dict) or data.get('sources') is None:
            #空文件 / 顶层不是 dict / sources 为空值：与原safe_load 的 `or {...}` 同义
            return {'sources': []}
        return data

    def _save_sources(self, data):
        """原子写回 sources.yaml，保留注释（见 _load_sources 的说明）。"""
        tmp = SOURCES_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            _yaml_rt().dump(data, f)
        os.replace(tmp, SOURCES_PATH)

    def _api_sources_get(self):
        data = self._load_sources()
        self._send_json({'ok': True, 'sources': data.get('sources', [])})

    def _api_sources_post(self):
        # 2026-09-27 二轮审计 S-4：改走 _read_body_json（非法 Content-Length 不再
        # 抛 500、超 1MB 请求体按空处理），与 /api/lecture/* 端点同一守卫。
        body = self._read_body_json()
        if not body:
            self._send_json({'ok': False, 'message': '无效的 JSON'}, 400)
            return
        # 类型校验：与 PUT /api/sources/<i> 一致，防止把非预期类型写进
        # sources.yaml 导致下次爬虫读取配置时崩溃
        for k in ('name', 'campus', 'base'):
            if k in body and not isinstance(body[k], str):
                self._send_json({'ok': False, 'message': f'{k} 必须为字符串'}, 400)
                return
        if 'list_urls' in body:
            if not isinstance(body['list_urls'], list):
                self._send_json({'ok': False, 'message': 'list_urls 必须为数组'}, 400)
                return
            for i, lu in enumerate(body['list_urls']):
                if not isinstance(lu, (str, dict)):
                    self._send_json({'ok': False, 'message': f'list_urls[{i}] 必须为字符串或对象'}, 400)
                    return
        name = (body.get('name') or '').strip()
        campus = (body.get('campus') or '').strip()
        base = (body.get('base') or '').strip()
        list_urls = body.get('list_urls') or []
        if not name or not base:
            self._send_json({'ok': False, 'message': 'name 和 base 为必填项'}, 400)
            return
        data = self._load_sources()
        # 条数上限：超限直接 400，不落盘（与既有校验同一返回形态）
        if len(data['sources']) >= MAX_SOURCES:
            self._send_json({'ok': False,
                             'message': f'信息源已达上限 {MAX_SOURCES} 条，请先删除不再使用的源'}, 400)
            return
        new_src = {'name': name, 'campus': campus or '', 'base': base, 'list_urls': list_urls}
        data['sources'].append(new_src)
        self._save_sources(data)
        self._send_json({'ok': True, 'index': len(data['sources']) - 1, 'source': new_src})

    def _api_sources_put(self, idx):
        data = self._load_sources()
        if idx < 0 or idx >= len(data['sources']):
            self._send_json({'ok': False, 'message': f'索引 {idx} 超出范围（共 {len(data["sources"])} 条）'}, 404)
            return
        # 2026-09-27 二轮审计 S-4：同 POST，改走 _read_body_json 统一守卫。
        body = self._read_body_json()
        if not body:
            self._send_json({'ok': False, 'message': '无效的 JSON'}, 400)
            return
        # 类型校验：防止把 list_urls 写成字符串等非预期类型导致 scraper 读取 sources.yaml 崩溃
        for k in ('name', 'campus', 'base'):
            if k in body and not isinstance(body[k], str):
                self._send_json({'ok': False, 'message': f'{k} 必须为字符串'}, 400)
                return
        if 'list_urls' in body:
            if not isinstance(body['list_urls'], list):
                self._send_json({'ok': False, 'message': 'list_urls 必须为数组'}, 400)
                return
            for i, lu in enumerate(body['list_urls']):
                if not isinstance(lu, (str, dict)):
                    self._send_json({'ok': False, 'message': f'list_urls[{i}] 必须为字符串或对象'}, 400)
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
        存在时必须指向 127.0.0.1/localhost/[::1]，拒绝跨站表单/fetch 触发本机写接口。"""
        for h in ('Origin', 'Referer'):
            v = (self.headers.get(h) or '').strip()
            if not v:
                continue
            if re.match(r'^https?://(127\.0\.0\.1|\[::1\]|localhost)(:\d+)?(/|$)', v, re.I):
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
        # 2026-09-26 审计 P3：GET 改状态且此前无 Origin 校验——外站 <img> 可借
        # 访客浏览器刷计数（Referer 为外站会被下方校验拒绝）。
        if not self._is_local_origin():
            return self._send_json({'ok': False, 'message': '跨站请求被拒绝'}, 403)
        ip = self._client_ip()
        now = time.time()
        # 2026-08-05 体检修正（中等-16）：锁内只改状态，锁外发响应。
        # 此前在 with _stat_lock 内直接 _send_json，慢客户端写响应期间
        # 会持锁阻塞所有其它统计请求。
        # 2026-10-02：锁内写盘也移出（锁内只置脏标记，见 _flush_stats 注释），
        # 否则一次计数变更会把其它统计请求堵住整个文件 IO（实测 p90=516ms）。
        with _stat_lock:
            last = _recent_site_ip.get(ip, 0)
            if now - last >= VISIT_THROTTLE:
                _site_visits['total'] = _site_visits.get('total', 0) + 1
                today = time.strftime('%Y-%m-%d', time.localtime(now))
                bd = _site_visits.setdefault('by_day', {})
                bd[today] = bd.get(today, 0) + 1
                _recent_site_ip[ip] = now
                _mark_dirty('visits')
            payload = {'ok': True, 'total': _site_visits.get('total', 0), 'by_day': dict(_site_visits.get('by_day', {}))}
        _flush_stats()
        return self._send_json(payload)

    def _api_lecture_stats_get(self):
        """返回每条讲座的访问/点赞统计：{url: {visits, likes}}。"""
        # 锁内浅拷贝快照、锁外发响应（同 中等-16 修正；避免慢客户端持锁）。
        with _stat_lock:
            snapshot = {u: dict(st) for u, st in _lecture_stats.items()}
        return self._send_json({'ok': True, 'stats': snapshot})

    def _read_body_json(self):
        # 2026-09-26 审计 P3：Content-Length 非数字不再抛未捕获异常；超过
        # MAX_BODY_BYTES 的请求读入后丢弃（保持连接流一致）并按空 body 处理。
        raw_len = (self.headers.get('Content-Length') or '').strip()
        try:
            length = int(raw_len or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            # 2026-10-02 安全修复：此前进入「读完丢弃」循环，而客户端若只声明
            # Content-Length 却迟迟不发数据（慢速发体 / 恶意挂起），服务端线程会
            # **无限期阻塞在 rfile.read()**。ThreadingHTTPServer 每连接一线程、
            # 默认无 socket 超时，故单个客户端可挂住任意多条线程拖垮本机服务。
            # 实测：声明 Content-Length: 99999999 后只发 2 字节，3 秒内无任何响应。
            #
            # 处置：既不等待也不读，直接断开连接（Connection: close）。本地 API 的
            # body 都是几十字节的小 JSON，超过 1MB 本身就是异常请求，断开比
            # 「读完再返回 400」更合适——继续读只会把攻击面交给对端。
            print(f'[API-WARN] 请求体过大，拒绝并断开: {length} bytes > {MAX_BODY_BYTES}',
                  file=sys.stderr)
            self.close_connection = True
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            return {}
        # 2026-09-30：非 dict 的合法 JSON（`[1,2]` / `"x"` / `42` / `null` / `true`）此前
        # 原样返回给调用方，而全部调用方第一件事就是 body.get(...) → AttributeError
        # 抛到 handler 外，本线程直接崩（非只影响这一个请求）。
        # 与非法 JSON 同等处置：按空 body 处理，让调用方走既有的 400 分支。
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _url_from_body(body):
        """从请求体取 url，返回去除首尾空白的字符串；不可用时返回 ''。

        2026-10-02 修复 B3 剩余半边：`_read_body_json` 只保证「body 是 dict」，但
        **dict 内的字段类型仍未校验**——`{"url": 123}` / `{"url": [1,2]}` /
        `{"url": {"a":1}}` 会让 `(body.get('url') or '').strip()` 抛
        AttributeError（123/列表/字典都是真值，逃过 `or ''`），异常抛到 handler
        外，本线程直接崩、客户端拿不到任何响应。

        为什么必须在这里统一收口：5 个统计端点（visit/like/unlike/want/unwant）
        各写一遍 `(body.get('url') or '').strip()`，逐处加 isinstance 是典型的
        「修一处漏四处」。故改为单一入口 —— 新增端点只需调用本方法即自动受保护。
        非字符串一律按「缺字段」处理（返回 '' → 走既有的 400 'url 必填' 分支），
        与非法 JSON 的既有语义保持一致。
        """
        v = (body or {}).get('url')
        if not isinstance(v, str):
            return ''
        return v.strip()

    def _api_lecture_visit_post(self):
        """记录一次讲座访问：同一 (IP, url) 3 分钟内只计 1 次。"""
        body = self._read_body_json()
        url = self._url_from_body(body)
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
                st = _lecture_stats.setdefault(url, {'visits': 0, 'likes': 0, 'wants': 0})
                st['visits'] = st.get('visits', 0) + 1
                _recent_lecture[key] = now
                _mark_dirty('lectures')
            cur = _lecture_stats.get(url, {'visits': 0, 'likes': 0, 'wants': 0})
            payload = {'ok': True, 'visits': cur.get('visits', 0)}
        _flush_stats()   # 锁外落盘（2026-10-02，见 _flush_stats）
        return self._send_json(payload)  # 锁外发响应（中等-16）

    def _api_lecture_like_post(self):
        """记录一次点赞：前端已做本机 toggle（奇数次赞、偶数次取消）。

        防刷：同一 IP 对同一讲座在 LIKE_THROTTLE 秒内重复「点赞」动作只计一次，
        防止脚本无限刷赞；允许 like↔unlike 交替（即正常用户切换点赞状态）。
        """
        body = self._read_body_json()
        url = self._url_from_body(body)
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
                cur = _lecture_stats.get(url, {'visits': 0, 'likes': 0, 'wants': 0})
                payload = {'ok': True, 'likes': cur.get('likes', 0), 'throttled': True}
            else:
                st = _lecture_stats.setdefault(url, {'visits': 0, 'likes': 0, 'wants': 0})
                if st.get('likes', 0) >= LIKE_CAP:
                    # 2026-09-26 审计 P3：单条讲座点赞封顶（返回 ok 保持前端 toggle
                    # 状态机不被打断，capped 标志供前端未来感知）
                    payload = {'ok': True, 'likes': st.get('likes', 0), 'capped': True}
                else:
                    st['likes'] = st.get('likes', 0) + 1
                    _recent_like_action[key] = (now, 'like')
                    _mark_dirty('lectures')
                    payload = {'ok': True, 'likes': st.get('likes', 0)}
        _flush_stats()   # 锁外落盘（2026-10-02，见 _flush_stats）
        return self._send_json(payload)  # 锁外发响应（中等-16）

    def _api_lecture_unlike_post(self):
        """取消一次点赞：前端偶数次点击触发，这里累减（最小 0）。

        防刷：同一 IP 对同一讲座在 LIKE_THROTTLE 秒内重复「取消」动作只计一次。
        """
        body = self._read_body_json()
        url = self._url_from_body(body)
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
                cur = _lecture_stats.get(url, {'visits': 0, 'likes': 0, 'wants': 0})
                payload = {'ok': True, 'likes': cur.get('likes', 0), 'throttled': True}
            else:
                st = _lecture_stats.setdefault(url, {'visits': 0, 'likes': 0, 'wants': 0})
                st['likes'] = max(0, st.get('likes', 0) - 1)
                _recent_like_action[key] = (now, 'unlike')
                _mark_dirty('lectures')
                payload = {'ok': True, 'likes': st.get('likes', 0)}
        _flush_stats()   # 锁外落盘（2026-10-02，见 _flush_stats）
        return self._send_json(payload)  # 锁外发响应（中等-16）

    def _api_lecture_want_post(self):
        """记录一次「想听」：前端已做本机 toggle（奇数次想听、偶数次取消）。

        防刷：同一 IP 对同一讲座在 WANT_THROTTLE 秒内重复「想听」动作只计一次。
        """
        body = self._read_body_json()
        url = self._url_from_body(body)
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
                _mark_dirty('lectures')
                payload = {'ok': True, 'wants': st.get('wants', 0)}
        _flush_stats()   # 锁外落盘（2026-10-02，见 _flush_stats）
        return self._send_json(payload)  # 锁外发响应（中等-16）

    def _api_lecture_unwant_post(self):
        """取消一次「想听」：前端偶数次点击触发，这里累减（最小 0）。

        防刷：同一 IP 对同一讲座在 WANT_THROTTLE 秒内重复「取消」动作只计一次。
        """
        body = self._read_body_json()
        url = self._url_from_body(body)
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
                _mark_dirty('lectures')
                payload = {'ok': True, 'wants': st.get('wants', 0)}
        _flush_stats()   # 锁外落盘（2026-10-02，见 _flush_stats）
        return self._send_json(payload)  # 锁外发响应（中等-16）

    def do_GET(self):
        if self.path.split('?')[0] == '/api/visits':
            return self._api_visits_get()
        if self.path.split('?')[0] == '/api/lecture/stats':
            return self._api_lecture_stats_get()
        if self.path.split('?')[0] == '/api/lectures':
            path = os.path.join(DATA_DIR, 'lectures.json')
            # N4（2026-09-22 批次 B）：删除 since 增量死分支——前端自 2026-08-05
            # 起已统一全量加载（曾因先传新 mtime 被判 unchanged、页面不刷新），
            # 全站 0 调用。若将来要做真增量，从 git 历史恢复，勿留两可状态。
            cur_mtime = os.path.getmtime(path) if os.path.exists(path) else 0
            data = []
            updated_at = ''
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        raw = json.load(f)
                except (json.JSONDecodeError, ValueError, OSError) as e:
                    # 2026-08-05 体检修正（中等-17）：数据文件损坏/读取失败时
                    # 返回明确的 500 与原因，而不是未捕获异常导致裸 traceback。
                    # 2026-09-26 审计脱敏：异常文本含绝对路径等本机信息，不再回传客户端，
                    # 仅落服务端日志。
                    print(f'[API-ERR] /api/lectures 读取失败: {e!r}', file=sys.stderr)
                    return self._send_json({'ok': False, 'message': 'lectures.json 读取失败（详见服务端日志）'}, 500)
                # 兼容包裹格式 {updatedAt, data} 与旧版纯数组
                if isinstance(raw, dict) and 'data' in raw:
                    data = raw.get('data', []) or []
                    updated_at = raw.get('updatedAt', '') or ''
                else:
                    data = raw if isinstance(raw, list) else []
            # 全局排除名单过滤：凡是列入的 URL 不应展示（与公网静态切片一致）。
            excluded = load_excluded()
            if excluded:
                # 2026-10-02：与 generate_frontend_data 同步改用记录级判定——
                # 跨源合并记录的 sources[].sourceUrl 命中名单时整条排除，
                # 否则本地能看到的页面公网看不到（或反之），两端行为分叉。
                data = [r for r in data if not is_record_excluded(r, excluded)]
            # 本地下发的 /api/lectures 须与公网静态切片一致地补上 unitType（场/期），
            # 否则 app.js 拿不到该字段会全部回退显示「期」。
            data = _attach_unit_types(data)
            self._send_json({'data': data, 'mtime': cur_mtime, 'updatedAt': updated_at})
            return
        if self.path.split('?')[0] == '/api/sources':
            return self._api_sources_get()
        # 写接口管理凭证发放（2026-09-26 审计 P1-2）：仅接受回环地址直连，
        # 局域网/公网拿不到 token 即无法调用任何写接口。
        if self.path.split('?')[0] == '/api/admin/token':
            if not _IS_LOOPBACK_RE.match(self.client_address[0] or ''):
                return self._send_json({'ok': False,
                                        'message': '管理凭证仅限本机获取'}, 403)
            return self._send_json({'ok': True, 'token': _ADMIN_TOKEN})
        # 静态资源守卫（2026-10-02 提取，供 GET / HEAD 共用）：
        # ① 屏蔽切片原子写留下的 *.tmp（写入窗口内可被读到半份 JSON）
        # ② 目录列表禁用（2026-09-26 审计 P3）：无 index.html 的目录（如lectures/）
        #    不再渲染文件清单；带 index.html 的根目录照常服务。
        #
        # ⚠ 两项都**必须基于解码后的路径**判定。此前只判 `self.path.endswith('.tmp')`
        # （原始未解码串），而文件查找用的 translate_path 会 unquote，
        # 于是 `/lectures/latest.json%2etmp` 不匹配后缀 → 守卫放行 →
        # 服务端按 `.tmp` 真实文件名找到半份 JSON 并 200 返回（实测复现）。
        return self._static_guard() or super().do_GET()

    def _static_guard(self):
        """静态资源守卫：命中应屏蔽的路径时返回 True（已响应），否则返回 False。

        2026-10-02：从 do_GET 提取为独立方法，好让 do_HEAD 复用——
        否则 HEAD 会绕过全部守卫（实测 `HEAD /lectures/x.json.tmp` 返回 200），
        攻击者可用 HEAD 探测写入窗口内半份文件的存在性与长度。
        """
        # unquote 而非只 split('?')：%3F（?）、%2e（.）等百分号编码都要还原后判定。
        # 只解一次即可——解码出的路径若仍含 %.. 之类，translate_path 内部的
        # 安全检查（越界即 403/404）会兜住。
        from urllib.parse import unquote, urlsplit
        path = unquote(urlsplit(self.path).path)
        if path.endswith('.tmp'):
            # 状态行仅 latin-1 安全，中文消息会 UnicodeEncodeError 断连（2026-09-26 修复）
            self.send_error(404, 'Temporary file not accessible')
            return True
        _dir = self.translate_path(path)
        if os.path.isdir(_dir) and not os.path.exists(os.path.join(_dir, 'index.html')):
            self.send_error(404, 'Directory listing not available')
            return True
        return False

    def do_HEAD(self):
        # 2026-10-02：HEAD 必须与 GET 走同一套静态守卫。此前本类未实现 do_HEAD，
        # 基类实现直接吐 Content-Length 200 —— .tmp 与目录列表守卫全部被绕过。
        if self._static_guard():
            return
        super().do_HEAD()

    def do_POST(self):
        if not self._is_local_origin():
            return self._send_json({'ok': False, 'message': '跨站请求被拒绝'}, 403)
        base = self.path.split('?')[0]
        # 写接口凭证校验（2026-09-26 审计 P1-2）：sources 写入与抓取触发需带 token
        if base in ('/api/scrape', '/api/sources') and not _check_admin(self):
            return self._send_json({'ok': False,
                                    'message': '缺少管理凭证（X-Admin-Token）；'
                                               '本机浏览器会自动获取，脚本请读 data/admin_token.json'}, 401)
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
                    # 2026-09-26 审计脱敏：stderr 可能含绝对路径/环境信息，不再回传客户端，
                    # 完整输出落服务端日志（server.py 本就运行在前台，stderr 可见）。
                    tail = (proc.stderr or proc.stdout or '')[-2000:]
                    print(f'[API-ERR] /api/scrape 采集失败 returncode={proc.returncode}')
                    print(tail, file=sys.stderr)
                    self._send_json({'ok': False,
                                     'message': '采集失败（请确认运行 server.py 的 Python 已安装 '
                                                'requests/bs4/rapidocr 等依赖，详见服务端日志）'}, 500)
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
                # 2026-09-26 审计脱敏：异常文本可能含本机路径，仅落日志。
                print(f'[API-ERR] /api/scrape 异常: {e!r}', file=sys.stderr)
                self._send_json({'ok': False, 'message': '抓取失败（详见服务端日志）'}, 500)
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
        if base.startswith('/api/sources/') and not _check_admin(self):
            return self._send_json({'ok': False,
                                    'message': '缺少管理凭证（X-Admin-Token）'}, 401)
        m = self._match_sources_index(base)
        if isinstance(m, int) and m >= 0:
            return self._api_sources_put(m)
        self.send_error(404)

    def do_DELETE(self):
        if not self._is_local_origin():
            return self._send_json({'ok': False, 'message': '跨站请求被拒绝'}, 403)
        base = self.path.split('?')[0]
        if base.startswith('/api/sources/') and not _check_admin(self):
            return self._send_json({'ok': False,
                                    'message': '缺少管理凭证（X-Admin-Token）'}, 401)
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
