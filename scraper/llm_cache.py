# -*- coding: utf-8 -*-
"""LLM/VLM 结果缓存（共享实现，2026-10-02）。

## 背景：两把锁对同一个文件

`data/.vlm_cache.json` 被两个模块同时读写：

  · scrapers/parsers.py      —— VLM（海报图片结构化），_VLM_CACHE_LOCK
  · scraper/llm_provider.py  —— 文本 LLM（双轨抽取），_CACHE_LOCK

两把锁**互不可见**，而两个模块都是「整文件读 → 改一个键 → 整文件写回」。
单进程 5 线程同时跑文本抽取与 VLM 抽取时，后写者会拿自己那份「读时快照」
覆盖文件，把另一通道刚写入的条目**整批丢掉**——表现为缓存命中莫名下降、
重复调用付费 API（2026-09-30 评审实锤；parsers.py 的原注释自称已修读改写竞争，
实际只修了自己那一半：把单模块内的竞态消了，跨模块的那一半原封不动）。

## 修法

把缓存收敛到本模块：同一把进程内锁 + 同一份读写路径，两个调用方都走它。
锁是**进程内**的——多进程并发（如 --out 分批重抓）由「原子替换」保证
读者不会读到半份文件，但两个进程之间仍可能互相覆盖条目；
那属于既有的、可接受的取舍（key 由内容哈希派生，重跑幂等，
最坏是缓存未命中而非数据错误），不在本次修复范围。

## 为什么不换成 sqlite / jsonl

改动面会大很多（两个模块的调用点形态不同），而当前痛点是「丢缓存 → 重复烧额度」，
共享一把锁即可根治。体积增长到需要换存储时再单独评估。

## 2026-10-14：写侧瞬时占用导致静默丢条目（test_53 本机偶红根因）

`os.replace(tmp, p)` 在 Windows 上是「删旧 + 改名」两步，目标文件被
AV / 索引器 / 上一次替换的收尾**瞬时占用**时会抛 `PermissionError: WinError 5`。
原实现的兜底是 `except Exception: pass`——该条写入静默消失（表现为缓存命中率
莫名下降、重复烧额度），且现场无任何痕迹。实测（tmp/repro_cache_race.py：
200 线程 × 8 轮）3 轮丢键，插桩证实丢键时刻 100% 伴随 replace 的 WinError 5，
读路径零异常。

修法：① 写 + 替换做 3 次小退避重试；② 读侧区分「真损坏（JSON 解不开）→ 允许
重建」与「瞬时不可读（OSError）→ 放弃本次写入」，后者**绝不**用空字典覆盖整份
文件（那会把一次瞬时故障放大成整库清空）；③ 两种最终失败都打 [WARN] 到 stderr
（与 llm_provider._llm_warn 同口径），不再静默。
"""
import json
import os
import sys
import threading
import time

# 全仓库唯一的 VLM/文本缓存锁（此前 parsers 与 llm_provider 各有一把，互不可见）
_CACHE_LOCK = threading.Lock()

# 瞬时占用重试（2026-10-14）：WinError 5 多为毫秒级窗口，三档退避足够
_WRITE_BACKOFF = (0.005, 0.02, 0.05)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _cache_warn(msg):
    """缓存写失败告警（stderr）。缓存是尽力而为的优化，但失败必须留痕。"""
    print('[WARN] llm_cache: %s' % msg, file=sys.stderr)


def _load_existing(p):
    """读回已有缓存内容。

    返回 dict（含空）表示可继续写；返回 None 表示**瞬时不可读**——
    调用方必须放弃本次写入，不得当成损坏去重建（否则一次瞬时故障会
    用只含单个键的字典覆盖整份缓存）。
    """
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding='utf-8') as f:
            txt = f.read()
    except OSError:
        return None
    try:
        d = json.loads(txt)
    except Exception:
        return {}          # 真损坏（内容解不开）→ 允许重建，见 test_55
    return d if isinstance(d, dict) else {}


def cache_path():
    """缓存文件路径（data/.vlm_cache.json，不入库）。"""
    return os.path.join(_ROOT, 'data', '.vlm_cache.json')


def cache_get(key):
    """取缓存值；文件缺失/损坏一律返回 None（缓存不可用不应阻断主链路）。"""
    with _CACHE_LOCK:
        try:
            p = cache_path()
            if not os.path.exists(p):
                return None
            with open(p, encoding='utf-8') as f:
                return json.load(f).get(key)
        except Exception:
            return None


def cache_set(key, val):
    """写入缓存。损坏时重建而非失败；瞬时 IO 故障重试，最终失败留痕。

    ⚠ 语义边界（2026-10-14）：只有「文件内容解不开（真损坏）」才重建；
    「读/写瞬时失败（OSError）」一律放弃本次写入并告警——绝不用空字典
    覆盖已有条目。缓存不可用可以接受，静默丢一整库不行。
    """
    with _CACHE_LOCK:
        p = cache_path()
        data = _load_existing(p)
        if data is None:
            _cache_warn('读缓存瞬时失败（OSError），跳过本次写入以免覆盖已有条目')
            return
        data[key] = val
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
        except OSError as e:
            _cache_warn('创建缓存目录失败: %s: %s' % (type(e).__name__, e))
            return
        tmp = p + '.tmp'
        last = None
        for i in range(len(_WRITE_BACKOFF) + 1):
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, p)      # 原子替换：读者永远看不到半份文件
                return
            except OSError as e:
                last = e
                if i < len(_WRITE_BACKOFF):
                    time.sleep(_WRITE_BACKOFF[i])
        # 重试耗尽：清理残片并告警（不静默——静默会表现为「缓存莫名命中率低」）
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        _cache_warn('写入缓存失败（重试 %d 次仍 OSError）: %s: %s'
                    % (len(_WRITE_BACKOFF), type(last).__name__, last))