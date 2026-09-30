/* 木铎金声 · 华南师范大学学术讲座聚合前端（Vue 3，免构建，配合 Tailwind CDN）
 * 功能：讲座总数显示、校区/学院/年份/关键词 多维筛选、可点击 Tag 直达筛选、本地点赞去重。
 */
// 防点击劫持（2026-09-27 二轮审计 S-2）：公网 GitHub Pages 无法下发
// X-Frame-Options / CSP frame-ancestors 响应头（meta 里的 frame-ancestors
// 会被浏览器忽略），用 JS 兜底禁止被第三方页面嵌套；若对方沙箱化导航则
// 隐藏本页内容。
(function () {
  try {
    if (window.top !== window.self) window.top.location.replace(window.self.location);
  } catch (e) {
    document.documentElement.style.display = 'none';
  }
})();
const { createApp } = Vue;

const LIKED_KEY = 'lecture_liked_urls_v1';
const WANTED_KEY = 'lecture_wanted_urls_v1';
const STAT_KEY = 'lecture_stats_v1';      // 本地缓存 + 后端合并后的讲座访问/点赞/想听统计
const COUNT_CAP = 300;                    // 点赞/想听超过此值显示 "300+"，防止虚高数字

// 顶部总数滚动动画的时长参数（2026-09-30，用于消除加载过程中的数字「锯齿跳动」）。
// 背景：分片由串行改 4 路并发后到达时序乱序化（单片实测 1.8~18s），而动画时长原为固定
// 500ms → 跑完即定格，数字空等 1~12 秒才被下个分片唤醒，观感是「平滑上滚 → 长时间不动
// → 猛跳一段」。改为「时长自适应 + 被打断时续接进度」——两处都必要，见 app.admin.js 文件头。
// 参数取值由门禁 tests/js/app_count_animation.js 实测锁定（弱网乱序 + 串行 + 本地快三场景）。
const COUNT_DUR_MIN = 500;                // 下限：太快仍有明显「窜」感
const COUNT_DUR_SOFT_CAP = 4000;         // idle 阻尼软上限：超过部分只按 1/COUNT_IDLE_DIV 计入
const COUNT_IDLE_DIV = 4;                // idle 超出软上限后的衰减倍数（12s 空档 → 约 4s）
const COUNT_DUR_MAX = 10000;              // 硬上限：兜底，防止极端空档把单次滚动拖到十几秒
const COUNT_MS_PER_ITEM = 4;              // 每条记录的滚动耗时（每片约 500 条 → 2000ms）

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
