/* 木铎金声 · 讲座统计页（Vue 3）
 * 从 stats.html 外部化，避免内联脚本在 file:// 或严格 CSP 下被拦截。
 */
const { createApp } = Vue;

createApp({
  data() {
    return {
      all: [],
      sortBy: 'college', // 'college' | 'total'
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
      const list = Object.keys(this.matrix).map(college => {
        const cells = this.years.map(y => this.matrix[college][y] || 0);
        const total = cells.reduce((a, b) => a + b, 0);
        return { college, cells, total };
      });
      if (this.sortBy === 'total') {
        list.sort((a, b) => b.total - a.total || (a.college || '').localeCompare(b.college || ''));
      } else {
        list.sort((a, b) => (a.college || '').localeCompare(b.college || ''));
      }
      return list;
    },

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
    load() {
      fetch('/api/lectures', { cache: 'no-store' })
        .then(r => { if (!r.ok) throw new Error('api'); return r.json(); })
        .then(resp => { this.all = Array.isArray(resp) ? resp : (resp.data || []); })
        .catch(() => fetch('lectures.json', { cache: 'no-store' })
          .then(r => r.json())
          .then(arr => { this.all = arr || []; })
          .catch(e => console.error('加载讲座数据失败', e)));
    },
  },

  mounted() {
    this.load();
  },
}).mount('#app');
