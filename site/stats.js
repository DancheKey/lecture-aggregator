/* 木铎金声 · 讲座统计页（Vue 3）
 * 从 stats.html 外部化，避免内联脚本在 file:// 或严格 CSP 下被拦截。
 *
 * 2026-07-15 优化：不再加载 2MB+ 全量 /api/lectures，改为加载预生成的
 * lectures/stats.json（约 75KB），仅包含学院-年份矩阵、年份合计与最小讲座索引；
 * 访问/点赞数仍通过 /api/lecture/stats 与本机 localStorage 动态合并后计算。
 */
const { createApp } = Vue;

const SORT_KEY_TOTAL = 'total';
const SORT_KEY_COLLEGE = 'college';
const SORT_KEY_VISITS = 'visits';
const SORT_KEY_LIKES = 'likes';
const STAT_KEY = 'lecture_stats_v1';   // 与 app.js 一致的本机统计键
const UNKNOWN_YEAR = '其他';             // 讲座时间缺失时归入此类

// 学院 / 年份 / 总计 统计页
// 支持点击表头按任意列排序（学院名/各年份讲座数/总计/访问数/点赞数），多次点击切换升/降序；
// 默认按学院名排序，仅显示学院、年份、总计列；点击访问数/点赞数后动态切换为仅显示对应指标；
// 表格首列固定、表头固定，未来年份多、学院多时仍可一页内滚动查看。
createApp({
  data() {
    return {
      // 来自 lectures/stats.json 的预计算数据
      summary: null,
      // 排序状态：key = 'college' | 'total' | 'visits' | 'likes' | 年份字符串(如 '2024')
      // order = 'asc' | 'desc'（多次点击切换）
      sortBy: { key: SORT_KEY_COLLEGE, order: 'asc' },
      // 显示模式：count | visits | likes —— 决定单元格与末列展示什么数值。
      // 仅由顶部 4 个排序按钮设置；点击年份列只改变 sortBy.key，不改变显示模式，
      // 这样在「访问数 / 点赞数」模式下点击年份，仍按该模式展示并按年份排序。
      displayMode: 'count',
      // 学院名过滤（未来学院数量多时便于定位）
      collegeFilter: '',
      // 每条讲座的访问/点赞统计：url -> {visits, likes}（后端优先，无后端时回退本机）
      lectureStats: {},
      // 是否正在加载数据（用于显示轻量提示）
      loading: true,
      // 加载失败提示
      loadError: '',
      // 站点总访问量
      siteVisits: 0,
      // 是否接入后端（决定总访问量显示后端数据或不蒜子）
      hasBackend: true,
    };
  },

  computed: {
    // 所有出现过的年份（由后端预计算好，最新年份在左，"其他"在最后）
    years() {
      return (this.summary && this.summary.years) || [];
    },

    // 当前展示模式：count（讲座数）/ visits（访问量）/ likes（点赞量）
    mode() {
      return this.displayMode;
    },

    // 去重后讲座总数
    lectureCount() {
      return (this.summary && this.summary.lectureCount) || 0;
    },

    // 来源通知总数（由后端预计算：按各讲座来源通知展开）
    sourceNoticeCount() {
      return (this.summary && this.summary.sourceNoticeCount) || 0;
    },

    // 学院 -> 年份 -> {count, visits, likes}
    // count 来自预计算矩阵；visits/likes 由最小讲座索引 + lectureStats 动态合并。
    matrix() {
      const m = {};
      const rawMatrix = (this.summary && this.summary.matrix) || {};
      // 先写入预计算的 count
      Object.entries(rawMatrix).forEach(([college, yearMap]) => {
        m[college] = {};
        Object.entries(yearMap).forEach(([year, count]) => {
          m[college][year] = { count: count || 0, visits: 0, likes: 0 };
        });
      });
      // 再叠加访问/点赞（按主卡 url 计一次，避免按来源重复累加）
      const lectures = (this.summary && this.summary.lectures) || [];
      lectures.forEach(l => {
        const st = this.lectureStats[l.u] || { visits: 0, likes: 0 };
        if (!st.visits && !st.likes) return;
        const c = l.c || '未分类';
        const y = l.y || UNKNOWN_YEAR;
        const cell = (m[c] && m[c][y]) || { count: 0, visits: 0, likes: 0 };
        cell.visits += (st.visits || 0);
        cell.likes += (st.likes || 0);
        if (!m[c]) m[c] = {};
        m[c][y] = cell;
      });
      return m;
    },

    // 行列表：每一行对应一个学院/部处
    rows() {
      const key = this.sortBy.key;
      const order = this.sortBy.order;
      const list = Object.keys(this.matrix).map(college => {
        const cells = this.years.map(y => this.matrix[college][y] || { count: 0, visits: 0, likes: 0 });
        const total = cells.reduce((a, b) => a + b.count, 0);
        const visitsTotal = cells.reduce((a, b) => a + b.visits, 0);
        const likesTotal = cells.reduce((a, b) => a + b.likes, 0);
        return { college, cells, total, visitsTotal, likesTotal };
      }).filter(row => {
        if (!this.collegeFilter) return true;
        return (row.college || '').toLowerCase().includes(this.collegeFilter.toLowerCase());
      });

      list.sort((a, b) => {
        let cmp = 0;
        if (key === SORT_KEY_COLLEGE) {
          cmp = (a.college || '').localeCompare(b.college || '');
        } else if (key === SORT_KEY_TOTAL) {
          cmp = a.total - b.total;
        } else if (key === SORT_KEY_VISITS) {
          cmp = a.visitsTotal - b.visitsTotal;
        } else if (key === SORT_KEY_LIKES) {
          cmp = a.likesTotal - b.likesTotal;
        } else {
          // 按具体年份列排序（当前模式下对应的数值）
          const idx = this.years.indexOf(key);
          if (idx >= 0) {
            if (this.mode === 'visits') cmp = (a.cells[idx].visits || 0) - (b.cells[idx].visits || 0);
            else if (this.mode === 'likes') cmp = (a.cells[idx].likes || 0) - (b.cells[idx].likes || 0);
            else cmp = (a.cells[idx].count || 0) - (b.cells[idx].count || 0);
          }
        }
        return order === 'asc' ? cmp : -cmp;
      });
      return list;
    },

    // 每年合计：count 来自预计算 yearTotals；visits/likes 动态叠加。
    yearTotals() {
      const rawTotals = (this.summary && this.summary.yearTotals) || {};
      const totals = {};
      Object.entries(rawTotals).forEach(([year, count]) => {
        totals[year] = { year, count: count || 0, visits: 0, likes: 0 };
      });
      const lectures = (this.summary && this.summary.lectures) || [];
      lectures.forEach(l => {
        const st = this.lectureStats[l.u] || { visits: 0, likes: 0 };
        if (!st.visits && !st.likes) return;
        const y = l.y || UNKNOWN_YEAR;
        const t = totals[y] || { year: y, count: 0, visits: 0, likes: 0 };
        t.visits += (st.visits || 0);
        t.likes += (st.likes || 0);
        totals[y] = t;
      });
      return this.years.map(y => totals[y] || { year: y, count: 0, visits: 0, likes: 0 });
    },

    // 总合计
    grandTotal() {
      return this.yearTotals.reduce((a, t) => a + t.count, 0);
    },
    // 全站访问数合计
    grandVisits() {
      return this.yearTotals.reduce((a, t) => a + t.visits, 0);
    },
    // 全站点赞数合计
    grandLikes() {
      return this.yearTotals.reduce((a, t) => a + t.likes, 0);
    },
  },

  methods: {
    // 切换排序：
    //  - 点击年份列：仅改变排序键，保持当前显示模式（仍按访问数/点赞数展示并排序）
    //  - 点击顶部排序按钮：同时设置显示模式与排序键
    toggleSort(key) {
      const isYearKey = key !== SORT_KEY_COLLEGE && key !== SORT_KEY_TOTAL
        && key !== SORT_KEY_VISITS && key !== SORT_KEY_LIKES;
      if (isYearKey) {
        if (this.sortBy.key === key) {
          this.sortBy.order = this.sortBy.order === 'asc' ? 'desc' : 'asc';
        } else {
          this.sortBy = { key, order: 'desc' };
        }
        return;
      }
      const newMode = key === SORT_KEY_VISITS ? 'visits'
        : key === SORT_KEY_LIKES ? 'likes' : 'count';
      if (this.sortBy.key === key) {
        this.sortBy.order = this.sortBy.order === 'asc' ? 'desc' : 'asc';
      } else {
        this.displayMode = newMode;
        this.sortBy = {
          key,
          order: (key === SORT_KEY_COLLEGE) ? 'asc' : 'desc',
        };
      }
    },
    // 表头显示的排序箭头
    sortIcon(key) {
      if (this.sortBy.key !== key) return '⇅';
      return this.sortBy.order === 'asc' ? '↑' : '↓';
    },
    // 根据当前模式取单元格数值
    cellValue(cell) {
      if (this.mode === 'visits') return cell.visits || 0;
      if (this.mode === 'likes') return cell.likes || 0;
      return cell.count || 0;
    },
    // 单元格显示：0 显示为 —，非 0 显示数值
    cellDisplay(cell) {
      const v = this.cellValue(cell);
      return v || '—';
    },
    // 读取每条讲座的访问/点赞：优先后端，无后端时回退本机。
    // 后端返回的 URL 会覆盖本地同名键，避免本机残留旧测试数据与后端不一致。
    loadLectureStats() {
      let localStats = {};
      try { localStats = JSON.parse(localStorage.getItem(STAT_KEY) || '{}'); }
      catch (e) { localStats = {}; }
      fetch('/api/lecture/stats', { cache: 'no-store' })
        .then(r => r.json())
        .then(j => {
          if (j && j.stats) {
            // 后端优先：后端有的 URL 直接覆盖本地；后端没有的保留本地
            this.lectureStats = { ...localStats, ...j.stats };
            localStorage.setItem(STAT_KEY, JSON.stringify(this.lectureStats));
          } else {
            this.lectureStats = localStats;
          }
        })
        .catch(() => { this.lectureStats = localStats; });
    },
    // 加载统计页专用数据切片（ lectures/stats.json ），体积极小，避免解析 2MB+ 全量数据。
    load() {
      this.loading = true;
      fetch('lectures/stats.json', { cache: 'no-store' })
        .then(r => { if (!r.ok) throw new Error('stats'); return r.json(); })
        .then(resp => {
          this.summary = resp || null;
          this.loading = false;
        })
        .catch(e => {
          console.error('加载统计数据失败', e);
          this.loadError = '统计数据加载失败，请稍后刷新重试。';
          this.loading = false;
        });
    },
    // 加载站点总访问量：有后端用后端，无后端（公网静态）降级用不蒜子
    loadSiteVisits() {
      fetch('/api/visits', { cache: 'no-store' })
        .then(r => r.json())
        .then(j => { if (j && j.total != null) { this.siteVisits = j.total; this.hasBackend = true; } })
        .catch(() => { this.hasBackend = false; this._loadBusuanzi(); });
    },
    _loadBusuanzi() {
      if (document.getElementById('busuanzi_pure_mini_js')) return;
      const s = document.createElement('script');
      s.id = 'busuanzi_pure_mini_js';
      s.async = true;
      s.src = 'https://busuanzi.ibruce.info/busuanzi/2.3/busuanzi.pure.mini.js';
      document.head.appendChild(s);
    },
  },

  mounted() {
    this.load();
    this.loadLectureStats();
    this.loadSiteVisits();
  },
}).mount('#app');
