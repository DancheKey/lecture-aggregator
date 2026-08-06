/* 木铎金声 · 讲座统计页（Vue 3）
 * 从 stats.html 外部化，避免内联脚本在 file:// 或严格 CSP 下被拦截。
 *
 * 2026-07-22 改版：不再依赖预计算的 lectures/stats.json，改为直接加载 lectures.json
 * 并在客户端计算统计摘要——确保统计页与首页使用同一份数据、计数口径完全一致。
 * stats.json 仍作为 fallback（网络异常或旧版部署时兜底）。
 * 访问/点赞数仍通过 /api/lecture/stats 与本机 localStorage 动态合并后计算。
 *
 * 2026-07-30 改版：统计页重新以 lectures/stats.json 为唯一权威数据源。
 * 原因：
 *   1) stats.json 仅 ~300KB（含预计算 matrix/yearTotals/campusMap 与最小讲座索引），
 *      而 lectures.json 全量 ~5.7MB；统计页渲染表格/排序/筛选完全不需要明细字段。
 *   2) 数据一致性由 scripts/generate_frontend_data.py 保证：每次更新 data/lectures.json
 *      都会同步重写 stats.json，与 daily.yml / deploy.yml 配套。
 *   3) 使用 cache:'default' 允许浏览器/GitHub Pages CDN 缓存，避免每次冷加载都回源。
 *      统计数据日更（每天凌晨 3 点），600s 缓存窗口带来的 10 分钟延迟可接受。
 * 失败时直接提示错误，不再 fallback 到 5.7MB 全量 lectures.json。
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
    // 页面加载瞬间就从共享缓存读访问量，避免一开始显示 0；没有缓存则显示占位符 —
    const cachedVisits = (() => {
      try {
        const v = parseInt(localStorage.getItem('site_visits_total') || '0', 10);
        return v > 0 ? v : null;
      } catch (e) { return null; }
    })();
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
      // 站点总访问量（null 表示尚未从任何来源拿到）
      siteVisits: cachedVisits,
      // 是否已从本地后端或第三方拿到有效值
      hasBackend: cachedVisits != null,
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

    // 当前校区下、各年份的来源通知合计（用于顶部摘要"覆盖 N 条来源通知"）
    filteredSourceCount() {
      const cols = this.campusColleges;
      const lectures = (this.summary && this.summary.lectures) || [];
      let total = 0;
      lectures.forEach(l => {
        // 2026-08-05 体检修正（中等-13）：讲座任一归属单位（cs）在当前校区即计入，
        // 与 matrix 的单位归属口径保持一致（此前只看主学院 l.c）。
        const cs = (l.cs && l.cs.length) ? l.cs : [l.c];
        if (cols && !cs.some(c => cols.has(c))) return;
        total += (l.s || 0);
      });
      return total;
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
        if (cols && !cols.has(row.college)) return false;
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
        // 2026-08-05 体检修正（中等-13）：与 matrix 同口径——按讲座归属的全部单位（cs）
        // 展开、逐单位过校区筛选。此前只按主学院 cols.has(l.c) 过滤，跨源合并讲座
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

    // 当前筛选（校区 + 学院名）下的讲座总数
    filteredLectureCount() {
      return this.rows.reduce((a, r) => a + r.total, 0);
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
    // 修复（2026-08-05 体检 中等-12）：与 app.js 对齐为「按 URL 字段级合并」。
    // 此前是整条覆盖（{...localStats, ...j.stats}）——服务端旧记录可能缺 wants
    // 字段，整条覆盖会把本机离线积累的 wants 抹掉（首页图标高亮但计数为 0）。
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
        })
        .catch(e => {
          console.error('加载统计数据失败', e);
          this.loadError = '统计数据加载失败，请稍后刷新重试。';
          this.loading = false;
          this.finalizeCountAnimation();
        });
    },
    // 加载站点总访问量：优先级 本地后端 > 不蒜子
    // 避免公网静态版因不蒜子偶发不可用而长期显示 0；首页/统计页同 origin 共享缓存，保证两页一致
    loadSiteVisits() {
      // 0) 优先读共享缓存（任一页面成功获取后写入），避免第三方接口偶发失败显示 0
      const cached = parseInt(localStorage.getItem('site_visits_total') || '0', 10);
      if (cached > 0) { this.siteVisits = cached; this.hasBackend = true; }
      // 1) 本地后端（server.py）直接返回真实总数
      fetch('/api/visits', { cache: 'no-store' })
        .then(r => r.json())
        .then(j => { if (j && j.total != null) { this.siteVisits = j.total; this.hasBackend = true; this._persistVisits(j.total); } else throw new Error('no-total'); })
        .catch(() => {
          // 2) 公网静态版无本地后端时，最终回退到不蒜子（第三方计数服务，best-effort，可能偶发不可用）。
          //    注：原 countapi.xyz 分支已下线（服务停运），其回退永远失败，故删除，避免无意义的外部请求。
          this._loadBusuanzi();
        });
    },
    _persistVisits(v) {
      try { localStorage.setItem('site_visits_total', String(v)); } catch (e) { /* ignore */ }
    },
    _loadBusuanzi() {
      if (document.getElementById('busuanzi_pure_mini_js')) return;
      const s = document.createElement('script');
      s.id = 'busuanzi_pure_mini_js';
      s.async = true;
      s.src = 'https://busuanzi.ibruce.info/busuanzi/2.3/busuanzi.pure.mini.js';
      document.head.appendChild(s);
      // 不蒜子异步更新后，把真实值写回共享缓存并更新 siteVisits，供另一页读取
      let tries = 0;
      const iv = setInterval(() => {
        const el = document.getElementById('busuanzi_value_site_pv');
        const v = el ? parseInt((el.textContent || '0').replace(/\D/g, ''), 10) || 0 : 0;
        if (v > 0) { this.siteVisits = v; this.hasBackend = true; this._persistVisits(v); clearInterval(iv); }
        else if (++tries > 20) clearInterval(iv);
      }, 500);
    },
  },

  mounted() {
    this.startCountAnimation();
    this.load();
    this.loadLectureStats();
    this.loadSiteVisits();
  },

  // 切换校区 / 输入学院名时，若动画已结束则即时更新顶部数字（不再重播动画）。
  // 顶部展示唯一讲座总数 / 来源通知总数（去重口径），不随筛选改变单位计数口径。
  watch: {
    campusFilter() {
      if (this._finalized) {
        this.displayLecture = this.lectureCount;
        this.displaySource = this.sourceNoticeCount;
      }
    },
    collegeFilter() {
      if (this._finalized) {
        this.displayLecture = this.lectureCount;
        this.displaySource = this.sourceNoticeCount;
      }
    },
  },
}).mount('#app');
