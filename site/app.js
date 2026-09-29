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
  data: APP_STATE,
  computed: APP_COMPUTED,
  methods: APP_METHODS,

  mounted() {
    this.startCountAnimation();
    this.loadLikes();
    this.loadWants();
    this.loadLectureStats();
    // 启动秒级心跳：驱动「即将开始」徽章的倒计时（statusInfo 内读取 this.tick 触发响应式）
    this._tickTimer = setInterval(() => { this.tick = Date.now(); }, 1000);
    // 公网静态托管不要先等 /api/lectures 超时；先秒开 latest.json，后台再补全量。
    // 本地后端（127.0.0.1/localhost）仍优先 /api/lectures，保证数据最新。
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
    if (this._resizeHandler) window.removeEventListener('resize', this._resizeHandler);
    if (this._clampTimer) clearTimeout(this._clampTimer);
    if (this._closeMenuHandler) {
      document.removeEventListener('click', this._closeMenuHandler);
    }
  },

  watch: {
    // 任一筛选条件变化，回到第一页
    query() {
      this.currentPage = 1;
      // 简介/摘要已从主分片剥离（按需加载），搜索需要它们才能命中长文本。
      // 输入停顿后后台预取全量，_longText 更新会让 computed 自动重算。
      if (this.query) this._prefetchLongText();
    },
    searchField() {
      this.currentPage = 1;
      if (this.query) this._prefetchLongText();
    },
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
