// 派生计算（原 app.js 的 computed）。
const APP_COMPUTED = {
    totalCount() { return this.all.length; },

    // 「更新于」显示的日期（2026-09-10）：优先用 last_run.json 的最近运行时间——它由 CI
    // 每次运行刷新（空增量日也刷新），因此即便今天没有新讲座，页面也照常显示今天，
    // 不会让访客误以为站点停更；拿不到该文件时回退到 updatedAt（数据最后变化时间）。
    displayUpdatedAt() {
      return this.lastRunAt || this.updatedAt || '';
    },

    // 来源通知总数（合并后按各讲座的 sourceCount 求和），用于首页说明与统计一致性
    // 口径与 stats.js / generate_frontend_data.py 统一：sourceCount 缺失时回退到 sources 长度
    sourceNoticeCount() {
      return this.all.reduce((a, l) => {
        const fb = (Array.isArray(l.sources) && l.sources.length) ? l.sources.length : 1;
        return a + (l.sourceCount != null ? l.sourceCount : fb);
      }, 0);
    },

    // 数据中出现过的年份（倒序，字符串便于与下拉值比较）
    years() {
      const set = new Set();
      this.all.forEach(l => { const y = this.yearOf(l); if (y) set.add(y); });
      return Array.from(set).sort((a, b) => b.localeCompare(a));
    },

    // 去重学院列表，按讲座数倒序，便于高频学院靠前。
    // 计数口径与筛选一致：合并讲座按「主学院 ∪ 来源单位」展开，每个相关单位各计一次。
    colleges() {
      const cnt = {};
      this.all.forEach(l => {
        const cs = new Set([l.college, ...(l.sources || []).map(s => s.college)].filter(Boolean));
        cs.forEach(c => { cnt[c] = (cnt[c] || 0) + 1; });
      });
      return Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a]);
    },

    // 搜索框占位提示：随所选维度变化（移动端使用单独短 placeholder，见 index.html）
    searchPlaceholder() {
      return {
        '': '搜索讲座 / 主讲人 / 地点…',
          college: '搜索单位…',
          location: '搜索地点…',
          topic: '搜索题目 / 主讲…',
          abstract: '搜索摘要…'
        }[this.searchField] || '搜索…';
    },

    // 是否有任何筛选条件激活（控制"清除"按钮显示）
    hasActiveFilter() {
      return !!(this.campus || this.college || this.year || this.query || this.searchField || this.showLikedOnly || this.speaker);
    },

    // 复合筛选 + 按讲座时间倒序
    filtered() {
      const q = this.query.trim().toLowerCase();
      const list = this.all.filter(l => {
        if (this.showLikedOnly && !this.hasLiked(l.sourceUrl)) return false;
        // 合并讲座可能跨校区/学院，任一来源匹配即保留
        if (this.campus) {
          const campuses = new Set([l.campus, ...(l.sources || []).map(s => s.campus)].filter(Boolean));
          if (!campuses.has(this.campus)) return false;
        }
        if (this.college) {
          const colleges = new Set([l.college, ...(l.sources || []).map(s => s.college)].filter(Boolean));
          if (!colleges.has(this.college)) return false;
        }
        if (this.year && this.yearOf(l) !== this.year) return false;
        if (this.speaker) {
          // 讲者聚合：按归一化键匹配（多人讲座任一键命中即保留）
          const keys = l.speakerKeys || [];
          if (!keys.includes(this.speaker)) return false;
        }
        if (q) {
          let hay;
          if (this.searchField === 'location') {
            // 地点：仅按讲座地点匹配（多来源取各自地点）；空地点记为「待公布」，可筛
            hay = [l.location || '待公布', ...(l.sources || []).map(s => s.location)]
              .filter(Boolean).join(' ').toLowerCase();
          } else if (this.searchField === 'topic') {
            // 题目：标题 + 题目字段 + 主讲人（与占位提示「搜索题目 / 主讲…」对齐）
            hay = [l.title, l.topic, l.listTitle, l.speaker].filter(Boolean).join(' ').toLowerCase();
          } else if (this.searchField === 'abstract') {
            // 摘要：仅按讲座摘要匹配（长文本已从主分片剥离，走 _longText 旁路）
            hay = [this.absRaw(l)].filter(Boolean).join(' ').toLowerCase();
          } else if (this.searchField === 'college') {
            // 单位：仅按主办单位匹配，避免场地在某单位的讲座被误配
            hay = [l.college, ...(l.sources || []).map(s => s.college)]
              .filter(Boolean).join(' ').toLowerCase();
          } else {
            // 全部（默认）：在所有常见字段中匹配
            hay = [l.title, l.topic, l.speaker, l.speakerAffiliation,
              this.bioRaw(l), l.listTitle, l.college, l.location, l.campus, l.organizer,
              this.absRaw(l)]
              .filter(Boolean).join(' ').toLowerCase();
          }
          if (!hay.includes(q)) return false;
        }
        return true;
      });
      list.sort((a, b) => {
        const ta = a.lectureStart || '', tb = b.lectureStart || '';
        if (!ta && !tb) return 0;
        if (!ta) return 1;
        if (!tb) return -1;
        // 主排序：日期倒序
        const da = ta.slice(0, 10), db = tb.slice(0, 10);
        if (da !== db) return db.localeCompare(da);
        // 同一天同系列（砺儒讲坛第X讲等）按编号倒序，让133讲在132讲之上
        // 中文数字（第三讲、第十二期）也支持，映射到 int 后比较
        const _cn2num = (s) => {
          const map = {零:0,一:1,二:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9,十:10,百:100};
          let r = 0, acc = 0;
          for (const ch of s) {
            const v = map[ch];
            if (v === undefined) return NaN;
            if (v >= 10) { acc = acc || 1; r += acc * v; acc = 0; }
            else acc = acc * 10 + v;
          }
          return r + (acc || 0);
        };
        const seriesNo = (title) => {
          const t = String(title || '');
          let m = t.match(/第(\d+)(?:讲|场|期|届)/);
          if (m) return parseInt(m[1], 10);
          m = t.match(/第([一二三四五六七八九十百零]+)(?:讲|场|期|届)/);
          return m ? _cn2num(m[1]) : 0;
        };
        const sa = seriesNo(a.title), sb = seriesNo(b.title);
        if (sa && sb && sa !== sb) return sb - sa;
        // 同页拆分的多期讲座（同 sourceUrl，lectureIndex 含 0）按期数倒序，让第1期在最下面
        const sameSource = a.sourceUrl && a.sourceUrl === b.sourceUrl;
        if (sameSource && typeof a.lectureIndex === 'number' && typeof b.lectureIndex === 'number' && a.lectureIndex !== b.lectureIndex) {
          return b.lectureIndex - a.lectureIndex;
        }
        // 否则按完整时间倒序
        return tb.localeCompare(ta);
      });
      return list;
    },

    // 渲染单元（2026-09-30 修订）：折叠粒度 = 「同源页 + 同一天」。
    // 仅当**同一天**的场次数 ≥ 阈值时才合并成一张论坛卡，其余（含跨天但每天只有 1~2 场
    // 的系列）逐场单卡展示。背景：旧规则按「同源页总场次 ≥3」折叠，把「跨 3 天、每天
    // 仅 1~2 场」的系列也整块折起（如美术学院 4 场分 3 天），反而藏掉了用户想看的逐场
    // 时间/地点。保持 filtered 的既有排序（论坛卡落在其所在天的位置）。
    units() {
      const MIN = 3; // 同日折叠阈值：同一天 ≥3 场才折叠（1~2 场的日子逐场展示更有用）
      const groupKey = l => (l.sourceUrl || '') + '|' + (l.lectureStart || '').slice(0, 10);
      // 先统计各「同源+同日」组场次，仅达标组折叠
      const counts = new Map();
      this.filtered.forEach(l => {
        if (!l.sourceUrl) return;
        const k = groupKey(l);
        counts.set(k, (counts.get(k) || 0) + 1);
      });
      const units = [];
      const seen = new Set();
      let solo = 0;
      this.filtered.forEach(l => {
        const u = l.sourceUrl || '';
        if (!u) {
          units.push({ type: 'single', recs: [l], key: 'x|' + (solo++) });
          return;
        }
        const k = groupKey(l);
        if ((counts.get(k) || 0) >= MIN) {
          if (seen.has(k)) return;
          seen.add(k);
          const recs = this.filtered.filter(x => groupKey(x) === k);
          units.push({
            type: 'forum', url: u, recs, head: recs[0],
            total: recs.length,
            dateLabel: (l.lectureStart || '').slice(0, 10),
            key: 'f|' + k,
          });
          return;
        }
        units.push({
          type: 'single', recs: [l],
          key: 's|' + (l.lectureIndex ?? 'x') + '|' + u,
        });
      });
      return units;
    },

    // 总页数（按渲染单元计：论坛折成 1 个卡片位）
    totalPages() {
      return Math.max(1, Math.ceil(this.units.length / this.pageSize));
    },

    // 智能分页页码：当前页前后各 2 页 + 首尾，省略号占位（边界平滑过渡）
    pageNumbers() {
      const total = this.totalPages;
      const cur = this.currentPage;
      if (total <= 9) return Array.from({ length: total }, (_, i) => i + 1);
      const pages = [];
      const left = Math.max(1, cur - 2);
      const right = Math.min(total, cur + 2);
      if (left > 2) {
        pages.push(1, '...');
      } else {
        for (let i = 1; i < left; i++) pages.push(i);
      }
      for (let i = left; i <= right; i++) pages.push(i);
      if (right < total - 1) {
        pages.push('...', total);
      } else {
        for (let i = right + 1; i <= total; i++) pages.push(i);
      }
      return pages;
    },

    // 当前页对应的单元列表（已筛选 + 按时间倒序 + 论坛折叠）
    pagedUnits() {
      const start = (this.currentPage - 1) * this.pageSize;
      return this.units.slice(start, start + this.pageSize);
    },

    // 当前页再按天分组（单元的日：论坛取最新场次日、单条取本场日），保持时间线视觉风格
    pagedGroups() {
      const groups = {};
      this.pagedUnits.forEach(u => {
        const headDate = u.type === 'forum' ? (u.head.lectureStart || '') : (u.recs[0].lectureStart || '');
        const k = this.dayKey(headDate);
        (groups[k] = groups[k] || []).push(u);
      });
      const keys = Object.keys(groups).sort((a, b) => {
        if (a === '时间待定') return 1;
        if (b === '时间待定') return -1;
        return b.localeCompare(a);
      });
      return keys.map(k => ({ key: k, items: groups[k] }));
    },
};
