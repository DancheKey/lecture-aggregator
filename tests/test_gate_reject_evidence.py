# -*- coding: utf-8 -*-
"""拒而未填报警（2026-10-07 拆闸配套）：把「用户肉眼发现主讲人丢了」变成 CI 红灯。

## 背景

8855 事故（maths 2026-10-06）：A 抽到 Quoc-Hung NGUYEN、值与出处都对，却被
人名词表闸门拒绝——llmRejected 只记了字段名，被拒的值与理由双双丢失，网站上
主讲人空白，静默存在直到用户肉眼撞见。本测试扫描生产库，锁定 8855 的签名：

    llmRejected 含 speaker 且 speaker 最终为空（整条链路都没能救回）

新增记录出现该签名 = 高概率误杀丢名，直接红灯（拒绝留证 llmRejectReason
已在同批改造中落地，红灯日志里能看到被拒的原值与拒因）。

## 与语料门禁（test_speaker_corpus_gate）的分工

  - 语料门禁：库内**已有**的 speaker 值必须过词表（词表↔数据一致性，阻断）；
  - 本测试：**新进**数据的「拒绝后仍空」事故签名（数据丢失，speaker 红灯、
    其余字段报告不拦——affiliation/bio/abstract 的拒绝通常是对的（A 幻觉被挡），
    「拒且空」是预期行为而非事故）。

运行：python tests/test_gate_reject_evidence.py
"""
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')

# 「拒且空」按预期处理（不拦）的字段：它们被拒多半是 A 改写/幻觉被正确挡下，
# 且无兜底可救；speaker 例外——被拒且仍空 = 丢人名（8855 签名），必须红。
EXPECTED_EMPTY_FIELDS = {'speakerTitle', 'speakerAffiliation', 'speakerBio', 'abstract'}


def _load_rows():
    if not os.path.exists(DATA_PATH):
        raise unittest.SkipTest('缺少 data/lectures.json')
    with open(DATA_PATH, encoding='utf-8') as f:
        doc = json.load(f)
    rows = doc.get('data') if isinstance(doc, dict) else doc
    if not rows:
        raise unittest.SkipTest('data/lectures.json 为空')
    return rows


class GateRejectEvidenceTest(unittest.TestCase):

    def test_01_speaker被拒不得仍为空(self):
        """8855 签名：speaker 被闸门拒绝、最终又没有其他路径救回 → 红灯。"""
        rows = _load_rows()
        hits = []
        for rec in rows:
            rej = str(rec.get('llmRejected') or '').split('|')
            if 'speaker' in rej and not (rec.get('speaker') or '').strip():
                hits.append((rec.get('sourceUrl') or '?',
                             rec.get('llmRejectReason') or '(无留证，改造前的老记录)'))
        self.assertEqual(
            hits, [],
            '发现 %d 条「speaker 被拒且最终为空」——疑似词表/溯源闸门误杀丢名，'
            '请按 llmRejectReason 核查原值（8855 型事故）：\n%s'
            % (len(hits), '\n'.join('  %s\n    %s' % h for h in hits)))

    def test_02_其余字段拒且空只报告不拦(self):
        """affiliation/bio/abstract「拒且空」多为正确拒绝（无兜底可救），打印分布供观察。"""
        rows = _load_rows()
        stats = {}
        for rec in rows:
            for fld in str(rec.get('llmRejected') or '').split('|'):
                fld = fld.strip()
                if fld and not (rec.get(fld) or '').strip():
                    stats[fld] = stats.get(fld, 0) + 1
        for fld in sorted(stats):
            print('  [观测] %-20s 拒且空 %d 条（预期行为，不拦）' % (fld, stats[fld]))
        unexpected = set(stats) - EXPECTED_EMPTY_FIELDS
        self.assertEqual(unexpected, set(),
                         '出现未归类的「拒且空」字段 %s——请评估是否应纳入 8855 型红灯'
                         % sorted(unexpected))

    def test_03_待核标记分布报告(self):
        """speakerUnverified（拆闸软放行）的量即「待核率」观察曲线（方案第 3 步）。"""
        rows = _load_rows()
        flagged = [(rec.get('sourceUrl') or '?', rec.get('speaker'))
                   for rec in rows if rec.get('speakerUnverified')]
        print('  [观测] speakerUnverified 待核 %d 条 / 共 %d 条' % (len(flagged), len(rows)))
        for url, sp in flagged[:20]:
            print('    %s  speaker=%r' % (url, sp))


if __name__ == '__main__':
    unittest.main(verbosity=2)
