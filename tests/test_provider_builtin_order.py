# -*- coding: utf-8 -*-
"""新加的内置服务商必须写进 _BUILTIN_ORDER。

那张表看着只是「显示顺序」，但它还有第二个用途：exe 里扫不到
core/providers 目录时，按它逐个 import（见 _load_builtin）。
没写进去的那一家在 exe 里会**整家缺席**——页面上只是少一个选项，
不报错，源码方式跑一辈子也复现不出来。

实际漏过：ake 和 yishou 加进来之后一直没写进表，
一直没被发现是因为 exe 里目录恰好扫得到。哪天扫不到就一次性少四家。
"""
import os
import unittest

from core import providers as P

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROV_DIR = os.path.join(HERE, "core", "providers")


def _module_names() -> set:
    """core/providers/ 下真正是服务商的那些模块名。"""
    return {f[:-3] for f in os.listdir(PROV_DIR)
            if f.endswith(".py") and not f.startswith("_") and f != "base.py"}


class BuiltinOrderTests(unittest.TestCase):

    def test_every_builtin_module_is_listed(self):
        """★ 漏一个 = exe 里少一家，而且不报错。"""
        missing = sorted(_module_names() - set(P._BUILTIN_ORDER))
        self.assertFalse(missing,
                         f"这几家在 core/providers/ 下有文件，但没写进 "
                         f"_BUILTIN_ORDER：{missing}。exe 里扫不到目录时"
                         f"它们会整家缺席，页面上只是少几个选项，不报错。")

    def test_the_list_has_no_ghosts(self):
        """反过来也要对：表里写了但文件没了，exe 启动时会 import 失败。"""
        ghosts = sorted(set(P._BUILTIN_ORDER) - _module_names())
        self.assertFalse(ghosts, f"_BUILTIN_ORDER 里这几家没有对应文件：{ghosts}")

    def test_the_registry_agrees_with_the_files(self):
        """每个模块都真的注册上了 —— 有文件但没注册也是静默少一家。"""
        self.assertEqual(set(P.REGISTRY), _module_names())


if __name__ == "__main__":
    unittest.main()
