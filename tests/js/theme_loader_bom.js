// theme-loader.js 行为测试（2026-10-30）。
//
// 背景：theme-loader 是后台切主题的唯一执行者，此前只有 Python 层「文件存在 +
// HTML 引用一致」的静态检查，**没有任何行为测试** —— BOM、JSONC、降级路径
// 全靠人肉实测，踩到了才发现。
//
// 本轮修的两处：
//   ① UTF-8 BOM：PowerShell 的 Out-File/Set-Content 默认写 BOM，而 JSON.parse
//      遇 \uFEFF 直接抛错 → 被 catch 吞掉 → 静默回落 vi。表现为「改了配置
//      刷新毫无反应」，且控制台一个字都没有。
//   ② 静默 catch：解析失败与「配置本来没改」在页面上表现完全一样，必须 warn。
//
// 运行：node tests/js/theme_loader_bom.js（纯 node，无浏览器无网络）
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');
const SRC = fs.readFileSync(path.join(SITE, 'theme-loader.js'), 'utf-8');

const errs = [];
const ok = (cond, msg) => { if (!cond) errs.push(msg); };

// 在隔离沙箱里跑一遍 theme-loader.js，返回可观测结果。
// links 记录两个主题 <link> 的 disabled 状态（loader 只改属性、不增删节点）。
function runLoader(responseText, status) {
  const attrs = {};
  const links = {};
  ['vi-theme', 'bw-theme'].forEach(id => {
    links[id] = {
      _disabled: true,
      removeAttribute(n) { if (n === 'disabled') this._disabled = false; },
      setAttribute(n) { if (n === 'disabled') this._disabled = true; },
    };
  });
  const warns = [];
  const sandbox = {
    XMLHttpRequest: function () {
      this.open = function () {};
      this.send = function () { this.status = status; this.responseText = responseText; };
    },
    document: {
      documentElement: { setAttribute(n, v) { attrs[n] = v; } },
      getElementById: id => links[id] || null,
    },
    console: {
      warn: (...a) => warns.push(a.map(String).join(' ')),
      log: () => {},
      error: () => {},
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: 'theme-loader.js' });
  return {
    theme: attrs['data-theme'],
    vi: !links['vi-theme']._disabled,
    bw: !links['bw-theme']._disabled,
    warns,
  };
}

// ---------- B1：BOM 前缀必须被剥掉（本轮主修） ----------
{
  const r = runLoader('\uFEFF{"mode":"bw"}', 200);
  ok(r.theme === 'bw', `B1 带 BOM 的 {"mode":"bw"} 应解析为 bw，实得 ${r.theme}（BOM 未剥 → 回落 vi）`);
  ok(r.vi === true && r.bw === true, 'B1 bw 模式应同时启用 vi 与 bw 两张表');
  ok(r.warns.length === 0, `B1 正常解析不应告警，实得 ${r.warns.length} 条：${r.warns[0] || ''}`);
}

// ---------- B2：无 BOM 的三种模式（回归基线） ----------
{
  const vi = runLoader('{"mode":"vi"}', 200);
  ok(vi.theme === 'vi' && vi.vi === true && vi.bw === false,
     `B2 vi 模式错：theme=${vi.theme} vi=${vi.vi} bw=${vi.bw}`);

  const def = runLoader('{"mode":"default"}', 200);
  ok(def.theme === 'default' && def.vi === false && def.bw === false,
     `B2 default 模式应两张表都禁用：theme=${def.theme} vi=${def.vi} bw=${def.bw}`);

  const bw = runLoader('{"mode":"bw"}', 200);
  ok(bw.theme === 'bw' && bw.vi === true && bw.bw === true,
     `B2 bw 模式错：theme=${bw.theme} vi=${bw.vi} bw=${bw.bw}`);
}

// ---------- B3：JSONC 注释（文件本身就是 JSONC，不能被剥坏） ----------
{
  const jsonc = '\uFEFF/* 后台配置\n * 可选 vi/default/bw */\n{\n  // 当前模式\n  "mode": "default"\n}\n';
  const r = runLoader(jsonc, 200);
  ok(r.theme === 'default', `B3 BOM + JSONC 注释应解析为 default，实得 ${r.theme}`);
  ok(r.warns.length === 0, 'B3 JSONC 合法时不应告警');
}

// ---------- B4：坏 JSON 必须 warn 且回落 vi（不再静默） ----------
{
  const r = runLoader('{"mode": "bw",,}', 200);
  ok(r.theme === 'vi', `B4 坏 JSON 应回落 vi，实得 ${r.theme}`);
  ok(r.warns.length === 1, `B4 坏 JSON 必须恰好告警 1 次（静默就无从诊断），实得 ${r.warns.length}`);
  ok(/theme-loader/.test(r.warns[0] || ''), `B4 告警应带 [theme-loader] 前缀：${r.warns[0] || ''}`);
}

// ---------- B5：非 200 / 空响应 → 静默回落 vi（缺文件是正常降级，不刷告警） ----------
{
  const miss = runLoader('', 404);
  ok(miss.theme === 'vi', `B5 404 应回落 vi，实得 ${miss.theme}`);
  ok(miss.warns.length === 0, 'B5 文件缺失属预期降级，不应告警');

  const empty = runLoader('', 200);
  ok(empty.theme === 'vi', `B5 空响应应回落 vi，实得 ${empty.theme}`);

  const noMode = runLoader('{"other":1}', 200);
  ok(noMode.theme === 'vi', `B5 缺 mode 字段应回落 vi，实得 ${noMode.theme}`);
}

// ---------- 汇总 ----------
if (errs.length) {
  console.log(`[FAIL] ${errs.length} 项`);
  errs.forEach(e => console.log(' - ' + e));
  process.exit(1);
}
console.log('[PASS] theme-loader BOM/JSONC/三模式/坏 JSON 告警/降级 全部通过');
