# -*- coding: utf-8 -*-
"""视频参考图：骨架不许在补图里再出现一次。

用户实遇（2026-08-26，配截图）：「双倍参考图并且乱来参考图」——
面板上一段视频「参 0/18」，一排全是「缺」。

机制：n13 的 `reference_order` 是**整条上传顺序**，骨架排在前面、补图接着排。
而骨架已经通过 `storyboard_refs` 单独交给出片那一层了 —— 两边都留着就是同一张
传两次：18 张里 9 张是重的。而 `image_n` 是按上传顺序算的，于是后面每一条
描述都套到别的图上。**画面出得来，参考全是错的。**

这是我自己引入的回归：前一天为了修「补图被静默挑掉」，把装配那一行的
`if asset_id in amap` 前置过滤整个删了 —— 而那一行顺手做了两件事：
剔骨架（对）、静默扔掉资产表认不出的补图（错）。删掉时把对的那件也删了。
现在只按骨架剔，认不出的照旧留着、file_ref 空着。
"""
import inspect
import unittest

from core import produce, run_v34


class AssemblyTests(unittest.TestCase):
    """装配那一层：按骨架去重，别按资产表一刀切。"""

    def _src(self):
        src = inspect.getsource(run_v34.build_tasks)
        i = src.index("n13_video")
        return src[i:i + 3000]

    def test_it_dedupes_against_the_spine(self):
        blk = self._src()
        self.assertIn("spine_ids", blk)
        self.assertIn("sheet_id", blk)

    def test_it_does_not_filter_by_the_asset_map_any_more(self):
        """★ 按 amap 一刀切会静默扔掉资产表认不出的补图 ——
        那正是「提示词里映射了 5 张、实际只传了 1 张」的来处。"""
        blk = self._src()
        self.assertNotIn('r.get("asset_id") in amap', blk)

    def test_it_matches_long_and_short_ids(self):
        """★ 骨架的 sheet_id 和 reference_order 里的写法可能一头长一头短
        （`PRJ__SBSHEET_EP01_SEG01_A_R01` vs `SBSHEET_EP01_SEG01_A_R01`）——
        只比全等会漏，漏了就还是传两遍。"""
        blk = self._src()
        self.assertIn("endswith", blk)

    def test_it_records_how_many_were_deduped(self):
        """排查「参考图数目对不对」时要这个数。"""
        self.assertIn("spine_deduped", self._src())

    def test_no_log_call_in_build_tasks(self):
        """★ `build_tasks` 不收 log 参数 —— 顺手调一个不存在的名字就是
        NameError，而它只在真跑到那一行时才炸（同一天刚踩过一次）。"""
        src = inspect.getsource(run_v34.build_tasks)
        self.assertNotIn("log(", src)


class ProduceTests(unittest.TestCase):
    """出片那一层再兜一道：同一个文件不许上传两次。"""

    def _src(self):
        src = inspect.getsource(produce.make_video_worker)
        i = src.index("aux = sorted")
        return src[i:i + 1400]

    def test_it_skips_files_already_in_the_spine(self):
        blk = self._src()
        self.assertIn("if f in seen", blk)

    def test_it_says_so_when_it_has_to_dedupe(self):
        """★ 走到这一步说明装配那一层漏剔了 —— 静默兜过去的话，
        下次同样的漏法没人会发现。"""
        blk = self._src()
        self.assertIn("已去重", blk)


class WorkerParamTests(unittest.TestCase):
    """★ 出图那条路上没有 `p` 这个变量。

    实遇（2026-08-26）：13 条资产图全挂在
    `NameError: name 'p' is not defined`（core/produce.py:789）——
    我把视频那条路的变量名抄了过来。它不是语法错，只有真跑到那一行才炸，
    而那一行在**出图成功之后**，所以钱已经花了。
    """

    def test_the_image_worker_has_no_bare_p(self):
        import re
        src = inspect.getsource(produce.make_image_worker)
        bad = [l.strip() for l in src.splitlines()
               if re.search(r"(?<![\w.])p\.get\(", l)]
        self.assertEqual(bad, [])

    def test_the_image_worker_reads_the_task_params(self):
        src = inspect.getsource(produce.make_image_worker)
        self.assertIn('(task.get("params") or {})', src)


class UnsafeIsAModerationRejectionTests(unittest.TestCase):
    """★ 「appear to be unsafe」要走改写重发那条路。

    用户实遇（2026-08-26，超模出图）：
        The generated images appear to be unsafe. Try modifying the prompt
        or seeds. (服务商错误码：image_task_error)

    以前判成 UNKNOWN，于是**一轮改写都不走** —— 短剧里打人流血是常规戏，
    本该自动改写重发的活儿直接算失败，人得手动去改提示词。

    判词表里英文那部分缺两族：`unsafe` 一族，以及英文的「改提示词」
    （中文有 `修改提示词`，英文一句都没有）。
    """

    def _code(self, msg, err_code=""):
        from core import diagnose
        return diagnose.code_of(msg, 0, err_code)

    def test_the_real_message_is_recognised(self):
        self.assertEqual(
            self._code("The generated images appear to be unsafe. "
                       "Try modifying the prompt or seeds.",
                       "image_task_error"),
            "CONTENT_REJECTED")

    def test_it_triggers_the_rewrite_flow(self):
        from core import soften
        from core.apiutil import ApiError
        exc = ApiError("The generated images appear to be unsafe.",
                       err_code="image_task_error")
        self.assertTrue(soften.is_content_rejection(exc))

    def test_the_english_modify_prompt_family(self):
        """★ 中文的「修改提示词」一直在表里，英文的同一句以前不在。"""
        for msg in ("Please modify the prompt", "try adjusting your prompt",
                    "rephrase the prompt and retry", "revise your prompt"):
            self.assertEqual(self._code(msg), "CONTENT_REJECTED", msg)

    def test_the_generic_error_code_alone_is_not_enough(self):
        """★ `image_task_error` 是通用码 —— 网络错、参数错也是它。
        拿它判审核会把一堆无关失败拖进改写循环，白花钱还查不到真因。"""
        self.assertNotEqual(self._code("connection reset by peer",
                                       "image_task_error"),
                            "CONTENT_REJECTED")

    def test_it_does_not_fire_on_ordinary_words(self):
        """★ 只收「明确说这东西不安全」的写法。剧本里「安全屋」「他不安全」
        会被回显进报错，裸的 safe/safety 一收就误伤 ——
        而误伤的表现是「网络错误被当成内容审核」：不再重试，还白跑几轮改写。"""
        for msg in ("safety belt fastened", "unsafely parked",
                    "他躲进安全屋，外面不安全", "read timeout after 900s"):
            self.assertNotEqual(self._code(msg), "CONTENT_REJECTED", msg)

    def test_no_stray_control_characters_in_the_pattern(self):
        """★ 写 `\b` 时被吃成 0x08 退格符 —— 正则照样编译、照样不匹配，
        而且看源码看不出来（这个坑本会话踩过两次）。"""
        from core import apiutil
        self.assertNotIn(chr(8), apiutil.CONTENT_REJECT_RE.pattern)


class MaterialPathDedupeTests(unittest.TestCase):
    """★ 材料导入那条路也会双倍 —— 而它和 LLM 那条路是两套装配代码。

    用户实遇（2026-08-26 → 27）：修完 `run_v34` 的装配之后「双倍问题还是存在」。
    原因是那个项目的任务是**材料导入**建的（产物名 `..._VIDEO_EP01_SEG01_R01.mp4`
    是材料里给的 filename，不是 run_v34 的 `{code}_{seg}.mp4`），
    压根没经过 `run_v34.build_tasks`。

    机制一样：`spine` 是从 `refs` 里挑出来的子集，而 `reference_images` 是整份
    `refs` —— 出片那一层传「storyboard_refs 整条 + reference_images 整条」，
    一加就双倍。9 张骨架变 18 张，`image_n` 按上传顺序算，后面每条描述都套错图。

    教训（写在这儿是为了下次别再犯）：**同一个毛病在两条装配路径上各有一份**，
    修一处就以为修完了。这个项目里「只改了 4 处里的 2 处」是最常见的返工原因。
    """

    def _mat(self, sheets=7, aux=1):
        import json as _j
        from core import matimport as M
        rows = [{"kind": "image", "key": f"PRJ__SBSHEET_EP01_SEG01_{L}_R01",
                 "filename": f"sb{L}.png", "prompt": "板"}
                for L in "ABCDEFGHI"[:sheets]]
        rows += [{"kind": "image", "key": f"PRJ__CHAR_{i:03d}_R02",
                  "filename": f"c{i}.png", "prompt": "人"}
                 for i in range(1, aux + 1)]
        vid = {"kind": "video", "key": "EP01-SEG01", "episode": "EP01",
               "seg": "SEG01", "filename": "v.mp4", "prompt": "片",
               "storyboard_refs": [
                   {"image_n": i, "key": f"PRJ__SBSHEET_EP01_SEG01_{L}_R01"}
                   for i, L in enumerate("ABCDEFGHI"[:sheets], 1)],
               "reference_images": [
                   {"image_n": sheets + i,
                    "key": f"PRJ__CHAR_{i:03d}_R02"}
                   for i in range(1, aux + 1)]}
        rows.append(vid)
        return M.build(M.parse("\n".join(_j.dumps(r, ensure_ascii=False)
                                         for r in rows)))

    def test_the_spine_is_not_also_in_the_supplemental_list(self):
        v = self._mat()["tasks"]["video_tasks"][0]
        self.assertEqual(len(v["storyboard_refs"]), 7)
        self.assertEqual([r["asset_id"] for r in v["reference_images"]],
                         ["PRJ__CHAR_001_R02"])

    def test_the_total_uploaded_is_not_doubled(self):
        """★ 这一条就是用户看到的那个数：以前 7+8=15（骨架重复），现在 8。"""
        v = self._mat()["tasks"]["video_tasks"][0]
        total = len(v["storyboard_refs"]) + len(v["reference_images"])
        self.assertEqual(total, 8)

    def test_a_video_with_only_a_spine_has_no_supplemental(self):
        """★ 全是骨架时补图必须是空的 —— 以前这里是 9 张全重复。"""
        v = self._mat(sheets=9, aux=0)["tasks"]["video_tasks"][0]
        self.assertEqual(len(v["storyboard_refs"]), 9)
        self.assertEqual(v["reference_images"], [])

    def test_the_first_sheet_is_still_exposed_for_old_readers(self):
        v = self._mat()["tasks"]["video_tasks"][0]
        self.assertTrue(v["storyboard_ref"])
