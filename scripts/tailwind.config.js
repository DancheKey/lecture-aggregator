/*
 * Tailwind 静态构建配置（用于生成 site/vendor/tailwind.css）
 *
 * 背景：原来页面用 https://cdn.tailwindcss.com 运行时编译器（约 115KB gzip + 浏览器端 JIT），
 * 在国内访问慢，导致「校训图片先出来、讲座列表迟迟不出」。改为预编译静态 CSS（约 16KB / gzip 约 5KB）。
 *
 * 何时需要重新构建：仅当修改了 site/*.html 或 site/*.js 里用到的 Tailwind 类名时。
 * 每日数据更新（daily.yml）不改类名，无需重建。
 *
 * 重建命令（在装有 tailwindcss v3 的环境执行，content 用相对路径）：
 *   npx tailwindcss@3 -c scripts/tailwind.config.js -i scripts/tailwind-input.css -o site/vendor/tailwind.css --minify
 */
module.exports = {
  content: [
    './site/*.html',
    './site/*.js',
  ],
  theme: { extend: {} },
  corePlugins: { preflight: true },
};
