# -*- coding: utf-8 -*-
"""信息源健康检查（2026-09-18）：主动探测 sources.yaml 中每个栏目 URL 的可达性。

为什么需要它：
    已有的「失败源告警」（daily.yml 末尾 + scraper.failed_sources）只能捕获爬虫在
    解析过程中**抛出的异常**。但 scraper.fetch() 对 4xx/5xx 是「返回 None 不抛异常」
    （scraper.py:174-177），而 collect_links(None) 又静默返回空列表（scraper.py:237-238）——
    因此**学院把栏目网址换掉、旧链接 404 时，爬虫不会报错**，failed_sources 为空，
    维护者无从知晓，外在表现只是「这个学院好久没有新讲座了」。
    本脚本直接探测每个 list_url 的 HTTP 状态，把这种「静默失效」变成「主动告警」。

判定：
    - HTTP 200 且响应体 ≥ MIN_BYTES       → ok
    - HTTP 200 但响应体 < MIN_BYTES       → suspect（疑似空页，如 200/69B 的占位页）
    - HTTP 非 200 / 超时 / 连接失败        → http / error（内部重试 RETRIES 次，过滤瞬时抖动）
    - 源本身 list_urls 为空                → empty（配置缺失，需补新网址）

告警：
    出现异常时在 GitHub 仓库创建 / 更新一条 Issue（标题以 [源健康检查] 开头），
    借助 GitHub 的邮件通知提醒维护者。仓库已有同名 open Issue 时改为**追加评论**，
    且「异常集合与上次相同」时跳过评论，避免每天刷屏；全部恢复时评论并自动关闭。

退出码：
    **始终为 0**——告警由 Issue 承载，不把工作流变红，避免与既有「失败源告警」的
    红色混淆（后者语义是「抓取失败需重试」，本脚本语义是「源可能已变更，需人工更新」）。

用法：
    python scripts/check_source_health.py
    python scripts/check_source_health.py --dry-run     # 只打印，不写盘、不建 Issue
    python scripts/check_source_health.py --no-issue    # 写台账但不建 Issue（本地调试）
"""
import os
import sys
import json
import time
import argparse
import datetime
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_PATH = os.path.join(ROOT, 'scraper', 'sources.yaml')
HEALTH_PATH = os.path.join(ROOT, 'data', 'source_health.json')

ISSUE_TITLE_PREFIX = '[源健康检查]'
# 与 scraper.py 的 HEADERS 保持一致：若源站对爬虫标识放行、对空 UA 拦截，
# 健康检查必须用同一身份探测，否则会产生与真实抓取不一致的误报。
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SCNULectureAggregator/0.1)'}
TIMEOUT = 12
RETRIES = 2
MIN_BYTES = 300          # 200 响应小于该字节数 → 疑似空页
WORKERS = 4              # 并发度：不同域名并行、同域最多 1~2 个（源通常只有 1~2 个栏目）
HISTORY_KEEP = 60        # 台账保留最近若干次检查
# 失效比例高于此值时不建 Issue：CI 跑在海外机器上，大面积同时失败更可能是出口网络
# 异常（超时/限流）而非所有源站一起失效，此时建 Issue 只会产生误导性告警。
MASS_FAILURE_RATIO = 0.5
CST = datetime.timezone(datetime.timedelta(hours=8))


def _warn(msg):
    print('::warning::' + msg)


def load_sources():
    import yaml
    with open(SOURCES_PATH, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    return (cfg or {}).get('sources') or []


def probe(url):
    """探测单个 URL，返回 (status, detail)。status ∈ {'ok','suspect','http','error'}。"""
    last = 'unknown'
    for i in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                code = getattr(r, 'status', None) or r.getcode()
                body = r.read(MIN_BYTES + 1)
            if code == 200:
                if len(body) < MIN_BYTES:
                    return 'suspect', 'HTTP 200 但响应仅 %d 字节（疑似空页）' % len(body)
                return 'ok', 'HTTP 200'
            last = 'HTTP %d' % code
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                # 4xx 是确定性错误（网址已失效 / 无权限），重试无意义，立即上报
                return 'http', 'HTTP %d' % e.code
            last = 'HTTP %d' % e.code
        except Exception as e:
            last = '%s: %s' % (type(e).__name__, e)
        if i < RETRIES - 1:
            time.sleep(2)
    if last.startswith('HTTP '):
        return 'http', last
    return 'error', last


def _task(item):
    src, url = item
    st, detail = probe(url)
    time.sleep(0.3)  # 礼貌性抖动
    return {'source': str(src.get('name') or ''), 'campus': str(src.get('campus') or ''),
            'url': url, 'status': st, 'detail': detail}


def run_checks(sources):
    tasks, results = [], []
    for src in sources:
        lus = src.get('list_urls') or []
        if not lus:
            results.append({'source': str(src.get('name') or ''),
                            'campus': str(src.get('campus') or ''), 'url': '',
                            'status': 'empty', 'detail': '未配置栏目 URL（list_urls 为空）'})
            continue
        for lu in lus:
            url = lu.get('url') if isinstance(lu, dict) else lu
            if url:
                tasks.append((src, str(url)))
    if tasks:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            results.extend(ex.map(_task, tasks))
    # 保持「按 sources.yaml 顺序」输出，便于对照
    order = {}
    for i, s in enumerate(sources):
        order[str(s.get('name') or '')] = i
    results.sort(key=lambda r: (order.get(r['source'], 999), r['url']))
    return results


def load_health():
    if not os.path.exists(HEALTH_PATH):
        return {'history': []}
    try:
        with open(HEALTH_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (ValueError, OSError) as e:
        _warn('台账读取失败，将重建（%s：%s）' % (type(e).__name__, e))
        return {'history': []}


def save_health(data):
    os.makedirs(os.path.dirname(HEALTH_PATH), exist_ok=True)
    tmp = HEALTH_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, HEALTH_PATH)


def build_issue_body(results, now_str, counts):
    fails = [r for r in results if r['status'] in ('http', 'error')]
    suspects = [r for r in results if r['status'] == 'suspect']
    empties = [r for r in results if r['status'] == 'empty']
    lines = ['## 信息源健康检查告警', '',
             '检查时间：%s' % now_str,
             '检查范围：%d 个源 / %d 个栏目 URL；失效 %d、疑似空页 %d、未配置 %d。'
             % (counts['totalSources'], counts['totalUrls'],
                len(fails), len(suspects), len(empties)), '']
    if fails:
        lines += ['### 失效栏目（HTTP 错误 / 网络异常）', '',
                  '| 学院 | 校区 | 栏目 URL | 状态 |', '|---|---|---|---|']
        for r in fails:
            lines.append('| %s | %s | %s | %s |' % (r['source'], r['campus'], r['url'], r['detail']))
        lines.append('')
    if suspects:
        lines += ['### 疑似空页（HTTP 200 但内容过短）', '']
        for r in suspects:
            lines.append('- %s（%s）%s —— %s' % (r['source'], r['campus'], r['url'], r['detail']))
        lines.append('')
    if empties:
        lines += ['### 未配置栏目 URL（长期待补，仅供参考）', '']
        for r in empties:
            lines.append('- %s（%s）' % (r['source'], r['campus']))
        lines.append('')
    lines += ['---',
              '> 本 Issue 由 CI 自动维护：后续检查异常集合有变化时追加评论，全部恢复后自动关闭。',
              '> 若某学院只是**更换了栏目网址**，请更新 `scraper/sources.yaml` 中该源的 `list_urls`。']
    return '\n'.join(lines)


def _gh(repo, token, path, method='GET', payload=None):
    url = 'https://api.github.com/repos/%s%s' % (repo, path)
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer %s' % token)
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('User-Agent', HEADERS['User-Agent'])
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read()
    return json.loads(raw.decode('utf-8')) if raw else {}


def find_open_issue(repo, token):
    items = _gh(repo, token, '/issues?state=open&per_page=100')
    for it in items:
        if 'pull_request' in it:
            continue
        if (it.get('title') or '').startswith(ISSUE_TITLE_PREFIX):
            return it['number']
    return None


def notify_issue(results, now_str, counts, cur_keys, prev_keys):
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    repo = os.environ.get('GITHUB_REPOSITORY')
    if not token or not repo:
        print('未检测到 GITHUB_TOKEN / GITHUB_REPOSITORY，跳过 Issue 告警（本地运行属正常）')
        return
    # 只有「失效 / 疑似空页」参与 Issue 的创建、更新与关闭判断；
    # 未配置（empty）不计入，否则一条待补事项会让 Issue 永久 open。
    bad = [r for r in results if r['status'] in ('http', 'error', 'suspect')]
    try:
        issue_no = find_open_issue(repo, token)
        if bad:
            if issue_no is None:
                _gh(repo, token, '/issues', 'POST',
                    {'title': '%s %d 个信息源栏目异常' % (ISSUE_TITLE_PREFIX, len(bad)),
                     'body': build_issue_body(results, now_str, counts)})
                print('已创建告警 Issue')
            elif cur_keys != prev_keys:
                detail = '\n'.join('- `%s` %s' % (r['status'], r['url'] or r['source']) for r in bad)
                _gh(repo, token, '/issues/%d/comments' % issue_no, 'POST',
                    {'body': '### 本次检查（%s）异常集合有变化\n\n%s' % (now_str, detail)})
                print('已在 Issue #%d 追加更新' % issue_no)
            else:
                print('异常集合与上次一致，跳过评论（避免刷屏）')
        elif issue_no is not None:
            _gh(repo, token, '/issues/%d/comments' % issue_no, 'POST',
                {'body': '### 本次检查（%s）全部正常\n\n所有信息源栏目均可达，自动关闭本 Issue。' % now_str})
            _gh(repo, token, '/issues/%d' % issue_no, 'PATCH', {'state': 'closed'})
            print('已关闭告警 Issue #%d（全部恢复）' % issue_no)
    except Exception as e:
        _warn('Issue 告警失败，本次仅落盘（%s：%s）' % (type(e).__name__, e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只打印，不写盘、不建 Issue')
    ap.add_argument('--no-issue', action='store_true', help='写台账但不建 Issue')
    args = ap.parse_args()

    if not os.path.exists(SOURCES_PATH):
        _warn('找不到 sources.yaml：%s' % SOURCES_PATH)
        return 0
    try:
        sources = load_sources()
    except Exception as e:
        _warn('sources.yaml 解析失败（%s：%s）' % (type(e).__name__, e))
        return 0

    print('开始信息源健康检查：%d 个源' % len(sources))
    results = run_checks(sources)

    now = datetime.datetime.now(CST)
    now_str = now.strftime('%Y-%m-%dT%H:%M:%S+08:00')
    today = now.strftime('%Y-%m-%d')

    fails = [r for r in results if r['status'] in ('http', 'error')]
    suspects = [r for r in results if r['status'] == 'suspect']
    empties = [r for r in results if r['status'] == 'empty']
    # 台账与日志记录全部异常；触发 Issue 告警的只有「失效 / 疑似空页」——
    # 未配置（empty）属已知待补缺口（sources.yaml 注释中已标明），若一并告警会长期占着
    # 一条 open Issue 变成噪声，故只在台账里体现。
    bad = fails + suspects + empties
    alerting = fails + suspects
    counts = {'totalSources': len(sources),
              'totalUrls': sum(1 for r in results if r['url']),
              'bad': len(alerting)}

    print('检查完成：正常 %d / 失效 %d / 疑似空页 %d / 未配置 %d'
          % (counts['totalUrls'] - len(fails) - len(suspects), len(fails), len(suspects), len(empties)))
    for r in bad:
        print('  [%s] %s（%s）%s —— %s'
              % (r['status'], r['source'], r['campus'], r['url'] or '(无 URL)', r['detail']))

    cur_keys = sorted(r['url'] for r in alerting)

    hist = load_health()
    prev_keys = set()
    if hist.get('history'):
        prev_keys = set(hist['history'][-1].get('failedKeys') or [])

    hist['lastCheckedAt'] = now_str
    hist['lastResults'] = results
    h = hist.setdefault('history', [])
    h.append({'date': today, 'checkedAt': now_str,
              'totalUrls': counts['totalUrls'],
              'failCount': len(fails), 'suspectCount': len(suspects),
              'unconfiguredCount': len(empties),
              'failedKeys': cur_keys})
    hist['history'] = h[-HISTORY_KEEP:]

    if alerting:
        _warn('信息源健康检查发现 %d 个失效/可疑栏目（详见 data/source_health.json 与告警 Issue）'
              % len(alerting))
    if empties:
        print('注：%d 个源尚未配置栏目 URL（长期待补）：%s'
              % (len(empties), '、'.join(r['source'] for r in empties)))

    if args.dry_run:
        print('--dry-run：未写盘、未建 Issue')
        return 0

    save_health(hist)
    print('已写入 %s（历史 %d 条）'
          % (os.path.relpath(HEALTH_PATH, ROOT), len(hist['history'])))
    if args.no_issue:
        print('--no-issue：跳过 Issue 告警')
        return 0

    checked = counts['totalUrls']
    if alerting and checked and len(alerting) > checked * MASS_FAILURE_RATIO:
        _warn('失效比例过高（%d/%d），疑似 CI 出口网络异常而非源站失效，本次跳过 Issue 告警'
              % (len(alerting), checked))
        return 0

    notify_issue(results, now_str, counts, cur_keys, prev_keys)
    return 0


if __name__ == '__main__':
    sys.exit(main())
