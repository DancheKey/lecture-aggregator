# .githooks — 本地 git 钩子

## 启用（每克隆一次配置一次）

```bash
git config core.hooksPath .githooks
```

CI 与 daily 自动提交不配置 hooksPath，不受影响。

## pre-commit

前端版本戳守卫：提交前比对页面 `?v=` 戳与索引中 JS/CSS 的真实内容 hash
（算法与 CI 的 `tests/test_frontend_schema.py::test_99` 完全同源）。

拦截场景：改了 `site/*.js` 忘跑 `scripts/generate_frontend_data.py` 刷戳就提交。
修复命令见 hook 输出。绕过（仅限明确知道后果时）：`git commit --no-verify`。
