# -*- coding: utf-8 -*-
"""入口文件必须能通过语法检查。

踩过：改 run.py 时转义被吃掉，留下一个断掉的 f-string。
**全套测试照样全绿**，因为没有任何测试导入过 run.py ——
直到打包时 PyInstaller 分析源码才报出来。

那时候的代价：以为改完了、跑了测试、开始打包，几分钟后才发现连语法都不对。
"""
import ast
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SyntaxTests(unittest.TestCase):
    """每个 .py 都过一遍 ast.parse。"""

    def _files(self):
        skip = {"build", "dist", "__pycache__", ".git", "projects", ".venv"}
        for base, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in skip]
            for n in names:
                if n.endswith(".py"):
                    yield os.path.join(base, n)

    def test_every_python_file_parses(self):
        bad = []
        for p in self._files():
            # utf-8-sig：有些文件带 BOM，Python 自己的加载器会剥掉，
            # 用 utf-8 读会当成非法字符 —— 那是我们读错了，不是文件坏了
            src = io.open(p, encoding="utf-8-sig").read()
            try:
                ast.parse(src, filename=p)
            except SyntaxError as exc:
                bad.append(f"{os.path.relpath(p, ROOT)}:{exc.lineno} {exc.msg}")
        self.assertFalse(bad, "语法错：\n" + "\n".join(bad))

    def test_the_entrypoints_are_covered(self):
        """★ 确认这条测试真的看到了那几个没被 import 的文件。

        不确认的话，_files() 哪天把它们漏掉，这条测试会安静地什么都不查。
        """
        got = {os.path.relpath(p, ROOT).replace("\\", "/") for p in self._files()}
        for must in ("run.py", "打包exe.py"):
            self.assertIn(must, got, must)


class RunPyTests(unittest.TestCase):

    def _src(self):
        return io.open(os.path.join(ROOT, "run.py"), encoding="utf-8").read()

    def test_it_imports_cleanly(self):
        """能 import 就说明模块级代码没问题（main() 不会被执行）。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_run_probe", os.path.join(ROOT, "run.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(hasattr(mod, "main"))

    def test_the_port_default_is_zero(self):
        """0 = 用这一版体系自己的端口。写死 8770 的话两个包又撞一起。"""
        self.assertIn('"--port", type=int, default=0', self._src())


if __name__ == "__main__":
    unittest.main()
