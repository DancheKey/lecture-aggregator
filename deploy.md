# 木铎金声 · 华南师范大学讲座聚合 — 部署说明

> 当前已上线公网演示（CloudStudio 静态托管）：
> **https://54d91f8875ba42539336af61185dbd02.app.codebuddy.work**
> 数据：115 条讲座，覆盖 2020–2026 年。

---

## 一、当前架构与发布约束

```
site/                 ← 前端（Vue3 + Tailwind CDN，纯静态）
  index.html          首页（信息流 + 筛选 + 点赞）
  stats.html          学院/部处 × 年份 统计表
  sources.html        信息源管理（需后端）
  app.js / style.css
  lectures.json       ← 静态数据源（部署副本，由脚本从 data/ 同步）
  scnu-emblem.png / motto.png / site-title.png
data/lectures.json    ← 爬虫产出的唯一数据源
scraper/               ← Python 爬虫（requests + bs4 + RapidOCR）
server.py              ← 本地开发/全栈后端（静态托管 + /api/*）
```

**关键约束**：网站原本依赖 `server.py` 提供 `/api/lectures`、`/api/scrape`、`/api/sources`，
因此默认只能在「本机 + 常驻 Python 进程」下运行。要放到公网，有两条路线：

- **方案 A（已采用）纯静态部署**：把数据预先生成为 `site/lectures.json`，前端直接读取，
  不依赖后端。优点是零运维、免费/廉价、不怕崩；缺点是「网页上点抓取」「信息源管理」不可用
  （这两个功能需后端，保留给本地或全栈部署，并在前端做了友好降级提示）。
- **方案 B 全栈部署**：保留 `server.py` 全部功能（含网页抓取），需一台公网云服务器 + 守护进程 + 域名。

---

## 二、方案 A：纯静态部署（推荐，当前已用）

### 1. 更新数据后重新发布

数据由爬虫在**本机**生成，再同步到 `site/` 并重新部署：

```bash
# 1) 本机运行爬虫（增量抓取新讲座）
cd scraper && python scraper.py          # 或 python scraper.py --since <上次时间>

# 2) 同步数据到前端静态目录
cp data/lectures.json site/lectures.json

# 3) 重新部署（CloudStudio / 见下方其它托管）
```

> 仅 CloudStudio 部署：在本项目对话里说「重新部署 site 目录」即可；
> 其它平台按各自方式上传 `site/` 整个目录。

### 2. 部署到其它静态托管（任选其一，大多免费）

| 平台 | 做法 |
|------|------|
| **CloudStudio** | 已部署；重部署时上传 `site/` 目录 |
| **GitHub Pages** | 把 `site/` 推到仓库 `gh-pages` 分支，`Settings → Pages` 选该分支根目录 |
| **Vercel / Netlify** | 拖入 `site/` 目录，或连接 Git 仓库的根目录设为 `site/` |
| **腾讯云 COS / 阿里云 OSS** | 上传 `site/` 为静态网站，配合 CDN 加速 |
| **自有服务器 Nginx** | `root` 指向 `site/`；`location / { try_files $uri $uri/ /index.html; }` |

> 纯静态托管下，若未配置「工作流触发代理」，`index.html` / `stats.html` 的「抓取新数据」
> 会提示「网站已配置每日凌晨 3 点自动更新…」；`sources.html` 不可用（属管理后台）。

### 3. 自动每日更新（GitHub Actions，推荐）

把仓库推到 GitHub 后，已内置 `.github/workflows/daily.yml`：

- **定时**：北京时间**每天凌晨 3:00**（cron `0 19 * * *`，GitHub 用 UTC）自动运行爬虫增量更新；
- **手动**：Actions 页面 `Run workflow`，或通过网站「抓取新数据」按钮经代理触发（见 SECURITY.md R6）；
- 运行方式：GitHub 临时云机器装 Python + 依赖（含 RapidOCR，已缓存模型）→ 跑 `scraper/scraper.py`
  （增量，**只补新讲座，不会重复解析已抓过的旧 URL**）→ 把 `data/lectures.json` 同步为
  `site/lectures.json` → 提交并推送 → Pages 自动重新发布。
- **全程免费、无需服务器、无需你每次操作**，访问者每天都能看到最新讲座。

> ⚠️ **增量陷阱（务必理解，否则旧数据永远修不了）**
> 自动任务以 `data/lectures.json` 为基底，**已抓过的 URL 不会再次下载解析**。后果：
> 1. 解析器升级 / 修了 bug 后，**旧讲座不会被自动修正**；
> 2. 某条旧记录时间或字段错了，daily 自动跑**永远修不了**（例如某图书馆讲座时间长期显示为发布日而非海报真实时间，根因即此）。
>
> **手动修复已抓旧记录的标准操作**（替换 `<URL>` / `<学院名>`）：
> ```bash
> # 1) 从 data 删除目标 URL（或整院记录）
> python -c "import json;p='data/lectures.json';o=json.load(open(p,encoding='utf-8'));o['data']=[x for x in o['data'] if x.get('sourceUrl')!='<URL>'];json.dump(o,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=2)"
> # 2) 重新抓取该院（该 URL 已不在基底，会重新下载并用新解析器解析）
> python scraper/scraper.py --source <学院名>
> # 3) 重新生成前端切片 + 同步静态副本
> python scripts/generate_frontend_data.py
> cp data/lectures.json site/lectures.json
> # 4) git add + commit + push（触发 Pages 重新部署）
> ```
> 详见 `docs/PARSING_RULES.md` 的「运维约定 / 增量陷阱」一节。

> 📌 **代码即规则**：上面的解析逻辑（OCR 触发、时间解析、新闻过滤、字段清洗）都已固化在
> `scraper/parsers.py`、`scraper/timeparse.py`、`scraper/scraper.py`、`scripts/generate_frontend_data.py`
> 中并带注释，平台独立运行时会自动生效，无需额外配置。本文件与 `docs/PARSING_RULES.md` 只补充
> 「代码无法自解释」的运维约定与踩坑来由。

> 发布前请确保：① `site/.nojekyll` 已存在（避免 Pages 用 Jekyll 处理静态文件）；
> ② `data/last_scrape.json` 已随仓库提交（增量基线，否则每次全量重扫）；
> ③ Pages 来源设为「分支 main / 目录 /site」。

### 4. 发布到 GitHub Pages 的具体步骤

1. 在 GitHub 新建仓库（公开/私有均可，代码本身无密钥）；
2. 把本项目（含 `site/`、`scraper/`、`data/`、`server.py`、`.github/`）推到 `main` 分支；
3. 仓库 `Settings → Pages → Build and deployment → Source` 选 **Deploy from a branch**，
   Branch 选 **main**，目录选 **/site**，保存；
4. 约 1 分钟后访问 `https://<用户名>.github.io/<仓库名>/`；
5. 此后每天 03:00 自动更新；也可在 Actions 页手动触发。

---

## 三、方案 B：全栈部署（保留网页抓取/信息源管理）

适合希望「在网页上点一下就抓最新讲座」的场景。

### 1. 需要的资源（用户准备）
- 一台公网云服务器（腾讯云 CVM / 阿里云 ECS，1 核 2G 约 ¥60–100/月；CPU 跑 RapidOCR 较慢，抓一次约数分钟）
- 服务器登录凭证（SSH）
- （可选）一个域名 + ICP 备案（**国内服务器必须备案**才能用 80/443 端口对外）

### 2. 依赖安装（服务器上）
```bash
pip install requests beautifulsoup4 charset-normalizer pyyaml rapidocr-onnxruntime opencv-python-headless
```

### 3. 守护运行 server.py（用 gunicorn + supervisor 示例）
```ini
# /etc/supervisor/conf.d/lectures.conf
[program:lectures]
command=python /opt/lectures/server.py
environment=PORT="8000"
autostart=true
autorestart=true
```

### 4. Nginx 反代 + HTTPS
```nginx
server {
  listen 80; server_name your.domain.com;
  location / { proxy_pass http://127.0.0.1:8000; }
}
# HTTPS 用 certbot --nginx 一键申请免费证书
```

---

## 四、你还需要做什么（清单）

| 事项 | 是否必须 | 说明 |
|------|----------|------|
| 公网访问 | 已完成 | CloudStudio 链接已可所有人访问 |
| 自定义域名 | 可选 | 在 CloudStudio/托管平台绑定你自己的域名 |
| ICP 备案 | 仅国内服务器 | 若用方案 B + 国内云服务器，域名必须备案 |
| 数据更新 | ✅ 已自动化 | GitHub Actions 每日凌晨 3 点自动抓取并更新（见上 §3）；临时手动更新可在 Actions 页 Run workflow |
| HTTPS | 建议 | CloudStudio 已带 HTTPS；自有域名用 Let's Encrypt 免费证书 |
| 点赞跨设备共享 | 可选增强 | 当前点赞存浏览器本地，不跨设备；若要全局共享需加一个后端存储 |

---

## 五、已知限制

1. 纯静态部署下：**网页抓取**、**信息源管理**不可用（前端已做降级提示）。
2. 点赞数据是**浏览器本地存储**（localStorage），换设备/清缓存会清零，不跨用户共享。
3. 静态数据是部署时的**快照**，更新需重新走「爬虫 → 同步 → 部署」流程。
4. 爬虫依赖 RapidOCR（CPU 模式较慢、包体大），全栈部署时首抓需耐心等待。

---

## 六、一键回滚到本地开发

```bash
cd 项目根目录
python server.py        # 默认 http://localhost:8000
```
本地开发走 `/api/lectures`（实时读 `data/lectures.json`），无需 `site/lectures.json`。
