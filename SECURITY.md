# 木铎金声 · 讲座聚合网站 安全分析报告

> 面向「部署到 GitHub Pages / 公网静态托管」场景的安全评估。
> 更新时间：2026-07-11

---

## 一、先厘清架构与真实攻击面

本项目有两种运行形态，安全模型完全不同：

| 形态 | 说明 | 有无后端 | 写操作是否可用 |
|------|------|----------|----------------|
| 本地开发 | `python server.py`，含 `/api/scrape`、`/api/sources` | 有 | 可用（仅本机） |
| 公网发布 | GitHub Pages 等纯静态托管，只有 HTML/JS/JSON | **无** | **不可用** |

**关键结论：** 公网是「只读静态站点」，`/api/*` 后端在 Pages 上根本不存在。因此「访问者随意添加网址」这类写攻击在公网**天然无法生效**（没有服务器接收请求）。但仍存在以下真实风险，本次已逐项处理。

---

## 二、发现的风险与处置

### 🔴 R1. 信息源管理入口暴露（已处理）
- **风险**：`sources.html` + 顶部「信息源管理」入口若公开，会暴露信息源结构，并给访问者「可以增删来源」的错误预期；一旦将来接了后端，就是任意 URL 注入 / SSRF 入口。
- **处置**：
  - 移除 `index.html`、`stats.html` 中所有「信息源管理」链接；
  - `.gitignore` 排除 `site/sources.html`，**该页永不推送到公网仓库 / Pages**，仅保留本地开发使用；
  - 信息源的维护改为本地直接编辑 `scraper/sources.yaml`（或本地跑 server.py 后台）。

### 🔴 R2. 前端 XSS —— 链接协议未校验（已处理）
- **风险**：讲座数据来自爬取的外部高校站点。若某来源页面被篡改，把文章链接写成 `javascript:...` 或 `data:text/html,...`，用户点击标题即执行任意脚本（窃取 localStorage 点赞、钓鱼跳转等）。
- **处置**：`app.js` 新增 `safeUrl()`，标题链接 `:href="safeUrl(l.sourceUrl)"` **仅放行 `http/https`**，其余一律置为 `#`。
- **补充**：其余字段（标题、题目、简介等）均通过 Vue 的 `{{ }}` 输出，Vue 默认对文本做 HTML 转义；全站**无 `v-html`**，无富文本注入面。

### 🟠 R3. 缺少内容安全策略 CSP（已处理）
- **风险**：无 CSP 时，一旦出现注入点，浏览器会无限制加载/执行外部资源。
- **处置**：`index.html`、`stats.html` 增加 `Content-Security-Policy` meta，限定：
  - 脚本仅允许自身 + `cdn.tailwindcss.com` + `unpkg.com`；
  - `object-src 'none'`、`base-uri 'self'`、`form-action 'self'`、`connect-src 'self'`；
  - 同时加 `referrer: no-referrer`，外链跳转不泄露来源。
- **已知代价**：Tailwind Play CDN 与 Vue 运行时编译需要 `'unsafe-eval'`，当前 CSP 保留了它（见 R7 后续加固建议）。

### 🟠 R4. 本地后端绑定 0.0.0.0（已处理）
- **风险**：`server.py` 原绑定 `0.0.0.0`，同一 Wi-Fi/局域网内任何人都能访问你的 `/api/scrape`、`/api/sources`（增删改），可篡改来源、狂发抓取。
- **处置**：默认绑定 `127.0.0.1`（仅本机）。如确需局域网访问需显式 `HOST=0.0.0.0` 并自担风险。

### 🟠 R5. 敏感/内部文件可能被推到公网仓库（已处理）
- **风险**：`.workbuddy/`（开发记忆）、`wechat_accounts.md`（官微调研）、`data/last_scrape.json`、`.env` 类文件若提交到公开仓库会造成信息泄露。
- **处置**：新增 `.gitignore` 统一排除上述文件及 `__pycache__`、`*token*.txt`、`secrets.*` 等。

### 🟡 R6. 抓取触发（路线 X / CI）滥用与 Token 暴露（部署时须落实）
- 这是把「公网点一下就更新」接上 GitHub Actions 时的风险，尚未接入，**现在先预警**：
  - **Token 明文风险**：静态页无保密环境，若把 PAT 直接写进前端 JS，任何人查看源码即可拿到。
  - **触发滥用**：匿名访客反复点「抓取」会空跑 CI，浪费 Actions 额度、触发限流。
- **接入时的正确做法**（届时我来实现）：
  1. Token **绝不进前端**。用一个极小的无服务器代理（Cloudflare Worker / Vercel Function）持有 Token，前端只调代理；
  2. 代理侧加**频率限制**（如每 IP 每 10 分钟 1 次）+ 简单校验；
  3. Token 使用**最小权限**（细粒度 PAT，仅限该仓库、仅 `actions:write` / `contents:write`），并设置到期时间、定期轮换；
  4. GitHub 仓库的 Actions Secrets 用于 CI 内部，天然不外泄。

### 🟡 R7. 静态托管无法设置 HTTP 安全响应头
- GitHub Pages 不能自定义 `X-Frame-Options`、`HSTS`、`X-Content-Type-Options` 等响应头，`frame-ancestors` 也无法通过 meta 生效。
- **建议（可选）**：若在意点击劫持/强制 HTTPS，可在站点前面套一层 **Cloudflare（免费）**，统一注入安全响应头，并顺带获得 CDN、WAF、隐藏源站的收益。

### 🟢 R8. 每日自动抓取工作流（已内置，低风险）
- **机制**：`.github/workflows/daily.yml` 由 GitHub 托管运行，**不经过任何外部服务器**，
  定时（北京时间每天 03:00 / cron `0 19 * * *`）或手动触发后在 GitHub 临时云机器上跑爬虫，
  更新 `data/lectures.json` + `site/lectures.json` 并推送，Pages 再发布。
- **为何风险低**：
  - 工作流**自身不需要任何密钥/Token**——它只用仓库内置的 `GITHUB_TOKEN`（已通过
    `permissions: contents: write` 限权），不接触你的任何凭据；
  - `concurrency` 保证同一时刻只跑一个抓取，避免并发写冲突与重复消耗额度；
  - 爬虫只读外部高校站点、只写本仓库，**无对外网络暴露、无用户输入执行面**；
  - 依赖装自 `scraper/requirements.txt`（固定清单），未执行任何仓库外下载的未知脚本。
- **需注意（已落实/建议）**：
  - ⚠️ 仅 `workflow_dispatch` 手动触发「点击更新」时才涉及 Token，仍遵循 R6 的代理 + 最小权限原则；
  - 仓库若**长期无任何提交**（含本工作流每日的提交），GitHub 会在 60 天后自动停用定时任务；
    本工作流每日都会提交（即使仅刷新 `last_scrape.json`），可保持活跃；
  - 定时任务可能比设定时间**延迟数分钟**，属 GitHub 正常抖动，不影响功能。

---

## 三、其它已确认「无问题」的点

- **无 `v-html` / `innerHTML` 渲染外部数据**（`sources.html` 内有 `innerHTML`，但该页不公开）。
- **点赞**：纯 `localStorage` 本地计数，不涉及后端与他人数据，无越权面；缺点是可被本人清缓存重置（属功能局限，非安全漏洞）。
- **外链**：已统一 `rel="noopener noreferrer"`，杜绝反向标签劫持与来源泄露。
- **无用户账号、无 Cookie、无数据库**：不存在 SQL 注入、会话劫持、越权读写等传统面。

---

## 四、给你的行动清单（配合 GitHub 部署）

1. **仓库可见性**：代码无任何密钥，公开私有均可。若想连内部文档一起备份又不泄露，建议**私有仓库**；用免费公开 Pages 则确保 `.gitignore` 生效（本次已配好）。
2. **Pages 只发布 `site/`**：部署工作流仅发布 `site/` 目录，`server.py`、`scraper/` 等即使在仓库里也不会被当作网页访问（且 `sources.html` 已被 gitignore，不会进入发布物）。
3. **Token**：生成**细粒度 PAT**，权限只勾该仓库 + 最小 scope，设过期时间。**不要**发我明文以外的长期主 Token；用完可随时在 GitHub 吊销。
4. **更新数据**：接入路线 X 后，点「抓取新数据」→ CI 跑爬虫 → 自动发布；在此之前仍是本机跑爬虫后重新部署。

---

## 五、本次改动文件一览

| 文件 | 改动 |
|------|------|
| `site/index.html` | 移除信息源入口；标题链接改 `safeUrl`；加 CSP + referrer meta |
| `site/stats.html` | 移除信息源入口；加 CSP + referrer meta |
| `site/app.js` | 新增 `safeUrl()` 协议白名单 |
| `server.py` | 默认绑定 `127.0.0.1`（本机专用） |
| `.gitignore` | 排除 `sources.html`、`.workbuddy/`、内部资料、凭据文件；**取消忽略 `data/last_scrape.json`**（CI 增量基线需入库） |
| `.github/workflows/daily.yml` | 每日 03:00（UTC 19:00）定时 + 手动触发，自动抓取并提交（R8） |
| `scraper/requirements.txt` | 爬虫依赖固定清单（CI 安装用） |
| `site/.nojekyll` | 防止 GitHub Pages 用 Jekyll 处理静态文件 |
| `site/app.js` | 新增 `safeUrl()`；`scrape()` 支持「工作流触发代理」可选对接（R6） |
| `deploy.md` / `SECURITY.md` | 新增每日自动更新部署说明与安全评估 |
