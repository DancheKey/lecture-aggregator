/*
 * theme-loader.js — 讲座站点「备选项」风格开关（后台切换，页面无 UI）
 * ------------------------------------------------------------------
 * 用法：编辑同目录下的 _theme.json 的 mode 字段，刷新页面即生效，
 *       无需改动任何 HTML。
 *
 * 可选值（三选一，引号内小写）：
 *   "vi"      华南师范大学 VI 主题（深藏青顶栏 + 校徽红点缀）—— 当前默认
 *   "default" 原始浅色配色（用于回退）
 *   "bw"      黑白哀悼模式：整个页面置为灰度，重大事件（如领导人逝世）期间使用
 *
 * 实现：本脚本置于各页 <head> 内、#vi-theme / #bw-theme 链接之后、且不加 defer，
 *       故在首帧绘制前同步执行；按 mode 切换两个静态 <link> 的 disabled 属性：
 *         vi      → 启用 #vi-theme，禁用 #bw-theme（零闪烁）
 *         default → 两个都禁用（回退原配色）
 *         bw      → 两个都启用（VI 蓝 + 灰度叠加）
 *
 * 两个主题样式表都在 HTML 里静态引用（便于缓存破坏版本戳与孤儿检测），
 * 仅通过 disabled 属性控制是否生效，故本脚本不动态创建/移除节点。
 *
 * 安全：纯前端、只读 _theme.json、不依赖任何外部网络；
 *       _theme.json 缺失或解析失败时默认 VI（与当前已评审状态一致）。
 *       支持 JSONC 注释（// 行注释与 /* 块注释 *\/），方便后台直接加说明。
 */
(function () {
  'use strict';

  var VI_ID = 'vi-theme', BW_ID = 'bw-theme';

  // 去除 JSONC 注释（// 行注释与 /* 块注释），且不破坏字符串内的 // 与 /*
  function stripJsonc(text) {
    var out = '', i = 0, n = text.length;
    var inStr = false, strCh = '', esc = false;
    var inLine = false, inBlock = false;
    while (i < n) {
      var c = text[i], nx = text[i + 1];
      if (inLine) {
        if (c === '\n') { inLine = false; out += c; }
        i++; continue;
      }
      if (inBlock) {
        if (c === '*' && nx === '/') { inBlock = false; i += 2; continue; }
        i++; continue;
      }
      if (inStr) {
        out += c;
        if (esc) esc = false;
        else if (c === '\\') esc = true;
        else if (c === strCh) inStr = false;
        i++; continue;
      }
      if (c === '"') { inStr = true; strCh = '"'; out += c; i++; continue; }
      if (c === '/' && nx === '/') { inLine = true; i += 2; continue; }
      if (c === '/' && nx === '*') { inBlock = true; i += 2; continue; }
      out += c; i++;
    }
    return out;
  }

  function getMode() {
    try {
      var xhr = new XMLHttpRequest();
      xhr.open('GET', '_theme.json', false); // 同步：确保首帧前完成判定
      xhr.send();
      if (xhr.status === 200) {
        // 剥 UTF-8 BOM：PowerShell 的 Out-File / Set-Content 默认写 BOM，
        // 而 JSON.parse 遇 \uFEFF 直接抛错 → 被 catch 吞掉 → 静默回落 vi，
        // 表现为「改了配置刷新毫无反应」（2026-10-30 实测踩到）。
        var text = (xhr.responseText || '{}').replace(/^\uFEFF/, '');
        var cfg = JSON.parse(stripJsonc(text));
        if (cfg && typeof cfg.mode === 'string') return cfg.mode;
      }
    } catch (e) {
      // 不能静默：解析失败与「配置本来就没改」在页面上表现完全一样，
      // 不留痕就无从诊断。只 warn 不抛，维持「读不到就用 vi」的降级语义。
      if (window.console && console.warn) {
        console.warn('[theme-loader] _theme.json 读取或解析失败，回落 vi：',
          (e && e.message) || e);
      }
    }
    return 'vi';
  }

  // 通过 disabled 属性启用/禁用静态主题样式表（不增删 DOM 节点）
  function setEnabled(id, on) {
    var el = document.getElementById(id);
    if (!el) return;
    if (on) el.removeAttribute('disabled');
    else el.setAttribute('disabled', '');
  }

  var mode = getMode();
  document.documentElement.setAttribute('data-theme', mode);

  if (mode === 'bw') {
    setEnabled(VI_ID, true);
    setEnabled(BW_ID, true);
  } else if (mode === 'vi') {
    setEnabled(VI_ID, true);
    setEnabled(BW_ID, false);
  } else {
    // default：两个都禁用，回退原始浅色配色
    setEnabled(VI_ID, false);
    setEnabled(BW_ID, false);
  }
})();
