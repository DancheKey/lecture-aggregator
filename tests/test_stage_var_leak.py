#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段函数变量漏传静态门禁（零外部依赖、确定性）。

## 为什么需要它

`_parse_detail_impl` 拆成 9 个模块级阶段函数后，同一个名字在 Python 里若**函数级
既有 store 又被引用**，就会被判为局部变量。此时若初始值没作为参数传进来，执行到
该分支必然 `UnboundLocalError`。

2026-09-30 全库重解析实测：267/3527 页（7.6%）崩在
`UnboundLocalError: cannot access local variable 't'` —— 阶段 5b（`_extract_speaker`）
的 OCR 海报覆盖路径读了阶段 4 的局部变量 `t`，而拆分时漏传。
**20 例行为快照与 171 项 golden 全绿都没抓到**，因为基线里没有走这条路径的页面。

## 判据（精确，零误报）

对阶段函数内的每个名字（**排除 comprehension / lambda 子作用域**）：

    函数级首次引用是 Load
    且 函数级存在 Store（不论先后 → Python 已将其局部化）
    且 不是形参
    ⇒ 必然 UnboundLocalError

只报这一类。以下都不报（实测 12 例全部正确排除）：
- 只在 comprehension 内出现的名字（`any(x.endswith(t) for t in ...)`）—— 子作用域；
- 嵌套函数名 / 内联 import / `except ... as e` —— 不是 `ast.Name` 的 Store；
- 模块级函数调用 —— 函数级无 Store。

⚠ 反面教训：早期两版判据都用「名字是否出现在 comprehension 里」做整体排除，
结果 `t` 恰好在 comprehension 里出现过就被掩盖（连续两次漏报）。
**comprehension 的排除只能用于「函数级零引用」的名字**。
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARSERS = os.path.join(ROOT, 'scraper', 'parsers.py')

# 拆分后的阶段函数；新增阶段函数时请同步登记
STAGE_FUNCS = (
    '_prep_doc_text',
    '_collect_assets',
    '_resolve_time_init_result',
    '_extract_topic_location',
    '_extract_speaker',
    '_extract_abstract_bio',
    '_finalize_record',
)

try:
    import builtins
    BUILTINS = set(dir(builtins))
except Exception:  # pragma: no cover
    BUILTINS = set()


def _sub_scope_names(fn):
    """comprehension / lambda 内部的变量名（子作用域局部，不计入函数级）。"""
    comp_nodes = [n for n in ast.walk(fn)
                  if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp,
                                    ast.GeneratorExp, ast.Lambda))]
    ids = {id(n) for n in comp_nodes}
    names = set()
    in_comp = set()

    def walk(node, inside):
        for ch in ast.iter_child_nodes(node):
            if id(ch) in ids:
                walk(ch, True)
            else:
                if isinstance(ch, ast.Name) and inside:
                    in_comp.add(id(ch))
                walk(ch, inside)

    walk(fn, False)
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and id(n) in in_comp:
            names.add(n.id)
    return names, in_comp


def _fn_level_refs(fn):
    """{name: [(lineno, ctx), ...]}，只含函数级（非 comprehension/lambda 内）引用。"""
    _, in_comp = _sub_scope_names(fn)
    refs = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and id(n) not in in_comp:
            ctx = 'store' if isinstance(n.ctx, ast.Store) else 'load'
            refs.setdefault(n.id, []).append((n.lineno, ctx))
    return refs


def _nested_ranges(fn):
    """嵌套函数/lambda 的行号区间 —— 落在其内的引用属于子作用域（形参等），需豁免。

    `src` 就是这种情形：它是嵌套函数 `_is_chrome_img(src)` 的形参，
    在外层函数看来既有 load 又有 store，但完全合法。
    """
    out = []
    for n in ast.walk(fn):
        if n is fn:
            continue
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            out.append((n.lineno, getattr(n, 'end_lineno', n.lineno)))
    return out


def find_leaks(src_path):
    """返回 [(func, lineno, name)] —— 每个都是必然 UnboundLocalError 的漏传。"""
    src = open(src_path, encoding='utf-8').read()
    tree = ast.parse(src)
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    leaks = []
    for name in STAGE_FUNCS:
        fn = fns.get(name)
        if fn is None:
            leaks.append((name, 0, '<函数缺失>'))
            continue
        params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        if fn.args.vararg:
            params.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            params.add(fn.args.kwarg.arg)
        refs = _fn_level_refs(fn)
        nested = _nested_ranges(fn)
        for var, occ in refs.items():
            if var in params or var in BUILTINS:
                continue
            first_ln = min(ln for ln, _ in occ)
            # 首次引用落在嵌套函数/lambda 内 → 属子作用域，豁免
            if any(a <= first_ln <= b for a, b in nested):
                continue
            first_ctx = [c for ln, c in occ if ln == first_ln][0]
            has_store = any(c == 'store' for _, c in occ)
            if first_ctx == 'load' and has_store:
                leaks.append((name, first_ln, var))
    return sorted(leaks, key=lambda x: (x[0], x[1]))


class StageVarLeakTest(unittest.TestCase):
    """阶段函数不得存在「读未传入的局部变量」。"""

    def test_no_unbound_local(self):
        leaks = find_leaks(PARSERS)
        self.assertEqual(
            [], leaks,
            '阶段函数存在必然 UnboundLocalError 的漏传（拆分时变量未传入）：\n'
            + '\n'.join(f'  {f} L{ln}: {v}' for f, ln, v in leaks)
            + '\n修法：把该变量加进函数形参，并在编排处由上一阶段的返回值传入。')

    def test_stage_funcs_exist(self):
        """守卫：阶段函数若被重命名/删除，上面那条会静默失效。"""
        src = open(PARSERS, encoding='utf-8').read()
        tree = ast.parse(src)
        top = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        missing = [f for f in STAGE_FUNCS if f not in top]
        self.assertEqual([], missing, f'阶段函数缺失（请同步更新本文件的 STAGE_FUNCS）：{missing}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
