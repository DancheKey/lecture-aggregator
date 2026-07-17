# 解析规则总纲 · 踩坑记录 · 运维约定

> 本文件是 `scnu-lecture-*` 系列 Agent Skill 的「代码化沉淀」。**平台部署到公网后没有 Agent/Skill 可用**，
> 所有「解析经验 / 踩坑来由 / 运维约定」必须写进代码或本仓库文档，才能让人或 CI 自动遵循。
>
> 定位：本文件替代 Skill 成为**权威说明**；代码（`scraper/*.py`、`scripts/*.py`）是规则的**执行体**。
> 两者冲突时以代码注释为准，并请同步更新本文件。

---

## 0. 解析管线（数据从网页到卡片的路径）

```
sources.yaml（学院/校区/列表页）
   │
   ▼
scraper/scraper.py  --source <name> | --full | --since
   │  fetch（_decode_html 编码兜底）→ 列链接（collect_mode / EXCLUDE_TITLE_KW）
   ▼
parsers.parse_detail(html, url, college, campus)
   │  ├─ 找正文容器 content_div（None 时整页收集图片）
   │  ├─ 提取 title/topic/location/speaker/abstract/speakerBio/organizer
   │  ├─ 时间：正文「时间：」标注 > 标题 > URL 路径日期兜底
   │  ├─ OCR（RapidOCR）：纯海报页 / 关键字段缺失 / 讲座页内容不完整 → 补/覆盖
   │  └─ 新闻过滤 is_news_record + is_news_article（两层）
   ▼
data/lectures.json  { updatedAt, data:[...] }     ← 爬虫唯一数据源
   │  cp 同步
   ▼
site/lectures.json  +  scripts/generate_frontend_data.py  →  site/lectures/{latest,lite,stats}.json
   │
   ▼
前端 site/index.html + app.js（GitHub Pages 直接读 site/lectures/lite.json）
```

---

## 1. 解析规则总纲（已固化进代码，平台独立运行自动生效）

### 1.1 OCR 决策（RapidOCR，ONNXRuntime 后端）
- **引擎**：`parsers._img_to_text` 用 RapidOCR（替代原 easyocr：中文海报更准、无 torch/paddle 重型依赖、沙箱不硬崩）。
- **触发**（共同前提：页面含图 `imgs` 非空）：
  - **T1 纯海报页**：`len(body_text) < 50` → 全量 OCR（topic/speaker/location/abstract/speakerBio 全从 OCR 取）。
  - **T2 关键字段缺失**：时间/地点/主讲/题目任一为空 → OCR 仅补缺失、不覆盖正文已正确的字段。
  - **T3 讲座页内容不完整**：标题含讲座类词（讲座/报告/工作坊/沙龙/论坛/研讨会/讲坛/座谈会）且含图 → OCR 抽到日期时**直接以海报日期覆盖** `lectureStart/End`（海报是讲座时间权威源）。
- **图片收集规则**：优先取 `content_div` 内 `<img>`；`content_div` 为 None（非 WebPlus 站点，如图书馆）则退化为整页收集。`src` 用 `urljoin` 绝对化；`_is_chrome_img` 过滤导航/页脚图标（icon/logo/foot/weixin/arrow/banner/qr…）与 svg/gif。
- **字段覆盖优先级**：海报 OCR 日期 > 正文「时间：」标签 > 列表标题 > URL 路径日期（最不可信，仅兜底）。location/topic/speaker 仅当正文缺失才用 OCR 补；abstract/speakerBio 仅当 OCR 已触发且正文无「摘要/简介」标签才补。

### 1.2 时间解析（`parsers.py` + `timeparse.py`）
- **正文显式标注优先**：`时间：2016-12-08 19:30:00` 这类标注是权威年份，**解析它时绝不传 `publish_time`**（否则 `_apply_publish_correction` 会把旧讲座年份抬到重发年，见 §2.2）。
- **中文「点」时间**：支持「下午3点」「上午10点30分」「3点半」→ 叠加上下午偏移。
- **URL 路径日期兜底** `_date_from_url`：华师 CMS 详情页 URL 内嵌完整日期（`/a/20241122/141.html`、`/a/2025/0507/74.html`），是相对时间/海报 OCR 不可用时的主力兜底。
- **年份修正 `_apply_publish_correction`**：**仅当解析年份比发布年早 ≥2 年才抬年**。年差 0/1 的跨年讲座（2025-12 讲座、2026-01 发布）是正常预告，不动。
- **「号」字兼容**：中文日期结尾有「日」也有「号」（如「2023年12月29号」）。`timeparse._parse_segment` 的完整中文日期正则必须同时支持两种结尾，否则完整年份会未被识别，回退到 `M月D日` 并用当前系统年（如 2026）填充，导致未来年污染。`parsers.py` 正文标注递归调用时仍须传 `title_year`/`url_year` 作为年份回退，但不传 `publish_time`。

### 1.3 字段清洗（`parsers.py`）
- **标题 `_clean_title`**：去除列表锚文本粘入的日期前缀（`2024-05-21艺术乡建…` → `艺术乡建…`）；仅匹配「年+月+日」完整日期，正文年份如「2023年越南…」不误删。
- **页脚污染 `_strip_footer`**：按页脚标记（关于华南师范大学/版权所有/粤ICP/常用链接/统一认证…）截断，仅当标记在文本后 30% 才截。
- **地点分离 `_split_location_time`**：把「南教-209教室14:30-17:00」式 OCR 中的时间分离出来，地点只留纯地点。
- **地点标签后缀**：`地点：xxx室 标签：yyy` → `标签` 已加入 location 的 `re.split` 噪声词与 LABELS/STOP。

### 1.4 新闻 vs 预告（两层过滤，`parsers.py`）
- **第一层 `is_news_record`**：`publishTime > lectureStart`（讲座结束后才发布 = 回顾新闻）。但很多学院回顾稿无显式发布时间戳（`publishTime=None`），第一层失效。
- **第二层 `is_news_article(title, body)`**：语义特征兜底——(a) 强总结语（讲座圆满结束/活动取得圆满成功）；(b) 「本次/此次讲座|报告」+ 总结性动词，**排除「将/拟/计划」等前向词**（华师预告页也用「本次报告将介绍…取得」，非新闻）；(c) 标题「举办/开展/举行…讲座」且整体不含「通知/预告/公示」；(d) 页脚「供稿+初审+终审」链；(e) 正文「YYYY年M月D日」+ 完成态动词；(f) 标题机构主语+参加动词。
- **人工复核工具** `tools/news_recheck.py`：重抓 data 每条跑 `is_news_article`，命中写 `tools/news_recheck.json`，`--apply` 删除并同步。

### 1.5 前端切片（`scripts/generate_frontend_data.py`）
- `site/lectures/lite.json`（首屏）与 `latest.json`（前 50 条）**保留全部字段**（含 `abstract`/`speakerBio`）。
- ⚠️ 历史上曾为减小体积剥离这两字段，导致**公网卡片比本地少「简介/内容摘要」两行**——这是切片裁剪不是数据缺失。任何「优化体积」而裁剪字段的冲动，先确认该字段在首页卡片被渲染。

---

## 2. 踩坑记录（每条都是真实踩过的雷，改动相关代码前先读）

1. **编码乱码（最常见）**：老站点用 GBK。`scraper._decode_html` 顺序：`<meta charset>` 声明 → UTF-8 严格 → GB18030 兜底。乱码会让中文日期丢失，仅剩 ASCII 日期被误当讲座时间。
2. **跨年新闻稿年份错位**：见 §1.2 `_apply_publish_correction`，**千万别改回「只要解析年<发布年就抬年」**——会让跨年正常预告被抬年后命中新闻过滤漏掉（国际商学院 3070 即此坑）。
3. **新闻两层过滤缺一不可**：第一层对无 `publishTime` 的回顾稿完全失效，必须靠第二层语义兜底。物理/心理/量子物院等真实讲座也用「本次报告将介绍…」前向句式，第二层必须排除「将/拟/计划」否则误删。
4. **侧边栏噪声**：详情页选错正文容器会抓到侧边栏「新闻推荐 2026-04-10」。优先用列表标题日期；正文「时间：」解析失败不要回退整段搜索。
5. **工作坊算讲座**：`EXCLUDE_TITLE_KW` 已移除「工作坊」（汕尾教学工作坊是真实讲座）。除非明确说某院工作坊不算，否则保留。
6. **负向词过滤**：`EXCLUDE_TITLE_KW` 含 通知/招聘/答辩/公示/大赛/培训/宣讲/论文/发表/成果获… 混入非讲座时优先加 `exclude_urls` 或负向词，而非改解析器。
7. **统计一致性**：首页去重讲座数 = 统计页 `lectureCount`；`sourceNoticeCount`（来源通知数）通常略大。不一致是 `generate_frontend_data.py` 计数口径错。统计页 `stats.json` 须含 `campusMap` 与每条 `s`(sourceCount)。
8. **年份区间误当紧凑日期**：`timeparse` 紧凑日期正则分隔符已收紧为仅空格/制表符 `[ \t]{0,2}`；`_parse_compact_run` 对「4 位数字且前两位 19/20 → 视为年份跳过」，避免「2004-2016 年」被误识成 `2016-02-02`。
9. **OCR 引擎 = RapidOCR**：仅当正文 <50 字才懒加载 OCR；批量修数据无需禁用 OCR；个别海报图不可读时 OCR 静默返回空串，解析走纯文本 + URL 日期兜底。
10. **旧讲座批量重发 → URL/侧边栏日期全错**：某些学院（如 `geography.scnu.edu.cn/learning/`）把 2016–2024 旧讲座在某天批量重发，URL/`publishTime`/侧边栏日期全指向重发日，唯一可信是正文「时间：…」标注。须信正文标注且**解析时不传 publish_time**（见 §1.2、§2.2）。
11. **标题日期前缀 + 地点「标签：」污染**：见 §1.3。修复后需删旧记录重抓（§3 增量陷阱）。
12. **公网卡片比本地少字段**：见 §1.5。改 `generate_frontend_data.py` 后需手动 `git add` 该脚本（daily.yml 只 add 数据/site 切片，不 add 脚本本身）。
13. **speakerBio 误触发**：海报「简介」二字常被标题误触发，使 `speakerBio` 变整段海报文字。守卫：若 `speakerBio` 来自 OCR 且含 `时间/地点/日期` 关键词则清空。
14. **OCR 沙箱硬崩**：RapidOCR 已无 torch 依赖，批量修数据无需禁用；若个别环境崩，`parsers._img_to_text = lambda img: ''` 置空即可降级。
15. **URL 路径日期被 SLASH_MONTHDAY 误匹配**：URL 路径 `2025/0507/` 中的 `25/05` 会被误当「月=25/日=05」触发异常。`timeparse._build` 已加 mo∈[1,12]、d∈[1,31]、y>0 校验，非法返回 None 回退其他模式。
16. **数据双份一致性**：`data/lectures.json`（爬虫产出）与 `site/lectures.json`（Pages 实际读）必须一致；手动改数据后务必 `cp data/lectures.json site/lectures.json`。
17. **正文时间标注里的「号」字导致未来年污染**：中文日期结尾有「日」也有「号」（如「2023年12月29号下午14:00」）。`timeparse._parse_segment` 完整中文日期正则只写了 `\s*日`，没兼容 `号`，导致命中正文 `时间：2023年12月29号` 后完整年份未被识别，回退到 `M月D日` 并用 `default_year=当前系统年`（如 2026）填充，生成 `2026-12-29` 这种错误。根因修复：`timeparse._parse_segment` 第 1 步改为 `\s*[日号]`。同时 `parsers.py` 高优先级时间标注递归调用传入 `title_year`/`url_year` 作为年份回退（仍不传 `publish_time`，防旧讲座重发被抬年）。修复后必须删旧记录重抓（计算机学院 2026-12-29 等 4 条即此修复）。


---

## 3. 运维约定（代码无法自解释，必须记牢）

### 3.1 ⚠️ 增量陷阱（最重要）
- `scraper.py --source` 以 `data/lectures.json` 为基底，**已抓过的 URL 不会再次下载解析**。
- 后果：① 解析器升级/修 bug 后旧讲座**不会自动修正**；② 某条旧记录错了，daily 自动跑**永远修不了**。
- 修复步骤（详见 `deploy.md` §3 警示框）：从 `data/lectures.json` 删该 URL → `--source <院>` 重抓 → `generate_frontend_data.py` → `cp` 同步 → commit/push。
- 多源串行跑（都写同一 `data/lectures.json`，并行会互相覆盖）。

### 3.2 数据双份 + 同步
- `data/lectures.json` 是唯一数据源；`site/lectures.json` 是 GitHub Pages 实际读取的静态副本。
- **手动改数据后**：`python scripts/generate_frontend_data.py` 生成 `site/lectures/{latest,lite,stats}.json`，再 `cp data/lectures.json site/lectures.json`。
- `data/last_scrape.json` 是每日增量基线，**须入库**（`.gitignore` 已注明），否则每次全量重扫。

### 3.3 daily.yml 的边界
- `.github/workflows/daily.yml` 每日 03:00（UTC 19:00）跑 `scraper/scraper.py` 增量 → 同步 `site/` → 提交推送 → Pages 重发布。
- daily.yml 只 `git add` 数据文件和 `site/` 切片，**不 add `scripts/generate_frontend_data.py` 等脚本**。改了脚本须**手动 commit** 并 push，否则下次自动跑仍用旧脚本生成切片。
- daily.yml 已适配 RapidOCR：移除 easyocr 的 `libgl1` 系统依赖与 `~/.EasyOCR` 模型缓存步骤；RapidOCR 模型随包分发 + `opencv-python-headless` 无需 GL 库。

### 3.4 本地预览（开发用）
- `server.py` 默认 `127.0.0.1:8000`；WorkBuddy 沙箱会拦截浏览器连接，必须 `dangerouslyDisableSandbox: true` 后台启动。
- 前端 `server.py` 走 `/api/lectures` 读完整 `data/lectures.json`；公网无后端走 `site/lectures/lite.json`。两者字段集须一致（见 §1.5），否则公网卡片比本地少字段。
- `start_local.bat` 须纯 ASCII（Windows `cmd` 默认 GBK，中文注释会破坏命令）。

### 3.5 推送约定
- 本项目约定：**不主动 `git push`**，本地验证无误、人确认后才推。调试工具（`tools/`）与本地 bat 已 `.gitignore` + `git rm --cached` 排除，不入库。
- 推送触发 `deploy.yml`（`on: push: [main]`）自动部署 `site/` 到 Pages，约 1–2 分钟生效；浏览器可能缓存旧 JSON，硬刷新 `Ctrl+Shift+R`。

---

## 4. 相关文件索引
| 作用 | 路径 |
|------|------|
| 爬虫 CLI | `scraper/scraper.py`（`--source` / `--full` / `--since`） |
| 源配置 | `scraper/sources.yaml` |
| 详情解析 | `scraper/parsers.py` |
| 时间解析 | `scraper/timeparse.py` |
| 切片生成 | `scripts/generate_frontend_data.py` |
| 主数据 | `data/lectures.json` |
| 静态副本 | `site/lectures.json` + `site/lectures/{latest,lite,stats}.json` |
| 前端 | `site/index.html` + `app.js`；`site/stats.html` + `stats.js` |
| 部署说明 | `deploy.md` |
| 每日自动 | `.github/workflows/daily.yml` + `.github/workflows/deploy.yml` |
| 离线重跑工具 | `tools/reparse_posters.py`、`tools/news_recheck.py`、`tools/clean_location_pollution.py` |
