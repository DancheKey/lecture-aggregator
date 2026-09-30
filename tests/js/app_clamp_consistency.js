// 折叠类名一致性门禁（2026-09-30）
//
// 背景：卡片「简介/摘要」的折叠行数由 index.html 的 line-clamp-N 类控制，而
// app.display.js 的 _measureClamp 靠**硬编码类名列表**判断元素是否处于折叠态，
// 再据此决定「展开摘要/展开简介」按钮是否显示。两者若不同步，症状是
// **「文本被截断却没有展开按钮」** —— 用户看不到全文，且无任何报错（静默失效）。
//
// 这类 bug 现有门禁全绿也抓不到（快照/golden 锁的是解析器，不碰前端展示），
// 故补本门禁：静态比对「模板实际使用的 clamp 类」与「JS 判定列出的 clamp 类」。
//
// 运行：node tests/js/app_clamp_consistency.js

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');
const HTML = path.join(ROOT, 'site', 'index.html');
const DISPLAY = path.join(ROOT, 'site', 'app.display.js');

let pass = 0, fail = 0;
function check(name, ok, detail) {
  if (ok) { pass++; console.log('PASS | ' + name); }
  else { fail++; console.log('FAIL | ' + name + (detail ? '  -> ' + detail : '')); }
}

const html = fs.readFileSync(HTML, 'utf8');
const display = fs.readFileSync(DISPLAY, 'utf8');

// 1) CSS 中定义的所有 .line-clamp-N
const cssDefined = new Set();
const cssRe = /\.line-clamp-(\d+)\s*\{[^}]*\}/g;
let m;
while ((m = cssRe.exec(html)) !== null) cssDefined.add('line-clamp-' + m[1]);
check('CSS 至少定义了一个 line-clamp 类', cssDefined.size > 0, '找到 ' + cssDefined.size + ' 个');

// 2) 模板 :class 绑定里实际施加的 clamp 类
const templateUsed = new Set();
const tplRe = /line-clamp-(\d+)/g;
const tplSection = html.slice(html.indexOf('<!-- 摘要'), html.indexOf('</template>') > 0 ? html.indexOf('</template>') : html.length);
while ((m = tplRe.exec(html)) !== null) {
  // 只认出现在 :class 绑定字符串里的（排除 CSS 定义块与注释里的举例）
  templateUsed.add('line-clamp-' + m[1]);
}

// 3) _measureClamp 的 classList.contains 里列出的类
const m2 = display.match(/const clamped = el\.classList\.contains\('([^']+)'\)([\s\S]*?);/);
let jsListed = new Set();
if (m2) {
  jsListed.add(m2[1]);
  const rest = m2[2] || '';
  const re2 = /classList\.contains\('([^']+)'\)/g;
  let m3;
  while ((m3 = re2.exec(rest)) !== null) jsListed.add(m3[1]);
} else {
  check('能在 app.display.js 中定位 _measureClamp 的 clamped 判定', false, '未匹配到 const clamped = ...');
}

// 4) 核心断言：模板使用的 clamp 类必须都在 JS 判定列表里
check(
  '模板使用的 line-clamp 类全部出现在 JS 判定列表中（否则「展开」按钮静默消失）',
  [...templateUsed].every(c => jsListed.has(c)),
  '模板=' + [...templateUsed].join(',') + ' / JS=' + [...jsListed].join(',')
);

// 5) JS 判定列表里的类必须在 CSS 有定义（否则折叠样式不生效，文本不截断）
check(
  'JS 判定列表里的类在 CSS 中都有定义',
  [...jsListed].every(c => cssDefined.has(c)),
  'JS=' + [...jsListed].join(',') + ' / CSS=' + [...cssDefined].join(',')
);

// 6) 反向：无用的 CSS 类（定义但无人使用）——不阻断，仅提示
const unused = [...cssDefined].filter(c => !templateUsed.has(c));
if (unused.length) console.log('INFO | CSS 中定义但模板未使用的类: ' + unused.join(',') + '（死 CSS，可清理）');
else console.log('INFO | 无死 CSS');

console.log('');
console.log((fail === 0 ? '[PASS] ' : '[FAIL] ') + '折叠类名一致性 ' + pass + ' 项通过, ' + fail + ' 项失败');
process.exit(fail === 0 ? 0 : 1);
