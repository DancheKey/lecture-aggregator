/* 木铎金声 · 华南师范大学学术讲座聚合前端（Vue 3，免构建，配合 Tailwind CDN）
 * 功能：讲座总数显示、校区/学院/年份/关键词 多维筛选、可点击 Tag 直达筛选、本地点赞去重。
 */
const { createApp } = Vue;

const LIKE_KEY = 'lecture_likes_v1';
const LIKED_KEY = 'lecture_liked_urls_v1';
const STAT_KEY = 'lecture_stats_v1';      // 本机讲座访问/点赞统计（公网无后端时降级使用）

// 配置项：若已部署「工作流触发代理」（持有 PAT 的 Cloudflare Worker / Vercel Function 等，
// 见 SECURITY.md R6），把其地址填到此处，公网「抓取新数据」按钮即可立即触发 GitHub Actions；
// 留空则按钮走友好降级——网站已配置「每日凌晨 3 点自动更新」，无需手动操作。
// ⚠️ 切勿把 PAT 直接写进前端：静态页无保密环境，会被任何人查看源码拿到。
const WORKFLOW_DISPATCH_URL = '';

createApp({
  data() {
    return {
      all: [],
      mtime: 0,
      updatedAt: '',   // 数据更新时间（ISO 字符串），来自后端 mtime 或静态文件 updatedAt
      // 校区固定顺序（与 sources.yaml / 后端一致）
      campusList: ['', '石牌', '大学城', '佛山', '汕尾', '校级'],
      campus: '',
      college: '',
      year: '',
      query: '',
      showLikedOnly: false,  // 仅显示已点赞讲座
      scraping: false,
      showMenu: false,    // 顶部栏更多操作下拉菜单
      likes: {},          // url -> count（本地点赞数）
      likedUrls: new Set(), // 当前浏览器已点赞的 url 集合
      loading: true,       // 首屏数据加载中（避免闪现空列表）
      dataStage: 'loading', // 'loading' | 'partial' | 'full'：渐进加载阶段
      siteVisits: 0,       // 站点总访问量
      hasBackend: false,   // 是否存在后端（/api/visits 可用）
      lectureStats: {},    // url -> {visits, likes}（后端优先，无后端时回退本机 localStorage）
      toast: { show: false, message: '', timer: null },
      pageSize: 25,        // 每页显示条数（配合渐进式加载，首屏更快）
      currentPage: 1,      // 当前页码
      showBackTop: false,  // 滚动超过阈值后显示「回到顶部」按钮
      expanded: {},         // 多来源讲座的「展开原文链接」状态：sourceUrl -> bool
      // 顶部数字「从 1 滚动增长」动画的展示值（真实数据到达后平滑定格）
      displayTotal: 1,
      displaySource: 1,
    };
  },

  computed: {
    totalCount() { return this.all.length; },

    // 来源通知总数（合并后按各讲座的 sourceCount 求和），用于首页说明与统计一致性
    sourceNoticeCount() {
      return this.all.reduce((a, l) => a + (l.sourceCount || 1), 0);
    },

    // 数据中出现过的年份（倒序，字符串便于与下拉值比较）
    years() {
      const set = new Set();
      this.all.forEach(l => { const y = this.yearOf(l); if (y) set.add(y); });
      return Array.from(set).sort((a, b) => b.localeCompare(a));
    },

    // 去重学院列表，按讲座数倒序，便于高频学院靠前
    colleges() {
      const cnt = {};
      this.all.forEach(l => { if (l.college) cnt[l.college] = (cnt[l.college] || 0) + 1; });
      return Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a]);
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
        if (q) {
          const hay = [l.title, l.topic, l.speaker, l.speakerAffiliation,
            l.speakerBio, l.listTitle, l.college].join(' ').toLowerCase();
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
        const seriesNo = (title) => {
          const m = String(title || '').match(/第(\d+)(?:讲|场|期|届)/);
          return m ? parseInt(m[1], 10) : 0;
        };
        const sa = seriesNo(a.title), sb = seriesNo(b.title);
        if (sa && sb && sa !== sb) return sb - sa;
        // 否则按完整时间倒序
        return tb.localeCompare(ta);
      });
      return list;
    },

    // 按天分组（倒序），供时间线渲染
    grouped() {
      const groups = {};
      this.filtered.forEach(l => {
        const k = this.dayKey(l.lectureStart);
        (groups[k] = groups[k] || []).push(l);
      });
      const keys = Object.keys(groups).sort((a, b) => {
        if (a === '时间待定') return 1;
        if (b === '时间待定') return -1;
        return b.localeCompare(a);
      });
      return keys.map(k => ({ key: k, items: groups[k] }));
    },

    // 总页数
    totalPages() {
      return Math.max(1, Math.ceil(this.filtered.length / this.pageSize));
    },

    // 智能分页页码：当前页前后最多2页 + 首尾，省略号占位
    pageNumbers() {
      const total = this.totalPages;
      const cur = this.currentPage;
      if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
      const pages = [];
      if (cur <= 3) {
        pages.push(1, 2, 3, 4, 5, '...', total);
      } else if (cur >= total - 2) {
        pages.push(1, '...', total - 4, total - 3, total - 2, total - 1, total);
      } else {
        pages.push(1, '...', cur - 1, cur, cur + 1, '...', total);
      }
      return pages;
    },

    // 当前页对应的扁平列表（已筛选 + 按时间倒序）
    pagedItems() {
      const start = (this.currentPage - 1) * this.pageSize;
      return this.filtered.slice(start, start + this.pageSize);
    },

    // 当前页再按天分组，保持时间线视觉风格
    pagedGroups() {
      const groups = {};
      this.pagedItems.forEach(l => {
        const k = this.dayKey(l.lectureStart);
        (groups[k] = groups[k] || []).push(l);
      });
      const keys = Object.keys(groups).sort((a, b) => {
        if (a === '时间待定') return 1;
        if (b === '时间待定') return -1;
        return b.localeCompare(a);
      });
      return keys.map(k => ({ key: k, items: groups[k] }));
    },
  },

  methods: {
    /* ---------- 工具 ---------- */
    yearOf(l) {
      if (!l) return '';
      if (l.lectureStart) return String(l.lectureStart).slice(0, 4);
      // 部分讲座未解析到具体时间，但发布时间或标题里含年份，据此归入对应年份（与 stats.js 保持一致）
      const m = (l.publishTime || '').match(/^(\d{4})/) || (l.title || '').match(/(\d{4})/);
      return m ? m[1] : '';
    },
    fmtDateTime(iso) {
      if (!iso) return '待定';
      const d = new Date(iso.replace(' ', 'T'));
      if (isNaN(d)) return '待定';
      const wk = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
      return `${d.getMonth() + 1}月${d.getDate()}日 周${wk} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    },
    dayKey(iso) {
      if (!iso) return '时间待定';
      const d = new Date(iso.replace(' ', 'T'));
      if (isNaN(d)) return '时间待定';
      const wk = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 周${wk}`;
    },
    statusInfo(iso) {
      if (!iso) return { label: '时间待定', hot: false };
      const d = new Date(iso.replace(' ', 'T'));
      if (isNaN(d)) return { label: '时间待定', hot: false };
      const now = new Date();
      if (d < now) return { label: '已结束', hot: false };
      const days = (d - now) / 86400000;
      return days <= 7 ? { label: '即将开始', hot: true } : { label: '即将开始', hot: false };
    },
    truncate(s, maxLen) {
      if (!s) return '';
      s = String(s);
      return s.length <= maxLen ? s : s.slice(0, maxLen - 1) + '…';
    },
    cleanFooter(s) {
      return (s || '').replace(/(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved)[\s\S]*/i, '').trim();
    },
    abstractOf(l) {
      if (l.abstract) return this.truncate(l.abstract, 300);
      if (l.listTitle && l.listTitle !== l.title) return this.truncate(l.listTitle, 150);
      return '';
    },
    // 安全链接：仅放行 http/https，阻断 javascript:/data: 等可执行协议，防止 XSS
    safeUrl(u) {
      if (!u) return '#';
      const s = String(u).trim();
      return /^https?:\/\//i.test(s) ? s : '#';
    },

    /* ---------- 本地点赞（同一浏览器去重） ---------- */
    loadLikes() {
      try {
        this.likes = JSON.parse(localStorage.getItem(LIKE_KEY) || '{}');
        this.likedUrls = new Set(JSON.parse(localStorage.getItem(LIKED_KEY) || '[]'));
        // 兼容旧数据：早期版本点赞只存 LIKE_KEY，未同步到 STAT_KEY；
        // 初始化时把已有 likes 合并进 lectureStats，确保统计页能正确汇总。
        this.loadLocalStats();
        for (const [url, count] of Object.entries(this.likes)) {
          const s = this.lectureStats[url] || { visits: 0, likes: 0 };
          if (count > (s.likes || 0)) {
            s.likes = count;
            this.lectureStats[url] = s;
          }
        }
        this.saveLocalStats();
      } catch (e) {
        this.likes = {};
        this.likedUrls = new Set();
      }
    },
    saveLikes() {
      try {
        localStorage.setItem(LIKE_KEY, JSON.stringify(this.likes));
        localStorage.setItem(LIKED_KEY, JSON.stringify(Array.from(this.likedUrls)));
      } catch (e) { /* ignore quota/storage errors */ }
    },
    likeCount(url) {
      return this.likes[url] || 0;
    },
    hasLiked(url) {
      return this.likedUrls.has(url);
    },
    toggleLike(url) {
      if (!url) return;
      if (this.hasLiked(url)) {
        // 偶数次点击：取消当次点赞
        this.likes[url] = Math.max(0, (this.likes[url] || 0) - 1);
        this.likedUrls.delete(url);
        this.saveLikes();
        this.bumpLocalStat(url, 'likes', -1);
        this.showToast('已取消点赞');
        // 同步到后端（累减）；失败不影响本机
        fetch('/api/lecture/unlike', {
          method: 'POST', cache: 'no-store',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        }).catch(() => {});
      } else {
        // 奇数次点击：点赞
        this.likes[url] = (this.likes[url] || 0) + 1;
        this.likedUrls.add(url);
        this.saveLikes();
        this.bumpLocalStat(url, 'likes', 1);
        this.showToast('点赞成功');
        // 同步到后端（累加）；失败不影响本机
        fetch('/api/lecture/like', {
          method: 'POST', cache: 'no-store',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        }).catch(() => {});
      }
    },

    /* ---------- 讲座级访问/点赞统计 ---------- */
    loadLocalStats() {
      try { this.lectureStats = JSON.parse(localStorage.getItem(STAT_KEY) || '{}'); }
      catch (e) { this.lectureStats = {}; }
    },
    saveLocalStats() {
      try { localStorage.setItem(STAT_KEY, JSON.stringify(this.lectureStats)); } catch (e) { /* ignore */ }
    },
    // 本机累计一次统计（公网无后端时降级使用，带 3 分钟防刷）
    bumpLocalStat(url, field, delta = 1) {
      const s = this.lectureStats[url] || { visits: 0, likes: 0 };
      s[field] = Math.max(0, (s[field] || 0) + delta);
      this.lectureStats[url] = s;
      this.saveLocalStats();
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
      }
      fetch('/api/lecture/visit', {
        method: 'POST', cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      }).catch(() => {});
    },
    // 加载站点访问量：优先级 本地后端 > countapi.xyz > 不蒜子
    // 避免公网静态版因不蒜子偶发不可用而长期显示 0
    loadSiteVisits() {
      // 1) 本地后端（server.py）直接返回真实总数
      fetch('/api/visits', { cache: 'no-store' })
        .then(r => r.json())
        .then(j => { if (j && j.total != null) { this.siteVisits = j.total; this.hasBackend = true; } else throw new Error('no-total'); })
        .catch(() => {
          // 2) 公网静态版：countapi.xyz（CORS 友好、无需密钥，两页共用同一命名空间保证一致）
          fetch('https://api.countapi.xyz/hit/lecture-aggregator/site', { cache: 'no-store' })
            .then(r => r.json())
            .then(j => { if (j && typeof j.value === 'number') { this.siteVisits = j.value; this.hasBackend = true; } else throw new Error('no-value'); })
            .catch(() => {
              // 3) 最终回退不蒜子
              this.hasBackend = false;
              this._loadBusuanzi();
            });
        });
    },
    _loadBusuanzi() {
      if (document.getElementById('busuanzi_pure_mini_js')) return;
      const s = document.createElement('script');
      s.id = 'busuanzi_pure_mini_js';
      s.async = true;
      s.src = 'https://busuanzi.ibruce.info/busuanzi/2.3/busuanzi.pure.mini.js';
      document.head.appendChild(s);
    },
    // 加载每条讲座的访问/点赞：优先后端，失败降级本机 localStorage
    loadLectureStats() {
      fetch('/api/lecture/stats', { cache: 'no-store' })
        .then(r => r.json())
        .then(j => { if (j && j.stats) this.lectureStats = j.stats; })
        .catch(() => { this.loadLocalStats(); });
    },
    showToast(msg) {
      this.toast.message = msg;
      this.toast.show = true;
      clearTimeout(this.toast.timer);
      this.toast.timer = setTimeout(() => { this.toast.show = false; }, 2000);
    },

    /* ---------- 筛选交互 ---------- */
    setCampus(c) { this.campus = c; this.showLikedOnly = false; },
    setCollege(c) { this.college = c; },
    setYear(y) { this.year = y; },
    toggleLikedFilter() { this.showLikedOnly = !this.showLikedOnly; this.campus = ''; this.college = ''; },
    clearFilters() { this.campus = ''; this.college = ''; this.year = ''; this.query = ''; this.showLikedOnly = false; },
    /* ---------- 分页 ---------- */
    gotoPage(p) {
      if (p < 1 || p > this.totalPages) return;
      this.currentPage = p;
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    prevPage() { this.gotoPage(this.currentPage - 1); },
    nextPage() { this.gotoPage(this.currentPage + 1); },
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
      l.sources.forEach(s => {
        const c = s.college || l.college;
        if (!seen.has(c)) { seen.add(c); out.push(c); }
      });
      return out;
    },
    // 多来源讲座：返回去重后的所有校区（用于标签展示）
    sourceCampuses(l) {
      if (!l || !l.sources || !l.sources.length) return [l.campus];
      const seen = new Set();
      const out = [];
      l.sources.forEach(s => {
        const c = s.campus || l.campus;
        if (!seen.has(c)) { seen.add(c); out.push(c); }
      });
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

    /* ---------- 数据加载（增量 / 渐进式） ----------
     * 本地后端存在时：走 /api/lectures，已启用 gzip，返回全量。
     * GitHub Pages 静态托管时：先拉体积最小的 latest.json（最新 50 条，约 20KB
     * gzip）立刻渲染第一页；后台再拉 lite.json（全量精简字段，约 240KB
     * gzip）启用完整筛选与翻页。
     */
    loadLectures(incremental) {
      const url = (incremental && this.mtime)
        ? `/api/lectures?since=${this.mtime}`
        : '/api/lectures';
      fetch(url, { cache: 'no-store' })
        .then(r => {
          if (!r.ok) throw new Error('api-unavailable');
          return r.json();
        })
        .then(resp => {
          if (resp.unchanged) return;
          this._applyLectureData(resp);
          this.dataStage = 'full';
          this.loading = false;
        })
        .catch(() => {
          // 静态托管（无后端）时回退：先 fastest latest，再 full lite
          if (incremental) return;
          this._loadStaticLatest();
        });
    },

    _applyLectureData(resp) {
      // 兼容多种后端返回：
      //  - {data:[...], updatedAt, mtime}            （新版 server.py，已解包）
      //  - {data:{updatedAt,data:[...]}, updatedAt}  （旧版 server.py，未解包）
      if (Array.isArray(resp)) { this.all = resp; this.mtime = 0; this.updatedAt = ''; return; }
      let arr = resp.data;
      let updatedAt = resp.updatedAt || '';
      if (arr && typeof arr === 'object' && !Array.isArray(arr) && Array.isArray(arr.data)) {
        arr = arr.data;
        if (!updatedAt) updatedAt = arr.updatedAt || '';
      }
      this.all = Array.isArray(arr) ? arr : [];
      this.mtime = resp.mtime || 0;
      this.updatedAt = updatedAt || (resp.mtime ? new Date(resp.mtime * 1000).toISOString() : '');
    },

    _loadStaticLatest() {
      // 先加载 latest.json：仅 50 条，用于首屏秒开
      fetch('lectures/latest.json', { cache: 'no-store' })
        .then(r => r.json())
        .then(resp => {
          this._applyLectureData(resp);
          this.dataStage = 'partial';
          this.loading = false;
          // 后台继续加载完整精简数据
          this._loadStaticFull();
        })
        .catch(() => { this._loadStaticFull(true); });
    },

    _loadStaticFull(fallbackToOriginal = false) {
      const path = fallbackToOriginal ? 'lectures.json' : 'lectures/lite.json';
      fetch(path, { cache: 'no-store' })
        .then(r => r.json())
        .then(resp => {
          this._applyLectureData(resp);
          this.dataStage = 'full';
        })
        .catch(e => { console.error('加载完整讲座数据失败', e); this.finalizeCountAnimation(); });
    },

    /* ---------- 顶部数字滚动动画 ----------
     * 页面加载即开始从 1 快速向上滚动；完整数据（dataStage='full'）到达后，
     * 从当前滚动值平滑过渡到真实值，定格在 totalCount / sourceNoticeCount，
     * 从而避免「先显示 50 再跳到 865」的突兀跳变。
     */
    startCountAnimation() {
      if (this._countRAF) return;
      const ROLL_MS = 1500;   // 滚动阶段时长（ease-out 逼近软上限）
      const CEIL = 950;       // 软上限，避免在没有真实值时飞得太高
      const t0 = performance.now();
      const tick = (now) => {
        if (!this._finalized) {
          const t = Math.min((now - t0) / ROLL_MS, 1);
          const e = 1 - Math.pow(1 - t, 3); // easeOutCubic
          const v = Math.max(1, Math.floor(1 + e * (CEIL - 1)));
          this.displayTotal = v;
          this.displaySource = Math.max(1, Math.floor(v * 1.02));
          this._countRAF = requestAnimationFrame(tick);
        } else {
          const dt = Math.min((now - this._finalStart) / 700, 1);
          const e = 1 - Math.pow(1 - dt, 3);
          this.displayTotal = Math.round(this._fromTotal + e * (this._toTotal - this._fromTotal));
          this.displaySource = Math.round(this._fromSource + e * (this._toSource - this._fromSource));
          if (dt >= 1) {
            this.displayTotal = this._toTotal;
            this.displaySource = this._toSource;
            this._countRAF = null;
            return;
          }
          this._countRAF = requestAnimationFrame(tick);
        }
      };
      this._countRAF = requestAnimationFrame(tick);
    },
    finalizeCountAnimation() {
      if (this._finalized) return;
      this._finalized = true;
      this._finalStart = performance.now();
      this._fromTotal = this.displayTotal;
      this._fromSource = this.displaySource;
      this._toTotal = this.totalCount;
      this._toSource = this.sourceNoticeCount;
    },

    /* ---------- 触发后端抓取 ---------- */
    scrape() {
      this.scraping = true;
      fetch('/api/scrape', { method: 'POST', cache: 'no-store' })
        .then(r => r.json().then(j => ({ ok: r.ok, j })))
        .then(({ ok, j }) => {
          if (ok && j.ok) {
            this.mtime = j.mtime || 0;
            this.loadLectures(true);
          } else {
            this.showToast('抓取失败：' + ((j && j.message) || ''));
          }
        })
        .catch(() => {
          // 静态托管（无后端）时的降级处理
          if (WORKFLOW_DISPATCH_URL) {
            fetch(WORKFLOW_DISPATCH_URL, { method: 'POST', cache: 'no-store' })
              .then(r => {
                if (r.ok) this.showToast('已触发后台更新，几分钟后刷新即可看到最新数据');
                else throw new Error('dispatch-failed');
              })
              .catch(() => this.showToast('立即更新触发失败，网站已配置每日凌晨 3 点自动更新'));
          } else {
            this.showToast('网站已配置每日凌晨 3 点自动更新；如需立即更新，请在本机运行爬虫或手动触发工作流');
          }
        })
        .finally(() => { this.scraping = false; });
    },
  },

  mounted() {
    this.startCountAnimation();
    this.loadLikes();
    this.loadSiteVisits();
    this.loadLectureStats();
    this.loadLectures(false);
    // 隐藏初始 loading 占位，避免 Vue 挂载前显示原始模板
    const pl = document.getElementById('page-loading');
    if (pl) pl.style.display = 'none';
    // 监听滚动，下滑超过阈值时显示「回到顶部」按钮
    this.onScroll();
    window.addEventListener('scroll', this.onScroll);
  },

  beforeUnmount() {
    window.removeEventListener('scroll', this.onScroll);
  },

  watch: {
    // 任一筛选条件变化，回到第一页
    query() { this.currentPage = 1; },
    campus() { this.currentPage = 1; },
    college() { this.currentPage = 1; },
    year() { this.currentPage = 1; },
    showLikedOnly() { this.currentPage = 1; },
    // 数据阶段从 partial 变 full 时，如果当前没有筛选，保持当前页；否则回到第一页
    dataStage(newVal, oldVal) {
      if (newVal === 'full') this.finalizeCountAnimation();
      if (oldVal === 'partial' && newVal === 'full') {
        if (!this.query && !this.campus && !this.college && !this.year && !this.showLikedOnly) {
          // 无筛选时，full 数据已包含当前 50 条，保持页面不跳变
          return;
        }
        this.currentPage = 1;
      }
    },
  },
}).mount('#app');
