# -*- coding: utf-8 -*-
"""同名建项目要拦 —— 这是最字面意义的「项目之间资产混用」。

用户问（2026-08-27）：「现在是否存在项目之间的资产混用的情况」。
查出来这一处：「单个项目 · 粘贴正文」不检查目录已存在，于是

  · 直接打开旧项目那个目录
  · `save_meta` 覆盖它的 `project.json` —— **连 system 一起覆盖**，
    体系一改，产物结构、环节表、任务结构的判断全乱
  · 剧本被新剧本盖掉
  · 而旧项目的 `02_固定资产/`、`04_故事板/`、`05_分段视频/`、`tasks.json`
    全都还在 → 新项目直接继承了另一部剧的全部资产和任务

页面上看着就是「建好了，而且居然已经有一堆资产」。
「批量建剧」那条一直拦着（同名项目目录已存在），只有这一条漏了。
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from core.store import Project


def _body(**kw):
    b = {"name": "剧甲", "project_code": "PROJ-001", "episode": "EP01",
         "system": "v34", "title": "剧甲", "script": "正文"}
    b.update(kw)
    return b


class CollisionTests(unittest.TestCase):
    """**打桩 load_config**，绝不碰真实的 projects 目录。

    这条注释是买来的：第一版测试直接改 `load_config()` 返回的那个 dict，
    以为改的是配置 —— 它每次返回新字典，于是两个测试项目被建进了用户
    真实的 projects/ 里（事后手动清掉的）。
    """

    def setUp(self):
        self.base = tempfile.mkdtemp()
        from server import app as A
        self.A = A
        real = A.load_config()
        self.cfg = dict(real, projects_dir=self.base)
        self.patch = mock.patch.object(A, "load_config",
                                       lambda: dict(self.cfg))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_the_first_one_works(self):
        r = self.A.api_post("/api/project/create", _body())
        self.assertTrue(r.get("root"))
        self.assertTrue(os.path.isfile(Project(r["root"]).meta_path))

    def test_the_same_name_is_refused(self):
        self.A.api_post("/api/project/create", _body())
        with self.assertRaises(ValueError) as cm:
            self.A.api_post("/api/project/create", _body(script="另一部剧"))
        self.assertIn("已经是一个项目", str(cm.exception))

    def test_the_old_project_is_untouched_after_the_refusal(self):
        """★ 拦住之后旧项目要一个字节都没变 —— 拦一半比不拦更糟。"""
        r = self.A.api_post("/api/project/create", _body())
        pj = Project(r["root"])
        before_meta = json.dumps(pj.meta(), sort_keys=True)
        before_script = open(pj.p("01_剧本与分段", "原始剧本.txt"),
                             encoding="utf-8").read()
        try:
            self.A.api_post("/api/project/create",
                            _body(script="另一部剧", system="v61"))
        except ValueError:
            pass
        self.assertEqual(json.dumps(pj.meta(), sort_keys=True), before_meta)
        self.assertEqual(open(pj.p("01_剧本与分段", "原始剧本.txt"),
                              encoding="utf-8").read(), before_script)

    def test_a_hand_made_empty_folder_is_allowed(self):
        """★ 判据是 project.json，不是「目录存在」—— 用户可能先建了个空目录。"""
        os.makedirs(os.path.join(self.base, "空壳"), exist_ok=True)
        r = self.A.api_post("/api/project/create", _body(name="空壳"))
        self.assertTrue(r.get("root"))

    def test_it_uses_the_real_meta_path(self):
        """★ `project.json` 在 `00_项目说明/` 里，不在项目根。
        自己拼一个根目录下的路径，这道拦截一次都不会生效
        （第一版就是这么写的，实测「没拦住」）。"""
        import inspect
        src = inspect.getsource(self.A.api_post)
        i = src.index('/api/project/create"')
        self.assertIn("Project(root).meta_path", src[i:i + 1600])

    def test_batch_still_refuses_too(self):
        """两条建剧路径要一致 —— 不一致就是「换个入口就能混」。"""
        import inspect
        src = inspect.getsource(self.A.api_post)
        i = src.index('/api/project/create_batch"')
        self.assertIn("同名项目目录已存在", src[i:i + 1800])
