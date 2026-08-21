# -*- coding: utf-8 -*-
"""视频被派早 4 分钟：场景状态图和故事板共用一个 kind 名。

排错包 `..._0820_1832.zip` 的时间线：

    18:20:17  SCST_EP01_SC01_01 出好      ← 场景状态图那一批的最后一条
    18:20:19  视频 SEG01/SEG04 报「固定故事板不存在」   ← 2 秒后
    18:20:50  EP01-SEG04_SHEET_C 才落盘
    18:21:57  EP01-SEG01_SHEET_C 才落盘
    18:23:26  EP01-SEG01_SHEET_B 才落盘
    18:24:55  EP01-SEG01_SHEET_A 才落盘

故事板一张都没少（25 条全 ok）。是视频被派早了。

机制：`_worker_kind()` 里 p2（场景状态图）和 p3（故事板）返回同一个字符串
`"storyboard"` —— 对选 worker 是对的，对 relay 是错的：它们是**两批活**。
于是 p2 跑完就调了 `relay.finished("storyboard")`，而 p3 还在跑，
从那一秒起每一张还没出的 sheet 都被判成「没人会做它了」。

外加用户当场否掉的一个设计：「他缺少实际条件他不能去做才对」——
原来「没人会做它了」时是**照旧派出去**让出图那层的硬停说话。
派出去撞一次空，面板上留下的是一条「失败」，而它不是失败，是条件不具备。
"""
import unittest

from core import pipeline as P61
from core import pipeline_v34 as P34
from core import sizes as Z
from core.relay import Relay


def _task(out, refs=None, spine=None):
    return {"key": out, "output": out,
            "reference_images": [{"file_ref": f} for f in (refs or [])],
            "storyboard_refs": [{"file_ref": f} for f in (spine or [])]}


class BatchKindTests(unittest.TestCase):

    def test_worker_kind_still_shares_for_p2_and_p3(self):
        """选 worker 时它们**应该**是同一类 —— 这一条不是要改的东西。"""
        self.assertEqual(P34._worker_kind("p2"), P34._worker_kind("p3"))

    def test_batch_kind_never_shares(self):
        """★ 这就是那个 bug：relay 的 kind 不许两步共用。"""
        kinds = [P34._batch_kind(s) for s in ("p1", "p2", "p3", "p4")]
        self.assertEqual(len(set(kinds)), 4, f"还有共用的：{kinds}")

    def test_relay_uses_batch_not_produce(self):
        """★ 用错哪一个都不报错 —— 只是等待被提前解除。"""
        import inspect
        src = inspect.getsource(P34)
        for call in ('relay.declare(_s["batch"]',
                     'relay.finished(s["batch"])',
                     'relay.ready_of(s["batch"])'):
            self.assertIn(call, src, call)
        self.assertNotIn('relay.finished(s["produce"])', src)
        self.assertNotIn('relay.ready_of(s["produce"])', src)

    def test_v61_is_wired_the_same_way(self):
        """★ V6.1 那三类今天不重名，所以没踩到 —— 但隐患一样，一起改掉。"""
        import inspect
        src = inspect.getsource(P61)
        self.assertIn('relay.finished(s["batch"])', src)
        self.assertNotIn('relay.finished(s["produce"])', src)

    def test_the_real_scenario_now_waits(self):
        """★ 复现那 4 分钟：场景状态图跑完了，故事板还在跑 —— 视频必须等。"""
        class PJ:
            root = ""

            def p(self, *a):
                return "/nope/" + "/".join(a)
        r = Relay(PJ())
        r.declare("p2", [_task("03b/scst.png")])
        r.declare("p3", [_task("04/sheet_a.png")])
        video = _task("05/v.mp4", spine=["04/sheet_a.png"])
        # 场景状态图那一批跑完了 —— 以前这一句会让视频立刻放行
        r.finished("p2")
        ok, why = r.ready_of("p4")(video)
        self.assertIs(ok, False, "场景状态图跑完不代表故事板跑完")
        # 等待说明里报的是**在等哪个文件**，不是在等哪一批 ——
        # 「在等 04/sheet_a.png」比「在等 p3」有用得多。
        self.assertIn("04/sheet_a.png", why)
        # 故事板那一批也跑完了、文件还是没有 → 条件不具备，别派
        r.finished("p3")
        ok, why = r.ready_of("p4")(video)
        self.assertIsNone(ok)
        self.assertIn("04/sheet_a.png", why)


class NoDispatchTests(unittest.TestCase):
    """「他缺少实际条件他不能去做才对」。"""

    def test_run_batch_marks_it_instead_of_firing(self):
        import inspect
        from core import executor
        src = inspect.getsource(executor.run_batch)
        self.assertIn("if ok is None:", src)
        self.assertIn("条件不具备，没发请求", src)

    def test_it_is_task_fatal_not_a_retry(self):
        """★ 重试一个条件不具备的任务只会重复撞空，还多花时间。"""
        import inspect
        from core import executor
        src = inspect.getsource(executor.run_batch)
        i = src.index("if ok is None:")
        self.assertIn("TASK_FATAL", src[i:i + 700])

    def test_the_message_says_it_did_not_spend(self):
        """★ 面板上要能分出「这条要修」和「这条没花钱」。"""
        import inspect
        from core import executor
        src = inspect.getsource(executor.run_batch)
        i = src.index("if ok is None:")
        self.assertIn("没发请求", src[i:i + 700])


class SizeTests(unittest.TestCase):
    """尺寸按各家的 api 规范换 —— 用户原话「转换成他能吃的值确保尺寸正确」。"""

    RATIO = ["1:1", "16:9", "9:16", "4:3", "3:4", "2:3", "3:2", "21:9"]
    PIXEL = ["1024x1536", "1024x1024", "1536x1024"]
    KUNJI = ["1K", "2K", "4K", "1024x1536", "1024x1024", "1536x1024",
             "2048x2048", "1792x1024"]

    def test_an_exact_match_is_sent_as_is(self):
        self.assertEqual(Z.resolve("16:9", self.RATIO), ("16:9", ""))
        self.assertEqual(Z.resolve("1024x1536", self.PIXEL), ("1024x1536", ""))

    def test_pixels_become_the_same_shape_as_a_ratio(self):
        """★ `1024x1536` 和 `2:3` 是同一个形状 —— 换写法，不换形状。"""
        v, note = Z.resolve("1024x1536", self.RATIO)
        self.assertEqual(v, "2:3")
        self.assertIn("形状一模一样", note)

    def test_a_ratio_becomes_the_nearest_pixel_size(self):
        v, note = Z.resolve("9:16", self.PIXEL)
        self.assertEqual(v, "1024x1536")
        self.assertIn("形状变了", note)

    def test_a_shape_change_is_always_announced(self):
        """★ 悄悄换形状和悄悄不换一样糟：人看到一张形状不对的图、日志里没话。"""
        for want in ("16:9", "21:9", "9:16"):
            _v, note = Z.resolve(want, self.PIXEL)
            self.assertTrue(note, want)

    def test_a_tier_on_a_ratio_only_provider_stops(self):
        """★ 档位说的是分辨率，比例说的是形状 —— 换不过来。

        按形状硬匹配的话 `2K` 会算成 1:1（代表像素 2048x2048），
        或者在只有比例的清单里挑出 21:9（「面积」最大）。
        两个结果都不是任何人的意思，而猜错的后果是几百张图形状不对却不报错。
        """
        v, note = Z.resolve("2K", self.RATIO)
        self.assertIsNone(v)
        self.assertIn("档位表达不了形状", note)
        self.assertIn("改成比例", note)

    def test_a_tier_stays_as_is_where_it_is_supported(self):
        self.assertEqual(Z.resolve("2K", self.KUNJI), ("2K", ""))

    def test_a_tier_never_resolves_to_a_bare_ratio(self):
        """★ 混着写的清单里也不许挑出一个比例来当分辨率。

        （`3K` 不是合法档位 —— 只有 1K/2K/4K，所以这里用 8K 那种不存在的
        写法测不到这条；拿一个**支持像素也支持比例**的清单来测。）
        """
        v, _ = Z.resolve("2K", ["1024x1536", "1536x1024", "21:9"])
        self.assertIsNotNone(v)
        self.assertNotIn(":", v)

    def test_an_illegal_tier_stops(self):
        """只有 1K/2K/4K 是档位。`3K` 看不懂 —— 停，不猜。"""
        v, note = Z.resolve("3K", self.KUNJI)
        self.assertIsNone(v)
        self.assertIn("看不懂", note)

    def test_something_unreadable_stops_instead_of_passing_through(self):
        """★ 原样发过去的话，多数家自己挑一个默认值 —— 图出来了、不报错。"""
        v, note = Z.resolve("大图", self.PIXEL)
        self.assertIsNone(v)
        self.assertIn("看不懂", note)

    def test_no_declared_list_means_send_as_is(self):
        """★ 服务商没声明支持什么，就说明我们不知道 —— 猜只会换一种错法。"""
        self.assertEqual(Z.resolve("16:9", []), ("16:9", ""))

    def test_ratios_are_reduced_not_rounded(self):
        """`1086x1448` 是 3:4 就报 3:4，不硬凑成 9:16。"""
        self.assertEqual(Z.as_ratio("1086x1448"), "3:4")
        self.assertEqual(Z.as_ratio("1024x1536"), "2:3")
        self.assertEqual(Z.as_ratio("1610x977"), "1610:977")

    def test_the_separators_people_actually_type_are_accepted(self):
        for s in ("1024x1536", "1024X1536", "1024*1536", "1024×1536"):
            self.assertEqual(Z.parse(s), ("pixel", 1024, 1536), s)
        for s in ("16:9", "16：9", " 16 : 9 "):
            self.assertEqual(Z.parse(s), ("ratio", 16, 9), s)

    def test_produce_raises_when_it_cannot_convert(self):
        import inspect
        from core import produce
        src = inspect.getsource(produce._fit_size)
        self.assertIn("raise RuntimeError(note)", src)

    def test_both_image_and_video_go_through_it(self):
        import inspect
        from core import produce
        src = inspect.getsource(produce)
        self.assertIn('_fit_size(provider_cfg, model, "image"', src)
        self.assertIn('_fit_size(provider_cfg, model, "video"', src)


if __name__ == "__main__":
    unittest.main()
