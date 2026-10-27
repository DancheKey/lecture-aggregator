/* 木铎金声 · 华南师范大学学术讲座聚合前端（Vue 3，免构建，配合 Tailwind CDN）
 * 功能：讲座总数显示、校区/学院/年份/关键词 多维筛选、可点击 Tag 直达筛选、本地点赞去重。
 */
// 防点击劫持（2026-10-14 一修 P1-1，替换 2026-09-27 S-2 有缺陷实现）：
// 公网 GitHub Pages 无法下发 X-Frame-Options / CSP frame-ancestors 响应头
// （meta 里的 frame-ancestors 会被浏览器忽略），防御分两层：
//   ① index.html/stats.html 的 <head> 内联 `html{display:none}`——CSS 先于
//      首帧渲染生效，被嵌入时页面在第一帧就不可见、不可点；
//   ② 本脚本（defer，DOM 解析后执行）确认是顶层访问才还原 display。
// 原实现的缺陷：try/catch 依赖 location 赋值抛 SecurityError 来触发隐藏，
// 但 sandbox iframe（无 allow-top-navigation）下该赋值是**静默失败**，catch
// 永不执行，页面照常渲染在攻击者页面对点击劫持敞开。「预隐藏 + 顶层放行」
// 不再依赖异常路径：顶层访问还原，被嵌入则保持隐藏（跳出尝试失败也无妨）。
// ⚠ 本 IIFE 是同源逻辑的**三份拷贝**之一（2026-10-14 二修补 trend 页时补齐
// 互认）：本文件 / stats.js / scripts/gen_visits_trend.py 的 JS 常量顶部，
// 逻辑改动须三处同步。trend 页的 .pre-hidden 规则落其外链 CSS（该页 CSP
// 禁 unsafe-inline，不能内联 head），与两页的内联 <style> 等效。
(function () {
  if (window.top === window.self) {
    // 顶层访问：移除预隐藏 class 还原。三层守卫容忍无 DOM 环境（node vm
    // 仿真的 documentElement 桩只有 style、没有 classList；有的测试干脆没有
    // document）——原实现在顶层路径不碰 DOM 故从未需要这些桩；生产浏览器里
    // documentElement/classList 必有，行为不受影响。
    var root = typeof document !== 'undefined' && document.documentElement;
    if (root && root.classList) root.classList.remove('pre-hidden');
    return;
  }
  // 被嵌入：class 保留（head 内联 CSS 的 .pre-hidden 生效），仍尝试顶层跳转
  try { window.top.location.replace(window.self.location); } catch (e) {}
})();
const { createApp } = Vue;

const LIKED_KEY = 'lecture_liked_urls_v1';
const WANTED_KEY = 'lecture_wanted_urls_v1';
const CAL_ADDED_KEY = 'lecture_cal_added_urls_v1'; // 已加进日历的 url 集合（本机高亮态，2026-10-20）
const STAT_KEY = 'lecture_stats_v1';      // 本地缓存 + 后端合并后的讲座访问/点赞/想听统计
const FAR_FOLD_STATE_KEY = 'lecture_far_future_open_v1';  // 远期折叠条展开态（2026-10-13 方案 B）
const COUNT_CAP = 300;                    // 点赞/想听超过此值显示 "300+"，防止虚高数字

// 注：顶部总数动画的时长参数（COUNT_DUR_MIN / COUNT_DUR_SOFT_CAP / COUNT_IDLE_DIV /
// COUNT_DUR_MAX / COUNT_MS_PER_ITEM）已于 2026-09-30 随「阶梯跳变」改造一并删除 ——
// 数字改为每个分片到达时直接落在已加载真实条数上（50 → 150 → 250 …），不再插值，
// 因此不需要时长估算。详见 app.admin.js 文件头（含一处负数 bug 的根因分析）。

// 配置项：若已部署「工作流触发代理」（持有 PAT 的 Cloudflare Worker / Vercel Function 等），
// 把其地址填到此处，公网「抓取新数据」按钮即可立即触发 GitHub Actions；
// 留空则按钮走友好降级——网站每天自动更新两次，无需手动操作。
// ⚠️ 切勿把 PAT 直接写进前端：静态页无保密环境，会被任何人查看源码拿到。
const WORKFLOW_DISPATCH_URL = '';

// 配置项：信息源报告的接收通道。
// 公网静态站（GitHub Pages）没有后端，接不住表单 POST；因此页面直接把内容
// POST 到 Web3Forms —— 一个面向静态站的表单转发服务（免费额度 250 条/月），
// 由它把内容转发到维护者邮箱。即：静态站不「接收」，只「发起」。
// access_key 按官方说明不是密钥、可公开：它只是「投递到哪个邮箱」的别名，
// 读不到任何数据；防滥用靠蜜罐 + 服务端限流。
// 注意：跨域域名必须在 index.html 的 CSP connect-src 中放行，否则浏览器会当场掐断。
const REPORT_ENDPOINT = 'https://api.web3forms.com/submit';
const REPORT_ACCESS_KEY = '1e9b5b37-ad58-4943-b509-8a4ab7974dd3';
// 反连点节流：同一浏览器 60 秒内只允许提交一次
const SRC_REPORT_TS = 'srcReportLastAt';
const SRC_REPORT_GAP_MS = 60000;

// 各职责分片的方法都挂到这个对象上，由 app.js 组装进组件（免构建，靠 defer 保证加载顺序）。
const APP_METHODS = {};

/* ---------- 筛选状态 ↔ URL 同步（2026-10-23 第三批） ----------
 * 进 URL 的内容维度（可分享/可回放）：campus / college / year / speaker /
 *   q（= query 搜索词）/ field（= searchField 搜索维度）/ online（= onlineOnly）/
 *   upcoming（= upcomingOnly，2026-10-16 加）/ page。
 * 不进 URL 的本机偏好：showLikedOnly（点赞是本机数据，分享给他人无意义）、
 *   farFutureOpen 与各类展开态（同为 localStorage 本机偏好，已有独立持久化）。
 * 写入约定：
 *   - 筛选维度变化 → pushState（浏览器前进/后退可在筛选快照间回放）；
 *   - 页码变化 → replaceState（翻页频繁，不污染历史栈）；
 *   - 写前与当前 location.search 去重；popstate 回放期间抑制写回（防循环）。
 * 单一事实源：校区白名单 CAMPUS_LIST 同时供 APP_STATE.campusList 与 URL 校验使用。
 */
const CAMPUS_LIST = ['石牌', '大学城', '佛山', '汕尾', '校级'];
const URL_SEARCH_FIELDS = ['college', 'location', 'topic', 'abstract'];  // searchField 合法枚举
// URL page 参数静态上界（2026-10-14 评审 P1-5）：全库 3831 条 / 每页 25 满打满算
// 154 页，1000 留足余量，只防「?page=99999」天文数字与数据增减后失效的旧深链接。
// 真正的越界钳位由 watch.totalPages 按实际数据完成（见 app.js）。
const MAX_URL_PAGE = 1000;

/* ---------- 搜索归一化（2026-10-27） ----------
 * 问题：搜索原先是 `hay.includes(q)` 裸比较，对**空格、全半角、繁简**三种差异敏感：
 *   「AI 讲座」搜不到「AI讲座」、「ＡＩ」搜不到「AI」、「講座」搜不到「讲座」。
 *   （实测库里全简体，繁体输入几乎零召回；全角多来自用户中文输入法。）
 * 做法：只做**字形层面**的等价归一，不改语义——
 *   ① 全角 ASCII（U+FF01-FF5E）→ 半角，全角空格 U+3000 → 半角空格；
 *   ② 繁体 → 简体（查表，只收高置信度常用字，见 SEARCH_T2S_*）；
 *   ③ 统一小写。
 *   **不去空白**：空白当作分词边界，多词按 AND 匹配（「张三 讲座」= 张三 AND 讲座），
 *   比把整串去空格后当子串更符合直觉，也不会漏掉跨字段的组合查询。
 * 性能：filtered() 里走「快路径 → 慢路径」——先按原样 includes，未命中才归一化重试。
 *   简体无空格输入（绝大多数）完全走老路径，零额外开销。
 * 映射表由 tmp/_gen_t2s.py 生成并校验（每对两字、均为 CJK、繁简不同形、繁体键唯一）。
 */
const SEARCH_T2S_SIMP =
  '学术讲题师范会议实验数据统计论报专业课硕毕礼试绩笔记义纲书馆档资库览导项经费评审员组织构机国际华侨台湾历艺体养济贸产农渔矿电医药卫环筑规划设区块链节点线网络云软备讯园门楼栋号厅场车飞广营销财务税贷银债价购传闻访说读写杂编辑纸张页词汇译优质标准则条践应发发创识别处图语习练测证结归纳绎间后现于还从进过开关这们为与周钟头举办参注册录闭启动暂继续载帐码输选择确认详寻筛幂响风雾边远达运连递迁适兴乐欢庆贺愿梦长团队众亲爱兰势县变吓吗呜咏哑唤啰啸喷嘱马龙凤伟军强荣贵宾贤辉锋钢铁铜钱钧铭锦镇东陈刘杨赵黄吴郑冯韩邓叶罗谭萧顾龚庞庄欧阳韦龟个么几样话请谢对错问只里里面干干系系表台才秋丑斗谷尽荐卷累蒙蒙蒙弥弥蔑宁千曲舍尸升松坛涂洼咸向须旋佣涌游余御郁吁扎占折征致制种朱冢时两见听让给带买卖岁万亿鸟鱼鸡丽丰亚仅伤侧儿党冲决况减刚剧劳勋协单厂压双围圆圣坚担拟拥挂挥损换职联声胜脑苏获蓝虑虽补装观视讨训诗谁调谈负责货赛赞转轮释邮阅陆随难静齐灯频献辞奖摄竞紧缩';
const SEARCH_T2S_TRAD =
  '學術講題師範會議實驗數據統計論報專業課碩畢禮試績筆記義綱書館檔資庫覽導項經費評審員組織構機國際華僑臺灣歷藝體養濟貿產農漁礦電醫藥衛環築規劃設區塊鏈節點線網絡雲軟備訊園門樓棟號廳場車飛廣營銷財務稅貸銀債價購傳聞訪說讀寫雜編輯紙張頁詞匯譯優質標準則條踐應發髮創識別處圖語習練測證結歸納繹間後現於還從進過開關這們為與週鐘頭舉辦參註冊錄閉啟動暫繼續載帳碼輸選擇確認詳尋篩冪響風霧邊遠達運連遞遷適興樂歡慶賀願夢長團隊眾親愛蘭勢縣變嚇嗎嗚詠啞喚囉嘯噴囑馬龍鳳偉軍強榮貴賓賢輝鋒鋼鐵銅錢鈞銘錦鎮東陳劉楊趙黃吳鄭馮韓鄧葉羅譚蕭顧龔龐莊歐陽韋龜個麼幾樣話請謝對錯問隻裡裏麵乾幹係繫錶檯纔鞦醜鬥穀盡薦捲纍矇濛懞瀰彌衊寧韆麯捨屍昇鬆壇塗窪鹹嚮須鏇傭湧遊餘禦鬱籲紮佔摺徵緻製種硃塚時兩見聽讓給帶買賣歲萬億鳥魚雞麗豐亞僅傷側兒黨衝決況減剛劇勞勳協單廠壓雙圍圓聖堅擔擬擁掛揮損換職聯聲勝腦蘇獲藍慮雖補裝觀視討訓詩誰調談負責貨賽讚轉輪釋郵閱陸隨難靜齊燈頻獻辭獎攝競緊縮';

// 繁体字 → 简体字（惰性构建一次；键是繁体，故「发←發/髮」这类一简对多繁不冲突）
let _SEARCH_T2S_MAP = null;
function _searchT2SMap() {
  if (!_SEARCH_T2S_MAP) {
    _SEARCH_T2S_MAP = Object.create(null);
    for (let i = 0; i < SEARCH_T2S_TRAD.length; i++) {
      _SEARCH_T2S_MAP[SEARCH_T2S_TRAD.charAt(i)] = SEARCH_T2S_SIMP.charAt(i);
    }
  }
  return _SEARCH_T2S_MAP;
}

// 字形归一（保留空白）：全角→半角、繁→简、小写。对已归一化的串再调用结果不变。
function normalizeSearchText(s) {
  if (!s) return '';
  s = String(s);
  const map = _searchT2SMap();
  let out = '';
  for (let i = 0; i < s.length; i++) {
    let c = s.charAt(i);
    const code = s.charCodeAt(i);
    if (code >= 0xFF01 && code <= 0xFF5E) c = String.fromCharCode(code - 0xFEE0);
    else if (code === 0x3000) c = ' ';
    const mapped = map[c];
    if (mapped) c = mapped;
    out += c.toLowerCase();
  }
  return out;
}

// 查询串 → 归一化后的词元数组（空白切分、丢空词元）。空查询返回 []。
function searchTokens(q) {
  const n = normalizeSearchText(q);
  if (!n) return [];
  const parts = n.split(/\s+/);
  const out = [];
  for (let i = 0; i < parts.length; i++) if (parts[i]) out.push(parts[i]);
  return out;
}

// 解析 location.search → 原始覆盖对象（只含 URL 里实际出现且格式合法的键）。
// format 校验在此做（year 四位数、field 枚举）；取值白名单校验（campus）在
// normalizeUrlFilters 里做（其依赖的 campusList 属组件 state）。
function parseUrlFilters() {
  const out = {};
  try {
    const p = new URLSearchParams(location.search);
    const get = (k) => (p.get(k) || '').trim();
    const campus = get('campus');
    if (campus) out.campus = campus;
    const college = get('college');
    if (college) out.college = college;
    const year = get('year');
    if (/^\d{4}$/.test(year)) out.year = year;
    const speaker = get('speaker');
    if (speaker) out.speaker = speaker;
    const q = get('q');
    if (q) out.query = q;
    const field = get('field');
    if (URL_SEARCH_FIELDS.includes(field)) out.searchField = field;
    if (p.get('online') === '1') out.onlineOnly = true;
    if (p.get('upcoming') === '1') out.upcomingOnly = true;
    const page = parseInt(p.get('page'), 10);
    if (page >= 2 && page <= MAX_URL_PAGE) out.page = page;   // page=1 与缺省等价，不写
  } catch (e) { /* file:// 等受限环境：按无筛选处理 */ }
  return out;
}

// 把解析结果归一化为完整 state 字段集（缺失/非法值一律回落空值），键名与
// APP_STATE 字段一一对应（含 currentPage），data() 初始化与 popstate 回放
// 共用，保证两条路径对同一 URL 给出同一状态。
function normalizeUrlFilters(f, campusList) {
  const wl = campusList || CAMPUS_LIST;
  return {
    campus: (f.campus && wl.includes(f.campus)) ? f.campus : '',
    college: f.college || '',
    year: f.year || '',
    speaker: f.speaker || '',
    query: f.query || '',
    searchField: f.searchField || '',
    onlineOnly: !!f.onlineOnly,
    upcomingOnly: !!f.upcomingOnly,
    currentPage: (f.page >= 2 && f.page <= MAX_URL_PAGE) ? f.page : 1,
  };
}

// 组件 state → 查询串（只含非默认值；键序固定，便于与当前 URL 去重比较）
function serializeUrlFilters(vm) {
  const p = new URLSearchParams();
  if (vm.campus) p.set('campus', vm.campus);
  if (vm.college) p.set('college', vm.college);
  if (vm.year) p.set('year', vm.year);
  if (vm.speaker) p.set('speaker', vm.speaker);
  if (vm.query && vm.query.trim()) p.set('q', vm.query.trim());
  if (vm.searchField) p.set('field', vm.searchField);
  if (vm.onlineOnly) p.set('online', '1');
  if (vm.upcomingOnly) p.set('upcoming', '1');
  if (vm.currentPage >= 2) p.set('page', String(vm.currentPage));
  return p.toString();
}

Object.assign(APP_METHODS, {
  // 从当前 URL 回放筛选（popstate）。同步赋值后用 $nextTick 恢复写回：
  // Vue3 watch 回调在 nextTick 之前 flush，期间 _urlSyncSuppress 保持 true，
  // 各维度 watch 触发的 _scheduleUrlSync 全部被抑制——不会把回放又写回 history。
  _applyUrlFiltersFromUrl() {
    const n = normalizeUrlFilters(parseUrlFilters(), this.campusList);
    // 清掉挂起的写回（2026-10-14 竞态修复）：popstate 可能打断一个未触发的
    // 防抖（如 450ms 搜索合并窗口内按「后退」）。不清则定时器照常触发
    // _flushUrlSync，把「后退前」的旧筛选又 push 回历史栈——正好抵消用户的
    // 后退操作，且 state 与 URL 从此分叉。
    clearTimeout(this._urlSyncTimer);
    this._urlSyncPendingPush = false;
    this._urlSyncSuppress = true;
    this.campus = n.campus;
    this.college = n.college;
    this.year = n.year;
    this.speaker = n.speaker;
    this.query = n.query;
    this.searchField = n.searchField;
    this.onlineOnly = n.onlineOnly;
    this.upcomingOnly = n.upcomingOnly;
    this.currentPage = n.currentPage;   // 最后赋：前面各维度的既有 watch 会先重置页码
    this.$nextTick(() => { this._urlSyncSuppress = false; });
  },

  // 防抖调度 URL 写入。push 语义一旦在本窗口内出现即保留（筛选变化通常伴随
  // 页码重置，两次调度合并后必须仍是 push，否则筛选快照进不了历史栈）。
  _scheduleUrlSync(push, delay) {
    if (this._urlSyncSuppress) return;
    this._urlSyncPendingPush = this._urlSyncPendingPush || !!push;
    clearTimeout(this._urlSyncTimer);
    this._urlSyncTimer = setTimeout(() => {
      const usePush = this._urlSyncPendingPush;
      this._urlSyncPendingPush = false;
      this._flushUrlSync(usePush);
    }, delay || 120);
  },

  _flushUrlSync(usePush) {
    if (this._urlSyncSuppress) return;   // 回放窗口内的残留定时器一律不写（双保险）
    const qs = serializeUrlFilters(this);
    const target = qs ? '?' + qs : location.pathname;   // 清空筛选时收掉查询串
    if ((qs ? '?' + qs : '') === (location.search || '')) return;  // 无变化不写
    try {
      if (usePush) history.pushState(null, '', target);
      else history.replaceState(null, '', target);
    } catch (e) { /* file:// 等不支持 history 写入的环境：静默降级 */ }
  },

  _onPopState() { this._applyUrlFiltersFromUrl(); },
});
