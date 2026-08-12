# -*- coding: utf-8 -*-
"""改写版要记住「基于哪一版内置改的」。

改写是**粘性**的：它永远盖住内置。程序升级把内置模板重写了
（这一轮就把五份从逐集改成全剧级、参考图映射从四段改成六字段），
带着旧改写的机器上那些改动**一条都不生效，而且一声不吭**。

换机器、复用配置时最容易撞上 —— 而那正是最难查的时候，
因为「我用的是新版程序」这个前提看起来没问题。
"""
import os
import shutil
import tempfile
import unittest

from core import paths, prompts as P, stages as S


class StalenessTests(unittest.TestCase):

    def setUp(self):
        # 要一个和程序目录分开的数据目录，才有真正的「改写层」
        self.dir = tempfile.mkdtemp()
        self._old = paths.data_dir()
        paths.set_data_dir(self.dir)

    def tearDown(self):
        paths.set_data_dir(self._old)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _entry(self, name="n2_rules"):
        return next(x for x in P.catalog() if x["name"] == name)

    def test_a_fresh_override_is_not_stale(self):
        P.save("n2_rules", S.load_prompt("n2_rules") + "\n\n补一句自定义要求。")
        e = self._entry()
        self.assertTrue(e["customized"])
        self.assertEqual(e["stale"], "", "刚存的改写不该被判成过时")

    def test_it_goes_stale_when_the_builtin_changes(self):
        """★ 这才是要防的：内置更新了，改写还停在旧版上。"""
        P.save("n2_rules", S.load_prompt("n2_rules") + "\n\n补一句。")
        # 模拟一次程序升级：内置模板被重写
        b = S.prompt_files("n2_rules")[0]
        orig = open(b, encoding="utf-8").read()
        try:
            open(b, "w", encoding="utf-8").write(orig + "\n\n# 新版加的一节\n")
            e = self._entry()
            self.assertIn("又更新过", e["stale"])
            self.assertIn("一条都不会生效", e["stale"])
        finally:
            open(b, "w", encoding="utf-8", newline="\n").write(orig)

    def test_an_override_with_no_baseline_is_flagged_too(self):
        """★ 老改写没有基准记录 —— 不能当成没问题。

        复用旧数据目录时全是这种，正好是最需要提醒的场景。
        """
        dst = os.path.join(paths.prompts_dir(), "n2_rules.md")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "w", encoding="utf-8").write("老改写，没有基准记录")
        e = self._entry()
        self.assertIn("没记录", e["stale"])

    def test_the_builtin_layer_is_never_stale(self):
        self.assertEqual(self._entry("n1_truth")["stale"], "")

    def test_resetting_clears_the_warning(self):
        P.save("n2_rules", S.load_prompt("n2_rules") + "\n\nx")
        P.reset("n2_rules")
        e = self._entry()
        self.assertFalse(e["customized"])
        self.assertEqual(e["stale"], "")

    def test_read_reports_it_too(self):
        """列表和详情都要报 —— 只在一处报，另一处就成了假的安全感。"""
        P.save("n2_rules", S.load_prompt("n2_rules") + "\n\nx")
        self.assertIn("stale", P.read("n2_rules", scope="global"))


class UiTests(unittest.TestCase):

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.html = open(os.path.join(root, "web", "index.html"),
                         encoding="utf-8").read()

    def test_the_editor_shows_the_warning(self):
        self.assertIn("c.stale ?", self.html)

    def test_the_list_marks_it_without_opening_each_one(self):
        """一份份点开才看得到，等于没有。"""
        self.assertIn("基于旧版", self.html)


if __name__ == "__main__":
    unittest.main()
