# -*- coding: utf-8 -*-
"""材料只能导进「选它的时候打开的那个项目」。

用户问（2026-08-27）：「会不会问题在导入生产材料那边，项目1和项目2
他不知道我导入的是哪个」—— **问到点上了，它确实不知道。**

`MAT` 是页面里的模块级变量，切项目时不清。在项目 A 里点「选文件」，
切到项目 B 再点「导入」，发出去的是 `{project_root: B, text: A 的材料}`：
**A 的材料静默导进 B**，重写 B 的 tasks.json 和全部提示词。
走 `rel` 那条更阴 —— B 里正好也有同名文件时读的是 B 的，什么都不报。

而我前一天刚加的页头项目切换器让这件事更容易撞上（切项目从「回项目页点打开」
变成「点一下页头」）。

两层防：页面按项目戳拦（第一层，也清掉陈旧选择），服务端按材料 key 的
项目码前缀报 warn（第二层，直接调接口或以后新增入口忘了盖戳时兜住）。
"""
import json
import tempfile
import unittest

from core.store import Project, read_text


class PageGuardTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = read_text("web/index.html")

    def test_every_selection_stamps_the_project(self):
        """★ 三处赋值都要盖戳 —— 漏一处就是那一条路径静默串项目。"""
        got = self.html.count("proj: PROJ")
        self.assertGreaterEqual(got, 3, f"只有 {got} 处盖了戳")

    def test_the_body_builder_refuses_a_mismatch(self):
        i = self.html.index("async function matBody")
        blk = self.html[i:i + 900]
        self.assertIn("MAT.proj !== PROJ", blk)
        self.assertIn("重新选一次", blk)

    def test_switching_project_clears_the_selection(self):
        """★ 第一层：不清的话卡片上还显示「选了 xxx.jsonl」，
        人以为那是这个项目的。"""
        i = self.html.index("async function openProject")
        blk = self.html[i:i + 700]
        self.assertIn("MAT = null", blk)
        self.assertIn("#matFile", blk)

    def test_the_path_split_handles_backslashes(self):
        """★ Windows 路径是反斜杠。只切正斜杠的话，报错里会印出整条路径
        而不是项目名 —— 那句话就白写了（这个转义本会话被吃掉过四次）。"""
        i = self.html.index("MAT.proj.split(")
        self.assertIn(chr(92) + chr(92) + "/", self.html[i:i + 40])


class ServerGuardTests(unittest.TestCase):
    """服务端这一层：**按「项目现有任务的编号前缀」比，不按项目码比。**

    原来拿 `project_code` 比 —— 实测没用：用户所有项目的编号都是默认的
    `PROJ-001`，而材料 key 的前缀是它自己起的（`PRJ_YHJ__…`）。两个命名体系
    压根不在一个宇宙里，一比就是每次都误报。而误报比漏报贵：人会学会忽略
    这条，然后真导错那次也被忽略。
    """

    def _proj(self, prefix=None):
        """建一个项目；给了 prefix 就先用那个前缀的材料填一遍任务。"""
        root = tempfile.mkdtemp()
        pj = Project(root)
        pj.init_dirs()
        pj.save_meta({"project_name": "x", "system": "v34",
                      "project_code": "PROJ-001"})
        if prefix:
            from server import app as A
            A.api_post("/api/material/import",
                       {"project_root": root, "text": self._mat(prefix)})
        return root

    def _mat(self, prefix):
        rows = [{"kind": "image", "key": f"{prefix}__CHAR_{i:03d}_R01",
                 "filename": f"{prefix}_c{i}.png", "prompt": "x"}
                for i in range(1, 8)]
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)

    def _codes(self, root, prefix):
        from server import app as A
        r = A.api_post("/api/material/preview",
                       {"project_root": root, "text": self._mat(prefix)})
        return [i["code"] for i in r["issues"]]

    def test_another_shows_material_is_flagged(self):
        """★ 项目里已经是 PRJ_YHJ 的活，导进来 PRJ_ABC 的材料 —— 真换剧了。"""
        self.assertIn("MAYBE_WRONG_PROJECT",
                      self._codes(self._proj("PRJ_YHJ"), "PRJ_ABC"))

    def test_reimporting_the_same_material_is_quiet(self):
        """★ 重新导入同一份是**最常见的正常操作**（改了装配逻辑要重建任务）。
        这里报一句就等于每次都报。"""
        self.assertNotIn("MAYBE_WRONG_PROJECT",
                         self._codes(self._proj("PRJ_YHJ"), "PRJ_YHJ"))

    def test_an_empty_project_gets_no_opinion(self):
        """★ 项目还是空的时候没有可比的东西 —— 表态就是瞎猜。"""
        self.assertNotIn("MAYBE_WRONG_PROJECT",
                         self._codes(self._proj(), "PRJ_YHJ"))

    def test_the_project_code_is_not_used(self):
        """★ 所有项目都是默认编号 PROJ-001 —— 拿它比就是每次都误报。"""
        # 断言**访问方式**，别断言这个词 —— 注释里解释「为什么不用它」
        # 本身就含这个词，按词断言会撞上自己的注释（今天第三次了）。
        import inspect
        from server import app as A
        src = inspect.getsource(A._wrong_project)
        self.assertNotIn('get("project_code")', src)
        self.assertNotIn("get('project_code')", src)

    def test_dash_and_underscore_count_as_the_same(self):
        self.assertNotIn("MAYBE_WRONG_PROJECT",
                         self._codes(self._proj("PRJ_YHJ"), "PRJ-YHJ"))

    def test_it_only_warns_never_blocks(self):
        """★ 同一个项目换一版材料、顺手改了前缀，是正常操作 —— 硬拦是死路。"""
        from server import app as A
        r = A.api_post("/api/material/preview",
                       {"project_root": self._proj("PRJ_YHJ"),
                        "text": self._mat("PRJ_ABC")})
        got = [i for i in r["issues"] if i["code"] == "MAYBE_WRONG_PROJECT"]
        self.assertEqual([i["level"] for i in got], ["warn"])

    def test_keys_without_a_prefix_are_not_guessed(self):
        """★ key 里没有 `__` 分段时没法比 —— 猜一个前缀出来只会误报。"""
        from server import app as A
        rows = [{"kind": "image", "key": f"C{i:03d}", "filename": f"c{i}.png",
                 "prompt": "x"} for i in range(1, 8)]
        r = A.api_post("/api/material/preview",
                       {"project_root": self._proj("PRJ_YHJ"),
                        "text": "\n".join(json.dumps(x, ensure_ascii=False)
                                          for x in rows)})
        self.assertNotIn("MAYBE_WRONG_PROJECT",
                         [i["code"] for i in r["issues"]])


class SwitchThenImportTests(unittest.TestCase):
    """★ 用户要的那句：**左上角切项目之后，导入要进切换后的那个项目。**

    这里验的是服务端契约 —— 材料按 `project_root` 落盘，两个项目各自独立。
    页面那一层（切项目清掉旧选择 + 项目戳）由 PageGuardTests 盯着。
    """

    def _new(self, name):
        root = tempfile.mkdtemp()
        pj = Project(root)
        pj.init_dirs()
        pj.save_meta({"project_name": name, "system": "v34",
                      "project_code": "PROJ-001"})
        return root, pj

    def test_each_project_gets_its_own_tasks(self):
        import os
        from server import app as A

        def mat(prefix):
            rows = [{"kind": "image", "key": f"{prefix}__CHAR_{i:03d}_R01",
                     "filename": f"{prefix}_c{i}.png", "prompt": "x"}
                    for i in range(1, 4)]
            return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)

        a_root, a_pj = self._new("剧甲")
        b_root, b_pj = self._new("剧乙")
        A.api_post("/api/material/import", {"project_root": a_root,
                                            "text": mat("PRJ_AAA")})
        A.api_post("/api/material/import", {"project_root": b_root,
                                            "text": mat("PRJ_BBB")})
        for root, want in ((a_root, "PRJ_AAA"), (b_root, "PRJ_BBB")):
            tj = json.load(open(os.path.join(root, "03_提示词", "tasks.json"),
                                encoding="utf-8"))
            keys = [t["key"] for t in tj["asset_tasks"]]
            self.assertTrue(all(k.startswith(want) for k in keys), keys)
            self.assertEqual(len(keys), 3)

    def test_importing_into_b_does_not_touch_a(self):
        """★ 这一条就是「导错项目」的实际损失：A 的任务表被 B 的材料覆盖。"""
        import os
        from server import app as A

        def mat(prefix, n):
            rows = [{"kind": "image", "key": f"{prefix}__CHAR_{i:03d}_R01",
                     "filename": f"{prefix}_c{i}.png", "prompt": "x"}
                    for i in range(1, n + 1)]
            return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)

        a_root, _ = self._new("剧甲")
        b_root, _ = self._new("剧乙")
        A.api_post("/api/material/import", {"project_root": a_root,
                                            "text": mat("PRJ_AAA", 5)})
        before = open(os.path.join(a_root, "03_提示词", "tasks.json"),
                      encoding="utf-8").read()
        A.api_post("/api/material/import", {"project_root": b_root,
                                            "text": mat("PRJ_BBB", 2)})
        after = open(os.path.join(a_root, "03_提示词", "tasks.json"),
                     encoding="utf-8").read()
        self.assertEqual(before, after, "导 B 动了 A 的任务表")
