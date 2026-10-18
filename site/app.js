/* 木铎金声 · 华南师范大学学术讲座聚合前端（Vue 3，免构建，配合 Tailwind CSS）
 * 组件由下列分片组装（按顺序 defer 加载，均挂到全局词法环境）：
 *   app.core.js     常量与 shared 方法容器
 *   app.state.js    data()
 *   app.computed.js computed
 *   app.display.js  展示 / 格式化 / clamp 实测
 *   app.social.js   点赞 / 想听 / 统计 / 筛选 / 分页
 *   app.data.js     分片加载与长文本按需
 *   app.admin.js    数字动画 / 抓取 / 信息源报告
 * 本文件只做组装与生命周期，不再内联业务逻辑。
 */

const app = createApp({
  // 初始状态：默认值（APP_STATE）+ URL 查询串回填（?campus=大学城&online=1…）。
  // 分享/刷新/直链场景在挂载前即处于正确筛选态；写回由 watch 负责（见文件尾 watch 注释）。
  data() {
    const s = APP_STATE();
    Object.assign(s, normalizeUrlFilters(parseUrlFilters(), s.campusList));
    return s;
  },
  computed: APP_COMPUTED,
  methods: APP_METHODS,

  mounted() {
    this.syncCountDisplay();
    this.loadLikes();
    this.loadWants();
    this.loadLectureStats();
    this.loadFarFuture();   // 远期折叠条展开态（localStorage 恢复，2026-10-13 方案 B）
    // 启动秒级心跳：驱动「即将开始」徽章的倒计时（statusInfo 内读取 this.tick 触发响应式）
    this._tickTimer = setInterval(() => { this.tick = Date.now(); }, 1000);
    // 先秒开 latest.json，后台再补全量（本地与公网统一走静态分片路径）。
    // 原注释「本地后端仍优先 /api/lectures」自静态化重构后已不成立——loadLectures
    // 只是 _loadStaticLatest 的包装（app.data.js），2026-10-17 复核修正。
    // IPv6 回环时浏览器返回的 hostname 是「[::1]」（带方括号），一并覆盖
    // 「更新于」用的运行时间与数据版本无关，两条加载路径都要拉取：
    // 公网静态托管走 _loadStaticLatest()，若只在 loadLectures() 里拉就会永远显示数据日。
    this._loadLastRun();
    const isLocal = ['localhost', '127.0.0.1', '::1', '[::1]'].includes(location.hostname);
    if (isLocal) {
      this.loadLectures();
    } else {
      this._loadStaticLatest();
    }
    // 隐藏初始 loading 占位，避免 Vue 挂载前显示原始模板
    // （v-cloak 已替代，无需手动隐藏 page-loading）
    // 监听滚动，下滑超过阈值时显示「回到顶部」按钮
    this.onScroll();
    window.addEventListener('scroll', this.onScroll);
    // 浏览器前进/后退：按当前 URL 回放筛选快照（2026-10-23 第三批）
    window.addEventListener('popstate', this._onPopState);
    // 点击顶部菜单外部时自动关闭（修复移动端因 mouseenter+click 竞态需点两次）
    this._closeMenuHandler = (e) => {
      const container = this.$refs.menuContainer;
      if (this.showMenu && container && !container.contains(e.target)) {
        this.showMenu = false;
      }
    };
    document.addEventListener('click', this._closeMenuHandler);
    // clamp 溢出实测：首帧后测一次，窗口尺寸变化（行数随之变化）后再测
    this._measureSoon();
    this._resizeHandler = () => this._measureSoon();
    window.addEventListener('resize', this._resizeHandler);
  },

  // 渲染完成后重测 clamp（翻页/筛选/展开/收起都会改变 DOM 中的实际行数）
  updated() {
    this._measureSoon();
  },

  beforeUnmount() {
    window.removeEventListener('scroll', this.onScroll);
    window.removeEventListener('popstate', this._onPopState);
    if (this._resizeHandler) window.removeEventListener('resize', this._resizeHandler);
    if (this._clampTimer) clearTimeout(this._clampTimer);
    if (this._urlSyncTimer) clearTimeout(this._urlSyncTimer);
    if (this._closeMenuHandler) {
      document.removeEventListener('click', this._closeMenuHandler);
    }
  },

  watch: {
    // 任一筛选条件变化，回到第一页；内容维度同步进 URL（push，可后退回放）。
    // 搜索词用较长防抖合并连续键入；其余维度 120ms 合并同 tick 的批量变化（如清除此）。
    // showLikedOnly 不进 URL（点赞是本机数据，分享无意义），故不挂 URL 同步。
    query() {
      this.currentPage = 1;
      // 长文本（简介/摘要）在主分片之外（detail 桶），搜索命中长文本时需要它们。
      // 首屏已自动后台预取（app.data._loadStaticLatest），这里再幂等触发一次兜底；
      // 即便尚未就绪，搜索也不再等待——先出已到结果，桶到齐后自动补全。
      this._ensureLongTextAll();
      this._scheduleUrlSync(true, 450);
    },
    searchField() {
      this.currentPage = 1;
      this._ensureLongTextAll();
      this._scheduleUrlSync(true, 120);
    },
    // 翻页也是「用户在深挖」的信号：提前把长文本拉齐，翻到哪页简介与摘要都已在手。
    // 页码写 URL 用 replace（不产生历史条目）；若同窗口内已有筛选变化标记 push，保留 push。
    currentPage() {
      this._ensureLongTextAll();
      this._scheduleUrlSync(false, 120);
    },
    campus() { this.currentPage = 1; this._scheduleUrlSync(true, 120); },
    college() { this.currentPage = 1; this._scheduleUrlSync(true, 120); },
    year() { this.currentPage = 1; this._scheduleUrlSync(true, 120); },
    // 讲者视图与线上筛选：入口方法（pickSpeaker/toggleOnline）已自设页码，此处只同步 URL
    speaker() { this._scheduleUrlSync(true, 120); },
    onlineOnly() { this._scheduleUrlSync(true, 120); },
    upcomingOnly() { this._scheduleUrlSync(true, 120); },
    showLikedOnly() { this.currentPage = 1; },
    // 页码越界钳位（2026-10-14 评审 P1-5）：URL 深链接可能带入超过总页数的
    // page（数据增减后旧链接失效），静态上界（MAX_URL_PAGE）拦不住这种「合法
    // 但越界」值。数据到达/筛选变化使 totalPages 变小时，把 currentPage 钳回
    // 末页——否则 pagedUnits 切片越界，渲染出空白列表。
    // 钳位会顺势触发上方 currentPage watcher 以 replace 语义更新 URL，不留脏链接。
    totalPages(n) {
      if (this.currentPage > n) this.currentPage = n;
    },
    // 数据阶段从 partial 变 full 时，如果当前没有筛选，保持当前页；否则回到第一页
    dataStage(newVal, oldVal) {
      // 计数键过渡期迁移（2026-10-18 B1）：数据每次到达（partial 50 条 → full
      // 全量）都触发一次；函数幂等（只补缺失新键），partial 先迁一部分、
      // full 到齐补齐剩余。挂此处而非 mounted：迁移需要 this.all 判定多场页，
      // 而 mounted 时数据尚未加载（loadLikes/loadWants 早于任何 fetch）。
      this.migrateCountKeys();
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
