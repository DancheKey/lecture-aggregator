// 组件初始状态（原 app.js 的 data()）。
const APP_STATE = function () {
    return {
      all: [],
      mtime: 0,
      updatedAt: '',   // 数据最后变化时间（ISO 字符串），来自后端 mtime 或静态文件 updatedAt
      lastRunAt: '',   // 最近一次抓取运行时间（来自 CI 写入的 lectures/last_run.json）
      // 校区固定顺序（与 sources.yaml / 后端一致）
      campusList: ['', '石牌', '大学城', '佛山', '汕尾', '校级'],
      campus: '',
      college: '',
      year: '',
      speaker: '',        // 讲者筛选（归一化键；点卡片主讲人名字进入该讲者视图）
      query: '',
      searchField: '',  // 搜索维度：''=全部 | college=单位 | location=地点 | topic=题目 | abstract=摘要
      showLikedOnly: false,  // 仅显示已点赞讲座
      scraping: false,
      showMenu: false,    // 顶部栏更多操作下拉菜单
      showReport: false,  // 信息源报告弹窗显隐
      // 信息源报告表单：用于反馈「某单位换了讲座栏目网址 / 栏目已失效」
      // hp 为反垃圾蜜罐（视觉隐藏，真人看不见，机器人会去填）
      reportForm: { unit: '', campus: '', url: '', oldUrl: '', note: '', contact: '', hp: '' },
      reportSending: false,  // 报告提交中（防重复点击 + 按钮态）
      likedUrls: new Set(), // 当前浏览器已点赞的 url 集合（计数见 lectureStats）
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
      expandedAbstract: {}, // 卡片摘要的展开状态：sourceUrl -> bool（默认 3 行截断）
      expandedBio: {},      // 主讲简介的展开状态：sourceUrl -> bool（长文默认折叠）
      expandedForums: {},   // 论坛卡的展开状态：sourceUrl -> bool（缺省=筛选子集自动展开，2026-09-26 方案B）
      // ---- clamp 溢出实测（2026-09-29）----
      // 「展开简介/摘要」按钮是否出现，改由 DOM 实测决定（内容真的被 clamp 裁掉才给按钮），
      // 取代原「按字符数估行数」的阈值：同一段文字在 375px 与 1440px 下行数差 3~4 倍，
      // 任何字符阈值在某一端必然误判（宽屏下多余按钮 / 窄屏下按钮缺失）。
      // 键：'abs|sourceUrl' / 'bio|sourceUrl'；每次变化重算（仅展开中的条目沿用旧值），
      // 避免字体加载替换造成的瞬时溢出被永久记住。
      _clampOverflow: {},
      _clampSig: '',           // 上次测量的状态签名（页码/筛选/展开态/视口宽），未变则跳过
      _clampTimer: null,
      _clampMeasured: false,   // 首次 DOM 测量是否已完成（兜底阈值在此之前生效）
      tick: Date.now(),     // 秒级心跳（响应式依赖）：statusInfo 内的「即将开始」倒计时读它触发每秒重渲染
      // 顶部数字展示值 = 已加载的真实条数。2026-09-30 起为「每分片跳变」，无插值动画：
      // 每次分片到达由 bumpCount() 直接赋值（50 → 550 → 1050 …），故这两个字段
      // 恒等于 totalCount / sourceNoticeCount，不存在中间过渡值。
      // 初值 0：mounted 时数据尚未加载，模板文案是「已加载 0+ 场讲座」。
      displayTotal: 0,
      displaySource: 0,
      loadedChunks: 0,   // 分片加载断点续传：已成功加载的分片数
      // ---- 长文本（简介/摘要）加载：2026-09-28 剥离，2026-09-30 改「交互即预取」----
      // 主分片只带结构化字段：bio+abstract 占 68% 体积，而列表默认只显示 2~3 行 clamp。
      // 策略：展开某条 -> 拉该条所在的 1 桶；用户一有交互（滚动 / 聚焦搜索框 / 翻页）就
      // 后台预取全量 16 桶，使卡片的简介与摘要随列表「同步」出现，而不是先渲染空壳再逐条补上。
      _longText: {},      // key -> {speakerBio, abstract}
      _ltFull: {},        // key -> true：已拿到全文（区别于 latest.json 的截断预览）
      _ltPending: {},     // key -> true：该桶正在加载
      _ltLoaded: {},      // 桶号 -> true
      _ltManifest: null,  // detail 清单
      _ltAllLoaded: false,
      _ltSearching: false,  // 后台预取进行中（交互触发或搜索触发）
      _ltUnavailable: false, // 清单拿不到等整体失败：搜索不再等待，退化为「就绪多少显示多少」
      _ltIncomplete: false, // 首轮有桶失败：允许后续交互补拉（不影响搜索放行）
      _ltRetry: 0           // 补拉轮次，超过上限即放弃，避免反复空跑
    };
};
