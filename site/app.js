/* 木铎金声 · 华南师范大学学术讲座聚合前端（Vue 3，免构建，配合 Tailwind CDN）
 * 功能：讲座总数显示、校区/学院/年份/关键词 多维筛选、可点击 Tag 直达筛选、本地点赞去重。
 */
const { createApp } = Vue;

const LIKE_KEY = 'lecture_likes_v1';
const LIKED_KEY = 'lecture_liked_urls_v1';

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
      scraping: false,
      showMenu: false,    // 顶部栏更多操作下拉菜单
      likes: {},          // url -> count（本地点赞数）
      likedUrls: new Set(), // 当前浏览器已点赞的 url 集合
      toast: { show: false, message: '', timer: null },
      pageSize: 25,        // 每页显示条数
      currentPage: 1,      // 当前页码
    };
  },

  computed: {
    totalCount() { return this.all.length; },

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
        if (this.campus && l.campus !== this.campus) return false;
        if (this.college && l.college !== this.college) return false;
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
      if (!l || !l.lectureStart) return '';
      return String(l.lectureStart).slice(0, 4);
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
      if (this.hasLiked(url)) {
        this.showToast('您已经赞过该讲座');
        return;
      }
      this.likes[url] = (this.likes[url] || 0) + 1;
      this.likedUrls.add(url);
      this.saveLikes();
      this.showToast('点赞成功');
    },
    showToast(msg) {
      this.toast.message = msg;
      this.toast.show = true;
      clearTimeout(this.toast.timer);
      this.toast.timer = setTimeout(() => { this.toast.show = false; }, 2000);
    },

    /* ---------- 筛选交互 ---------- */
    setCampus(c) { this.campus = c; },
    setCollege(c) { this.college = c; },
    setYear(y) { this.year = y; },
    clearFilters() { this.campus = ''; this.college = ''; this.year = ''; this.query = ''; },
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

    /* ---------- 数据加载（增量） ---------- */
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
          if (resp.unchanged) return;       // 无更新
          // 兼容后端 {data:[...], updatedAt} 与静态数组两种格式
          if (Array.isArray(resp)) { this.all = resp; this.mtime = 0; this.updatedAt = ''; }
          else {
            this.all = resp.data || [];
            this.mtime = resp.mtime || 0;
            // 优先用文件内嵌 updatedAt；本地模式下回退用 mtime 推算
            this.updatedAt = resp.updatedAt || (resp.mtime ? new Date(resp.mtime * 1000).toISOString() : '');
          }
        })
        .catch(() => {
          // 静态托管（无后端）时回退读取站点根 lectures.json
          if (incremental) return;          // 增量轮询失败静默忽略
          fetch('lectures.json', { cache: 'no-store' })
            .then(r => r.json())
            .then(arr => {
              if (Array.isArray(arr)) { this.all = arr; this.updatedAt = ''; }
              else { this.all = arr.data || []; this.updatedAt = arr.updatedAt || ''; }
              this.mtime = 0;
            })
            .catch(e => console.error('加载讲座失败', e));
        });
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
    this.loadLikes();
    this.loadLectures(false);
  },

  watch: {
    // 任一筛选条件变化，回到第一页
    query() { this.currentPage = 1; },
    campus() { this.currentPage = 1; },
    college() { this.currentPage = 1; },
    year() { this.currentPage = 1; },
  },
}).mount('#app');
