// 交互层：点赞与想听、本机统计缓存、筛选切换、分页与滚动。
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
      // toast 不先于 fetch 无条件报成功（C1-c）：按真实结果分支提示
      const endpoint = willLike ? 'like' : 'unlike';
      fetch('/api/lecture/' + endpoint, {
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
      // toast 不先于 fetch 无条件报成功（C1-c，与点赞对称）：按真实结果分支提示
      const endpoint = willWant ? 'want' : 'unwant';
      fetch('/api/lecture/' + endpoint, {
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
    },
    // 显示计数：超过 300 显示 "300+"，避免被攻击造成虚高数字
    capDisplay(n) {
      return n > COUNT_CAP ? COUNT_CAP + '+' : String(n);
    },

    /* ---------- 讲座级访问/点赞/想听统计 ---------- */
    loadLocalStats() {
      try { this.lectureStats = JSON.parse(localStorage.getItem(STAT_KEY) || '{}'); }
      catch (e) { this.lectureStats = {}; }
    },
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
        // 仅在本机未节流时才通知后端（后端自身也有节流，此为双重防护，并避免无意义请求）
        fetch('/api/lecture/visit', {
          method: 'POST', cache: 'no-store',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        }).catch(() => {});
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
      fetch('/api/lecture/stats', { cache: 'no-store' })
        .then(r => r.json())
        .then(j => {
          if (j && j.stats) {
            const merged = { ...localStats };
            for (const [url, serverSt] of Object.entries(j.stats)) {
              merged[url] = { ...(merged[url] || {}), ...serverSt };
            }
            this.lectureStats = merged;
            localStorage.setItem(STAT_KEY, JSON.stringify(merged));
          } else {
            this.lectureStats = localStats;
          }
        })
        .catch(() => { this.lectureStats = localStats; });
    },
    showToast(msg, ms = 2000) {
      this.toast.message = msg;
      this.toast.show = true;
      clearTimeout(this.toast.timer);
      this.toast.timer = setTimeout(() => { this.toast.show = false; }, ms);
    },

    /* ---------- 筛选交互 ---------- */
    setCampus(c) { this.campus = c; this.showLikedOnly = false; },
    setCollege(c) { this.college = c; },
    setYear(y) { this.year = y; },
    toggleLikedFilter() { this.showLikedOnly = !this.showLikedOnly; this.campus = ''; this.college = ''; },
    clearFilters() { this.campus = ''; this.college = ''; this.year = ''; this.query = ''; this.searchField = ''; this.showLikedOnly = false; this.speaker = ''; this.currentPage = 1; },
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
    // 多来源讲座：返回去重后的所有校区（用于标签展示）
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
    // 切换多来源讲座的原文链接展开
    toggleSources(url) {
      if (!url) return;
      this.expanded = { ...this.expanded, [url]: !this.expanded[url] };
    },
    /* ---------- 回到顶部 ---------- */
    onScroll() {
      this.showBackTop = (window.scrollY || window.pageYOffset || 0) > 400;
    },
    scrollToTop() {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
});
