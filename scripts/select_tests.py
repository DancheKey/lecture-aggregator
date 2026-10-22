#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/select_tests.py — 分层验证：改动文件 → 相关测试子集（2026-10-18 甲方案）。

背景：门禁涨到 677 个 pytest + 21 个 node 套件后，每次改动跑全套
（98.7s + 23.7s）再加纯净检出复跑，单次验证执行成本 5-6 分钟——瓶颈不在
测试数量，在「每次都全量」。本脚本把验证分成三层：

  ① 改动中（秒级）：只跑本脚本输出的 pytest 子集 + node 子集
     （2026-10-18 热缓存实测：趋势页 0.4s / 审计脚本 1.4s / 数据 18s /
     站点 JS 36s / 云端计数 37s / server 45s / 解析器 48s；全套 133s。
     首例有冷启动开销，约 +20s）
  ② 本地 commit 前：同 ①；触及 site/*.js 先重跑 generate_frontend_data.py 刷版本戳
  ③ 推送前：必跑全套（pytest tests/ + node tests/js/*.js）；仅当改动触及
     tests/、.github/、生成器时才做纯净检出复跑（CI 的 deploy gate 会跑同一批
     门禁，纯产物改动无需本机重复）

用法：
  python scripts/select_tests.py                  # 读 git 工作区改动（含未暂存/未跟踪）
  python scripts/select_tests.py --files a b c    # 显式指定改动文件（相对仓库根）
  python scripts/select_tests.py --check          # 映射完整性自检（tests/test_layer_mapping.py 同源）

stdout 契约（两行，机器可读）：
  PYTESTS: ALL | NONE | tests/a.py tests/b.py ...
  NODE:    ALL | NONE | tests/js/x.js tests/js/y.js ...

stderr 是人类可读报告（命中组、耗时提示、推送前清单）。退出码 0 恒为成功
（ALL/NONE 都是正常结果）；1 仅用于参数错误或 git 读取失败。

两类映射，两种防腐化机制：
  - pytest：按主题手工归组（test 文件与模块的对应关系稳定），漏登记由
    check ① 抓（每个 test_*.py 必须被某组收录或列入 FULL_ONLY）。
  - node：**从套件源码机械提取依赖**（readFileSync/path.join(SITE,...)/内联
    python 探针的 import），套件自己声明依赖、映射零维护；提不出依赖的套件
    必须显式列入 NODE_ALWAYS（check ⑥ 强制 conscious 决策）。
  - 两条共同红线：未知文件 → ALL（安全默认：宁可多跑，不可漏跑）；
    每个被跟踪路径必须匹配某条规则或在 DOCS/FULL 显式名单里。
"""
import fnmatch
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- pytest 分组
# name:  组名（报告用）
# pats:  命中模式。'dir/' 结尾 = 递归前缀；含通配符 = fnmatch；否则精确路径
# tests: 该组要跑的 pytest 文件（相对仓库根）
GROUPS = [
    # 元改动：测试自身 / CI / 映射器 → 全套（这些文件决定其他门的公信力）
    {'name': 'full', 'pats': ['tests/', '.github/', 'requirements.txt',
                              '.gitattributes', 'scripts/select_tests.py'],
     'tests': []},
    # 云端计数（Supabase）：app.cloud.js / footer-counter.js / SQL 生成器 / 快照脚本
    {'name': 'cloud', 'pats': ['site/app.cloud.js', 'site/footer-counter.js',
                               'site/supabase-config.json', 'scripts/gen_supabase_sql.py',
                               'scripts/gen_supabase_patch.py', 'scripts/fetch_visits_snapshot.py',
                               'supabase-migration.sql', 'supabase-patch-current.sql'],
     'tests': ['tests/test_supabase_migration.py']},
    # 趋势页（node 门禁 visits_trend_render.js + 资产门禁 test_site_assets：
    # 本页的 icon/og meta 在 gen_visits_trend.py 的模板里，产物会被次日 CI 覆盖，
    # 只改产物不改模板等于没改——故模板一改就连资产守卫一起跑）
    {'name': 'trend', 'pats': ['scripts/gen_visits_trend.py', 'site/visits-trend.html',
                               'site/visits-trend.js', 'site/visits-trend.css',
                               'site/visits-trend-data.json'],
     'tests': ['tests/test_site_assets.py']},
    # 前端站点产物 / 版本戳（test_server 独占 29s，不在此组——见 server 组）
    # test_site_assets（2026-10-20）：图标/og 卡片/sitemap/404 的静态资产守卫，
    # 与 site/ 产物同组——改任何一个页面或生成器都要连它一起跑。
    {'name': 'frontend', 'pats': ['site/', 'scripts/generate_frontend_data.py',
                                  'scripts/frontend_fields.py', 'scripts/tailwind-input.css',
                                  'scripts/tailwind.config.js'],
     'tests': ['tests/test_frontend_schema.py', 'tests/test_frontend_consistency.py',
               'tests/test_module_compile.py', 'tests/test_env_flags.py',
               'tests/test_site_assets.py']},
    # server.py（含它镜像的前端派生逻辑；socket 超时用例使其偏重）
    {'name': 'server', 'pats': ['server.py'],
     'tests': ['tests/test_server.py', 'tests/test_frontend_consistency.py',
               'tests/test_module_compile.py', 'tests/test_env_flags.py']},
    # 解析器（parsers.py 是单体巨文件，拆函数都归这里；env_flags 多处共用）
    {'name': 'parser', 'pats': ['scraper/parsers.py', 'scraper/timeparse.py',
                                'scraper/field_vocab.py', 'scraper/hybrid.py',
                                'scraper/cited_judge.py', 'scraper/llm_provider.py',
                                'scraper/env_flags.py'],
     'tests': ['tests/test_bio_bracket.py', 'tests/test_bio_stop_words.py',
               'tests/test_location_prefix.py', 'tests/test_meta_time_conflict_20261016.py',
               'tests/test_multi_session_split.py', 'tests/test_parser_golden.py',
               'tests/test_parser_snapshot.py', 'tests/test_speaker_aff_glue_20261014.py',
               'tests/test_speaker_corpus_gate.py', 'tests/test_speaker_duty_suffix.py',
               'tests/test_speaker_latin_name.py', 'tests/test_speaker_normalize.py',
               'tests/test_speaker_title_cut.py', 'tests/test_time_fixes_20261004.py',
               'tests/test_time_unknown_autotag.py', 'tests/test_timeparse.py',
               'tests/test_title_clean.py', 'tests/test_title_vocab_convergence.py',
               'tests/test_hybrid.py', 'tests/test_cited_judge.py',
               'tests/test_hallucination_guard.py', 'tests/test_stage_var_leak.py',
               'tests/test_listdate.py', 'tests/test_module_compile.py',
               'tests/test_poster_late_gate.py',
               'tests/test_abstract_provenance.py',
               'tests/test_env_flags.py']},
    # 抓取主流程 / 水位 / 台账 / 缓存 / sources.yaml
    {'name': 'crawl', 'pats': ['scraper/scraper.py', 'scraper/sources.yaml',
                               'scraper/llm_cache.py', 'scraper/env_flags.py',
                               'scraper/requirements.txt', 'scripts/merge_remote.py',
                               'scripts/lectures_writer.py', 'scripts/check_source_health.py',
                               'scripts/excluded_urls.py', 'scripts/test_invariants.py'],
     'tests': ['tests/test_admin_notice_ad3.py', 'tests/test_audit_excluded_records.py',
               'tests/test_data_reconciliation.py', 'tests/test_full_shrink_protection.py',
               'tests/test_gate_reject_evidence.py', 'tests/test_list_page_failure.py',
               'tests/test_main_water_gate.py', 'tests/test_new_source_full_scan.py',
               'tests/test_gate_source_base.py',
               'tests/test_source_count_consistency.py', 'tests/test_ledger.py',
               'tests/test_listdate.py', 'tests/test_llm_cache.py',
               'tests/test_module_compile.py', 'tests/test_env_flags.py']},
    # 主数据（data/ 每日增量都在变；只有人工改 data/ 才值得跑对账组）。
    # scripts/test_invariants.py 是对账被测脚本（被 test_data_reconciliation 调用）
    {'name': 'data', 'pats': ['data/', 'scripts/test_invariants.py'],
     'tests': ['tests/test_data_quality.py', 'tests/test_data_reconciliation.py',
               'tests/test_audit_excluded_records.py', 'tests/test_source_count_consistency.py',
               'tests/test_speaker_corpus_gate.py']},
    # 审计报告脚本（HTML 转义门禁的被测对象）+ 数据回填脚本
    {'name': 'audit', 'pats': ['scripts/audit_fields.py', 'scripts/audit_data_quality.py',
                               'scripts/gen_title_audit_html.py', 'scripts/gen_reparse_review_html.py',
                               'scripts/backfill_abstract.py'],
     'tests': ['tests/test_audit_html_escape.py', 'tests/test_backfill_abstract.py']},
    # 一次性运维工具（fix_/apply_/scan_/resplit_/reparse_）：无任何测试覆盖是现状，
    # 显式列出而非通配——新增工具脚本会逼一次 conscious 决策（见 docstring 红线）。
    {'name': 'tools', 'pats': ['scripts/apply_duty_suffix_fix.py', 'scripts/apply_title_fragment_fix.py',
                               'scripts/fix_psy_speaker_restore.py', 'scripts/resplit_page.py',
                               'scripts/reparse_diff_report.py', 'scripts/scan_title_fragment_residues.py'],
     'tests': []},
    # 纯文档：不触发任何测试
    {'name': 'docs', 'pats': ['README.md', 'AGENTS.md', '.gitignore', '.zcodeignore',
                              '.githooks/'],
     'tests': []},
]

# 只在 FULL 模式跑的 pytest（跨领域元测试；子集组跑它们没有意义）。
# test_layer_mapping 自身也是元测试（锁本映射表）——它和 ci_gate_coverage
# 一样只该在 tests/ 或 .github/ 改动时跑，故入 FULL_ONLY 而非任何子集组。
FULL_ONLY = ['tests/test_ci_gate_coverage.py', 'tests/test_layer_mapping.py']

# ---------------------------------------------------------------- node 依赖提取
# 提不出依赖的套件必须显式列入这里（check ⑥ 强制 conscious 决策）；
# 列入 = 任何改动都跑它（安全但慢，应尽量通过改进提取规则把它摘掉）。
NODE_ALWAYS = []

NODE_DIR = 'tests/js'


def match_pattern(path, pat):
    """路径匹配：'dir/' 递归前缀 / 通配符 fnmatch / 精确相等。"""
    if pat.endswith('/'):
        return path.startswith(pat)
    if any(c in pat for c in '*?['):
        return fnmatch.fnmatch(path, pat)
    return path == pat


def _dep_covers(dep, changed):
    """node 依赖命中判定：dep 是目录或文件，changed 落在其下或相等即命中。"""
    if dep.endswith('/'):
        return changed.startswith(dep)
    return changed == dep or changed.startswith(dep + '/')


def _resolve(segs):
    """归一化路径段：['site','..','scripts','gen_visits_trend.py'] → 'scripts/gen_visits_trend.py'。"""
    out = []
    for s in segs:
        if s == '..':
            if out:
                out.pop()
        elif s != '.':
            out.append(s)
    return '/'.join(out)


def suite_deps(suite):
    """从 tests/js/<suite> 源码机械提取它依赖的仓库文件（自己声明的依赖）。

    提取规则（宁多勿漏——多提只多跑几个套件，漏提才是静默风险）：
      ① path.join(SITE|ROOT, ...) → 取括号内全部字面量段，经 _resolve 归一
        （覆盖 site/index.html、site/../scripts/gen_visits_trend.py 等形态）；
        括号内混有非字面量参数（变量文件名）时额外加 'site/'（保守整站依赖）
      ② readdirSync(SITE) → 'site/'
      ③ readFileSync('site|scripts|data/...') 字面量 → 该路径
      ④ 内联 python 探针：sys.path.insert(0, 'scraper|scripts') 后
         from/import X                     → <base>/X.py（跨语言一致性套件）
    """
    src = open(os.path.join(ROOT, NODE_DIR, suite), encoding='utf-8').read()
    deps = set()
    for m in re.finditer(r"path\.join\(\s*(SITE|ROOT)\s*,\s*([^)]+)\)", src):
        base = 'site' if m.group(1) == 'SITE' else ''
        segs = ([base] if base else []) + re.findall(r"'([^']+)'", m.group(2))
        if not segs:
            continue
        p = _resolve(segs)
        if p:
            deps.add(p)
        # 括号里除了字面量还有变量参数（如 path.join(SITE, f)）→ 保守加整站依赖
        residual = re.sub(r"'[^']*'", '', m.group(2))
        if re.search(r"[A-Za-z_]", residual):
            deps.add('site/')
    if re.search(r"readdirSync\(\s*SITE", src):
        deps.add('site/')
    for m in re.finditer(r"readFileSync\(\s*'((?:site|scripts|data)/[^']+)'", src):
        deps.add(m.group(1))
    for m in re.finditer(r"sys\.path\.insert\(\s*0\s*,\s*'(scraper|scripts)'", src):
        base = m.group(1)
        for im in re.finditer(r"(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", src):
            deps.add(base + '/' + im.group(1) + '.py')
    return deps


def _norm(files):
    """统一为 posix 相对路径，去重去空。

    ⚠ 只剥开头的 './' 两个字，禁用 lstrip('./')——那会把 '.github'/'.gitignore'
    的点前缀一起剥掉（2026-10-18 实测踩过，check 报了 9 个假问题）。
    """
    out = set()
    for f in files:
        f = f.strip().replace('\\', '/')
        while f.startswith('./'):
            f = f[2:]
        if f:
            out.add(f)
    return sorted(out)


def _hit_groups(files):
    """→ (命中组列表, 未匹配文件列表)。两层选择共用同一套命中/未匹配判定。"""
    files = _norm(files)
    hit = [g for g in GROUPS
           if any(match_pattern(f, p) for f in files for p in g['pats'])]
    unmatched = [f for f in files
                 if not any(match_pattern(f, p) for g in GROUPS for p in g['pats'])]
    return hit, unmatched


def select_pytests(files):
    """→ (stdout 契约字符串, 命中组名列表)。ALL / NONE / 空格分隔文件列表。"""
    hit, unmatched = _hit_groups(files)
    if any(g['name'] == 'full' for g in hit):
        return 'ALL', [g['name'] for g in hit]
    if unmatched:
        # 未知文件：无法证明它不影响任何门禁 → 全套。宁可多跑一分钟，不漏一个静默错。
        return 'ALL', [g['name'] for g in hit] + ['<unknown:%s>' % unmatched[0]]
    tests = sorted({t for g in hit for t in g['tests']})
    if not tests:
        return 'NONE', [g['name'] for g in hit]
    return ' '.join(tests), [g['name'] for g in hit]


def select_node(files):
    """→ node 套件选择。依赖机械提取 + NODE_ALWAYS；命中 full 组或未匹配 → ALL。"""
    hit, unmatched = _hit_groups(files)
    if any(g['name'] == 'full' for g in hit) or unmatched:
        return 'ALL'
    suites = set(NODE_ALWAYS)
    for suite in sorted(os.listdir(os.path.join(ROOT, NODE_DIR))):
        if not suite.endswith('.js'):
            continue
        deps = suite_deps(suite)
        if not deps:
            suites.add(suite)  # 提不出依赖 → 保守全跑（check ⑥ 会要求显式登记）
            continue
        if any(_dep_covers(d, f) for d in deps for f in _norm(files)):
            suites.add(suite)
    if not suites:
        return 'NONE'
    return ' '.join(sorted(NODE_DIR + '/' + s for s in suites))


def git_changed_files():
    """git 工作区全部改动：已暂存 + 未暂存 + 未跟踪（porcelain 一次覆盖）。"""
    r = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('git status 失败: ' + (r.stderr or '').strip())
    out = []
    for line in r.stdout.splitlines():
        if len(line) > 3:
            out.append(line[3:].split(' -> ')[-1])
    return _norm(out)


def git_tracked_files():
    r = subprocess.run(['git', 'ls-files'], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('git ls-files 失败: ' + (r.stderr or '').strip())
    return _norm(r.stdout.splitlines())


def check_mapping(tracked=None, test_files=None, node_suites=None):
    """映射完整性自检 → 问题列表（空 = 通过）。纯函数，测试可用合成输入。"""
    if tracked is None:
        tracked = git_tracked_files()
    if test_files is None:
        test_files = _norm('tests/' + f for f in os.listdir(os.path.join(ROOT, 'tests'))
                           if f.startswith('test_') and f.endswith('.py'))
    if node_suites is None:
        node_suites = sorted(f for f in os.listdir(os.path.join(ROOT, NODE_DIR))
                             if f.endswith('.js'))
    problems = []
    # ① 每个 pytest 文件必须被某组收录或列入 FULL_ONLY
    covered = set(FULL_ONLY)
    for g in GROUPS:
        covered.update(g['tests'])
    for t in test_files:
        if t not in covered:
            problems.append('测试文件未被任何组收录（新增测试忘了进映射？）: ' + t)
    # ② 每个被跟踪路径必须匹配某条规则（新源码文件漏映射 → 红）
    for p in tracked:
        if not any(match_pattern(p, pat) for g in GROUPS for pat in g['pats']):
            problems.append('被跟踪路径不匹配任何规则（新文件？请 conscious 归组）: ' + p)
    # ③ 组内登记的 pytest 文件必须存在（防删了测试还留着映射条目）
    existing = set(test_files)
    for g in GROUPS:
        for t in g['tests']:
            if t not in existing:
                problems.append('组 %s 登记了不存在的测试文件: %s' % (g['name'], t))
    # ④ FULL_ONLY 不得同时出现在子集组（避免「以为只跑子集却漏跑元测试」）
    subset_all = {t for g in GROUPS if g['name'] != 'full' for t in g['tests']}
    for t in FULL_ONLY:
        if t in subset_all:
            problems.append('FULL_ONLY 文件又被子集组收录（口径分裂）: ' + t)
    # ⑤ NODE_ALWAYS 必须真实存在
    for s in NODE_ALWAYS:
        if s not in node_suites:
            problems.append('NODE_ALWAYS 登记了不存在的套件: ' + s)
    # ⑥ 每个 node 套件必须能提出依赖或显式列入 NODE_ALWAYS
    for s in node_suites:
        if s in NODE_ALWAYS:
            continue
        if not suite_deps(s):
            problems.append('node 套件提不出依赖且未列入 NODE_ALWAYS: %s'
                            '（改进 suite_deps 提取规则，或 conscious 登记）' % s)
    return problems


def _report(files, py, node, groups):
    """stderr 人类可读报告：命中组 + 两层选择 + 推送前清单 + 生成器提醒。"""
    e = sys.stderr
    print('[分层] 改动文件 %d 个 → 命中组: %s' % (len(files), ', '.join(groups)), file=e)
    print('[分层] PYTESTS: %s' % py, file=e)
    print('[分层] NODE:    %s' % node, file=e)
    need_clean = any(f.startswith(('tests/', '.github/')) or f == 'scripts/select_tests.py'
                     for f in files)
    need_gen = any(f == 'scripts/generate_frontend_data.py' or f == 'scripts/frontend_fields.py'
                   or (f.startswith('site/') and f.endswith(('.js', '.html', '.css')))
                   for f in files)
    print('[分层] 推送前: ①全套 pytest tests/ ②全套 node tests/js/*.js '
          '③纯净检出复跑:%s' % ('需要（触及 tests/.github/）' if need_clean else '不需要'), file=e)
    if need_gen:
        print('[分层] 提醒: 改过 site/*.js 或生成器 → 先跑 '
              'scripts/generate_frontend_data.py 刷版本戳', file=e)


def main(argv):
    if argv and argv[0] == '--check':
        problems = check_mapping()
        for p in problems:
            print('MAPPING-PROBLEM: ' + p, file=sys.stderr)
        print('映射完整性: %s' % ('通过' if not problems else '%d 个问题' % len(problems)),
              file=sys.stderr)
        return 0 if not problems else 1
    files = None
    if argv and argv[0] == '--files':
        files = _norm(argv[1:])
        if not files:
            print('--files 未给出文件', file=sys.stderr)
            return 1
    if files is None:
        try:
            files = git_changed_files()
        except RuntimeError as ex:
            print(str(ex), file=sys.stderr)
            return 1
    py, groups = select_pytests(files)
    node = select_node(files)
    _report(files, py, node, groups)
    print('PYTESTS: ' + py)
    print('NODE: ' + node)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
