# -*- coding: utf-8 -*-
"""改写层和内置层不能是同一个文件。

源码方式跑（以及配置放在程序目录的老装法）时，数据目录**就是程序目录**，
于是「全局改写」和「内置」指向同一个 prompts/ 目录。

后果一个比一个糟：
  · 24 份模板全被标成「已改写」，看不出到底改过哪几份
  · 点「保存」是在改**源码里的内置模板**
  · 点「还原」会 os.remove 那个路径 —— **把内置模板删掉**，不可逆

打包成 exe 之后两者不同（内置在解压的临时目录里），所以只在开发时踩 ——
但开发时踩掉的是仓库里的模板。
"""
import os
import unittest

from core import prompts as P, stages as S


class CollisionTests(unittest.TestCase):

    def test_this_checkout_actually_collides(self):
        """先确认前提成立，否则下面几条测了个寂寞。"""
        b, g, _ = S.prompt_files("n2_rules")
        self.assertTrue(P._same(b, g),
                        "这个环境里两层不重合，下面的用例证明不了什么")

    def test_nothing_is_reported_as_customized_when_they_collide(self):
        """★ 重合时把内置当成改写，会让人以为自己改过一堆模板。"""
        self.assertEqual([i["name"] for i in P.catalog() if i["customized"]], [])

    def test_saving_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            P._target("n2_rules", None, "global")
        self.assertIn("同一个文件", str(cm.exception))

    def test_resetting_does_not_delete_the_builtin(self):
        """★ 这条是最要命的：还原本来会直接 os.remove 掉内置模板。"""
        b = S.prompt_files("n2_rules")[0]
        with self.assertRaises(ValueError):
            P.reset("n2_rules")
        self.assertTrue(os.path.isfile(b), "内置模板被删了")

    def test_the_message_says_how_to_get_a_real_override_layer(self):
        with self.assertRaises(ValueError) as cm:
            P._target("n2_rules", None, "global")
        self.assertIn("--data", str(cm.exception))

    def test_reading_still_works(self):
        """只是不能存改写，看和用都不受影响。"""
        self.assertTrue(P.read("n2_rules")["text"].strip())
        self.assertTrue(S.load_prompt("n2_rules").strip())


class SameTests(unittest.TestCase):

    def test_case_insensitive_on_windows(self):
        self.assertTrue(P._same(r"C:\A\b.md", r"c:\a\B.MD"))

    def test_blank_is_never_the_same(self):
        self.assertFalse(P._same("", ""))
        self.assertFalse(P._same("a.md", ""))


if __name__ == "__main__":
    unittest.main()
