// 交互层：点赞与想听、本机统计缓存、筛选切换、分页与滚动。
//
// ⚠ 计数键约定（2026-10-18）：本文件所有 url 形参实际是**讲座级计数键**（数据里
// 的 ck 字段，由 scripts/generate_frontend_data.py::count_key 产出），不是源页网址：
//   单场讲座   ck = sourceUrl          （与改造前逐字一致，存量计数零迁移）
//   多场拆分   ck = sourceUrl~场次序号  （同一通知页的 N 场各自独立计数）
// 历史 bug：此前直接用 sourceUrl 当键，导致同一页的 3 场共享一份计数——
// 点一场 3 场同时 +1、统计页逐条累加把 1 次点赞显示成 3。改键规则只需动生成器。
Object.assign(APP_METHODS, {
    /* ---------- 本地点赞（同一浏览器去重） ---------- */
    loadLikes() {
      try {
        this.likedUrls = new Set(JSON.parse(localStorage.getItem(LIKED_KEY) || '[]'));
        // 计数以 lectureStats（后端权威 / 本机 STAT_KEY 缓存）为准。
        // 旧版 LIKE_KEY 本地计数键已废弃（N5，2026-09-22）：只写不读的死数据，
        // 一次性清掉残留，之后不再读写。
        localStorage.removeItem('lecture_likes_v1');
      } catch (e) {
        this.likedUrls = new Set();
      }
    },
    saveLikes() {
      try {
        localStorage.setItem(LIKED_KEY, JSON.stringify(Array.from(this.likedUrls)));
      } catch (e) { /* ignore quota/storage errors */ }
    },

    /* ---------- 远期折叠条展开态（>7 天待开始讲座，2026-10-13 方案 B） ---------- */
    // 与点赞/想听同模式：纯本机偏好，存 localStorage，刷新/重开浏览器保留。
    loadFarFuture() {
      try {
        this.farFutureOpen = localStorage.getItem(FAR_FOLD_STATE_KEY) === '1';
      } catch (e) {
        this.farFutureOpen = false;
      }
    },
    toggleFarFuture() {
      this.farFutureOpen = !this.farFutureOpen;
      this.saveFarFuture();
    },
    saveFarFuture() {
      try {
        localStorage.setItem(FAR_FOLD_STATE_KEY, this.farFutureOpen ? '1' : '0');
      } catch (e) { /* ignore quota/storage errors */ }
    },
    likeCount(url) {
      // 计数以 lectureStats（后端权威 / 本机 STAT_KEY 缓存）为唯一来源，
      // 不回退旧本地计数键（残留脏值会导致「刷新后数值增加」）。
      const s = this.lectureStats[url];
      if (s && typeof s.likes === 'number') return s.likes;
      return 0;
    },
    hasLiked(url) {
      return this.likedUrls.has(url);
    },
    // 点赞/想听点击弹跳反馈：图标缩放回弹、计数轻微跳动（0.32s，纯视觉）
    _pulse(evt) {
      const btn = evt && evt.currentTarget;
      if (!btn || !btn.querySelector) return;
      const els = [btn.querySelector('svg'), btn.querySelector('span')].filter(Boolean);
      els.forEach(el => {
        el.classList.remove('pop-bounce');
        void el.offsetWidth;   // 连续点击时重置动画
        el.classList.add('pop-bounce');
      });
      setTimeout(() => els.forEach(el => el.classList.remove('pop-bounce')), 420);
    },
    toggleLike(url, evt) {
      if (!url) return;
      this._pulse(evt);
      const willLike = !this.hasLiked(url);
      // 本地 UI 立即切换（乐观更新），保证点击即时反馈
      if (willLike) { this.likedUrls.add(url); } else { this.likedUrls.delete(url); }
      const delta = willLike ? 1 : -1;
      // 乐观更新展示计数；后端返回真实值后会被覆盖（统一数据源，消除首页/统计页不一致）
      // 用 typeof 判断，避免 lectureStats.likes === 0 时被 || 误判为缺失而回退到旧本地值。
      const s = this.lectureStats[url];
      const cur = (s && typeof s.likes === 'number') ? s.likes : 0;
      const next = Math.max(0, cur + delta);
      if (!this.lectureStats[url]) this.lectureStats[url] = { visits: 0, likes: 0 };
      this.lectureStats[url].likes = next;
      this.saveLikes();
      this.saveLocalStats();
      // toast 不先于 fetch 无条件报成功（C1-c）：按真实结果分支提示。
      // 2026-10-17 云端优先链：有 supabase-config.json 时走 Supabase 全局共享计数；
      // 无配置 / 云端失败 → 退回本地 server /api → 再降级本机（原链保持不动）。
      const endpoint = willLike ? 'like' : 'unlike';
      LectureCloud.bump('like', url, delta).then(cloud => {
        if (cloud && typeof cloud.likes === 'number') {
          // 云端权威值覆盖本机乐观值
          if (!this.lectureStats[url]) this.lectureStats[url] = { visits: 0, likes: 0 };
          this.lectureStats[url].likes = cloud.likes;
          this.saveLocalStats();
          this.showToast(willLike ? '点赞成功' : '已取消点赞');
          return;
        }
        return fetch('/api/lecture/' + endpoint, {
          method: 'POST', cache: 'no-store',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        })
          .then(r => (r && r.ok ? r.json() : null))
          .then(j => {
            if (j && j.ok && typeof j.likes === 'number') {
              // 后端权威值覆盖本机乐观值
              if (!this.lectureStats[url]) this.lectureStats[url] = { visits: 0, likes: 0 };
              this.lectureStats[url].likes = j.likes;
              this.saveLocalStats();
              this.showToast(willLike ? '点赞成功' : '已取消点赞');
            } else {
              // 后端失败 / 被节流 / 公网无后端：本机状态已生效，不冒称服务端成功
              this.showToast('已本地记录');
            }
          })
          .catch(() => { this.showToast('已本地记录'); }); // 离线：本机值已生效
      });
    },

    /* ---------- 本地「想听」（同一浏览器去重，逻辑与点赞对称） ---------- */
    loadWants() {
      try {
        this.wantedUrls = new Set(JSON.parse(localStorage.getItem(WANTED_KEY) || '[]'));
        // 计数以 lectureStats（后端权威 / 本机 STAT_KEY 缓存）为准。
        // 旧版 WANT_KEY 本地计数键已废弃（N5，2026-09-22）：只写不读的死数据，
        // 一次性清掉残留，之后不再读写。
        localStorage.removeItem('lecture_wants_v1');
      } catch (e) {
        this.wantedUrls = new Set();
      }
    },
    saveWants() {
      try {
        localStorage.setItem(WANTED_KEY, JSON.stringify(Array.from(this.wantedUrls)));
      } catch (e) { /* ignore quota/storage errors */ }
    },

    /* ---------- 计数键下沉过渡期迁移（2026-10-18 B1，一次性） ---------- */
    // ck 改造前，多场拆分讲座的赞/想听记在**页面级**裸 URL 上；改造后展开场次
    // 查 sourceUrl~N 新键。两个过渡期问题：
    //
    // ① 老用户「已赞」状态丢失（图标不高亮，云端计数还在，再点一次 +1）。
    // ② **反向问题（2026-10-19 用户实测）**：早前把旧键展开到「该页全部场次」，
    //    但只有**合并卡**（同页同日 ≥3 场）才是「整页一个赞」的语义；普通多场页
    //    每场是独立按钮，展开会把用户没点过的场次也标成已赞——图标高亮、数字 0，
    //    「只看已赞」还会多筛出几条。全站 94 个多场源页里，只有 30 组会渲染成
    //    合并卡，其余 60+ 页的展开都是伪造状态。
    //
    // 现规则（口径与 app.computed.js units() 的 MIN=3 完全一致）：
    //   · 合并组 = 「同源页 + 同一天」场次 ≥3 的那一组，它渲染成一张合并卡，
    //     卡片计数键 = 无后缀 sourceUrl（页面级）。
    //   · expand：旧页面级键 → 只展开到**合并组内**的场次。非合并页不展开
    //     （会造成虚假已赞）；混合页（同页既有合并组、又有零散场次）也不会
    //     波及零散场次——那些场次是独立单卡，各自有自己的按钮。
    //   · prune：把「不属于任何合并组的计数为 0 的键」清掉——它们只可能来自
    //     历史展开，会让用户没点过的场次错误高亮。合并页的页面级裸键、合并组内
    //     场次键一律保留；计数 > 0 的键也一律保留（可能是真实点赞但计数未落盘，
    //     宁可留错也不误删）。
    //   · prune 需要 lectureStats 已就绪，否则会把所有键当「计数 0」误删——
    //     故加 statReady 守卫；loadLectureStats 完成后再调一次本函数补齐。
    // 幂等：只补缺失的键、只删计数为 0 的伪造键，重复调用无副作用。
    migrateCountKeys() {
      const all = this.all || [];
      if (!all.length) return;   // 数据未就绪：等下次数据阶段变化时再跑

      // 该页全部场次：sourceUrl -> Set(lectureIndex)
      const multi = new Map();
      // 会渲染成合并卡的源页（存在任一同日 ≥3 场的组）
      const mergedUrls = new Set();
      // 属于合并组的场次：sourceUrl -> Set(lectureIndex)，expand/prune 的共同依据
      const mergedIdx = new Map();
      const dayIdx = new Map();   // sourceUrl|日期 -> lectureIndex[]
      all.forEach(l => {
        if (!l || !l.sourceUrl || l.lectureIndex == null) return;
        let s = multi.get(l.sourceUrl);
        if (!s) { s = new Set(); multi.set(l.sourceUrl, s); }
        s.add(l.lectureIndex);
        const gk = l.sourceUrl + '|' + (l.lectureStart || '').slice(0, 10);
        let arr = dayIdx.get(gk);
        if (!arr) { arr = []; dayIdx.set(gk, arr); }
        arr.push(l.lectureIndex);
      });
      if (!multi.size) return;
      dayIdx.forEach((idxs, gk) => {
        if (idxs.length < 3) return;                  // 同日 <3 场：不折叠，逐场单卡
        const u = gk.slice(0, gk.lastIndexOf('|'));
        mergedUrls.add(u);
        let ms = mergedIdx.get(u);
        if (!ms) { ms = new Set(); mergedIdx.set(u, ms); }
        idxs.forEach(i => ms.add(i));
      });

      let dirty = false;

      // ① prune：不属于任何合并组、且计数为 0 的键 → 删除（历史展开的伪造状态）
      const stats = this.lectureStats || {};
      if (Object.keys(stats).length > 0) {
        const prune = (set) => {
          const del = [];
          set.forEach(k => {
            const i = k.lastIndexOf('~');
            const u = i > 0 ? k.slice(0, i) : k;
            if (!multi.has(u)) return;              // 单场页：键即 sourceUrl，合法
            const idx = i > 0 ? k.slice(i + 1) : null;
            if (idx === null) {
              // 页面级裸键：只有合并页才有这个 UI 入口（合并卡的赞记在裸 URL 上）
              if (mergedUrls.has(u)) return;
            } else {
              const ms = mergedIdx.get(u);
              if (ms && ms.has(Number(idx))) return;   // 合并组内的场次键：合法
            }
            const st = stats[k];
            if (!st || (!st.likes && !st.wants)) del.push(k);
          });
          del.forEach(k => set.delete(k));
          return del.length > 0;
        };
        if (this.likedUrls && prune(this.likedUrls)) dirty = true;
        if (this.wantedUrls && prune(this.wantedUrls)) dirty = true;
      }

      // ② expand：旧页面级键 → 合并组内的场次键（改造前「整页一个赞」= 整卡赞）
      const expand = (set) => {
        const add = [];
        set.forEach(old => {
          if (old.indexOf('~') !== -1) return;   // 已是新键
          const idxs = mergedIdx.get(old);       // 非合并页没有这个表项 → 不展开
          if (!idxs) return;
          idxs.forEach(i => add.push(old + '~' + i));
        });
        let touched = false;
        add.forEach(k => { if (!set.has(k)) { set.add(k); touched = true; } });
        return touched;
      };
      if (this.likedUrls && expand(this.likedUrls)) dirty = true;
      if (this.wantedUrls && expand(this.wantedUrls)) dirty = true;
      if (dirty) { this.saveLikes(); this.saveWants(); }
    },
    wantCount(url) {
      const s = this.lectureStats[url];
      if (s && typeof s.wants === 'number') return s.wants;
      // 与 likeCount 一致：计数以「后端全局权威值」为准，不回退本机 localStorage 残留值，
      // 否则旧版想听逻辑遗留的脏值会在刷新后被显示出来（表现为「刷新数值增加」）。
      return 0;
    },
    hasWanted(url) {
      return this.wantedUrls.has(url);
    },
    toggleWant(url, evt) {
      if (!url) return;
      this._pulse(evt);
      const willWant = !this.hasWanted(url);
      if (willWant) { this.wantedUrls.add(url); } else { this.wantedUrls.delete(url); }
      const delta = willWant ? 1 : -1;
      // 用 typeof 判断，避免 lectureStats.wants === 0 时被 || 误判为缺失而回退到旧本地值。
      const s = this.lectureStats[url];
      const cur = (s && typeof s.wants === 'number') ? s.wants : 0;
      const next = Math.max(0, cur + delta);
      if (!this.lectureStats[url]) this.lectureStats[url] = { visits: 0, likes: 0, wants: 0 };
      this.lectureStats[url].wants = next;
      this.saveWants();
      this.saveLocalStats();
      // toast 不先于 fetch 无条件报成功（C1-c，与点赞对称）：按真实结果分支提示。
      // 2026-10-17 云端优先链：同 toggleLike——Supabase → 本地 /api → 本机。
      const endpoint = willWant ? 'want' : 'unwant';
      LectureCloud.bump('want', url, willWant ? 1 : -1).then(cloud => {
        if (cloud && typeof cloud.wants === 'number') {
          if (!this.lectureStats[url]) this.lectureStats[url] = { visits: 0, likes: 0, wants: 0 };
          this.lectureStats[url].wants = cloud.wants;
          this.saveLocalStats();
          this.showToast(willWant ? '已标记想听' : '已取消想听');
          return;
        }
        return fetch('/api/lecture/' + endpoint, {
          method: 'POST', cache: 'no-store',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        })
          .then(r => (r && r.ok ? r.json() : null))
          .then(j => {
            if (j && j.ok && typeof j.wants === 'number') {
              if (!this.lectureStats[url]) this.lectureStats[url] = { visits: 0, likes: 0, wants: 0 };
              this.lectureStats[url].wants = j.wants;
              this.saveLocalStats();
              this.showToast(willWant ? '已标记想听' : '已取消想听');
            } else {
              this.showToast('已本地记录');
            }
          })
          .catch(() => { this.showToast('已本地记录'); }); // 离线：本机值已生效
      });
    },
    // 显示计数：超过 300 显示 "300+"，避免被攻击造成虚高数字
    capDisplay(n) {
      return n > COUNT_CAP ? COUNT_CAP + '+' : String(n);
    },

    /* ---------- 讲座级访问/点赞/想听统计 ---------- */
    // loadLocalStats() 已于 2026-10-19 删除：全仓 0 处调用，loadLectureStats()
    // 自己读 localStorage 并做键迁移/合并，保留一个无调用的同名方法只会误导排查。
    saveLocalStats() {
      try { localStorage.setItem(STAT_KEY, JSON.stringify(this.lectureStats)); } catch (e) { /* ignore */ }
    },
    // 点击讲座标题时记录一次访问（fire-and-forget；后端优先，失败降级本机）
    recordVisit(url) {
      const now = Date.now();
      const s = this.lectureStats[url] || { visits: 0, likes: 0, lastVisit: 0 };
      if (now - (s.lastVisit || 0) >= 180000) {  // 3 分钟内同一讲座只计 1 次
        s.visits = (s.visits || 0) + 1;
        s.lastVisit = now;
        this.lectureStats[url] = s;
        this.saveLocalStats();
        // 仅在本机未节流时才通知后端（后端自身也有节流，此为双重防护，并避免无意义请求）。
        // 2026-10-17：云端（Supabase）优先，无配置/失败时静默退回本地 /api；两者皆无=纯本机。
        LectureCloud.bump('visit', url, 1).then(cloud => {
          if (cloud) return;
          fetch('/api/lecture/visit', {
            method: 'POST', cache: 'no-store',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
          }).catch(() => {});
        });
      }
    },
    // 加载每条讲座的访问/点赞/想听：优先后端，失败降级本机 localStorage。
    // 注意：后端 lecture_stats.json 里旧记录可能没有 `wants` 字段；
    // 若直接 `this.lectureStats = j.stats` 覆盖，会把本机 STAT_KEY 中已保存
    // 的 wants 一并清空，导致「图标已高亮（wantedUrls 还记得）但数字为 0」。
    // 因此按 URL 做字段级合并：后端没有的字段保留本地值，后端有的字段以
    // 后端权威值覆盖本地。
    loadLectureStats() {
      let localStats = {};
      try { localStats = JSON.parse(localStorage.getItem(STAT_KEY) || '{}'); }
      catch (e) { localStats = {}; }
      // 计数就绪后必须再跑一次键迁移（2026-10-19）：migrateCountKeys 的 prune 段
      // 以「该键计数为 0」为判据，lectureStats 还没赋值时整个 prune 被守卫跳过；
      // 而数据阶段的调用发生在 loadLectureStats 之前，于是伪造键永远清不掉
      // （用户实测：只赞 1 条，「只看已赞」筛出 3 条）。这里在每次赋值后补一次。
      const applyStats = (obj) => {
        this.lectureStats = obj;
        this.migrateCountKeys();
      };
      // 2026-10-17 云端优先链：Supabase 批量读全表（一次请求拿全部讲座计数）；
      // 无配置/失败 → 本地 server /api → 本机。合并与写侧容错逻辑三条链共用。
      LectureCloud.fetchAll().then(cloudStats => {
        if (cloudStats) {
          const merged = { ...localStats };
          for (const [url, serverSt] of Object.entries(cloudStats)) {
            merged[url] = { ...(merged[url] || {}), ...serverSt };
          }
          applyStats(merged);
          // 写侧单独容错（2026-10-14 评审 P1-6）：localStorage 配额满/
          // 隐私模式下 setItem 抛 QuotaExceededError，若不让它在此止住，
          // 会掉进下方 .catch 用本地旧值覆盖掉已合并的服务端权威数据。
          try { localStorage.setItem(STAT_KEY, JSON.stringify(merged)); } catch (e) { /* 保留内存态即可 */ }
          return;
        }
        return fetch('/api/lecture/stats', { cache: 'no-store' })
          // 2026-10-17 复核 P2-8：补 r.ok——公网 404 页此前靠 HTML 解析抛异常才落到
          // 下方 catch（与 latest.json 2026-10-13 修复前同款模式），改走正常判断路径。
          .then(r => { if (!r.ok) throw new Error('stats-' + r.status); return r.json(); })
          .then(j => {
            if (j && j.stats) {
              const merged = { ...localStats };
              for (const [url, serverSt] of Object.entries(j.stats)) {
                merged[url] = { ...(merged[url] || {}), ...serverSt };
              }
              applyStats(merged);
              try { localStorage.setItem(STAT_KEY, JSON.stringify(merged)); } catch (e) { /* 保留内存态即可 */ }
            } else {
              applyStats(localStats);
            }
          })
          .catch(() => { applyStats(localStats); });
      });
    },
    showToast(msg, ms = 2000) {
      this.toast.message = msg;
      this.toast.show = true;
      clearTimeout(this.toast.timer);
      this.toast.timer = setTimeout(() => { this.toast.show = false; }, ms);
    },

    /* ---------- 筛选交互 ---------- */
    // 2026-10-17 复核 P1-2：setCollege 补清 showLikedOnly，与 setCampus 对称——
    // 「只看已赞」与其他筛选互斥（口径源头是 toggleLikedFilter：清 campus/college
    // 不清 year）。setYear 刻意不清：反向入口与源头保持同一口径，勿"顺手修过头"。
    setCampus(c) { this.campus = c; this.showLikedOnly = false; },
    setCollege(c) { this.college = c; this.showLikedOnly = false; },
    setYear(y) { this.year = y; },
    toggleLikedFilter() { this.showLikedOnly = !this.showLikedOnly; this.campus = ''; this.college = ''; },
    clearFilters() { this.campus = ''; this.college = ''; this.year = ''; this.query = ''; this.searchField = ''; this.showLikedOnly = false; this.speaker = ''; this.onlineOnly = false; this.upcomingOnly = false; this.currentPage = 1; },
    /* ---------- 分页 ---------- */
    gotoPage(p) {
      if (p < 1 || p > this.totalPages) return;
      this.currentPage = p;
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    prevPage() { this.gotoPage(this.currentPage - 1); },
    nextPage() { this.gotoPage(this.currentPage + 1); },
    // 跳页输入框：支持「跳转」按钮或回车，自动 clamp 到 [1, 总页数]
    jumpToPage() {
      const n = parseInt(this.gotoInput, 10);
      if (!Number.isNaN(n)) {
        this.gotoPage(n);
        this.gotoInput = '';
      }
    },
    // 点击卡片上的学院/校区 Tag → 直接筛选该维度
    onTagClick(field, val) {
      if (field === 'college') this.college = val;
      else this.campus = val;
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    // 线上讲座筛选开关（2026-10-13）：点卡片「# 线上」标签或顶部筛选片的 × 切换。
    // 与其他标签筛选不同：无固定候选值列表，是布尔开关，再点一次即取消。
    toggleOnline() {
      this.onlineOnly = !this.onlineOnly;
      this.currentPage = 1;
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    // 待开始讲座筛选开关（2026-10-16）：点计数行「待开始 N 场」的数字进入，
    // 再点数字或筛选片 × 取消。与线上开关同款：布尔开关、叠加于其他筛选、
    // 入口方法自设页码（URL 同步由 app.js 的 upcomingOnly watch 负责）。
    toggleUpcoming() {
      this.upcomingOnly = !this.upcomingOnly;
      this.currentPage = 1;
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    // 多来源讲座：返回去重后的所有来源单位（用于标签展示）
    sourceColleges(l) {
      if (!l || !l.sources || !l.sources.length) return [l.college];
      const seen = new Set();
      const out = [];
      // 主记录自身的学院
      if (l.college && !seen.has(l.college)) { seen.add(l.college); out.push(l.college); }
      // 合并来源的学院
      l.sources.forEach(s => {
        const c = s.college || l.college;
        if (!seen.has(c)) { seen.add(c); out.push(c); }
      });
      return out;
    },
    // 多来源讲座：返回去重后的所有「归口校区」（用于标签展示，不含 actualCampus）
    sourceCampuses(l) {
      // 空校区不参与渲染：否则会生成一个只有「#」没有文字的标签（点它也不会有任何按钮高亮）
      if (!l) return [];
      const seen = new Set();
      const out = [];
      const push = (c) => { if (c && !seen.has(c)) { seen.add(c); out.push(c); } };
      // 主记录自身的校区
      push(l.campus);
      // 合并来源的校区（缺省回退到主记录）
      (l.sources || []).forEach(s => push(s.campus || l.campus));
      return out;
    },
    // 标签行校区集合 = 归口校区 ∪ actualCampus（实际举办校区）去重。
    // 2026-10-13 用户截图反馈：多来源校级记录（如社会科学处+心理学院）里，
    // 来源校区（心理学院→石牌）与 actualCampus（国际会议厅→石牌）会渲染出
    // 两个「# 石牌」——合并到同一集合统一去重，single 卡走本方法。
    campusTags(l) {
      const out = this.sourceCampuses(l);
      if (l && l.actualCampus && !out.includes(l.actualCampus)) out.push(l.actualCampus);
      return out;
    },
    // actualCampus 是否与归口校区重复（重复则 forum 卡不再渲染独立标签）
    actualCampusDup(l) {
      return !!(l && l.actualCampus && this.sourceCampuses(l).includes(l.actualCampus));
    },
    // 切换多来源讲座的原文链接展开
    toggleSources(url) {
      if (!url) return;
      this.expanded = { ...this.expanded, [url]: !this.expanded[url] };
    },
    /* ---------- 回到顶部 ---------- */
    onScroll() {
      const y = window.scrollY || window.pageYOffset || 0;
      this.showBackTop = y > 400;
      // 滚动 = 最早的真实交互信号：立刻后台预取长文本，让往下翻时简介与摘要已在手
      // （幂等，内部有就绪/进行中/不可用三重短路，挂在 scroll 上无额外开销）
      if (y > 120) this._ensureLongTextAll();
    },
    scrollToTop() {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
});
