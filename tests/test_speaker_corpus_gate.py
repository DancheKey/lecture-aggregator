# -*- coding: utf-8 -*-
"""语料门禁（2026-10-07 拆闸配套）：生产库全部非空 speaker 反钉人名词表。

## 背景

speaker 词表（_SURNAME_RE 姓氏表/英文形态）历史上四轮人肉补漏——09-01、09-05、
09-24、10-06——每次都是「线上丢名字 → 用户肉眼发现 → 事后补表」。本测试把
「存量数据必须能通过当前词表」固化为 CI 阻断：任何收紧词表的改动，若会误杀
生产库里已在的真实姓名，合并前即红（拆闸方案第 1 步的基线：9/3033 失败 →
补表 + 清脏后归零）。

⚠ 这是**阻断性**门禁（与 test_gate_reject_evidence 的报警区分）：
  - 本测试红 = 词表/数据不一致，必须修（补姓或清脏）才能合并；
  - 拆闸第二刀（F3 词表降级为 doubt 标记）落地时，本测试需同步降为度量，
    因为彼时库内将合法存在 speakerUnverified 的「姓表查无」值。

运行：python tests/test_speaker_corpus_gate.py
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

# ⚠ 必须在 import parsers 之前关掉双轨（与 test_speaker_latin_name 同理）：
# 本套件断言的是纯规则词表判定，不掺模型行为，避免本地/CI 环境漂移。
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'

import parsers as P  # noqa: E402

DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')


def _load_rows():
    if not os.path.exists(DATA_PATH):
        raise unittest.SkipTest('缺少 data/lectures.json')
    with open(DATA_PATH, encoding='utf-8') as f:
        doc = json.load(f)
    rows = doc.get('data') if isinstance(doc, dict) else doc
    if not rows:
        raise unittest.SkipTest('data/lectures.json 为空')
    return rows


class SpeakerCorpusGateTest(unittest.TestCase):
    """生产库每个非空 speaker（含多讲者逐段）都必须通过严格口径词表。"""

    def test_00_全量speaker通过人名词表(self):
        rows = _load_rows()
        seen = set()
        failures = []
        for rec in rows:
            sp = (rec.get('speaker') or '').strip()
            if not sp:
                continue
            segs = [s.strip() for s in re.split(r'[、,，]', sp) if s.strip()] or [sp]
            for seg in segs:
                if seg in seen:
                    continue
                seen.add(seg)
                if not P._looks_like_real_name(seg):
                    failures.append((seg, rec.get('sourceUrl') or '?',
                                     rec.get('speakerSource')))
        self.assertEqual(
            failures, [],
            '生产库 %d 个去重 speaker 值中 %d 个未通过词表——'
            '若是真名请补 _SURNAME_RE/分隔符集（并在用例里留档），'
            '若是脏值请用 scripts/ 清理：\n%s'
            % (len(seen), len(failures),
               '\n'.join('  %r  src=%s  %s' % f for f in failures)))

    def test_01_拆闸已知真名回归钉(self):
        """四轮人肉补漏 + 本轮补表的全部真名，逐一钉死防回退。"""
        for name in ('初景利', '化柏林', '盖雯雯', '骈文景', '卿前恺', '迟国泰',
                     '帅青红', '代志新', '华胜亚',        # 09-24 补
                     '黄佩瑶', '揭建文',                    # 09-05/09-01 补
                     '关淑华', '奉国和', '员巧云',          # 10-07 补
                     '吴重庆',                              # 10-07 黑名单消歧
                     'Quoc-Hung NGUYEN',                    # 10-06 连字符
                     'Timi O’Neill',                        # 10-07 弯引号
                     'Yiu Por (Vincent) Chen'):             # 10-07 括号注记
            self.assertTrue(P._looks_like_real_name(name),
                            '真实姓名被词表误杀（拆闸回归）：%r' % name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
