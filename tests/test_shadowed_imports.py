# -*- coding: utf-8 -*-
"""函数里的局部变量，不许和模块级导入的名字撞。

实跑撞到（点「生产」直接 HTTP 500）：

    cannot access free variable 'probe' where it is not associated
    with a value in enclosing scope

`server/app.py` 的 `api_post` 是一个 741-1519 行的巨型函数，其中一个分支写了：

    probe = os.path.join(d, ".写入自检")      # 目录可写性自检，用完就删

而模块顶上有 `from core import ... probe ...`。Python 看到函数体里有赋值，
就把 `probe` 当成**整个函数的局部变量** —— 于是几百行外另一个分支里的
`probe.have_output(...)` 读到一个还没赋值的名字，整条「生产」路径崩掉。

这类 bug 特别难查：
  · 语法没问题、导入没问题、被遮的那行代码一个字没改
  · 只有走到「另一个分支」时才炸，而两个分支在同一个函数里隔着几百行
  · 报错说的是 `probe`，而写坏它的那行代码跟 probe 这个模块毫无关系

所以用一条机器检查兜住 —— 靠肉眼是看不出来的。
"""
import ast
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _files() -> list:
    out = []
    for d in ("core", "server", os.path.join("core", "providers")):
        p = os.path.join(ROOT, d)
        if not os.path.isdir(p):
            continue
        out += [os.path.join(p, f) for f in sorted(os.listdir(p))
                if f.endswith(".py")]
    for f in ("run.py", "打包exe.py"):
        if os.path.isfile(os.path.join(ROOT, f)):
            out.append(os.path.join(ROOT, f))
    return out


def _module_names(tree: ast.Module) -> set:
    """模块级导入进来的名字。"""
    names = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            names |= {(a.asname or a.name.split(".")[0]) for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            names |= {(a.asname or a.name) for a in n.names}
    return names


def _assigned(fn) -> set:
    """这个函数体里被赋值的名字（不含嵌套函数自己的局部）。"""
    out = set()

    def walk(node, top=False):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)) and not top:
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                out.add(child.id)
            elif isinstance(child, (ast.Global, ast.Nonlocal)):
                # 用 difference_update，不用 `out -= ...` —— 后者是赋值，
                # 会把 out 变成 walk 的局部变量，然后在这行之前读它就炸。
                # 写这个检查的时候我自己先犯了一次这个错，很能说明问题。
                out.difference_update(child.names)
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.Lambda, ast.ClassDef)):
                walk(child)
    walk(fn, top=True)
    return out


def offenders() -> list:
    bad = []
    for path in _files():
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read())
        except SyntaxError:                                  # 另有用例管这个
            continue
        mods = _module_names(tree)
        if not mods:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = {a.arg for a in
                    node.args.args + node.args.kwonlyargs + node.args.posonlyargs}
            hit = (_assigned(node) & mods) - args
            for name in sorted(hit):
                bad.append(f"{os.path.relpath(path, ROOT)}:{node.lineno} "
                           f"{node.name}() 里的局部变量 {name!r} "
                           f"遮住了模块级导入的 {name!r}")
    return bad


class ShadowTests(unittest.TestCase):

    def test_no_local_shadows_a_module_level_import(self):
        """★ 这条就是那次 HTTP 500。

        参数同名不算（那是有意的注入），只查函数体里的赋值。
        """
        bad = offenders()
        self.assertEqual(bad, [], "局部变量遮住了导入的模块／名字：\n" + "\n".join(bad))

    def test_the_check_actually_catches_the_real_case(self):
        """★ 检查本身要能抓到那次的写法 —— 抓不到的检查等于没有。"""
        src = ("from core import probe\n"
               "def api_post(path, body):\n"
               "    if path == 'a':\n"
               "        probe = 'tmp'\n"
               "        open(probe, 'w')\n"
               "    if path == 'b':\n"
               "        return probe.have_output('x')\n")
        tree = ast.parse(src)
        fn = tree.body[1]
        self.assertIn("probe", _assigned(fn) & _module_names(tree))

    def test_a_parameter_with_the_same_name_is_fine(self):
        """别误报：参数同名是有意的（依赖注入、测试替身）。"""
        src = ("from core import probe\n"
               "def f(probe):\n"
               "    return probe.have_output('x')\n")
        tree = ast.parse(src)
        self.assertIn("probe", _module_names(tree))
        fn = tree.body[1]
        args = {a.arg for a in fn.args.args}
        self.assertEqual((_assigned(fn) & _module_names(tree)) - args, set())

    def test_a_nested_functions_own_local_is_not_blamed_on_the_outer_one(self):
        """内层函数自己的局部变量不算外层的 —— 那是合法的。"""
        src = ("from core import probe\n"
               "def outer():\n"
               "    def inner():\n"
               "        probe = 1\n"
               "        return probe\n"
               "    return inner\n")
        tree = ast.parse(src)
        self.assertEqual(_assigned(tree.body[1]) & _module_names(tree), set())


if __name__ == "__main__":
    unittest.main()
