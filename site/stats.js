/* 木铎金声 · 讲座统计页（Vue 3）
 * 从 stats.html 外部化，避免内联脚本在 file:// 或严格 CSP 下被拦截。
 */
const { createApp } = Vue;

const SORT_KEY_TOTAL = 'total';
const SORT_KEY_COLLEGE = 'college';
const SORT_KEY_VISITS = 'visits';
const SORT_KEY_LIKES = 'likes';
const STAT_KEY = 'lecture_stats_v1';   // 与 app.js 一致的本机统计键

// 学院 / 年份 / 总计 统计页
// 支持点击表头按任意列排序（学院名/各年份讲座数/总计/访问数/点赞数），多次点击切换升/降序；
// 默认按学院名排序，仅显示学院、年份、总计列；点击访问数/点赞数后动态切换为仅显示对应指标；
// 表格首列固定、表头固定，未来年份多、学院多时仍可一页内滚动查看。
createApp({
  data() {
    return {
      all: [],
      // 排序状态：key = 'college' | 'total' | 'visits' | 'likes' | 年份字符串(如 '2024')
      // order = 'asc' | 'desc'（多次点击切换）
      sortBy: { key: SORT_KEY_COLLEGE, order: 'asc' },
      // 学院名过滤（未来学院数量多时便于定位）
      collegeFilter: '',
      // 每条讲座的访问/点赞统计：url -> {visits, likes}（后端优先，无后端时回退本机）
      lectureStats: {},
    };
  },

  computed: {
    // 所有出现过的年份（升序）
    years() {
      const set = new Set();
      this.all.forEach(l => {
        const y = this.yearOf(l);
        if (y) set.add(y);
      });
      return Array.from(set).sort((a, b) => a.localeCompare(b));
    },

    // 当前展示模式：count（讲座数）/ visits（访问量）/ likes（点赞量）
    mode() {
      if (this.sortBy.key === SORT_KEY_VISITS) return 'visits';
      if (this.sortBy.key === SORT_KEY_LIKES) return 'likes';
      return 'count';
    },

    // 学院 -> 年份 -> {count, visits, likes}
    matrix() {
      const m = {};
      this.all.forEach(l => {
        const y = this.yearOf(l);
        if (!y) return;
        const c = l.college || '未分类';
        const url = l.sourceUrl || '';
        const st = this.lectureStats[url] || { visits: 0, likes: 0 };
        m[c] = m[c] || {};
        const cell = m[c][y] = m[c][y] || { count: 0, visits: 0, likes: 0 };
        cell.count += 1;
        cell.visits += (st.visits || 0);
        cell.likes += (st.likes || 0);
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

    // 每年合计（包含讲座数、访问量、点赞量）
    yearTotals() {
      return this.years.map(y => {
        const yearLectures = this.all.filter(l => this.yearOf(l) === y);
        const count = yearLectures.length;
        const visits = yearLectures.reduce((sum, l) => {
          const st = this.lectureStats[l.sourceUrl || ''] || { visits: 0, likes: 0 };
          return sum + (st.visits || 0);
        }, 0);
        const likes = yearLectures.reduce((sum, l) => {
          const st = this.lectureStats[l.sourceUrl || ''] || { visits: 0, likes: 0 };
          return sum + (st.likes || 0);
        }, 0);
        return { year: y, count, visits, likes };
      });
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
    yearOf(l) {
      if (!l || !l.lectureStart) return '';
      return String(l.lectureStart).slice(0, 4);
    },
    // 切换排序：点击同一列切换顺序，点击新列默认按数值降序、学院名升序
    toggleSort(key) {
      if (this.sortBy.key === key) {
        this.sortBy.order = this.sortBy.order === 'asc' ? 'desc' : 'asc';
      } else {
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
    // 读取每条讲座的访问/点赞：优先后端，但与本地 localStorage 合并（取最大值），
    // 避免本地已有点赞/访问被后端空数据覆盖。
    loadLectureStats() {
      let localStats = {};
      try { localStats = JSON.parse(localStorage.getItem(STAT_KEY) || '{}'); }
      catch (e) { localStats = {}; }
      fetch('/api/lecture/stats', { cache: 'no-store' })
        .then(r => r.json())
        .then(j => {
          if (j && j.stats && Object.keys(j.stats).length) {
            const merged = { ...localStats };
            for (const [url, st] of Object.entries(j.stats)) {
              const local = merged[url] || { visits: 0, likes: 0 };
              merged[url] = {
                visits: Math.max(local.visits || 0, st.visits || 0),
                likes: Math.max(local.likes || 0, st.likes || 0),
              };
            }
            this.lectureStats = merged;
            localStorage.setItem(STAT_KEY, JSON.stringify(merged));
          } else {
            this.lectureStats = localStats;
          }
        })
        .catch(() => { this.lectureStats = localStats; });
    },
    load() {
      fetch('/api/lectures', { cache: 'no-store' })
        .then(r => { if (!r.ok) throw new Error('api'); return r.json(); })
        .then(resp => { this.all = (resp && resp.data) ? resp.data : (Array.isArray(resp) ? resp : []); })
        .catch(() => fetch('lectures.json', { cache: 'no-store' })
          .then(r => r.json())
          .then(arr => { this.all = (arr && arr.data) ? arr.data : (arr || []); })
          .catch(e => console.error('加载讲座数据失败', e)));
    },
  },

  mounted() {
    this.load();
    this.loadLectureStats();
  },
}).mount('#app');
