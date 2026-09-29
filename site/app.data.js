// 数据层：latest → chunks 渐进加载、长文本（简介/摘要）按需取桶。
// 本地与公网的形态分叉由 tests/test_frontend_consistency.py 锁死，勿顺手统一。
Object.assign(APP_METHODS, {
    /* ---------- 数据加载（增量 / 渐进式） ----------
     * 本地与公网统一走静态分片路径：latest.json（70KB 首屏秒开）→ chunks
     * （8×334KB 分片，4 路并发 + 协商缓存）→ detail（16 桶长文本按需）。
     * 不再先试 /api/lectures（7.4MB 全量下载，长文本分离前是本地慢的根因）。
     * server.py 的 /api/lectures 保留供管理端/调试用，但前端列表不再走它。
     * GitHub Pages 的 max-age=600 通过 cache:'default' 生效，二次访问秒开。
     */
    // 最近一次抓取运行时间（2026-09-10）：独立小文件，由 CI 每次运行刷新，
    // 与数据版本 updatedAt 分离，见 displayUpdatedAt 的说明。
    // 调用点在 mounted()：本地与公网是两条互斥的加载路径，必须各自都能取到该文件，
    // 只挂在 loadLectures() 上会让公网静态路径永远拿不到 lastRunAt（表现为「更新于」停在数据日）。
    _loadLastRun() {
      fetch('lectures/last_run.json', { cache: 'no-store' })
        .then(r => (r.ok ? r.json() : null))
        .then(j => { if (j && j.lastRunAt) this.lastRunAt = j.lastRunAt; })
        .catch(() => {});
    },

    loadLectures() {
      // 本地与公网统一走静态分片路径，不再先试 /api/lectures（7.4MB 全量下载慢）。
      // latest.json 70KB 首屏秒开 → 后台 4 路并发拉 8 分片 → 长文本按需。
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
    // （本地后端不做长文本分离，公网 latest.json 内联的是截断预览），必须收进旁路，
    // 否则 hasBio/absRaw 恒空 → 简介与摘要整行不显示。
    _absorbAll(arr) {
      for (const it of arr) this._absorbLongText(it);
      this.all = arr;
    },

    _loadStaticLatest() {
      // 先加载 latest.json：仅 50 条，用于首屏秒开；cache:default 让浏览器复用 600s 缓存
      fetch('lectures/latest.json', { cache: 'no-store' })
        .then(r => r.json())
        .then(resp => {
          this._applyLectureData(resp);
          this.loadedChunks = 0;     // 全新一次完整加载，从第 0 片开始
          this.dataStage = 'partial';
          this.loading = false;
          this.bumpCount();          // 数字先滚到 50（首屏已加载真实条数）
          // 后台继续分片加载完整数据（启用完整筛选翻页）
          this._loadStaticFull();
        })
        .catch(() => { this._loadStaticFull(true); });
    },

    _loadStaticFull(fallbackToOriginal = false) {
      // 分片加载（更小请求、逐片重试、数字渐进滚动）；fallbackToOriginal 时回退整文件。
      if (fallbackToOriginal) return this._loadFullSingle();
      this._loadChunks();
    },

    async _loadChunks() {
      let manifest;
      try {
        const mres = await fetch('lectures/chunks.json', { cache: 'no-store' });
        if (!mres.ok) throw new Error('no-manifest');
        manifest = await mres.json();
      } catch (e) {
        // 分片清单缺失（旧部署 / 生成失败）：回退整文件 lectures.json
        return this._loadFullSingle();
      }
      const chunks = (manifest && manifest.chunks) || [];
      if (!chunks.length) return this._loadFullSingle();
      this.dataStage = 'partial';
      const failed = [];
      // 2026-09-28 性能修复：分片由严格串行改为并发池（默认 4 路）。
      // 诊断：公网 8 片 gzip 后合计约 2.4MB，单片实测 1.8~18s（链路速度 80~540KB/s
      // 波动极大）；串行把每片 RTT 逐段累加，全量首屏要 30~40s，弱网直逼几分钟。
      // 并发后只剩 2 轮 RTT。合并是同步调用（JS 单线程），先到先合并，
      // 顶部数字仍随每片到达向上滚动。
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
              const cres = await fetch(url, { cache: 'default' });
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
      this.loadedChunks = 0;
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
    // 键必须与 generate_frontend_data._lt_key() 逐字一致
    _ltKey(l) {
      return (l.sourceUrl || '') + '#' + (l.lectureIndex != null ? l.lectureIndex : '');
    },
    _ltOf(l) { return this._longText[this._ltKey(l)] || null; },
    // 条目自身已不含长文本，模板/搜索统一走这里取
    hasBio(l) { const t = this._ltOf(l); return !!(t && t.speakerBio); },
    bioRaw(l) { const t = this._ltOf(l); return (t && t.speakerBio) || ''; },
    absRaw(l) { const t = this._ltOf(l); return (t && t.abstract) || ''; },

    _absorbLongText(l) {
      // latest.json 的 50 条内联了截断预览，这里收进 _longText（_ltFull 不置位，
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
      const r = await fetch('lectures/detail/manifest.json', { cache: 'default' });
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
        const r = await fetch(file, { cache: 'default' });
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

    // 搜索需要简介/摘要正文才能命中：后台预取全量，完成后 computed 自动重算
    async _prefetchLongText() {
      if (this._ltAllLoaded || !this.query || this._ltSearching) return;
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
        this._ltAllLoaded = true;
      } catch (e) {
        console.warn('长文本预取失败，搜索将只覆盖已加载部分', e);
      } finally {
        this._ltSearching = false;
      }
    },
});
