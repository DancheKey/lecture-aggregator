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
"""
import json
import os
import threading

# 全仓库唯一的 VLM/文本缓存锁（此前 parsers 与 llm_provider 各有一把，互不可见）
_CACHE_LOCK = threading.Lock()

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


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
    """写入缓存。损坏时重建而非失败；异常一律吞掉（缓存是尽力而为的优化）。"""
    with _CACHE_LOCK:
        try:
            p = cache_path()
            data = {}
            if os.path.exists(p):
                try:
                    with open(p, encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    data = {}          # 损坏 → 重建，不让单个键的写入失败
            data[key] = val
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, p)          # 原子替换：读者永远看不到半份文件
        except Exception:
            pass