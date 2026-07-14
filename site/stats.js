/* 木铎金声 · 讲座统计页（Vue 3）
 * 从 stats.html 外部化，避免内联脚本在 file:// 或严格 CSP 下被拦截。
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
      all: [],
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
    };
  },

  computed: {
    // 所有出现过的年份（降序：最新年份在左），无法识别的讲座时间归入“其他”并放在最后
    years() {
      const set = new Set();
      this.all.forEach(l => {
        const y = this.yearOf(l);
        if (y) set.add(y);
      });
      const arr = Array.from(set).sort((a, b) => b.localeCompare(a));
      const idx = arr.indexOf(UNKNOWN_YEAR);
      if (idx >= 0) {
        arr.splice(idx, 1);
        arr.push(UNKNOWN_YEAR);
      }
      return arr;
    },

    // 当前展示模式：count（讲座数）/ visits（访问量）/ likes（点赞量）
    mode() {
      return this.displayMode;
    },

    // 来源通知总数（合并后按各讲座的 sourceCount 求和）
    sourceNoticeCount() {
      return this.all.reduce((a, l) => a + (l.sourceCount || 1), 0);
    },

    // 学院 -> 年份 -> {count, visits, likes}
    // count 按各讲座的 sources（来源通知）展开计数，使「各学院/部处之和」= 原始发布条数；
    // visits/likes 为讲座级统计，仅按主卡 url 计一次，避免按来源重复累加。
    matrix() {
      const m = {};
      this.all.forEach(l => {
        const y = this.yearOf(l);
        if (!y) return;
        const primaryUrl = l.sourceUrl || '';
        const st = this.lectureStats[primaryUrl] || { visits: 0, likes: 0 };
        const sources = (l.sources && l.sources.length) ? l.sources : [l];
        sources.forEach(src => {
          const c = src.college || '未分类';
          m[c] = m[c] || {};
          const cell = m[c][y] = m[c][y] || { count: 0, visits: 0, likes: 0 };
          cell.count += 1;
          if (src.sourceUrl === primaryUrl) {
            cell.visits += (st.visits || 0);
            cell.likes += (st.likes || 0);
          }
        });
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

    // 每年合计：count 按来源通知展开，visits/likes 按主卡计一次
    yearTotals() {
      return this.years.map(y => {
        const yearLectures = this.all.filter(l => this.yearOf(l) === y);
        let count = 0, visits = 0, likes = 0;
        yearLectures.forEach(l => {
          const sources = (l.sources && l.sources.length) ? l.sources : [l];
          count += sources.length;
          const st = this.lectureStats[l.sourceUrl || ''] || { visits: 0, likes: 0 };
          visits += (st.visits || 0);
          likes += (st.likes || 0);
        });
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
      if (!l) return UNKNOWN_YEAR;
      if (l.lectureStart) return String(l.lectureStart).slice(0, 4);
      // 部分讲座未解析到具体时间，但发布时间或标题里包含年份，据此归入对应年份
      const m = (l.publishTime || '').match(/^(\d{4})/) || (l.title || '').match(/(\d{4})/);
      if (m) return m[1];
      return UNKNOWN_YEAR;
    },
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
    const loader = document.getElementById('page-loading');
    if (loader) loader.style.display = 'none';
  },
}).mount('#app');
