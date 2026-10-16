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
  夹具 + 逐字段断言。VLM 与 OCR 均走**录制回放**（`tests/fixtures/vlm_cache.json`
  / `ocr_cache.json`，未录制必红）。模块在 import parsers 前 setdefault
  `SCNU_LLM_TEXT/RICH=0` 锁纯规则基线（与 CI 无密钥态一致）——此前本地 .env
  默认开双轨、逐例真实调用大模型，全量 ~17 分钟且随网络波动；现全量 ~10s。
  双轨验证显式 `SCNU_LLM_TEXT=1 python tests/test_parser_golden.py`；
  OCR 重录：`SCNU_OCR_RECORD=1 python tests/test_parser_golden.py` 后提交夹具。

## 3. 数据更新后必须重新生成前端切片

改了 `data/lectures.json` 后依次执行（顺序不可反）：

1. `git add site/*.js`（若前端代码有改动——stamp `_short_hash` 哈希的是 **git
   索引内容**，不是工作区）
2. `python scripts/generate_frontend_data.py`
3. `git add` 生成的 `site/lectures/*.json` 产物
4. commit + push
