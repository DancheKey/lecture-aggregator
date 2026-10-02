#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 门禁清单一致性守卫（2026-09-30 引入）。

背景：本日打通 CI 时发现 deploy.yml 是单 job、无 `needs`，与 test.yml 互不阻塞
——「测试全红照样上线」。修复采用**在 deploy.yml 内加 gate job 重跑同一套门禁**
（而非 `on: workflow_run`跨工作流门禁），代价是门禁清单在两个工作流里各存一份。

两份清单一旦漂移，后果是隐蔽的：新增测试只加进 test.yml → gate 少跑一项，
门禁形同虚设，且**没有任何报错**。故本测试把两份清单当作被测数据，逐项比对。

三条断言：
  ① deploy.yml 的 GATE_TESTS 覆盖 test.yml 里全部 `python tests/...` / `node tests/...` 步骤
  ② test.yml 里不存在「有测试文件但两个工作流都没引用」的游离测试
  ③ deploy.yml 的 deploy job 确实 `needs: gate`（否则门禁跑完也不拦）

只用标准库 + 极简 YAML 行扫描（不引 pyyaml：test.yml 里有注释与多行标量，
完整解析反而脆弱；此处只需把`run:` 行里的路径 token 抓出来）。
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF_DIR = os.path.join(ROOT, '.github', 'workflows')
TEST_YML = os.path.join(WF_DIR, 'test.yml')
DEPLOY_YML = os.path.join(WF_DIR, 'deploy.yml')
TESTS_DIR = os.path.join(ROOT, 'tests')

# 本测试自身必须在两侧清单里（它是防漂移的那一个）
SELF = 'tests/test_ci_gate_coverage.py'

_CMD_RE = re.compile(r'(?:python|node)\s+((?:tests/[\w./-]+\.(?:py|js)))')


def _referenced(path):
    """抓出该工作流里所有被 `run:` 引用到的测试脚本路径（去重、保持出现顺序）。"""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    seen, out = set(), []
    for m in _CMD_RE.finditer(text):
        rel = m.group(1).replace('\\', '/')
        if rel not in seen:
            seen.add(rel)
            out.append(rel)
    return out


def _gate_env_list():
    """从 deploy.yml 的 GATE_TESTS 环境变量里解析清单（YAML 的 `>-` 折叠标量）。"""
    with open(DEPLOY_YML, encoding='utf-8') as f:
        lines = f.readlines()
    out, in_block = [], False
    for line in lines:
        if re.match(r'\s*GATE_TESTS:\s*>-\s*$', line):
            in_block = True
            continue
        if in_block:
            # 折叠标量的续行是更深的缩进；遇到更浅缩进即结束
            if line.strip() and len(line) - len(line.lstrip()) <= 8:
                break
            m = _CMD_RE.search(line) or re.search(r'(tests/[\w./-]+\.(?:py|js))', line)
            if m:
                out.append(m.group(1).replace('\\', '/'))
    return out


def _all_test_files():
    out = []
    for sub in ('', 'js'):
        d = os.path.join(TESTS_DIR, sub) if sub else TESTS_DIR
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if sub:
                if fn.endswith('.js'):
                    out.append(f'tests/js/{fn}')
            elif fn.endswith('.py') and fn.startswith('test_'):
                out.append(f'tests/{fn}')
    return out


class GateCoverageTest(unittest.TestCase):
    def setUp(self):
        self.test_yml_refs = _referenced(TEST_YML)
        self.gate_refs = _gate_env_list()

    def test_00_两侧清单都非空(self):
        self.assertTrue(self.test_yml_refs, '未从 test.yml 解析出任何测试引用——解析逻辑坏了')
        self.assertTrue(self.gate_refs, '未从 deploy.yml 解析出 GATE_TESTS——解析逻辑坏了')
        self.assertIn(SELF, self.gate_refs, f'门禁清单必须含本测试：{self.gate_refs}')

    def test_01_门禁覆盖testyml全部引用(self):
        """gate 的门禁必须 ⊇ test.yml 的引用（少一项 = 门禁被架空）。"""
        missing = [t for t in self.test_yml_refs if t not in self.gate_refs]
        self.assertEqual(missing, [],
                         f'deploy.yml 的 GATE_TESTS 漏了：{missing}')

    def test_02_无游离测试(self):
        """任何测试文件都必须被至少一个工作流引用（否则改了没人跑）。"""
        allf = _all_test_files()
        # tests/fetch_fixtures.py 是测试辅助模块、非 test_ 前缀，本就不进门禁
        referenced = set(self.test_yml_refs) | set(self.gate_refs)
        orphans = [t for t in allf if t not in referenced]
        self.assertEqual(orphans, [],
                         f'这些测试未被任何工作流引用：{orphans}')

    def test_03_deploy_必须依赖gate(self):
        """门禁跑完却没拦住部署，等于没有门禁。"""
        with open(DEPLOY_YML, encoding='utf-8') as f:
            text = f.read()
        m = re.search(r'^  deploy:\n((?:    .*\n|\n)+)', text, re.M)
        self.assertIsNotNone(m, 'deploy.yml 里找不到 deploy job')
        block = m.group(1)
        self.assertRegex(block, r'needs:\s*gate',
                         'deploy job 未声明 needs: gate —— 门禁不生效')

    def test_04_门禁不得吞掉失败(self):
        """门禁脚本必须「有任一失败即非零退出」，且 set -euo pipefail 在位。"""
        with open(DEPLOY_YML, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('set -euo pipefail', text, '门禁脚本缺 set -euo pipefail')
        self.assertIn('exit 1', text, '门禁失败时未 exit 1')
        # 循环体不得用|| true 之类吞掉失败
        self.assertNotRegex(text, r'if\s+!\s+\$\w+;\s*then\s+rc=0',
                            '门禁失败分支被改成恒成功（失败被吞）')


class GateSanityTest(unittest.TestCase):
    """锁住「为什么不用 workflow_run」这个决策，避免日后有人「优化」回去。"""

    def test_05_未改回跨工作流门禁(self):
        """若有人把 deploy.yml 的 on: 改成 workflow_run，本测试提醒重新评估。

        workflow_run 的坑：只在其工作流文件已存在于默认分支时触发；
        test.yml 一旦被改名/删除，部署会**静默不再触发**，公网日更无声停摆。
        """
        with open(DEPLOY_YML, encoding='utf-8') as f:
            text = f.read()
        head = text[:text.index('jobs:')]
        self.assertNotIn('workflow_run', head,
                         'deploy.yml 改用了 workflow_run 跨工作流门禁——'
                         '请重新评估「test.yml 被改名则静默停发」的风险')


class CronDocConsistencyTest(unittest.TestCase):
    """定时班次的时刻：workflow 配置与两处文案必须一致（2026-10-02）。

    背景：cron 时刻在三个地方各写了一份——
      ① `.github/workflows/daily.yml` 的 `on.schedule`（唯一事实源）
      ② `docs/deploy.md`（部署文档）
      ③ `site/app.admin.js` 的降级 toast 文案（访客在页面上看到）

    实测已漂移两次：文档写 03:00/17:00 UTC（实际 01:00/16:00），页面文案写
    「11:00 与次日 01:00」（实际 09:00 与次日 00:00）。后果不是功能故障而是
    **误导**：访客/维护者按文案去Actions 查结果，发现「这个点还没跑」，
    进而怀疑流水线挂了。三处各改一处必然还会漂第四次，故固化为断言。

    注：docs/ 在 .gitignore 内（本地文档），故该用例在 CI 上会自动跳过——
    保留它是为了本地跑全量门禁时能拦住漂移。
    """

    DAILY_YML = os.path.join(WF_DIR, 'daily.yml')
    DOC = os.path.join(ROOT, 'docs', 'deploy.md')
    ADMIN_JS = os.path.join(ROOT, 'site', 'app.admin.js')

    def _cron_hours(self):
        """从 daily.yml 取两班的**小时**（cron 格式 '分 时 日 月 周'，第 2 段是小时）。"""
        with open(self.DAILY_YML, encoding='utf-8') as f:
            text = f.read()
        hs = []
        for m in re.finditer(r"-\s*cron:\s*['\"]?(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*['\"]?", text):
            hs.append(int(m.group(2)))          # group(1)=分，group(2)=时
        self.assertEqual(len(hs), 2,
                         f'daily.yml 的 cron 班次数异常（解析到 {hs}）——'
                         f'改班次时请同步 deploy.md 与 site/app.admin.js 的文案')
        return sorted(hs)

    def test_06_cron班次为两班且升序(self):
        a, b = self._cron_hours()
        self.assertLess(a, b)

    def test_07_文档时刻与daily_yml一致(self):
        if not os.path.exists(self.DOC):
            self.skipTest('docs/ 不入库（本地文档），跳过')
        a, b = self._cron_hours()
        with open(self.DOC, encoding='utf-8') as f:
            text = f.read()
        want_utc = {'%02d:00' % a, '%02d:00' % b}
        bj = {'%02d:00' % ((a + 8) % 24), '%02d:00' % ((b + 8) % 24)}

        # 只在**与 cron/班次相关**的行里比对时刻，避免把文档别处的时刻
        # （如「超时 08:00」「保留 30 天」之类）误当成班次。
        lines = [l for l in text.splitlines()
                 if ('cron' in l or '北京时间' in l or 'UTC' in l or '自动更新' in l
                     or '自动抓取并更新' in l)]
        code = '\n'.join(lines)
        # 排除「历史错值说明」行（如「本文档写的是 03:00/17:00 UTC，与实际配置差 2 小时」）：
        # 那是纠错记录而非当前配置，计入会造成假红。
        code = '\n'.join(l for l in lines if '实测发现' not in l)

        for u in want_utc:
            self.assertIn(u, code, f'daily.yml 的 {u} UTC 未出现在 deploy.md 的班次描述里')
        for t in bj:
            self.assertIn(t, code, f'docs/deploy.md 未写北京时间 {t}（与 daily.yml 不符）')
        # 反向：班次描述行里不得出现其它时刻
        stale = set(re.findall(r'\b\d{2}:\d{2}\b', code)) - want_utc - bj
        self.assertEqual(stale, set(),
                         f'docs/deploy.md 的班次描述里残留与 daily.yml 不符的时刻：'
                         f'{sorted(stale)}')

    def test_08_页面文案与daily_yml一致(self):
        if not os.path.exists(self.ADMIN_JS):
            self.skipTest('site/app.admin.js 不存在')
        a, b = self._cron_hours()
        want = {'%02d:00' % ((a + 8) % 24), '%02d:00' % ((b + 8) % 24)}
        with open(self.ADMIN_JS, encoding='utf-8') as f:
            text = f.read()
        # 只看 toast 文案（排除注释里的说明）
        code_lines = [l for l in text.splitlines() if 'showToast' in l and not l.strip().startswith('//')]
        code = '\n'.join(code_lines)
        found = set(re.findall(r'\b\d{2}:\d{2}\b', code))
        self.assertTrue(found, '未能从 toast 文案里解析出任何时刻')
        for t in found:
            self.assertIn(t, want,
                          f'页面文案写「{t}」与 daily.yml 推出的北京时间 {sorted(want)} 不符')
        for t in want:
            self.assertIn(t, found, f'页面文案缺少班次时刻 {t}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
