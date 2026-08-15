/* 木铎金声 · 讲座统计页（Vue 3）
 * 从 stats.html 外部化，避免内联脚本在 file:// 或严格 CSP 下被拦截。
 *
 * 以 lectures/stats.json 为唯一权威数据源，约 300KB（含预计算
 * matrix/yearTotals/campusMap 与最小讲座索引），避免加载 ~5.7MB 全量 lectures.json。
 * 数据一致性由 scripts/generate_frontend_data.py 保证。
 * 访问/点赞数仍通过 /api/lecture/stats 与本机 localStorage 动态合并后计算。
 * 失败时直接提示错误，不再 fallback。
 */
const { createApp } = Vue;

const SORT_KEY_TOTAL = 'total';
const SORT_KEY_COLLEGE = 'college';
const SORT_KEY_VISITS = 'visits';
const SORT_KEY_LIKES = 'likes';
const STAT_KEY = 'lecture_stats_v1';   // 与 app.js 一致的本机统计键
const UNKNOWN_YEAR = '其他';             // 讲座时间缺失时归入此类
// 校区筛选顺序（与 sources.yaml / 首页一致）；空串代表"全部"
const CAMPUSES = ['', '石牌', '大学城', '佛山', '汕尾', '校级'];

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
      sortBy: { key: SORT_KEY_TOTAL, order: 'desc' },
      // 显示模式：count | visits | likes —— 决定单元格与末列展示什么数值。
      // 仅由顶部 4 个排序按钮设置；点击年份列只改变 sortBy.key，不改变显示模式，
      // 这样在「访问数 / 点赞数」模式下点击年份，仍按该模式展示并按年份排序。
      displayMode: 'count',
      // 学院名过滤（未来学院数量多时便于定位）
      collegeFilter: '',
      // 校区筛选：'' = 全部；其余为具体校区
      campusFilter: '',
      // 校区选项（来自 CAMPUSES 常量）
      campuses: CAMPUSES,
      // 每条讲座的访问/点赞统计：url -> {visits, likes}（后端优先，无后端时回退本机）
      lectureStats: {},
      // 是否正在加载数据（用于显示轻量提示）
      loading: true,
      // 加载失败提示
      loadError: '',
      // 顶部数字「从 1 滚动增长」动画的展示值（数据到达后平滑定格）
      displayLecture: 1,
      displaySource: 1,
    };
  },

  computed: {
    // 所有出现过的年份（由后端预计算好，最新年份在左，"其他"在最后）
    years() {
      return (this.summary && this.summary.years) || [];
    },

    // 当前展示模式：count（讲座数）/ visits（访问量）/ likes（点赞量）—— stats.html 直接引用 displayMode
    //（mode 计算属性已在 2026-08-13 清理冗余时删除，模板统一使用 displayMode）

    // 当前校区筛选下的学院集合（'' = 全部学院）
    campusColleges() {
      if (!this.campusFilter) return null;
      const cmap = (this.summary && this.summary.campusMap) || {};
      const set = new Set();
      Object.entries(cmap).forEach(([college, campus]) => {
        if (campus === this.campusFilter) set.add(college);
      });
      return set;
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
      // 再叠加访问/点赞：按该讲座归属的每个单位（cs）展开累加，
      // 与矩阵计数口径一致——联合发布的讲座其访问/点赞也在每个相关单位计入。
      const lectures = (this.summary && this.summary.lectures) || [];
      lectures.forEach(l => {
        const st = this.lectureStats[l.u] || { visits: 0, likes: 0 };
        if (!st.visits && !st.likes) return;
        const y = l.y || UNKNOWN_YEAR;
        const cols = (l.cs && l.cs.length) ? l.cs : [l.c || '未分类'];
        cols.forEach(c => {
          const cell = (m[c] && m[c][y]) || { count: 0, visits: 0, likes: 0 };
          cell.visits += (st.visits || 0);
          cell.likes += (st.likes || 0);
          if (!m[c]) m[c] = {};
          m[c][y] = cell;
        });
      });
      return m;
    },

    // 行列表：每一行对应一个学院/部处（受校区 + 学院名双重过滤）
    rows() {
      const key = this.sortBy.key;
      const order = this.sortBy.order;
      const cols = this.campusColleges;
      const list = Object.keys(this.matrix).map(college => {
        if (cols && !cols.has(college)) return null;
        const cells = this.years.map(y => this.matrix[college][y] || { count: 0, visits: 0, likes: 0 });
        const total = cells.reduce((a, b) => a + b.count, 0);
        const visitsTotal = cells.reduce((a, b) => a + b.visits, 0);
        const likesTotal = cells.reduce((a, b) => a + b.likes, 0);
        return { college, cells, total, visitsTotal, likesTotal };
      }).filter(row => {
        if (!row) return false;
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
            if (this.displayMode === 'visits') cmp = (a.cells[idx].visits || 0) - (b.cells[idx].visits || 0);
            else if (this.displayMode === 'likes') cmp = (a.cells[idx].likes || 0) - (b.cells[idx].likes || 0);
            else cmp = (a.cells[idx].count || 0) - (b.cells[idx].count || 0);
          }
        }
        return order === 'asc' ? cmp : -cmp;
      });
      return list;
    },

    // 每年合计：受校区筛选影响；count 来自矩阵累加，visits/likes 动态叠加。
    yearTotals() {
      const cols = this.campusColleges;
      const counts = {};
      Object.entries(this.matrix).forEach(([college, yearMap]) => {
        if (cols && !cols.has(college)) return;
        Object.entries(yearMap).forEach(([y, c]) => { counts[y] = (counts[y] || 0) + (c.count || 0); });
      });
      const totals = {};
      this.years.forEach(y => { totals[y] = { year: y, count: counts[y] || 0, visits: 0, likes: 0 }; });
      const lectures = (this.summary && this.summary.lectures) || [];
      lectures.forEach(l => {
        const st = this.lectureStats[l.u] || { visits: 0, likes: 0 };
        if (!st.visits && !st.likes) return;
        const y = l.y || UNKNOWN_YEAR;
        // 与 matrix 同口径——按讲座归属的全部单位（cs）展开、逐单位过校区筛选。
        // 主学院不在当前校区、次学院在时，列里计入了、合计行却漏掉，
        // 「按访问数/点赞数」模式下合计行小于各列之和。
        const cs = (l.cs && l.cs.length) ? l.cs : [l.c || '未分类'];
        cs.forEach(c => {
          if (cols && !cols.has(c)) return;
          const t = totals[y] || { year: y, count: 0, visits: 0, likes: 0 };
          t.visits += (st.visits || 0);
          t.likes += (st.likes || 0);
          totals[y] = t;
        });
      });
      return this.years.map(y => totals[y] || { year: y, count: 0, visits: 0, likes: 0 });
    },

    // 当前筛选（校区 + 学院名）下的学院/部处（单位）数量
    filteredCollegeCount() {
      return this.rows.length;
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
      if (this.displayMode === 'visits') return cell.visits || 0;
      if (this.displayMode === 'likes') return cell.likes || 0;
      return cell.count || 0;
    },
    // 单元格显示：0 显示为 —，非 0 显示数值
    cellDisplay(cell) {
      const v = this.cellValue(cell);
      return v || '—';
    },
    // 读取每条讲座的访问/点赞：优先后端，无后端时回退本机。
    // 按 URL 字段级合并，避免整条覆盖丢失 wants 字段。
    // 字段级合并：后端没有的字段保留本地值，后端有的字段以后端权威值覆盖。
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
    // 顶部数字「从 1 滚动增长」动画：数据到达后平滑定格到真实值。
    // 不预设硬上限，避免旧数值（如 950）在屏幕上「定格」等待。
    startCountAnimation() {
      if (this._countRAF) return;
      // 加载阶段：线性慢速增长，明确是「加载中占位」而非真实数据；
      // 不封顶在像真实值的数字上，避免误导。完整数据到达后由 finalize 快速滚到真实值。
      const SPEED = 65;       // 每秒约 65，视觉上慢慢滚但不会定格成假数字
      const t0 = performance.now();
      const tick = (now) => {
        if (!this._finalized) {
          const elapsed = (now - t0) / 1000;
          const v = Math.max(1, Math.floor(1 + SPEED * elapsed));
          this.displayLecture = v;
          this.displaySource = Math.max(1, Math.floor(v * 1.02));
          this._countRAF = requestAnimationFrame(tick);
        } else {
          // 数据到达后快速（300ms）滚到真实值，营造「最后冲刺」感
          const dt = Math.min((now - this._finalStart) / 300, 1);
          const e = 1 - Math.pow(1 - dt, 3);
          this.displayLecture = Math.round(this._fromL + e * (this._toL - this._fromL));
          this.displaySource = Math.round(this._fromS + e * (this._toS - this._fromS));
          if (dt >= 1) {
            this.displayLecture = this._toL;
            this.displaySource = this._toS;
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
      this._fromL = this.displayLecture;
      this._fromS = this.displaySource;
      // 顶部始终展示「唯一讲座总数」（去重口径），不随跨源合并展开而放大；
      // 各单位计数之和（合计行）会因联合发布而大于此数，由统计页说明文字解释。
      this._toL = this.lectureCount;
      this._toS = this.sourceNoticeCount;
    },
    // 移动端 div 表格：表头与数据各自独立的横滑容器（#m-thead-scroll / #m-scrollx），
    // 双向同步它们的 scrollLeft，仅用于让表头年份列与数据年份列横向对齐。
    // 首列（学院名）的固定改用 CSS 原生 sticky left-0（写在 HTML 元素的 sticky left-0 工具类上），
    // 由浏览器合成层原生钉住、零帧延迟、无抖动；此处不再用 JS translate3d 推回。
    initMobileSticky() {
      const headSc = document.getElementById('m-thead-scroll');
      const dataSc = document.getElementById('m-scrollx');
      if (!headSc || !dataSc || headSc.dataset.bound) return;
      headSc.dataset.bound = '1';

      // 双向同步两个滚动容器的 scrollLeft（syncing 标志防止循环），仅用于年份列对齐
      let syncing = false;
      const sync = (src, dst) => {
        if (syncing) return;
        syncing = true;
        dst.scrollLeft = src.scrollLeft;
        requestAnimationFrame(() => { syncing = false; });
      };
      headSc.addEventListener('scroll', () => sync(headSc, dataSc), { passive: true });
      dataSc.addEventListener('scroll', () => sync(dataSc, headSc), { passive: true });
    },
    // 以预计算的 lectures/stats.json 为唯一权威数据源。
    // 统计页只渲染 matrix/yearTotals/campusMap 与访问/点赞数，不需要 lectures.json 全量明细。
    load() {
      this.loading = true;
      fetch('lectures/stats.json', { cache: 'default' })
        .then(r => { if (!r.ok) throw new Error('stats'); return r.json(); })
        .then(resp => {
          this.summary = resp || null;
          this.loading = false;
          this.finalizeCountAnimation();
          // 数据渲染完成后绑定移动端表头滚动同步（元素此时才存在）
          this.$nextTick(() => this.initMobileSticky());
        })
        .catch(e => {
          console.error('加载统计数据失败', e);
          this.loadError = '统计数据加载失败，请稍后刷新重试。';
          this.loading = false;
          this.finalizeCountAnimation();
        });
    },
  },

  mounted() {
    this.startCountAnimation();
    this.load();
    this.loadLectureStats();
  },

  // 切换校区 / 输入学院名时，若动画已结束则即时更新顶部数字（不再重播动画）。
  // 顶部展示唯一讲座总数 / 来源通知总数（去重口径），不随筛选改变单位计数口径。
  // 注意：lectureCount / sourceNoticeCount 来自预计算 summary，筛选不影响其值，故无需 watch 重赋。
  },
}).mount('#app');
