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
18. **dedup 误删不同讲座（致命隐性丢数据）**：`scraper.dedup` 原判定键为 `(college, _normalize_title(title))`。当某院列表标题是通用词（如「学术报告通知」「学术讲座信息」），且 §1.3 的 `_clean_title` 已把锚文本里的日期前缀去掉后，多个**不同日期、不同 URL**的讲座会归一化成同一标题 → 撞键被合并成 1 条，其余**静默丢弃**。计算机学院曾因此从列表可达的 42 条掉到 21 条（如 `2682`「学术报告通知」与 `2715`「学术报告通知」撞键只留 1 条，零散丢失、不易察觉）。根因修复：`dedup` 判定键改为 `(college, 归一化标题, 讲座日期, 来源URL)`——**只要 sourceUrl 不同就视为不同讲座，绝不合并且丢弃**；同 URL 真重复仍正确合并（保留字段更完整的）。⚠️ 以后新增/修源若发现某院条数明显少于列表可达数，先怀疑 dedup 而非增量。
19. **首页 / 统计页数字动画定格在旧数 + 统计页访问量为 0**：
    - 数字动画旧实现设了 `CEIL = 950` 软上限，完整 JSON 加载前数字滚到 950 就停下，等好几秒后才跳到真实值（如 1741），造成「卡死」错觉。第一版修复用 `SPEED * sqrt(elapsed)`，但平方根曲线数值增长偏慢（2 秒才到几十），用户反馈「前面太慢、到 1000+ 才快」。最终改为**指数逼近曲线** `v = 1 + (CEIL-1) * (1 - exp(-K*elapsed))`（`CEIL=2200` 软渐近线、`K=0.23`）：起步快（约 1 秒到几百）、随后自然减速，绝不硬定格；数据到达后由 `finalizeCountAnimation` 平滑过渡（见 §3.7.1）。
    - 统计页访问量为 0 的根因是 CSP 太严：`stats.html` 的 `connect-src 'self'` 把 `countapi.xyz` 和不蒜子都拦截了；`index.html` 的 CSP 也没放行 `countapi.xyz`。修复：两页 CSP 统一放行 `https://busuanzi.ibruce.info` 与 `https://api.countapi.xyz`，并让 `loadSiteVisits` 先读 `localStorage` 共享缓存、再按「后端 > countapi > 不蒜子」优先级获取（见 §3.7.2）。


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

### 3.6 ⚠️ 源级「漏抓」问题 → 必须全量重爬（禁用增量）
- **用户明确约定**：一旦某信息源（或某几个源）被指出「有讲座没抓到 / 抓错」，**不再套用「只抓该时间之后发布的新讲座」的增量逻辑**，而要对该源执行 `scraper.py --full --source <name>` 全量重新爬取更新（覆盖该源历史全部讲座），再重生成切片。
- 理由：增量 `since` 只补新讲座、不回头修旧记录；而漏抓的往往是历史老讲座（2015–2022），增量永远补不上。常见根因两类：① 解析器 bug（如 §2.17「号」字、§2.18 dedup 误删）→ 旧记录不会自动更新；② 列表标题/新闻过滤误判 → 须修代码后全量重爬。
- 多个源同时被指出问题时，逐个 `--full --source` **串行**重爬（避免写同一 `data/lectures.json` 互相覆盖）。

### 3.7 首页 / 统计页数字动画与访问量一致性（纯静态部署）

公网无后端，首页先加载 `lectures/latest.json`（50 条）再后台加载 `lectures/lite.json`（全量）；统计页直接加载 `lectures/stats.json`。两页顶部都有「讲座数 / 来源通知数」滚动动画，底部共享站点总访问量。为保证体验与一致性，约定如下：

#### 3.7.1 数字滚动动画：不设硬上限、慢慢滚、数据到达后平滑过渡
- 旧的实现用 `CEIL = 950` 作为滚动软上限，导致 950 这个「历史数字」在屏幕上停留数秒，等完整 JSON 到达后才跳到真实值（如 1741），视觉上像「卡死」。
- 正确做法：`startCountAnimation` 不设硬上限，从 1 开始按**指数逼近曲线** `v = 1 + (CEIL-1) * (1 - Math.exp(-K * elapsed))` 增长（`CEIL = 2200` 为软渐近线、`K = 0.23` 为速率）：起步快（约 1 秒到几百）、随后自然减速逼近软上限，**绝不硬定格在某一数字**；数据到达后调用 `finalizeCountAnimation`，从当前显示值平滑过渡（easeOutCubic，约 700ms）到 `totalCount` / `sourceNoticeCount`。
- 即使完整数据在 3–5 秒后才到，数字也只会滚到一两百，然后自然补到真实值，**不会出现定格在旧数字上的情况**。
- 涉及文件：`site/app.js`（`displayTotal` / `displaySource`）、`site/stats.js`（`displayLecture` / `displaySource`）。

#### 3.7.2 站点总访问量：两页必须同源、共享缓存
- 问题：统计页 CSP 仅允许 `connect-src 'self'`，把 `countapi.xyz` 和不蒜子脚本都拦截了；首页 CSP 又未放行 `countapi.xyz`。结果统计页拿不到访问量，显示 0。
- 正确做法：
  1. 两页 CSP 统一放行 `https://busuanzi.ibruce.info`（script + connect）与 `https://api.countapi.xyz`（connect）。
  2. `loadSiteVisits` 优先级：**本地后端 `/api/visits` > countapi.xyz > 不蒜子**。任一来源成功都把值写入 `localStorage['site_visits_total']`。
  3. 每次进入页面**先读 `localStorage` 缓存**，即使第三方接口暂时失败也不显示 0；接口成功后更新缓存供另一页读取。
  4. 两页使用同一 countapi 命名空间 `lecture-aggregator/site`，与不蒜子站点 PV 语义一致，保证跨页一致。
- 涉及文件：`site/app.js`、`site/stats.js`、`site/index.html`（CSP）、`site/stats.html`（CSP）。

### 3.8 站点访问量：本地独立计数 + 「每年每月」报告（不依赖外部）

#### 3.8.1 外部计数器的本质局限（必须先讲清）
- **纯静态站（GitHub Pages）没有后端**，就不可能在「服务端」聚合访问量。busuanzi / countapi.xyz 这类外部服务扮演的正是「接收每次点击的远端」。
- 它们**只返回累计总数**，永远给不了「按年 / 按月」的明细。因此「每年每个月的访问量」用外部服务**原理上就做不到**——外部接口从未记录过按月数据。
- 外链一旦失效（busuanzi 抽风、countapi 限流/关停），统计就跟着失效或归零。这正是不依赖外部方案的动机。

#### 3.8.2 本地独立计数器（已落地，零外部依赖）
- `server.py` 的 `GET /api/visits` 就是**完全本地**的计数器：把 `{"total": N, "by_day": {"YYYY-MM-DD": 次数}}` 写到 `data/visits.json`，不连任何外部服务。
- 本次升级后它**按本地日期累计 `by_day`**（同一 IP 3 分钟内只计 1 次，防刷），旧格式（仅 `total`）自动兼容为「历史遗留总数」。
- 首页/统计页 `loadSiteVisits` 的**第一优先级就是 `/api/visits`**——所以本地 `server.py` 运行时，统计根本不走外部；只有公网静态版（无后端）才回退到 countapi/不蒜子。

#### 3.8.3 生成「每年每月」报告（独立运行的代码）
- 脚本：`scripts/gen_visits_report.py`（纯标准库，跨平台直接跑）。
  ```
  "D:/Tools/Python 312/python.exe" scripts/gen_visits_report.py
  ```
- 输出：`reports/visits-by-month.html`——**自包含单文件**：数据以 JSON 内联进页面，双击即可在浏览器查看（`file://` 也行）；若由 `server.py` 托管后访问，页面会再拉一次 `/api/visits` 取实时数据刷新。
- 表格结构：行=年份，列=1–12 月，单元格=当月访问量（按数值做热力着色），外加「年计」列、底部「月计/合计」行与概览卡片（累计总数 / 按日明细 / 历史遗留 / 有记录天数 / 起止日期）。
- 该报告**刻意不放进 `site/`**，因此不会被部署到公网；只作本地/自托管查看用。`reports/` 也不入库（生成的产物）。

#### 3.8.4 公网要真正「不依赖外部」怎么做
- GitHub Pages 本身无后端，必须自己**自托管一个计数端点**：把 `server.py`（或只保留 `/api/visits` 的极简服务）跑在可达地址（VPS / 内网穿透 / 自己的机器），把前端 `loadSiteVisits` 第一优先级的 `/api/visits` 指向它（例如改 baseURL 或部署时注入配置）。这样访问量完全自控，外链崩了也不影响。
- 若暂不自托管，公网仍走 countapi/不蒜子当「总访问量」降级来源（仅总数，无按月明细），与本地 `by_day` 互不冲突。

#### 3.8.5 历史月份明细无法补回
- 外部服务从未记录按月数据，过去的月份明细**无法补回**。本报告里的「按年每月」数据，从**启用按日记录之日起**随真实访问累加；当前 `data/visits.json` 里早于按日记录的总数是「历史遗留」，不摊到具体月份。
- `data/visits.json` 已被 `.gitignore` 忽略（属本地运行时数据），不入库、公网也不读取。

---

## 4. 相关文件索引
| 作用 | 路径 |
|------|------|
| 爬虫 CLI | `scraper/scraper.py`（`--source` / `--full` / `--since`） |
| 源配置 | `scraper/sources.yaml` |
| 详情解析 | `scraper/parsers.py` |
| 时间解析 | `scraper/timeparse.py` |
| 切片生成 | `scripts/generate_frontend_data.py` |
| 本地服务/计数 | `server.py`（含 `/api/visits` 按日累计 `by_day`，数据写 `data/visits.json`） |
| 访问量报告 | `scripts/gen_visits_report.py` → `reports/visits-by-month.html`（按年/月，自包含单文件） |
| 主数据 | `data/lectures.json` |
| 静态副本 | `site/lectures.json` + `site/lectures/{latest,lite,stats}.json` |
| 前端 | `site/index.html` + `app.js`；`site/stats.html` + `stats.js` |
| 部署说明 | `deploy.md` |
| 每日自动 | `.github/workflows/daily.yml` + `.github/workflows/deploy.yml` |
| 离线重跑工具 | `tools/reparse_posters.py`、`tools/news_recheck.py`、`tools/clean_location_pollution.py` |
