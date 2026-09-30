# -*- coding: utf-8 -*-
"""环境变量开关的统一判定（单一事实源）。

背景（2026-09-30 第三方评审 env 漂移项）
------------------------------------------
仓库里同一类「布尔开关」的判定散落在 4 处，关闭值集合**互不一致**：

  scraper/scraper.py:920   SCNU_LISTDATE_SKIP  not in ('0', 'false', 'no')
  scraper/scraper.py:952   SCNU_LEDGER_SKIP    not in ('0', 'false', 'no')
  scraper/cited_judge.py:363  SCNU_JUDGE_SE    not in ('0', 'false', 'no')
  scraper/parsers.py:1546      SCNU_LLM_TEXT   not in ('0', 'false', 'False', '')

后果实测（大小写敏感 → 同一个人为直觉的写法在不同开关上效果相反）：

  写法SCNU_LISTDATE/LEDGER/JUDGE_SE   SCNU_LLM_TEXT/RICH
  '0'     关（正确）                    关（正确）
  'false' 关（正确）                    关（正确）
  'False' **开（误判）**                关（正确）← 唯一把 'False' 写对的
  'no'    关（正确）                    **开（误判）**
  'NO'    **开（误判）**                **开（误判）**
  'off'   **开（误判）**                **开（误判）**
  'Off'   **开（误判）**                **开（误判）**

即：`SCNU_JUDGE_SE=False` 会被当成**开启**（想关反而开着），
`SCNU_LLM_TEXT=no` 也会被当成**开启**。这类错误无任何告警，只是「配置没生效」。

统一后的口径
------------
关闭值 = 大小写不敏感的 {'0', 'false', 'no', 'off'}；其余一律视为开启。
空串/未设置 → 取 default（各调用点原本的默认值保持不变，见flag_ 的 default 参数）。

⚠ **不改变 `'0'` 与 `'1'` 的行为**——仓库内现有全部调用点（含 6 个脚本、golden/snapshot
测试）都只用这两个值，故此改动对现有生产路径零影响。`True/False` 亦按开/关识别。

用法
----
    from env_flags import flag
    if flag('SCNU_LEDGER_SKIP', default='1'):
        ...

⚠ 本模块**刻意不读 .env**。是否读 .env 由各调用点自行决定（历史上 parsers 读、
其余不读，那是另一件事；此处只统一「值怎么算真/假」）。需要 .env 回退的调用点
自行先查 .env 再把值传进来。
"""
import os

#关闭值集合：小写形式。判定时对输入做 .strip().lower() 后比对。
_FALSE_VALUES = frozenset(('0', 'false', 'no', 'off'))


def flag(name, default='1', env=None):
    """读环境变量开关并归一为 True/False。

    :param name: 环境变量名
    :param default: 未设置或空串时的默认语义，**'1'/'true' 等为开，其余按关**处理
                    （沿用旧代码 `(os.environ.get(name) or default) not in (...)` 的形状：
                    default 传 '1' 表示「默认开」，传 '0' 表示「默认关」）
    :param env: 可选的映射（测试用），默认取 os.environ
    :return: bool

    例：flag('SCNU_X', default='1')  未设置 → True（开）
        flag('SCNU_X', default='0')  未设置 → False（关）
    """
    src = os.environ if env is None else env
    raw = src.get(name)
    if raw is None or str(raw).strip() == '':
        raw = default
    return str(raw).strip().lower() not in _FALSE_VALUES


def is_true(value):
    """把任意字符串按flag 的同一口径归一为 bool（供已取出值的场合使用）。"""
    return str(value).strip().lower() not in _FALSE_VALUES
