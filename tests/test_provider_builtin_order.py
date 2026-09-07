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
        """每个内置模块都真的注册上了 —— 有文件但没注册也是静默少一家。

        **只比内置那部分。** 原来这里拿整个 REGISTRY 去比 core/providers/ 下的
        文件，而 REGISTRY 里还有外挂（数据目录 providers/ 里的 .py）——
        于是**装任何一个外挂插件，这条就变红**。而外挂是这个项目明确支持、
        README 里写着「丢一个 .py 进去就多一家，不用改程序」的功能。

        用 SOURCES 分开两者（`status()` 判断 builtin 也是用它），
        这样「内置文件没注册上」照样拦得住，装插件不再误伤。
        实遇 2026-09-07：装云会画插件之后全量测试红了一条。
        """
        builtin = {pid for pid in P.REGISTRY if P.SOURCES.get(pid) == "内置"}
        self.assertEqual(builtin, _module_names())


if __name__ == "__main__":
    unittest.main()
