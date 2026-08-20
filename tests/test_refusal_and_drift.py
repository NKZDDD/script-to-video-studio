# -*- coding: utf-8 -*-
"""用户实遇的「十七环节全崩，而前面一路不报错」——**一条因果链上的三个洞**。

链子是这样的（时间顺序，和花钱顺序一致）：

  ① 第十环节装箱漏了 SH_EP01_004（时间线 30.0-38.0 秒没有容器装它）
     → 没人查。第十一、十二环节只看装出来的 SEG，看不出少了一个镜头。

  ② 第十一环节有一条跑偏了：SCST_EP01_SC01_01 的正文写的是 EP01-SEG09。
     → 我们**把段号盖成了要的那一段**，唯一的证据被擦掉。
       于是第一段拿第九段的世界状态去出图。

  ③ 到第十三环节，模型自己把 ① 算出来了，在 time_budget_check 里写
     「SH_EP01_004 的 8 秒执行窗口位于 30.0-38.0 秒，未被 30 秒容器分配
     …因此不生成视频执行计划和可投喂提示词」，交了 `video_prompt: ""`。
     → 校验只查「键在不在」，键在，判过。空提示词落盘，当这一段做完了。

三个洞的共同点：**每一个都不报错，只是少了东西或者错了东西。**
所以这三组测试各钉一个洞，钉的都是「会不会说话」，不是「说得好不好」。
"""
import re
import unittest

from core import diagnose as D
from core import run_v34 as R
from core import system_v34 as V
from core.llm import check_keys, refusal_reason, refused_because


class RefusalTests(unittest.TestCase):
    """③ 模型明确拒绝产出的时候，别当成成功。"""

    # 用户那份 n13_video.json 里的原话
    REAL = {
        "seg_id": "EP01-SEG04",
        "windows": [1], "reference_order": [1],
        "video_prompt": "",
        "capability_note": "REFERENCE_DIMENSION_COVERAGE_GAP; "
                           "VIDEO_PROMPT_RELEASE_BLOCKED",
        "time_budget_check": "不通过：SH_EP01_004 的 8 秒执行窗口位于 "
                             "30.0-38.0 秒，未被 30 秒容器分配，"
                             "因此不生成视频执行计划和可投喂提示词。",
    }

    def test_an_empty_video_prompt_no_longer_passes(self):
        """★ 这是那一晚十七个环节全白跑的**唯一一处**放行点。"""
        _tpl, _deps, req = V.LLM_SPEC["n13"]
        self.assertIn("video_plan[].video_prompt!", req,
                      "少了那个 ! —— 键存在就算过，空提示词会被收下")
        miss = check_keys({"video_plan": [dict(self.REAL)]}, req)
        self.assertIn("video_plan[].video_prompt!", miss)

    def test_a_real_prompt_still_passes(self):
        row = dict(self.REAL, video_prompt="一段真的提示词")
        self.assertEqual(check_keys({"video_plan": [row]}, V.LLM_SPEC["n13"][2]),
                         [])

    def test_the_models_own_reason_comes_out(self):
        """★ 不读出来的话，报的是「输出缺少必需字段」—— 看着像模型没按格式答。

        然后照着这个假原因重试三次，全废，而真正要改的在上游。
        """
        data = {"video_plan": [dict(self.REAL)]}
        why = refused_because(data, ["video_plan[].video_prompt!"])
        self.assertIn("SH_EP01_004", why)
        self.assertIn("未被 30 秒容器分配", why)
        self.assertIn("重试没有意义", why)

    def test_a_genuinely_missing_key_does_not_get_a_made_up_reason(self):
        """缺键和被拒是两回事。乱猜原因会把人带到错的地方去。"""
        self.assertEqual(refused_because({"video_plan": []}, ["video_plan[]"]), "")

    def test_normal_gate_statuses_are_not_read_as_refusal(self):
        """★ `..._GATE: REQUIRED` 写在**每一条合格产物**上。

        把它算成拒绝就是全员误判 —— 那比不报错更糟，人会开始无视报错。
        """
        self.assertEqual(refusal_reason({
            "capability_note": "STORYBOARD_REFERENCE_ADMISSION_GATE: REQUIRED",
            "time_budget_check": "通过：全部镜头落在容器内"}), "")

    def test_the_two_other_stages_are_covered_too(self):
        """同一类放行点还有两处：故事板提示词、场景状态提示词。"""
        self.assertIn("sbpkg[].sheets[].storyboard_prompt!", V.LLM_SPEC["n12"][2])
        self.assertIn("scstates[].prompt!", V.LLM_SPEC["n11"][2])


class PackingTests(unittest.TestCase):
    """① 装箱漏镜头 —— 链子的起点，也是最便宜的拦点。"""

    class _PJ:
        root = ""

        def __init__(self, timing):
            self._t = timing

        def stage_data(self, name, ep=""):
            return {"timing_plan": self._t} if name == "n9_shots" else {}

    @staticmethod
    def _timing(n):
        return [{"shot_id": f"SH_EP01_{i:03d}", "start": (i - 1) * 8.0,
                 "end": i * 8.0} for i in range(1, n + 1)]

    def _run(self, segs, timing=None, adjustments=None):
        pj = self._PJ(timing or self._timing(4))
        out = {"segs": segs, "boundary_adjustments": adjustments or []}
        R.check_packing(pj, "n10", out, {"duration": 30}, "EP01", log=None)

    def test_a_shot_that_no_container_covers_stops_it(self):
        """★ 用户那次全崩就是这里漏的：SH_EP01_004 在 24-32 秒，没箱子装。"""
        with self.assertRaises(RuntimeError) as e:
            self._run([{"seg_id": "EP01-SEG01",
                        "included_shots": ["SH_EP01_001", "SH_EP01_002",
                                           "SH_EP01_003"]}])
        msg = str(e.exception)
        self.assertIn("SH_EP01_004", msg)
        self.assertIn("没有任何容器装它", msg)

    def test_it_says_which_seconds_so_the_cause_is_readable(self):
        """漏的都在尾巴上 —— 报出秒数才看得出是「容器没装够」。"""
        with self.assertRaises(RuntimeError) as e:
            self._run([{"seg_id": "EP01-SEG01",
                        "included_shots": ["SH_EP01_001"]}])
        self.assertRegex(str(e.exception), r"SH_EP01_004 在时间线的 24-32 秒")

    def test_a_shot_in_two_containers_stops_it_too(self):
        """重复装 = 同一段内容做两遍、付两次钱，画面还会重复。"""
        with self.assertRaises(RuntimeError) as e:
            self._run([{"seg_id": "EP01-SEG01",
                        "included_shots": ["SH_EP01_001", "SH_EP01_002"]},
                       {"seg_id": "EP01-SEG02",
                        "included_shots": ["SH_EP01_002", "SH_EP01_003",
                                           "SH_EP01_004"]}])
        self.assertIn("重复装了 2 次", str(e.exception))

    def test_everything_packed_is_quiet(self):
        self._run([{"seg_id": "EP01-SEG01",
                    "included_shots": ["SH_EP01_001", "SH_EP01_002"]},
                   {"seg_id": "EP01-SEG02",
                    "included_shots": ["SH_EP01_003", "SH_EP01_004"]}])

    def test_a_shot_the_model_said_it_cut_is_not_a_hole(self):
        """★ 模板允许边界校正时砍东西 —— **交代过的是决定，没交代的才是窟窿。**

        不区分的话，一次合法的删减会把整集拦死，然后人学会绕过这道检查。
        """
        self._run([{"seg_id": "EP01-SEG01",
                    "included_shots": ["SH_EP01_001", "SH_EP01_002",
                                       "SH_EP01_003"]}],
                  adjustments=[{"seg_id": "EP01-SEG01", "problem": "装不下",
                                "action": "按第 2 条调的",
                                "what_was_cut": "砍了 SH_EP01_004 的空镜"}])

    def test_it_does_not_fire_on_other_stages(self):
        pj = self._PJ(self._timing(4))
        R.check_packing(pj, "n9", {"segs": []}, {}, "EP01", log=None)
        R.check_packing(pj, "n12", {"segs": []}, {}, "EP01", log=None)

    def test_no_timeline_means_nothing_to_check(self):
        """第九环节没排时间线是另一回事，schema 那层管 —— 别在这儿抢着报。"""
        R.check_packing(self._PJ([]), "n10", {"segs": []}, {}, "EP01", log=None)

    def test_the_code_is_in_the_catalog_and_is_an_error(self):
        """★ 目录里没有的代号会退回 UNKNOWN：页面标题变成一句废话，

        真正那段话只剩在 raw 里。而这一条必须一眼看出是错、不是提醒。
        """
        self.assertEqual(D.CATALOG["SEG_SHOT_UNPACKED"]["level"], "error")


class DriftTests(unittest.TestCase):
    """② 段号跑偏 —— 而我们曾经亲手把证据盖掉。"""

    def test_a_different_seg_id_is_not_relabelled(self):
        """★ 原来是无条件 `item["seg_id"] = sid`。"""
        why = R.seg_drift({"seg_id": "EP01-SEG09"}, "EP01-SEG01")
        self.assertIn("EP01-SEG09", why)
        self.assertIn("另一段", why)

    def test_the_source_no_longer_overwrites_blindly(self):
        import inspect
        src = inspect.getsource(R.run_segment_stage)
        self.assertIn("drift = seg_drift(item, sid)", src)
        self.assertLess(src.index("drift = seg_drift"),
                        src.index('item["seg_id"] = sid'),
                        "先盖后查等于没查")

    def test_prose_that_never_mentions_this_seg_is_drift(self):
        """字段填对了、正文整条写的是别段 —— 用户那条就是这样。"""
        why = R.seg_drift({"prompt": "本段 EP01-SEG09 的世界状态：…"},
                          "EP01-SEG01")
        self.assertIn("一次都没提", why)

    def test_mentioning_a_neighbour_is_normal(self):
        """★ 承接上一段是正常写法。这一条误报的话，每一段都会被拦。"""
        self.assertEqual(R.seg_drift(
            {"seg_id": "EP01-SEG01",
             "prompt": "承接 EP01-SEG09 的收尾状态，本段 EP01-SEG01 …"},
            "EP01-SEG01"), "")

    def test_scene_and_shot_ids_are_not_seg_ids(self):
        """SC01、SH_EP01_004 不是段号。认错形状就是天天误报。"""
        self.assertEqual(R.seg_drift(
            {"prompt": "SC01 里的 SH_EP01_004，CVS_EP01_SC01_01"},
            "EP01-SEG01"), "")

    def test_an_absent_seg_id_is_still_stamped(self):
        """模型不填段号是允许的 —— 那本来就由我们来填。"""
        self.assertEqual(R.seg_drift({"prompt": "本段 EP01-SEG01 …"},
                                     "EP01-SEG01"), "")


if __name__ == "__main__":
    unittest.main()
