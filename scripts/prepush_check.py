#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/prepush_check.py — 推送前一键 GO / NO-GO 自检（2026-11-06 评审 NEW1）。

背景：版本戳漏刷、workflow YAML 语法、测试误用 pytest、纯净检出复跑、
origin/main 追踪引用陈旧、Windows schannel 502 —— 这几轮反复踩的坑原先只写在
笔记里，靠人记。本脚本把「推送前四条 + 分层验证规则」变成一次可执行判定。

**只判定，不自动修复、不自动推送、不写任何仓库文件**（纯净检出解到系统临时目录）。
复用现成门禁与现成分层脚本，不重造判定逻辑——门禁改了这里跟着变，不会分叉。

用法（在仓库根执行）：
  python scripts/prepush_check.py                 # 全项检查（含 fetch）
  python scripts/prepush_check.py --no-network   # 离线：跳过 fetch 与远端比对
  python scripts/prepush_check.py --skip-clean   # 跳过纯净检出复跑（省时）
  python scripts/prepush_check.py --files a b c  # 显式指定本次改动文件

退出码：0 = GO（可以推）；1 = NO-GO（有硬性失败项）。WARN 不影响 GO。

⚠ 操作约定（NEW2）：origin 的 SSH / HTTPS 混用曾让 refs/remotes/origin/main
长期不刷新，假报「N 条未推送」。今后 **fetch 与 push 一律写 `origin main`**
（`git fetch origin main` / `git push origin main`），不要对裸 URL 操作；
本脚本的第 ⑥ 项也按这个口径刷新引用。Windows 上若遇 TLS/schannel 502，
统一加 `GIT_SSL_BACKEND=openssl`（第 ⑥ 项已带）。
"""
import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
WF_FILES = ('.github/workflows/test.yml', '.github/workflows/deploy.yml',
            '.github/workflows/daily.yml')
# 触及这些路径时，纯净检出复跑按分层规则必须做（与 select_tests.py 的说明一致）
CLEAN_TRIGGERS = ('tests/', '.github/', 'scripts/select_tests.py',
                  'scripts/generate_frontend_data.py', 'scripts/gen_visits_trend.py')

results = []


def record(name, ok, detail, warn=False):
    results.append((name, ok, detail, warn))
    tag = 'WARN' if warn else ('PASS' if ok else 'FAIL')
    print(f'[{tag}] {name}: {detail}')
    return ok


def run(cmd, cwd=ROOT, env=None, timeout=600):
    """统一走 list 形式直调，禁止拼字符串命令（bash 假绿陷阱）。"""
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=cwd, env=e, capture_output=True,
                          text=True, timeout=timeout)


def short(out, limit=400):
    s = (out or '').strip().replace('\r', '')
    return s[-limit:] if len(s) > limit else s


def changed_files(explicit):
    if explicit:
        return list(explicit)
    # 推送前最后一次提交视为「本轮改动」；只有一个根提交时退回空列表。
    r = run(['git', 'diff', '--name-only', 'HEAD~1', 'HEAD'])
    if r.returncode != 0:
        r = run(['git', 'show', '--pretty=format:', '--name-only', 'HEAD'])
    return [x.strip() for x in (r.stdout or '').splitlines() if x.strip()]


def check_clean_tree():
    r = run(['git', 'status', '--porcelain'])
    if r.returncode != 0:
        return record('① 工作区干净', False, 'git status 失败：' + short(r.stderr))
    lines = [x for x in (r.stdout or '').splitlines() if x.strip()]
    if lines:
        return record('① 工作区干净', False,
                      f'还有 {len(lines)} 个未提交改动（前 3 条：'
                      + ' / '.join(x.strip()[:70] for x in lines[:3]) + '）')
    return record('① 工作区干净', True, '无未提交改动')


def check_stamps():
    # 复用现成门禁：版本戳、工作区/索引换行一致、孤儿资源都在这一个文件里。
    r = run([PY, 'tests/test_frontend_schema.py'])
    if r.returncode == 0:
        return record('② 版本戳门禁', True, 'test_frontend_schema.py 全绿')
    return record('② 版本戳门禁', False,
                  '先 git add -A site/ 再重跑生成器（二轮收敛）：' + short(r.stdout + r.stderr))


def check_workflows():
    try:
        import yaml
    except ImportError:
        return record('③ workflow YAML', False, '缺 PyYAML：pip install -r requirements.txt')
    problems = []
    node_jobs = 0
    for rel in WF_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            problems.append(f'{rel} 不存在')
            continue
        try:
            with open(path, encoding='utf-8') as f:
                doc = yaml.safe_load(f)
        except Exception as e:
            problems.append(f'{rel} safe_load 失败：{type(e).__name__}: {e}')
            continue
        jobs = (doc or {}).get('jobs') or {}
        if not jobs:
            problems.append(f'{rel} 解析不到任何 job（total_count=0 先查 YAML 语法）')
        for job_name, job in jobs.items():
            steps = (job or {}).get('steps') or []
            if not steps:
                problems.append(f'{rel}:{job_name} 没有 steps')
            uses = ' '.join(str(s.get('uses') or '') for s in steps if isinstance(s, dict))
            runs = ' '.join(str(s.get('run') or '') for s in steps if isinstance(s, dict))
            # #32：凡跑 node 门禁的 job 必须固定 Node 版本，否则跨时区测试可能空转。
            # 两种形态都要认：test.yml 的 `node tests/js/x.js` 直跑，以及 deploy.yml
            # gate 里 GATE_TESTS 循环的 `cmd="node $t"`（后者极易被漏掉）。
            if re.search(r'\bnode\s+(?:tests/|\$t\b)', runs):
                node_jobs += 1
                if 'actions/setup-node@' not in uses:
                    problems.append(f'{rel}:{job_name} 跑 node 门禁却没 setup-node')
    if problems:
        return record('③ workflow YAML', False, '；'.join(problems[:4]))
    return record('③ workflow YAML', True,
                  f'{len(WF_FILES)} 个 workflow 语法正常、jobs > 0；{node_jobs} 个 node job 已固定版本')


def check_no_pytest():
    bad = []
    tests_dir = os.path.join(ROOT, 'tests')
    for fn in sorted(os.listdir(tests_dir)):
        if not fn.endswith('.py'):
            continue
        with open(os.path.join(tests_dir, fn), encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                if re.match(r'\s*(import\s+pytest\b|from\s+pytest\b)', line):
                    bad.append(f'{fn}:{i}')
    if bad:
        return record('④ 测试纯 unittest', False,
                      'CI 按文件直跑、装不了 pytest：' + ', '.join(bad[:5]))
    return record('④ 测试纯 unittest', True, 'tests/*.py 无 pytest 依赖')


def check_layer_mapping():
    r = run([PY, 'scripts/select_tests.py', '--check'])
    if r.returncode == 0:
        return record('⑤ 分层映射自检', True, 'select_tests.py --check 通过')
    return record('⑤ 分层映射自检', False, short(r.stdout + r.stderr))


def clean_checkout(files, skip):
    if skip:
        return record('⑥ 纯净检出复跑', True, '按 --skip-clean 跳过', warn=True)
    if not any(f.startswith(t) if t.endswith('/') else f == t
               for f in files for t in CLEAN_TRIGGERS):
        return record('⑥ 纯净检出复跑', True,
                      '改动未触及 tests/ .github/ 生成器，按分层规则无需复跑', warn=True)
    sel = run([PY, 'scripts/select_tests.py', '--files'] + files)
    py_line = next((x for x in (sel.stdout or '').splitlines()
                    if x.startswith('PYTESTS:')), '')
    node_line = next((x for x in (sel.stdout or '').splitlines()
                      if x.startswith('NODE:')), '')
    py_arg = py_line.split(':', 1)[1].strip() if py_line else 'ALL'
    node_arg = node_line.split(':', 1)[1].strip() if node_line else 'ALL'
    py_tests = ([os.path.join('tests', f) for f in sorted(os.listdir(os.path.join(ROOT, 'tests')))
                 if f.startswith('test_') and f.endswith('.py')]
                if py_arg == 'ALL' else py_arg.split())
    node_tests = (['tests/js/' + f for f in sorted(os.listdir(os.path.join(ROOT, 'tests', 'js')))
                   if f.endswith('.js')]
                  if node_arg == 'ALL' else node_arg.split())
    tmp = tempfile.mkdtemp(prefix='lecture_prepush_')
    try:
        arc = subprocess.run(['git', 'archive', '--format=tar', 'HEAD'], cwd=ROOT,
                             capture_output=True, timeout=120)
        if arc.returncode != 0 or not arc.stdout:
            return record('⑥ 纯净检出复跑', False, 'git archive HEAD 失败：' + short(arc.stderr))
        with tarfile.open(fileobj=io.BytesIO(arc.stdout)) as tf:
            tf.extractall(tmp, filter='data')
        # git archive 解出来的是「纯文件」，不是 git 仓库；而 test_layer_mapping /
        # test_ci_gate_coverage / test_frontend_schema 会用 git ls-files、
        # git show :path 判定文件是否入库与 blob hash。在临时目录里 init + add
        # （内容就是 HEAD，blob 与真实仓库同源），让这些门在纯净环境里**真跑**
        # 而不是静默 skip —— 否则「纯净检出全绿」是假的。
        init = run(['git', 'init', '-q'], cwd=tmp)
        add = run(['git', 'add', '-A'], cwd=tmp) if init.returncode == 0 else init
        if init.returncode != 0 or add.returncode != 0:
            return record('⑥ 纯净检出复跑', False,
                          '纯净检出建 git 索引失败：' + short(init.stderr + add.stderr))
        fails = []
        for t in py_tests:
            r = run([PY, t], cwd=tmp)
            if r.returncode != 0:
                fails.append(t + ' → ' + short(r.stdout + r.stderr, 200))
        for t in node_tests:
            try:
                r = run(['node', t], cwd=tmp)
            except FileNotFoundError:
                return record('⑥ 纯净检出复跑', False, '本机找不到 node，无法复跑 node 套件')
            if r.returncode != 0:
                fails.append(t + ' → ' + short(r.stdout + r.stderr, 200))
        if fails:
            return record('⑥ 纯净检出复跑', False,
                          f'{len(py_tests)} py + {len(node_tests)} node 中失败 {len(fails)} 个：'
                          + '；'.join(fails[:3]))
        return record('⑥ 纯净检出复跑', True,
                      f'纯净检出直跑 {len(py_tests)} 个 py + {len(node_tests)} 个 node 全绿')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_remote(no_network):
    if no_network:
        return record('⑦ 远端引用一致', True, '按 --no-network 跳过', warn=True)
    env = {'GIT_SSL_BACKEND': 'openssl',
           'GIT_SSH_COMMAND': 'ssh -o BatchMode=yes'}
    r = run(['git', '-c', 'http.version=HTTP/1.1', '-c', 'core.compression=0',
             'fetch', 'origin', 'main'], env=env, timeout=180)
    if r.returncode != 0:
        return record('⑦ 远端引用一致', False,
                      'fetch origin main 失败（Windows 可加 GIT_SSL_BACKEND=openssl）：'
                      + short(r.stderr))
    c = run(['git', 'rev-list', '--left-right', '--count', 'HEAD...origin/main'])
    if c.returncode != 0:
        return record('⑦ 远端引用一致', False, 'rev-list 失败：' + short(c.stderr))
    parts = (c.stdout or '').split()
    ahead, behind = (int(parts[0]), int(parts[1])) if len(parts) == 2 else (0, 0)
    if behind:
        return record('⑦ 远端引用一致', False,
                      f'落后远端 {behind} 个提交，需先 rebase/合并（禁止 force-push）')
    return record('⑦ 远端引用一致', True,
                  f'领先 {ahead} / 落后 {behind}；'
                  + ('推送命令：GIT_SSL_BACKEND=openssl git push origin main' if ahead
                     else '无待推送提交'))


def main():
    ap = argparse.ArgumentParser(description='推送前 GO / NO-GO 自检')
    ap.add_argument('--no-network', action='store_true', help='跳过远端 fetch 与引用比对')
    ap.add_argument('--skip-clean', action='store_true', help='跳过纯净检出复跑')
    ap.add_argument('--files', nargs='*', default=None, help='显式指定本次改动文件')
    args = ap.parse_args()
    files = changed_files(args.files)
    print(f'本轮改动文件 {len(files)} 个'
          + ('：' + ', '.join(files[:5]) if files else '（未取到，按 ALL 处理）'))
    print('-' * 72)
    check_clean_tree()
    check_stamps()
    check_workflows()
    check_no_pytest()
    check_layer_mapping()
    clean_checkout(files, args.skip_clean)
    check_remote(args.no_network)
    print('-' * 72)
    hard = [r for r in results if not r[1] and not r[3]]
    warns = [r for r in results if r[3]]
    for name, _ok, detail, _w in hard:
        print(f'  阻断：{name} — {detail}')
    print(f'结论：{"NO-GO" if hard else "GO"}'
          f'（{len(results) - len(hard) - len(warns)} PASS / {len(warns)} WARN / {len(hard)} FAIL）')
    return 1 if hard else 0


if __name__ == '__main__':
    sys.exit(main())
