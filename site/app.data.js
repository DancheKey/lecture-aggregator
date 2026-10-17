// 数据层：latest → chunks 渐进加载、长文本（简介/摘要）按需取桶。
// 本地与公网的形态分叉由 tests/test_frontend_consistency.py 锁死，勿顺手统一。
Object.assign(APP_METHODS, {
    /* ---------- 数据加载（增量 / 渐进式） ----------
     * 本地与公网统一走静态分片路径：latest.json（约 105KB 首屏秒开）→ chunks
     * （39 片 × 约 66KB，4 路并发 + 版本化缓存）→ detail（16 桶长文本按需）。
     * 不再先试 /api/lectures（约 2.5MB 全量下载，长文本分离前是本地慢的根因）。
     * server.py 的 /api/lectures 保留供管理端/调试用，但前端列表不再走它。
     * GitHub Pages 的 max-age=600 通过 cache:'default' 生效，二次访问秒开；
     * 版本化缓存（chunks.json 清单的 updatedAt 进分片 URL）见下方 _loadChunks。
     */
    // 最近一次抓取运行时间（2026-09-10）：独立小文件，由 CI 每次运行刷新，
    // 与数据版本 updatedAt 分离，见 displayUpdatedAt 的说明。
    // 调用点在 mounted()：本地与公网是两条互斥的加载路径，必须各自都能取到该文件，
    // 只挂在 loadLectures() 上会让公网静态路径永远拿不到 lastRunAt（表现为「更新于」停在数据日）。
    _loadLastRun() {
      // cache-busting（2026-10-13）：URL 带唯一时间戳。`cache:'no-store'` 拦不住不遵守
      // 缓存指令的中间层（代理/加速器/部分 WebView）——用户实测 last_run.json 的 10-06
      // 旧副本卡死 7 天：「更新于」在 10-06 与当天值之间反复横跳，而列表数据是新的
      // （total 3824 vs 10-06 版 3812、首条卡片亦不同），铁证旧副本就在这个 47B 小文件。
      // 查询串变则缓存键变，任何缓存层都无法把旧副本映射到新 URL；文件仅 47B，
      // 每次回源的成本可忽略。
      fetch('lectures/last_run.json?t=' + Date.now(), { cache: 'no-store' })
        .then(r => (r.ok ? r.json() : null))
        .then(j => { if (j && j.lastRunAt) this.lastRunAt = j.lastRunAt; })
        .catch(() => {});
    },

    loadLectures() {
      // 本地与公网统一走静态分片路径，不再先试 /api/lectures（约 2.5MB 全量下载慢）。
      // latest.json 约 105KB 首屏秒开 → 后台 4 路并发拉 39 分片 → 长文本按需。
      this._loadStaticLatest();
    },

    _applyLectureData(resp) {
      // 兼容多种后端返回：
      //  - {data:[...], updatedAt, mtime}            （新版 server.py，已解包）
      //  - {data:{updatedAt,data:[...]}, updatedAt}  （旧版 server.py，未解包）
      if (Array.isArray(resp)) { this._absorbAll(resp); this.mtime = 0; this.updatedAt = ''; return; }
      let arr = resp.data;
      let updatedAt = resp.updatedAt || '';
      if (arr && typeof arr === 'object' && !Array.isArray(arr) && Array.isArray(arr.data)) {
        if (!updatedAt) updatedAt = arr.updatedAt || '';
        arr = arr.data;
      }
      this._absorbAll(Array.isArray(arr) ? arr : []);
      this.mtime = resp.mtime || 0;
      this.updatedAt = updatedAt || (resp.mtime ? new Date(resp.mtime * 1000).toISOString() : '');
    },

    // 整批收编长文本：本地 /api/lectures 与静态 latest.json 的条目都**内联**简介/摘要
    // （本地后端不做长文本分离，公网 latest.json 的 LATEST_PREVIEW_LEN=0 即全文内联、
    // 不截断），必须收进旁路，否则 hasBio/absRaw 恒空 → 简介与摘要整行不显示。
    _absorbAll(arr) {
      for (const it of arr) this._absorbLongText(it);
      this.all = arr;
    },

    _loadStaticLatest() {
      // 先加载 latest.json：仅 50 条，用于首屏秒开。
      // cache-busting（2026-10-13）：同 _loadLastRun——latest.json 是「更新于」的回退源
      // （displayUpdatedAt = lastRunAt || updatedAt，updatedAt 即来自本文件），旧副本会让
      // 日期退回数据日。no-store 语义本就要求每次重新获取，加唯一时间戳只是让不遵守该
      // 指令的中间层也无旧副本可用；105KB 回源与 no-store 的既有成本相同。
      //
      // 失败重试（2026-10-13）：latest.json 一失败就会掉进 2.6MB 无长文本的降级路径
      // （_loadFullSingle），首屏退化成「最简版」、简介摘要要等用户滚动才补——用户实测
      // 遇到过（其网络中间层与 last_run.json 卡旧副本同源）。抖动与未部署是两种处置：
      // 先重试两次（800ms/1600ms 退避，与 _loadChunks 同口径），仍失败才降级；
      // r.ok 检查让 404/5xx 与网络抖动走同一重试口径（原代码直接 r.json()，
      // 404 页可能解析出非预期结构）。
      const attempt = (n) => {
        fetch('lectures/latest.json?t=' + Date.now(), { cache: 'no-store' })
          .then(r => {
            if (!r.ok) throw new Error('latest-' + r.status);
            return r.json();
          })
          .then(resp => {
            this._applyLectureData(resp);
            this.dataStage = 'partial';
            this.loading = false;
            this.bumpCount();          // 数字跳到 50（首屏已加载真实条数；阶梯跳变无动画）
            // 后台继续分片加载完整数据（启用完整筛选翻页）
            this._loadStaticFull();
            // 首屏一渲染即在后台静默预取全部长文本（简介/摘要，16 桶 gzip 约 1.6MB）——
            // 2026-09-30 用户定：原策略是「交互（滚动/聚焦/翻页）才预取」，导致「打开页面就
            // 立即搜索」时桶还没下完、搜索被迫等待并弹「正在加载完整内容」提示。改为首屏即拉，
            // 用户开始搜索时通常已就绪；即便未就绪，搜索也不再阻塞（先出已到结果、到齐静默补全），
            // 见 computed.filtered 与 index.html 时间线分支。
            this._ensureLongTextAll();
          })
          .catch((e) => {
            // 2026-10-02：补原始错误。同文件 :167-174 的 catch 已是 console.error(e) 口径，
            // 此处原先是 `() => {...}` 把 e 丢掉——「网络抖动」与「latest.json 未部署/404」
            // 是两种完全不同的处置（前者重试即可，后者要查部署），丢掉错误就无从区分。
            if (n < 3) {
              console.warn(`[load] latest.json 第 ${n} 次加载失败，退避后重试`, e);
              setTimeout(() => attempt(n + 1), 800 * Math.pow(2, n - 1));
              return;
            }
            console.error('[load] latest.json 重试后仍失败，回退整文件 lectures.json', e);
            this._loadStaticFull(true);
          });
      };
      attempt(1);
    },

    _loadStaticFull(fallbackToOriginal = false) {
      // 分片加载（更小请求、逐片重试、数字渐进滚动）；fallbackToOriginal 时回退整文件。
      if (fallbackToOriginal) return this._loadFullSingle();
      this._loadChunks();
    },

    async _loadChunks() {
      let manifest;
      try {
        // cache-busting（2026-10-13）：同 _loadLastRun。清单旧副本会让 _ver 带上旧
        // updatedAt → 分片 URL 命中旧分片缓存 → 混龄覆盖（d27a79f 事故类复发）。
        const mres = await fetch('lectures/chunks.json?t=' + Date.now(), { cache: 'no-store' });
        if (!mres.ok) throw new Error('no-manifest');
        manifest = await mres.json();
      } catch (e) {
        // 分片清单缺失（旧部署 / 生成失败）：回退整文件 lectures.json
        return this._loadFullSingle();
      }
      const chunks = (manifest && manifest.chunks) || [];
      if (!chunks.length) return this._loadFullSingle();
      // 进度分母（清单自带 total，几十字节先于分片到手）：模板「已加载 X / Y」用。
      this.chunkTotal = manifest.total || 0;
      this.dataStage = 'partial';
      // 分片 URL 按数据版本分桶（2026-10-12 混龄缓存事故）：
      // latest.json 是 no-store（恒新），而分片走 cache:'default' 命中 GitHub Pages
      // 的 max-age=600 旧副本——部署后的 10 分钟窗口内，浏览器会拿**旧分片覆盖
      // 新 latest 记录**（_mergeChunk 的覆盖设计），表现为「刚刷新有阿里云、加载
      // 完又没了」，且各设备因缓存龄不同而表现不一（移动端有/电脑没有）。
      // 修法：用清单里的 updatedAt 给分片 URL 加 ?v= —— 数据版本变则 URL 变，
      // 浏览器缓存天然按版本隔离；同版本重复访问仍命中缓存，性能不损失。
      // GitHub Pages 忽略查询串，CDN 侧同理按完整 URL 键控，新版本 URL 必为回源。
      const _ver = (manifest && manifest.updatedAt) || '';
      const _urlOf = u => _ver
        ? u + (u.includes('?') ? '&' : '?') + 'v=' + encodeURIComponent(_ver)
        : u;
      const failed = [];
      // 2026-09-28 性能修复：分片由严格串行改为并发池（默认 4 路）。
      // 诊断：单片实测 1.8~18s（链路速度 80~540KB/s 波动极大）；串行把每片 RTT
      // 逐段累加，全量首屏要 30~40s，弱网直逼几分钟。并发后只剩 N/4 轮 RTT。
      // 合并是同步调用（JS 单线程），先到先合并，顶部数字随每片到达跳变。
      // 2026-10-14：分片粒度 500→100 条/片（用户定）——阶梯跳变下数字只在分片
      // 到达时更新，100/片让 3830 条约 38 次小步跳变（+100 ≈ 4 页）持续可见，
      // 总下载量与 8×500 相同，单请求更小、首片到达更快。
      // 顺序影响：列表由 list.sort() 按日期倒序 + 同日系列编号倒序决定，
      // 不依赖数组原始顺序，故并发完成顺序不影响展示。
      const CONCURRENCY = 4;
      let cursor = 0;
      const worker = async () => {
        while (cursor < chunks.length) {
          const i = cursor++;
          const url = chunks[i];
          let ok = false;
          for (let attempt = 0; attempt < 3 && !ok; attempt++) {
            try {
              // cache:'default' —— GitHub Pages 对静态文件发 Cache-Control:max-age=600，
              // 走协商缓存后二次进入命中本地副本（数据每天只更新一次，10 分钟窗口可接受）。
              // 原先这里是 no-store，等于每次访问都强制重下全部 2.4MB，这正是
              // 「以前很快、现在一直没加载出来」的主因。分片清单 chunks.json 仍保持
              // no-store（仅 297B），保证分片清单第一时间拿到。
              const cres = await fetch(_urlOf(url), { cache: 'default' });
              if (!cres.ok) throw new Error('chunk-' + cres.status);
              const cj = await cres.json();
              this._mergeChunk((cj && cj.data) || []);
              this.bumpCount();
              ok = true;
            } catch (err) {
              console.warn(`讲座分片 ${url} 第 ${attempt + 1} 次加载失败`, err);
              if (attempt < 2) await this._sleep(800 * Math.pow(2, attempt));
            }
          }
          // 单片最终失败不再中止：记录后继续加载后续分片。
          // 弱网下把损失从「此后所有分片全部缺失」压到「仅缺失该片」。
          if (!ok) failed.push(url);
        }
      };
      const pool = Math.min(CONCURRENCY, chunks.length);
      await Promise.all(Array.from({ length: pool }, () => worker()));
      if (failed.length) {
        // 仍有分片失败：保留已加载的真实条数并暴露重试入口；
        // 绝不 finalize 到 50 死值，避免数字定格成假数据。
        this.dataStage = 'partial-error';
        console.warn(`共 ${failed.length} 个分片加载失败（可重试）：`, failed);
      } else {
        this.dataStage = 'full';
      }
    },

    _loadFullSingle() {
      fetch('lectures.json', { cache: 'no-store' })
        .then(r => r.json())
        .then(resp => {
          this._applyLectureData(resp);
          this.dataStage = 'full';
          this.loading = false;
          // 2026-10-13：降级路径也自动预取长文本——lectures.json 无简介/摘要，
          // 不预取则首屏停在「最简版」且要等用户滚动/搜索才补（onScroll 才触发
          // _ensureLongTextAll）。正常路径 _loadStaticLatest 首屏即拉，此前两条
          // 路径不对称；本行使降级体验与正常路径对齐（几秒后自动补齐）。
          this._ensureLongTextAll();
        })
        .catch(e => {
          console.error('加载完整讲座数据失败', e);
          // 不 finalize 到 50 死值；保留已加载真实条数，可点击重试。
          this.dataStage = 'partial-error';
          this.loading = false;
        });
    },

    _mergeChunk(arr) {
      // 将分片数据并入 this.all：首屏 latest 的 50 条是全集子集，
      // 用完整分片覆盖首屏预览（abstract/speakerBio 更全），其余追加。
      const keyOf = it => (it.sourceUrl || '') + '|' + (it.lectureStart || '') + '|'
        + (it.title || '') + '|' + (it.lectureIndex != null ? it.lectureIndex : '');
      const idxMap = new Map();
      this.all.forEach((it, idx) => idxMap.set(keyOf(it), idx));
      for (const it of arr) {
        // 长文本收进旁路存储 _longText，条目只留结构化字段
        this._absorbLongText(it);
        const clean = it;
        if (clean.speakerBio !== undefined) delete clean.speakerBio;
        if (clean.abstract !== undefined) delete clean.abstract;
        const k = keyOf(clean);
        if (idxMap.has(k)) {
          this.all[idxMap.get(k)] = clean;   // 完整数据覆盖首屏预览
        } else {
          idxMap.set(k, this.all.length);
          this.all.push(clean);
        }
      }
    },

    _sleep(ms) { return new Promise(res => setTimeout(res, ms)); },

    /* ---------- 长文本（简介/摘要）按需加载 ---------- */
    // 键必须与 scripts/frontend_fields.py 的 lt_key() 逐字一致（实现已随前端拆分
    // 从 app.js 移到本文件 app.data.js——旧注释写 app.js 会让人去错文件里找）。
    // 两端无共享代码，只靠此约定；一致性由 tests/js/app_ltkey_consistency.js
    // 跨语言逐条比对守护，改任一侧即红。
    _ltKey(l) {
      return (l.sourceUrl || '') + '#' + (l.lectureIndex != null ? l.lectureIndex : '');
    },
    _ltOf(l) { return this._longText[this._ltKey(l)] || null; },
    // 条目自身已不含长文本，模板/搜索统一走这里取
    hasBio(l) { const t = this._ltOf(l); return !!(t && t.speakerBio); },
    bioRaw(l) { const t = this._ltOf(l); return (t && t.speakerBio) || ''; },
    absRaw(l) { const t = this._ltOf(l); return (t && t.abstract) || ''; },

    _absorbLongText(l) {
      // latest.json 的 50 条内联了完整长文本（LATEST_PREVIEW_LEN=0），这里收进 _longText（_ltFull 不置位，
      // 展开时仍会拉全文覆盖它）
      const bio = l.speakerBio, abs = l.abstract;
      if (bio || abs) {
        const k = this._ltKey(l);
        this._longText = { ...this._longText, [k]: { speakerBio: bio || '', abstract: abs || '' } };
      }
    },
    _absorbDetailRows(rows) {
      if (!rows || !rows.length) return;
      const patch = {};
      for (const r of rows) {
        patch[r.key] = { speakerBio: r.speakerBio || '', abstract: r.abstract || '' };
        this._ltFull = { ...this._ltFull, [r.key]: true };
      }
      this._longText = { ...this._longText, ...patch };
    },

    async _ltManifestJson() {
      if (this._ltManifest) return this._ltManifest;
      // no-store（2026-10-12 与分片同因）：清单仅 ~600B，若走 default 命中旧副本，
      // 下方桶 URL 会带旧版本戳去取**旧桶覆盖新 latest 的内联长文本**，与分片同一类
      // 混龄覆盖。与 chunks.json 同口径恒新，桶按版本戳隔离。
      // cache-busting（2026-10-13）：同 _loadLastRun——清单旧副本会让桶 URL 带旧版本戳，
      // 命中旧桶缓存覆盖新 latest 的内联长文本（与分片同一类混龄事故）。
      const r = await fetch('lectures/detail/manifest.json?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) throw new Error('detail-manifest-' + r.status);
      this._ltManifest = await r.json();
      return this._ltManifest;
    },

    async _loadDetailBucket(idx) {
      if (idx == null || this._ltLoaded[idx]) return;
      this._ltPending = { ...this._ltPending, [idx]: true };
      try {
        const man = await this._ltManifestJson();
        const file = (man.files || []).find(f => f.endsWith('detail_' + String(idx).padStart(2, '0') + '.json'));
        if (!file) return;
        // 版本戳与分片同口径（2026-10-12）：按清单 updatedAt 分桶缓存键
        const ver = man.updatedAt || '';
        const url = ver
          ? file + (file.includes('?') ? '&' : '?') + 'v=' + encodeURIComponent(ver)
          : file;
        const r = await fetch(url, { cache: 'default' });
        if (!r.ok) throw new Error('detail-' + r.status);
        const j = await r.json();
        this._absorbDetailRows(j.data);
        this._ltLoaded = { ...this._ltLoaded, [idx]: true };
      } catch (e) {
        console.warn('长文本桶 ' + idx + ' 加载失败', e);
      } finally {
        const p = { ...this._ltPending }; delete p[idx];
        this._ltPending = p;
      }
    },

    // 展开某条前确保其全文到手（只拉它所在的 1 桶，16 桶之一）
    async _ensureLongText(l) {
      const k = this._ltKey(l);
      if (this._ltFull[k]) return;
      const idx = l && l.b != null ? l.b : null;
      if (idx == null) return;
      await this._loadDetailBucket(idx);
    },

    /* ---------- 长文本（简介/摘要）预取 ---------- */
    // 模板入口：聚焦搜索框即预热（模板不宜直接调用下划线开头的内部方法）
    warmLongText() { this._ensureLongTextAll(); },

    // 交互即预取（2026-09-30 用户定）：滚动 / 聚焦搜索框 / 翻页任一发生，就后台拉全部 16 桶。
    // 起因：长文本被剥离后，只有首屏 50 条带简介与摘要，其余卡片要等搜索或展开才补 ——
    // 表现为「搜索出的卡片先没有简介/摘要，过一会儿整块冒出来」，体验差。
    // 幂等：已全量就绪 / 正在预取 / 已判定不可用 时直接返回，故可挂在任何高频事件上。
    _ensureLongTextAll() {
      if (this._ltUnavailable || this._ltSearching) return;
      if (this._ltAllLoaded && !this._ltIncomplete) return;
      this._prefetchLongText();
    },

    // 全量预取：4 路并发拉 16 桶（gzip 后约 1.6MB），完成后 _ltAllLoaded 置位。
    async _prefetchLongText() {
      if (this._ltUnavailable || this._ltSearching) return;
      if (this._ltAllLoaded && !this._ltIncomplete) return;
      this._ltSearching = true;
      try {
        const man = await this._ltManifestJson();
        const files = man.files || [];
        const CONC = 4;
        let cur = 0;
        const idxs = files.map(f => parseInt((f.match(/detail_(\d+)\.json/) || [])[1], 10));
        const worker = async () => {
          while (cur < idxs.length) await this._loadDetailBucket(idxs[cur++]);
        };
        await Promise.all(Array.from({ length: Math.min(CONC, idxs.length) }, worker));
        // 搜索放行与「是否拉齐」解耦：_ltAllLoaded 一经首轮结束就置位（缺桶不阻塞搜索，
        // 否则弱网下单桶失败会让搜索永远停在等待态）；_ltIncomplete 只用于允许后续交互补拉。
        const got = Object.keys(this._ltLoaded).length;
        this._ltAllLoaded = true;
        this._ltIncomplete = got < idxs.length;
        if (this._ltIncomplete) {
          this._ltRetry = (this._ltRetry || 0) + 1;
          if (this._ltRetry > 2) this._ltIncomplete = false;   // 连续补不齐则放弃，避免反复空跑
          else console.warn(`长文本桶未拉齐（${got}/${idxs.length}），下次交互将补拉失败桶`);
        } else {
          this._ltRetry = 0;
        }
      } catch (e) {
        // 清单/整批不可用：置位后不再尝试预取（避免每次交互都空跑一遍）；
        // 搜索本就不阻塞，此时退化为「就绪多少搜多少」（即 2026-09-30 之前的行为）。
        this._ltUnavailable = true;
        console.warn('长文本预取不可用，搜索结果将只覆盖已加载部分', e);
      } finally {
        this._ltSearching = false;
      }
    },
});
