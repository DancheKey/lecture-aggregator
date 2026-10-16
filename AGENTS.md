# AGENTS.md

本文件给后续会话（人/AI）的项目级约定。改动时请保持条目简短、可执行。

## 1. 写盘铁律：入库文件必须 LF（Windows CRLF 教训，已三次踩坑）

- Windows 文本模式 `open(path, 'w')` 会把 `\n` 写成 `\r\n`。`.gitattributes`
  （`* text=auto eol=lf`）在 `git add` 时会自动转 LF 入库，无实际损坏，但工作区
  会脏、diff 出现整文件噪音——这是三次踩坑的共同形态。
- **写 `data/lectures.json` 的唯一入口**：`scripts/lectures_writer.py::write_lectures`
  （备份 + 原子写 + ensure_ascii=False + indent=2 + LF）。新修复脚本一律导入使用，
  不要手写 `json.dump` + `os.replace`。注意该脚本按 `.gitignore` 策略**不入库**
  （本地专用，fresh clone 无此文件）；`scripts/fix_*`、`scripts/backfill_*` 等
  一次性脚本同样刻意不入库——这是仓库策略，不是丢文件。
- **写其它入库 JSON/文本**：`open(path, 'w', encoding='utf-8', newline='\n')`。
- 存量已知债（约 15 处裸写，多为一次性 audit/backfill 脚本，未逐一修；写新代码
  时勿模仿）：`backfill_vlm_posters` / `backfill_llm_by_year` / `dedup_existing` /
  `fix_real_8am_lectures` / `fix_time_known_issues` / `merge_remote` /
  `fix_source_count`(out_path) / `clean_data_issues` / `audit_data_quality` /
  `audit_fields` / `verify_missing_fields` / `reverify_fetch_failed` 等。

## 2. 测试分层：不是每次修改都跑 golden

| 改动范围 | 本地跑什么 |
|---|---|
| 前端 JS / `data/` / workflows / 文档 | 不用跑 golden；跑对应快改即可 |
| `scraper/`（parsers/timeparse/hybrid/field_vocab） | 迭代期：针对性测试 + snapshot；**push 前**跑一次全量 golden 兜底 |
| 任何时候 | CI（test.yml/deploy.yml）每次 push 都跑全量，是最终防线；本地漏跑会被拦 |

- golden（`tests/test_parser_golden.py`）：31 个真实历史页面的解析回归锁，HTML
  夹具 + 逐字段断言。VLM 走录制回放（`tests/fixtures/vlm_cache.json`，未录制必红）。
- 已知慢点：海报页 VLM 回放未命中的用例回退 `_do_ocr`，**现场下载图片 + CPU 跑
  rapidocr**，单例 33~73s（全量约 17 分钟）。OCR 录制回放（`_img_to_text` stub，
  键含图片 URL 哈希）落地后应降至分钟级——落地后删除本条。

## 3. 数据更新后必须重新生成前端切片

改了 `data/lectures.json` 后依次执行（顺序不可反）：

1. `git add site/*.js`（若前端代码有改动——stamp `_short_hash` 哈希的是 **git
   索引内容**，不是工作区）
2. `python scripts/generate_frontend_data.py`
3. `git add` 生成的 `site/lectures/*.json` 产物
4. commit + push
