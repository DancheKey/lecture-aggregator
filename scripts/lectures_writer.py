# -*- coding: utf-8 -*-
"""主库统一原子写（2026-09-27 二轮审计 P1-4 收敛）。

此前 8 个修复脚本各自 json.dump 直写 data/lectures.json——`open(path,'w')`
**先清空再写**，中途崩溃会留下 0 字节/截断文件；其中 2 个连备份都没有。
这正是 test_invariants「空库 PASS」护栏最现实的触发源。全部收敛到本函数：

  1) 备份：data/lectures.json.bak-<时间戳>-<tag>（与仓库既有备份惯例一致）；
  2) 原子写：先写 .tmp 再 os.replace，任何时刻磁盘上都有一份完整文件；
  3) 格式铁律：ensure_ascii=False + indent=2 + LF（与主库现状一致）。

用法：
    from lectures_writer import write_lectures
    write_lectures(data, tag='fix_xxx')     # data = 完整 {updatedAt, data:[...]}
"""
import os
import json
import shutil
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, 'data', 'lectures.json')


def write_lectures(data, tag='writer', backup=True, path=DATA_PATH):
    if backup and os.path.exists(path):
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        bak = f'{path}.bak-{ts}-{tag}'
        shutil.copyfile(path, bak)
        print(f'已备份 → {bak}')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    print(f'落库完成（原子写）: {path}')
