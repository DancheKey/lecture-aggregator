/* 木铎金声 · 华南师范大学学术讲座聚合前端（Vue 3，免构建，配合 Tailwind CDN）
 * 功能：讲座总数显示、校区/学院/年份/关键词 多维筛选、可点击 Tag 直达筛选、本地点赞去重。
 */
const { createApp } = Vue;

const LIKE_KEY = 'lecture_likes_v1';
const LIKED_KEY = 'lecture_liked_urls_v1';
const WANT_KEY = 'lecture_wants_v1';
const WANTED_KEY = 'lecture_wanted_urls_v1';
const STAT_KEY = 'lecture_stats_v1';      // 本地缓存 + 后端合并后的讲座访问/点赞/想听统计
const COUNT_CAP = 300;                    // 点赞/想听超过此值显示 "300+"，防止虚高数字

// 配置项：若已部署「工作流触发代理」（持有 PAT 的 Cloudflare Worker / Vercel Function 等，
// 见 SECURITY.md R6），把其地址填到此处，公网「抓取新数据」按钮即可立即触发 GitHub Actions；
// 留空则按钮走友好降级——网站已配置「每日凌晨 3 点自动更新」，无需手动操作。
// ⚠️ 切勿把 PAT 直接写进前端：静态页无保密环境，会被任何人查看源码拿到。
const WORKFLOW_DISPATCH_URL = '';

const app = createApp({
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
      searchField: '',  // 搜索维度：''=全部 | college=单位 | location=地点 | topic=题目 | abstract=摘要
      showLikedOnly: false,  // 仅显示已点赞讲座
      scraping: false,
      showMenu: false,    // 顶部栏更多操作下拉菜单
      likes: {},          // url -> count（本地点赞数）
      likedUrls: new Set(), // 当前浏览器已点赞的 url 集合
      wants: {},          // url -> count（本地想听数）
      wantedUrls: new Set(), // 当前浏览器已标记想听的 url 集合
      loading: true,       // 首屏数据加载中（避免闪现空列表）
      dataStage: 'loading', // 'loading' | 'partial' | 'partial-error' | 'full'：渐进加载阶段
    lectureStats: {},    // url -> {visits, likes}（后端优先，无后端时回退本机 localStorage）
      toast: { show: false, message: '', timer: null },
      pageSize: 25,        // 每页显示条数（配合渐进式加载，首屏更快）
      currentPage: 1,      // 当前页码
      gotoInput: '',        // 跳页输入框的临时值
      showBackTop: false,  // 滚动超过阈值后显示「回到顶部」按钮
      expanded: {},         // 多来源讲座的「展开原文链接」状态：sourceUrl -> bool
      // 顶部数字「从 1 滚动增长」动画的展示值（真实数据到达后平滑定格）
      displayTotal: 1,
      displaySource: 1,
      loadedChunks: 0,   // 分片加载断点续传：已成功加载的分片数
    };
  },

  computed: {
    totalCount() { return this.all.length; },

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
      return !!(this.campus || this.college || this.year || this.query || this.searchField || this.showLikedOnly);
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
          let hay;
          if (this.searchField === 'location') {
            // 地点：仅按讲座地点匹配（多来源取各自地点）
            hay = [l.location, ...(l.sources || []).map(s => s.location)]
              .filter(Boolean).join(' ').toLowerCase();
          } else if (this.searchField === 'topic') {
            // 题目：标题 + 题目字段 + 主讲人（与占位提示「搜索题目 / 主讲…」对齐）
            hay = [l.title, l.topic, l.listTitle, l.speaker].filter(Boolean).join(' ').toLowerCase();
          } else if (this.searchField === 'abstract') {
            // 摘要：仅按讲座摘要匹配
            hay = [l.abstract].filter(Boolean).join(' ').toLowerCase();
          } else if (this.searchField === 'college') {
            // 单位：仅按主办单位匹配，避免场地在某单位的讲座被误配
            hay = [l.college, ...(l.sources || []).map(s => s.college)]
              .filter(Boolean).join(' ').toLowerCase();
          } else {
            // 全部（默认）：在所有常见字段中匹配
            hay = [l.title, l.topic, l.speaker, l.speakerAffiliation,
              l.speakerBio, l.listTitle, l.college, l.location, l.campus, l.organizer, l.abstract]
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

    // 总页数
    totalPages() {
      return Math.max(1, Math.ceil(this.filtered.length / this.pageSize));
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
    // 判断是否「时间待定」：
    // 优先使用结构化标记 timeUnknown；未设置时回落旧启发式
    // （占位哨兵 08:00 / 00:00 表示页面未抽取到具体时刻）。
    // timeUnknown===false 时即使时刻为 08:00 也按真实时间展示，
    // 杜绝「真 8 点讲座」被误判为时间待定。
    isTimeTBD(l) {
      if (!l) return true;
      if (l.timeUnknown === true) return true;
      if (l.timeUnknown === false) return false;
      const iso = l.lectureStart;
      if (!iso || typeof iso !== 'string') return true;
      const d = new Date(iso.replace(' ', 'T'));
      if (isNaN(d)) return true;
      const hh = d.getHours(), mm = d.getMinutes();
      return (hh === 8 && mm === 0) || (hh === 0 && mm === 0);
    },
    fmtDateTime(l) {
      if (!l) return '待定';
      const iso = l.lectureStart;
      if (!iso || typeof iso !== 'string') return '待定';
      const d = new Date(iso.replace(' ', 'T'));
      if (isNaN(d)) return '待定';
      const wk = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
      const hh = d.getHours(), mm = d.getMinutes();
      const clock = `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
      if (this.isTimeTBD(l)) {
        return `${d.getMonth() + 1}月${d.getDate()}日 周${wk} 时间待定`;
      }
      return `${d.getMonth() + 1}月${d.getDate()}日 周${wk} ${clock}`;
    },
    dayKey(iso) {
      if (!iso || typeof iso !== 'string') return '时间待定';
      const d = new Date(iso.replace(' ', 'T'));
      if (isNaN(d)) return '时间待定';
      const wk = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 周${wk}`;
    },
    statusInfo(iso) {
      if (!iso || typeof iso !== 'string') return { label: '时间待定', hot: false };
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
    // 安全读取 notes，过滤内部技术标记（Pattern4/OCR/置信度等不应展示给用户）
    notesText(l) {
      const n = l && l.notes;
      const INTERNAL = /Pattern4|夹逼定位|置信度|建议人工核验|OCR.*跳过|识别失败|内部|调试/i;
      if (Array.isArray(n)) {
        return n.filter(item => !INTERNAL.test(String(item))).join('；');
      }
      if (typeof n === 'string') return INTERNAL.test(n) ? '' : n.replace(/^[；;]\s*/, '');
      return '';
    },
    cleanFooter(s) {
      return String(s == null ? '' : s).replace(/(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved)[\s\S]*/i, '').trim();
    },
    // 安全渲染 abstract 字段（已将内部技术标记过滤，可安全展示）
    abstractOf(l) {
      const ab = String(l.abstract || '').trim();
      if (!ab) return '';
      // 过滤：abstract 实际是主讲人简介（解析器常见误抽）则视为无摘要
      // 检测依据：(a) 以「主讲人简介/报告人简介/嘉宾介绍」等前缀开头；
      // (b) 内容与 speakerBio 高度重叠（去空格后互相包含）。
      const bioPrefixes = ['主讲人简介', '报告人简介', '嘉宾介绍', '专家介绍',
        '报告人介绍', '主讲人介绍', '演讲者简介', 'About the Speaker'];
      for (const p of bioPrefixes) {
        if (ab.startsWith(p)) return '';
      }
      const sb = (l.speakerBio || '').replace(/\s/g, '');
      if (sb.length > 10 && sb.includes(ab.replace(/\s/g, ''))) return '';
      // 过滤：abstract 是站点侧边栏「资讯及通知」模块的行政通知列表
      // （如"关于征集国家社科基金...关于申报教育部..."），不是讲座摘要。
      // 特征：含"资讯及通知"栏目标题，或 ≥2 条"关于…通知/公告/申报/征集"短语。
      if (/资讯及通知|(?:关于.{2,40}(?:通知|公告|申报|征集|转发|招标|遴选).*){2,}/.test(ab)) return '';
      return this.truncate(ab, 300);
    },
    // 安全链接：仅放行 http/https，阻断 javascript:/data: 等可执行协议，防止 XSS
    safeUrl(u) {
      if (!u) return '#';
      const s = String(u).trim();
      return /^https?:\/\//i.test(s) ? s : '#';
    },
    // 判断讲座题目(topic)里是否已经包含当前分期的期/讲/场号，避免前端重复追加「（第X期）」
    // 注意：传入的是 topic（单场题目），而非 title（系列名）；故命名为 topicHasSession。
    // 支持：第3讲 / 第3期 / 讲座三 / 三讲 / 3讲 / 第III期 等
    topicHasSession(title, idx) {
      if (!title || !idx) return false;
      const n = parseInt(idx, 10);
      if (!n || n <= 0) return false;
      const arabic = String(n);
      // 中文数字 1-99
      const units = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九'];
      let chinese;
      if (n <= 10) {
        chinese = n === 10 ? '十' : units[n];
      } else if (n < 20) {
        chinese = '十' + units[n % 10];
      } else {
        chinese = units[Math.floor(n / 10)] + '十' + units[n % 10];
      }
      const t = String(title);
      const patterns = [
        new RegExp('第\\s*' + arabic + '\\s*[场期讲]'),
        new RegExp('第\\s*' + chinese + '\\s*[场期讲]'),
        new RegExp('讲座\\s*' + arabic),
        new RegExp('讲座\\s*' + chinese),
        new RegExp(arabic + '\\s*讲'),
        new RegExp(chinese + '\\s*讲'),
      ];
      return patterns.some(re => re.test(t));
    },

    /* ---------- 本地点赞（同一浏览器去重） ---------- */
    loadLikes() {
      try {
        this.likes = JSON.parse(localStorage.getItem(LIKE_KEY) || '{}');
        this.likedUrls = new Set(JSON.parse(localStorage.getItem(LIKED_KEY) || '[]'));
        // 计数以 lectureStats（后端权威 / 本机 STAT_KEY 缓存）为准；
        // 不再把旧版 LIKE_KEY 的本地计数合并进 lectureStats，否则刷新后会
        // 把已取消的脏值重新显示出来。
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
      // 后端 lectureStats 为权威（与统计页同源）；无后端时回退本机 this.likes
      const s = this.lectureStats[url];
      if (s && typeof s.likes === 'number') return s.likes;
      // 不再回退本机 localStorage 残留值：本机只代表「本机是否点过赞」(hasLiked)，
      // 计数以「后端全局权威值」为准，避免旧版逻辑残留的脏值被刷新后显示出来。
      return 0;
    },
    hasLiked(url) {
      return this.likedUrls.has(url);
    },
    toggleLike(url) {
      if (!url) return;
      const willLike = !this.hasLiked(url);
      // 本地 UI 立即切换（乐观更新），保证点击即时反馈
      if (willLike) { this.likedUrls.add(url); } else { this.likedUrls.delete(url); }
      const delta = willLike ? 1 : -1;
      // 乐观更新展示计数；后端返回真实值后会被覆盖（统一数据源，消除首页/统计页不一致）
      // 用 typeof 判断，避免 lectureStats.likes === 0 时被 || 误判为缺失而回退到旧本地值。
      const s = this.lectureStats[url];
      const cur = (s && typeof s.likes === 'number') ? s.likes : 0;
      const next = Math.max(0, cur + delta);
      this.likes[url] = next;
      if (!this.lectureStats[url]) this.lectureStats[url] = { visits: 0, likes: 0 };
      this.lectureStats[url].likes = next;
      this.saveLikes();
      this.saveLocalStats();
      this.showToast(willLike ? '点赞成功' : '已取消点赞');
      const endpoint = willLike ? 'like' : 'unlike';
      fetch('/api/lecture/' + endpoint, {
        method: 'POST', cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      })
        .then(r => r.json())
        .then(j => {
          if (j && j.ok && typeof j.likes === 'number') {
            // 后端权威值覆盖本机乐观值
            if (!this.lectureStats[url]) this.lectureStats[url] = { visits: 0, likes: 0 };
            this.lectureStats[url].likes = j.likes;
            this.likes[url] = j.likes;
            this.saveLikes();
            this.saveLocalStats();
          }
          // 后端失败 / 被节流：保留本机乐观值（无后端时即为真实值）
        })
        .catch(() => { /* 离线：保留本机值 */ });
    },

    /* ---------- 本地「想听」（同一浏览器去重，逻辑与点赞对称） ---------- */
    loadWants() {
      try {
        this.wants = JSON.parse(localStorage.getItem(WANT_KEY) || '{}');
        this.wantedUrls = new Set(JSON.parse(localStorage.getItem(WANTED_KEY) || '[]'));
        // 计数以 lectureStats（后端权威 / 本机 STAT_KEY 缓存）为准；
        // 不再把旧版 WANT_KEY 的本地计数合并进 lectureStats，避免刷新后显示
        // 已取消的旧值。
      } catch (e) {
        this.wants = {};
        this.wantedUrls = new Set();
      }
    },
    saveWants() {
      try {
        localStorage.setItem(WANT_KEY, JSON.stringify(this.wants));
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
    toggleWant(url) {
      if (!url) return;
      const willWant = !this.hasWanted(url);
      if (willWant) { this.wantedUrls.add(url); } else { this.wantedUrls.delete(url); }
      const delta = willWant ? 1 : -1;
      // 用 typeof 判断，避免 lectureStats.wants === 0 时被 || 误判为缺失而回退到旧本地值。
      const s = this.lectureStats[url];
      const cur = (s && typeof s.wants === 'number') ? s.wants : 0;
      const next = Math.max(0, cur + delta);
      this.wants[url] = next;
      if (!this.lectureStats[url]) this.lectureStats[url] = { visits: 0, likes: 0, wants: 0 };
      this.lectureStats[url].wants = next;
      this.saveWants();
      this.saveLocalStats();
      this.showToast(willWant ? '已标记想听' : '已取消想听');
      const endpoint = willWant ? 'want' : 'unwant';
      fetch('/api/lecture/' + endpoint, {
        method: 'POST', cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      })
        .then(r => r.json())
        .then(j => {
          if (j && j.ok && typeof j.wants === 'number') {
            if (!this.lectureStats[url]) this.lectureStats[url] = { visits: 0, likes: 0, wants: 0 };
            this.lectureStats[url].wants = j.wants;
            this.wants[url] = j.wants;
            this.saveWants();
            this.saveLocalStats();
          }
        })
        .catch(() => { /* 离线：保留本机值 */ });
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
    clearFilters() { this.campus = ''; this.college = ''; this.year = ''; this.query = ''; this.searchField = ''; this.showLikedOnly = false; },
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
      if (!l || !l.sources || !l.sources.length) return [l.campus];
      const seen = new Set();
      const out = [];
      // 主记录自身的校区
      if (l.campus && !seen.has(l.campus)) { seen.add(l.campus); out.push(l.campus); }
      // 合并来源的校区
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
     * 本地后端存在时：走 /api/lectures，返回全量最新数据。
     * GitHub Pages 静态托管时：先拉体积最小的 latest.json（最新 50 条）立刻渲染
     * 第一页；后台再拉 lectures.json 启用完整筛选与翻页。
     * 关键优化：公网环境下不要先等 /api/lectures 超时，而是直接走静态切片；
     * 浏览器缓存使用 default，让 GitHub Pages 的 max-age=600 生效，避免每次刷新
     * 都重新下载 6MB 的 lectures.json。
     */
    loadLectures() {
      fetch('/api/lectures', { cache: 'default' })
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
        if (!updatedAt) updatedAt = arr.updatedAt || '';
        arr = arr.data;
      }
      this.all = Array.isArray(arr) ? arr : [];
      this.mtime = resp.mtime || 0;
      this.updatedAt = updatedAt || (resp.mtime ? new Date(resp.mtime * 1000).toISOString() : '');
    },

    _loadStaticLatest() {
      // 先加载 latest.json：仅 50 条，用于首屏秒开；cache:default 让浏览器复用 600s 缓存
      fetch('lectures/latest.json', { cache: 'default' })
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
        const mres = await fetch('lectures/chunks.json', { cache: 'default' });
        if (!mres.ok) throw new Error('no-manifest');
        manifest = await mres.json();
      } catch (e) {
        // 分片清单缺失（旧部署 / 生成失败）：回退整文件 lectures.json
        return this._loadFullSingle();
      }
      const chunks = (manifest && manifest.chunks) || [];
      if (!chunks.length) return this._loadFullSingle();
      this.dataStage = 'partial';
      for (let i = this.loadedChunks || 0; i < chunks.length; i++) {
        let ok = false;
        for (let attempt = 0; attempt < 3 && !ok; attempt++) {
          try {
            const cres = await fetch(chunks[i], { cache: 'default' });
            if (!cres.ok) throw new Error('chunk-' + cres.status);
            const cj = await cres.json();
            this._mergeChunk((cj && cj.data) || []);
            this.bumpCount();
            ok = true;
          } catch (err) {
            console.warn(`讲座分片 ${chunks[i]} 第 ${attempt + 1} 次加载失败`, err);
            if (attempt < 2) await this._sleep(800 * Math.pow(2, attempt));
          }
        }
        if (!ok) {
          // 该分片最终失败：保留已加载的真实条数，展示重试入口；
          // 绝不 finalize 到 50 死值，避免手机端数字定格成假数据。
          this.dataStage = 'partial-error';
          return;
        }
        this.loadedChunks = i + 1;
      }
      this.loadedChunks = 0;
      this.dataStage = 'full';
    },

    _loadFullSingle() {
      fetch('lectures.json', { cache: 'default' })
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
        const k = keyOf(it);
        if (idxMap.has(k)) {
          this.all[idxMap.get(k)] = it;   // 完整数据覆盖首屏预览
        } else {
          idxMap.set(k, this.all.length);
          this.all.push(it);
        }
      }
    },

    _sleep(ms) { return new Promise(res => setTimeout(res, ms)); },

    /* ---------- 顶部数字滚动动画（目标驱动）----------
     * displayTotal 始终向 this._countTarget 平滑靠拢；每加载一片数据就调用 bumpCount()，
     * 把目标抬到当前已加载真实条数，数字持续向上滚动（50 -> 1100 -> ... -> 3000+），
     * 不会中途定格成死值。任一时刻数字都代表「已加载的真实条数」。
     */
    startCountAnimation() {
      this._countFrom = 0;
      this._countSourceFrom = 0;
      this._countTarget = 0;
      this._countSourceTarget = 0;
      this._countStart = performance.now();
      this._countDur = 600;
      if (!this._countRAF) this._countRAF = requestAnimationFrame(this._countTick);
    },
    _countTick(now) {
      const t = Math.min((now - this._countStart) / this._countDur, 1);
      const e = 1 - Math.pow(1 - t, 3);
      const from = this._countFrom, to = this._countTarget;
      this.displayTotal = Math.round(from + e * (to - from));
      this.displaySource = Math.round(this._countSourceFrom + e * (this._countSourceTarget - this._countSourceFrom));
      if (t >= 1) {
        this.displayTotal = to;
        this.displaySource = this._countSourceTarget;
        this._countRAF = null;
        return;
      }
      this._countRAF = requestAnimationFrame(this._countTick);
    },
    bumpCount() {
      // 把滚动目标抬到当前已加载真实条数，并从当前显示值平滑接续；
      // 若 RAF 已停（动画定格），重新启动一段滚动；运行中则就地更新目标，无跳变。
      this._countFrom = this.displayTotal;
      this._countSourceFrom = this.displaySource;
      this._countTarget = this.totalCount;
      this._countSourceTarget = this.sourceNoticeCount;
      this._countStart = performance.now();
      this._countDur = 500;
      if (!this._countRAF) this._countRAF = requestAnimationFrame(this._countTick);
    },
    retryLoadFull() {
      // 断点续传：从失败的分片继续，已加载条数保留并显示，不回退到 50。
      this.dataStage = 'partial';
      this._loadStaticFull();
    },

    /* ---------- 触发后端抓取 ---------- */
    scrape() {
      this.scraping = true;
      fetch('/api/scrape', { method: 'POST', cache: 'no-store' })
        .then(r => r.json().then(j => ({ ok: r.ok, j })))
        .then(({ ok, j }) => {
          if (ok && j.ok) {
            // 修复（2026-08-05 体检 严重-5）：此前先把 mtime 更新为抓取后的新值，
            // 再以 /api/lectures?since=<新mtime> 增量拉取——服务端比对 mtime 判定
            // unchanged 返回空数组，页面既不刷新也无成功提示。抓取后文件必然已变，
            // 直接全量加载并给出提示。
            this.loadLectures(false);
            this.showToast(j.message || '抓取完成');
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
    this.loadWants();
    this.loadLectureStats();
    // 公网静态托管不要先等 /api/lectures 超时；先秒开 latest.json，后台再补全量。
    // 本地后端（127.0.0.1/localhost）仍优先 /api/lectures，保证数据最新。
    // IPv6 回环时浏览器返回的 hostname 是「[::1]」（带方括号），一并覆盖
    const isLocal = ['localhost', '127.0.0.1', '::1', '[::1]'].includes(location.hostname);
    if (isLocal) {
      this.loadLectures(false);
    } else {
      this._loadStaticLatest();
    }
    // 隐藏初始 loading 占位，避免 Vue 挂载前显示原始模板
    // （v-cloak 已替代，无需手动隐藏 page-loading）
    // 监听滚动，下滑超过阈值时显示「回到顶部」按钮
    this.onScroll();
    window.addEventListener('scroll', this.onScroll);
    // 点击顶部菜单外部时自动关闭（修复移动端因 mouseenter+click 竞态需点两次）
    this._closeMenuHandler = (e) => {
      const container = this.$refs.menuContainer;
      if (this.showMenu && container && !container.contains(e.target)) {
        this.showMenu = false;
      }
    };
    document.addEventListener('click', this._closeMenuHandler);
  },

  beforeUnmount() {
    window.removeEventListener('scroll', this.onScroll);
    if (this._closeMenuHandler) {
      document.removeEventListener('click', this._closeMenuHandler);
    }
  },

  watch: {
    // 任一筛选条件变化，回到第一页
    query() { this.currentPage = 1; },
    searchField() { this.currentPage = 1; },
    campus() { this.currentPage = 1; },
    college() { this.currentPage = 1; },
    year() { this.currentPage = 1; },
    showLikedOnly() { this.currentPage = 1; },
    // 数据阶段从 partial 变 full 时，如果当前没有筛选，保持当前页；否则回到第一页
    dataStage(newVal, oldVal) {
      if (newVal === 'full') this.bumpCount();
      if (oldVal === 'partial' && newVal === 'full') {
        if (!this.query && !this.campus && !this.college && !this.year && !this.showLikedOnly) {
          // 无筛选时，full 数据已包含当前 50 条，保持页面不跳变
          return;
        }
        this.currentPage = 1;
      }
    },
  },
});

// 全局渲染错误兜底：单条脏数据不再导致 Vue 卸载整页（白屏）
// 在 mount 之前注册，渲染异常仅打印日志，不卸载组件树
app.config.errorHandler = function(err, instance, info) {
  console.error('[渲染异常]', err && err.message ? err.message : err, info);
};

app.mount('#app');
