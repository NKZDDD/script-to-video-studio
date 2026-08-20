# -*- coding: utf-8 -*-
"""三份下划线模板在下拉里**不许叫同一个名字**。

用户原话：「画面上的字、UI 的这个我手动去改，好像不听我的」。

发生了什么：下拉里三份 `_` 开头的模板全叫「所有环节共用的系统提示词」
（`_spec()` 的兜底名）。用户按这个名字挑了一份，改里面
「画面内禁止出现任何文字、字幕、水印、UI 面板」那一行 ——
挑到的是 `_settings_extract.md`，而那一行在那份里只是**引用的一个反面
例子**（用来教模型认出散文互相矛盾）。改它对出图出片没有任何影响。

保存成功、标了「已改写」、没有任何提示。这就是这个项目里最常见的那一类：
**不报错，只是不生效。**

顺带钉住真正的那个杠杆：画面里能不能有字是**按设置生成**的，
不是靠改散文 —— 改散文反而会和生成出来的那一句互相矛盾。
"""
import tempfile
import unittest

from core import prompts as P
from core import settings as S
from core import stages as ST
from core.store import Project


class LabelTests(unittest.TestCase):

    NAMES = ("_common", "_settings_extract", "_soften")

    def test_each_underscore_template_has_its_own_name(self):
        """★ 这一条就是那个 bug。三份同名 = 必然改错一份。"""
        labels = [P._spec(n)[1] for n in self.NAMES]
        self.assertEqual(len(set(labels)), len(labels),
                         f"还有同名的：{labels}")

    def test_no_label_is_the_old_catch_all(self):
        for n in self.NAMES:
            self.assertNotEqual(P._spec(n)[1], "所有环节共用的系统提示词", n)

    def test_the_extractor_says_it_does_not_touch_the_picture(self):
        """★ 用户改的就是这一份。得当面说清它不管画面。"""
        note = P.note_of("_settings_extract")
        self.assertIn("不参与出图出片", note)
        self.assertIn("反面例子", note)

    def test_the_global_rules_note_points_at_the_real_lever(self):
        """★ 光说「改这份」不够 —— 第 10 条是生成的，写散文会打架。"""
        note = P.note_of("_common")
        self.assertIn("设置 → 字幕", note)
        self.assertIn("互相矛盾", note)

    def test_the_soften_note_says_it_is_only_for_rejections(self):
        self.assertIn("被服务商拒了", P.note_of("_soften"))

    def test_every_underscore_template_on_disk_has_a_note(self):
        """★ 从盘上扫 —— 以后新加一份 `_xxx.md` 忘了写说明，这条会红。

        忘了的后果就是这次这件事再来一遍。
        """
        import glob
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for f in glob.glob(os.path.join(root, "prompts", "_*.md")):
            n = os.path.splitext(os.path.basename(f))[0]
            self.assertTrue(P.note_of(n), f"{n} 没有「这一份管什么」的说明")

    def test_the_note_reaches_the_detail_payload(self):
        """不下发的话页面上看不到，改了后端等于没改。"""
        import inspect
        self.assertIn('"note": note_of(name)', inspect.getsource(P))

    def test_the_page_renders_it(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html = io.open(os.path.join(root, "web", "index.html"),
                       encoding="utf-8").read()
        self.assertIn("mdBold(c.note)", html)


class TheRealLeverTests(unittest.TestCase):
    """画面里能不能有字，靠的是设置，不是改散文。

    而这里只剩一个开关：字幕。剧情本身要求的文字一律允许 ——
    用户原话「实际上画面上的字都是要有的，我需要控制的只是有没有字幕」。
    """

    def setUp(self):
        self.pj = Project(tempfile.mkdtemp(prefix="onscreen-"))
        self.pj.init_dirs()

    def test_story_text_is_allowed_by_default(self):
        """★ 原来默认是「禁止出现任何文字」—— 手机屏幕、招牌、信件、

        弹幕一起被禁掉，而且是静默的：图出来了、字没了、不报错。
        """
        r = S.subtitle_rule(self.pj)
        self.assertIn("剧情本身要求的文字", r)
        self.assertNotIn("禁止出现任何文字", r)

    def test_subtitles_are_off_by_default(self):
        self.assertIn("不要字幕", S.subtitle_rule(self.pj))

    def test_the_switch_turns_subtitles_on(self):
        S.save(self.pj, {"subtitle": True, "subtitle_lang": "中文"})
        r = S.subtitle_rule(self.pj)
        self.assertIn("中文字幕", r)
        self.assertIn("直接印进画面", r)
        self.assertNotIn("不要字幕", r)

    def test_watermarks_and_ui_stay_forbidden_either_way(self):
        """★ 放开剧情文字不等于放开水印和 UI 面板。"""
        for on in (True, False):
            S.save(self.pj, {"subtitle": on})
            self.assertIn("水印", S.subtitle_rule(self.pj))

    def test_it_actually_reaches_the_system_prompt(self):
        """★ 这才是「听不听我的」的判据 —— 两套体系共用这一条路径。"""
        S.save(self.pj, {"subtitle": True, "subtitle_lang": "中英双语"})
        sp = ST.system_prompt(self.pj, {})
        self.assertIn("中英双语字幕", sp)
        self.assertNotIn("{{SUBTITLE_RULE}}", sp)

    def test_a_retired_field_is_still_readable(self):
        """★ `load()` 只走 FIELDS —— 字段一删，用户填过的值就再也读不到。

        那是他亲手写的东西，悄悄丢掉正是这套东西要防的那一类。
        """
        self.assertIn("on_screen_text", S.RETIRED_KEYS)
        meta = dict(self.pj.meta() or {})
        meta["settings"] = {"on_screen_text": "弹幕字号不要太小"}
        self.pj.save_meta(meta)
        self.assertEqual(S.load(self.pj)["on_screen_text"], "弹幕字号不要太小")
        self.assertIn("弹幕字号不要太小", S.subtitle_rule(self.pj))


if __name__ == "__main__":
    unittest.main()
