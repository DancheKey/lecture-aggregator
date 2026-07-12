/* 木铎金声 · 讲座统计页（Vue 3）
 * 从 stats.html 外部化，避免内联脚本在 file:// 或严格 CSP 下被拦截。
 */
const { createApp } = Vue;

const SORT_KEY_TOTAL = 'total';
const SORT_KEY_COLLEGE = 'college';

// 学院 / 年份 / 总计 统计页
// 支持点击表头按任意列排序，多次点击切换升/降序；表格首列固定，表头固定，
// 未来年份多、学院多时仍可一页内滚动查看。
createApp({
  data() {
    return {
      all: [],
      // 排序状态：key = 'college' | 'total' | 年份字符串(如 '2024')
      // order = 'asc' | 'desc'（多次点击切换）
      sortBy: { key: SORT_KEY_COLLEGE, order: 'asc' },
      // 学院名过滤（未来学院数量多时便于定位）
      collegeFilter: '',
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

    // 学院 -> 年份 -> 数量的矩阵
    matrix() {
      const m = {};
      this.all.forEach(l => {
        const y = this.yearOf(l);
        if (!y) return;
        const c = l.college || '未分类';
        m[c] = m[c] || {};
        m[c][y] = (m[c][y] || 0) + 1;
      });
      return m;
    },

    // 行列表：每一行对应一个学院/部处
    rows() {
      const key = this.sortBy.key;
      const order = this.sortBy.order;
      const list = Object.keys(this.matrix).map(college => {
        const cells = this.years.map(y => this.matrix[college][y] || 0);
        const total = cells.reduce((a, b) => a + b, 0);
        return { college, cells, total };
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
        } else {
          // 按具体年份列排序
          const idx = this.years.indexOf(key);
          if (idx >= 0) cmp = (a.cells[idx] || 0) - (b.cells[idx] || 0);
        }
        return order === 'asc' ? cmp : -cmp;
      });
      return list;
    },

    // 排序按钮/表头的激活状态（兼容旧用法）
    sortDesc() { return this.sortBy.order === 'desc'; },

    // 每年合计
    yearTotals() {
      return this.years.map(y => ({
        year: y,
        count: this.all.filter(l => this.yearOf(l) === y).length,
      }));
    },

    // 总合计
    grandTotal() {
      return this.yearTotals.reduce((a, t) => a + t.count, 0);
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
          order: key === SORT_KEY_COLLEGE ? 'asc' : 'desc',
        };
      }
    },
    // 表头显示的排序箭头
    sortIcon(key) {
      if (this.sortBy.key !== key) return '⇅';
      return this.sortBy.order === 'asc' ? '↑' : '↓';
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
  },
}).mount('#app');
