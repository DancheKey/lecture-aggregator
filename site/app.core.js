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
const STAT_KEY = 'lecture_stats_v1';      // 本地缓存 + 后端合并后的讲座访问/点赞/想听统计
const FAR_FOLD_STATE_KEY = 'lecture_far_future_open_v1';  // 远期折叠条展开态（2026-10-13 方案 B）
const COUNT_CAP = 300;                    // 点赞/想听超过此值显示 "300+"，防止虚高数字

// 注：顶部总数动画的时长参数（COUNT_DUR_MIN / COUNT_DUR_SOFT_CAP / COUNT_IDLE_DIV /
// COUNT_DUR_MAX / COUNT_MS_PER_ITEM）已于 2026-09-30 随「阶梯跳变」改造一并删除 ——
// 数字改为每个分片到达时直接落在已加载真实条数上（50 → 550 → 1050 …），不再插值，
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
