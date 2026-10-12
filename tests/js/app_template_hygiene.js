// 模板卫生门禁（2026-10-12）：Vue 模板里禁止调用以 _ / $ 开头的方法。
//
// 背景（2026-10-11 白屏事故）：B1 修复把模板里的 clamp 键改成调用 `_clampKey` /
// `_expKey` —— Vue 3 的 applyOptions 对 `_`/`$` 前缀方法 **continue 跳过绑定**
// （dev 构建只警告，prod 构建静默跳过），模板 `with(this)` 作用域链穿透 →
// ReferenceError × 25 → errorHandler 只打日志 → v-cloak 整页隐藏 → 白屏。
//
// 为什么必须静态门禁：现有 node 仿真门禁（app_bundle_smoke 等）用 vm 直接执行
// JS 分片，**跳过 Vue 模板编译环节**——方法作为普通对象属性挂载正常、全部绿，
// 而 bug 恰恰藏在模板编译产物与 Vue 运行时的交互里。这与 test_stage_var_leak
// （拆巨函数后 267 页 UnboundLocalError 而快照全绿）同类：动态测试有覆盖盲区
// 时，用确定性静态分析补位。
//
// 检查口径：
//   ① 剔除 <!-- --> 注释、<style> 整块、<script> 整块后，扫描 `(?<![\w$.])[_$]\w+\s*(`；
//      · 负向断言挡住 `obj._method(` 这类合法属性访问；
//      · 剔 <style> 是因为 index.html:26 的 CSS 注释里出现过 `_measureClamp`
//        （HTML 注释剔除覆盖不到），将来注释里写示例代码即误报。
//   ② `$` 前缀同属 isReservedPrefix 崩溃类，一并检查；但 Vue 内置方法
//      （$emit / $nextTick …）在模板里合法，命中内置表即豁免。
//   ③ 防「扫了空气」假绿：断言至少扫到 2 个 HTML 页且含 v- 指令。
//
// 扫描范围用 fs.readdirSync('site') 而非 git ls-files：本机沙箱下 node 子进程
// 一律 EPERM（MEMORY 有记录），零子进程版本本地/CI 都能跑。gitignored 的
// site/sources.html 若本地存在也会被扫——本地 Vue 页踩同一坑时本地即红，属特性非误报。
//
// 运行：node tests/js/app_template_hygiene.js
const fs = require('fs');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');

// Vue 3 模板里合法的内置方法（$ 前缀）。来源：runtime-core 公共实例 API。
// 新增 Vue 内置方法时同步此表；第三方库的 $ 方法（如 jQuery）不该出现在模板里。
const VUE_BUILTINS = new Set([
  '$emit', '$nextTick', '$watch', '$once', '$forceUpdate', '$set', '$delete',
  '$refs', '$el', '$parent', '$root', '$slots', '$attrs', '$props', '$data',
]);

const HAZARD = /(?<![\w$.])[_$]\w+\s*\(/g;

function stripNonTemplate(html) {
  // 顺序：先注释、再 style/script 整块（style 内 CSS 注释、script 内 JS 代码都不属于模板）
  return html
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<script[\s\S]*?<\/script>/gi, '');
}

const errs = [];
let pages = 0, tmplPages = 0;

const files = fs.readdirSync(SITE).filter(f => f.endsWith('.html')).sort();
for (const f of files) {
  const raw = fs.readFileSync(path.join(SITE, f), 'utf-8');
  if (/\bv-[\w-]+\s*=|\{\{[^}]+\}\}/.test(raw)) tmplPages++;
  pages++;
  const tpl = stripNonTemplate(raw);
  let m;
  HAZARD.lastIndex = 0;
  while ((m = HAZARD.exec(tpl)) !== null) {
    const ident = m[0].replace(/\s*\($/, '');
    if (ident.startsWith('$') && VUE_BUILTINS.has(ident)) continue;
    // 带上命中行（剔注释后的相对行仅供定位参考）
    const line = tpl.slice(0, m.index).split('\n').length;
    errs.push(`${f}:${line} 模板调用了 ${ident}( —— Vue 对 _/$ 前缀方法静默跳过绑定（prod 构建零警告），`
      + `渲染时 ReferenceError 会致整页白屏（2026-10-11 事故）。改用不带前缀的方法名。`
      + (ident.startsWith('$') ? `（若是 Vue 内置新方法，把 ${ident} 加进本文件 VUE_BUILTINS）` : ''));
  }
}

if (pages < 2 || tmplPages < 1) {
  errs.push(`扫描范围异常：site/ 下只有 ${pages} 个 HTML（Vue 模板页 ${tmplPages} 个）——`
    + '正则或目录结构失效？宁可红也不放过「扫了空气」的假绿');
}

if (errs.length) {
  console.log('[FAIL] 模板卫生门禁');
  errs.forEach(e => console.log(' -', e));
  process.exit(1);
}
console.log(`[PASS] 模板卫生：${pages} 个 HTML（Vue 模板页 ${tmplPages} 个）无 _/$ 前缀方法调用`);
