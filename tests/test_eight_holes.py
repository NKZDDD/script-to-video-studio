# -*- coding: utf-8 -*-
"""用户列的八条里，前四条各自的洞。

四条的共同点还是那一个：**不报错，只是少了或者错了**。
所以每组钉的都是「会不会说话」，以及「说的是不是那件事」。

  ① 故事板给了 2 张，视频只用得上 1 张 —— 骨架后半截被悄悄截掉
  ② 一直跳时间 —— 时间线上有几秒没人负责
  ③ 60 秒一集，可它照抄剧本里的「第几集」，没按总时长切
  ④ 第十二环节没做完，一路还是走到了成片 —— 成片只是短一段
"""
import unittest

from core import diagnose as D
from core import episodes as E
from core import run_v34 as R
from core import stages as S


class RefLimitTests(unittest.TestCase):
    """① 骨架装不下要说出来，不是砍掉后半截照做。"""

    def test_the_template_declares_every_sheet_not_just_one(self):
        """★ 模板里只示范一条 `Image 1 = SB_...` 的时候：

        第十二环节按 V6.2 出 2 张，程序把 2 张都传上去，而提示词只说了
        第一张是谁 —— 第二张没有身份说明，编号还整体错位一位。两边都不报错。
        """
        t = self._tpl("n13_video")
        self.assertIn("骨架有几张就写几条，一张都不许少", t)
        self.assertIn("SHEET_A", t)
        self.assertIn("SHEET_B", t)

    def test_each_sheet_must_carry_its_own_time_window(self):
        """★ 两张都写「本段全程」= 告诉模型这两张说的是同一段时间。"""
        t = self._tpl("n13_video")
        self.assertIn("不许写「本段全程」", t)

    def test_the_quota_goes_to_the_spine_first(self):
        """★ 「按最小充分集挑」单独出现时，模型会把骨架当可裁的。"""
        t = self._tpl("n13_video")
        self.assertIn("额度先给骨架", t)
        self.assertIn("要裁只裁补图", t)
        blk = R._ref_limit_block({"ref_limit": 9})
        self.assertIn("额度先给故事板骨架", blk)

    def test_the_spine_over_limit_is_not_a_prompt_problem(self):
        """★ 骨架本身超上限时让它砍补图是无解的 —— 补图本来就没有。

        这一条必须指向「合并承载颗粒度或换模型」，不是「减参考图」。
        """
        t = self._tpl("n13_video")
        self.assertIn("那不是让你砍骨架", t)

    def test_unknown_limit_does_not_invent_a_number(self):
        blk = R._ref_limit_block({})
        self.assertIn("未知", blk)

    def test_the_code_is_an_error_not_a_warning(self):
        self.assertEqual(D.CATALOG["VIDEO_REF_OVER_LIMIT"]["level"], "error")

    @staticmethod
    def _tpl(name):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return io.open(os.path.join(root, "prompts", f"{name}.md"),
                       encoding="utf-8").read()


class TimelineTests(unittest.TestCase):
    """② 一直跳时间。"""

    class _PJ:
        root = ""

        def stage_data(self, *a, **k):
            return {}

    @staticmethod
    def _plan(rows):
        return {"timing_plan": [{"shot_id": i, "start": a, "end": b}
                                for i, a, b in rows]}

    def _run(self, rows):
        R.check_timeline(self._PJ(), "n9", self._plan(rows), {}, "EP01", log=None)

    def test_a_gap_stops_it(self):
        """★ 24 秒到 30 秒之间没有任何镜头 —— 那 6 秒没人负责。"""
        with self.assertRaises(RuntimeError) as e:
            self._run([("SH1", 0, 16), ("SH2", 16, 24), ("SH3", 30, 38)])
        msg = str(e.exception)
        self.assertIn("中间 6 秒没有任何镜头", msg)
        self.assertIn("动作接不上", msg)

    def test_an_overlap_stops_it(self):
        """重了就是同一件事演两回，还付两次钱。"""
        with self.assertRaises(RuntimeError) as e:
            self._run([("SH1", 0, 16), ("SH2", 12, 24)])
        self.assertIn("叠在一起", str(e.exception))

    def test_a_backwards_shot_stops_it(self):
        with self.assertRaises(RuntimeError) as e:
            self._run([("SH1", 0, 16), ("SH2", 16, 10)])
        self.assertIn("早于开始时间", str(e.exception))

    def test_rounding_slop_is_not_a_gap(self):
        """★ 取整本来就有零点几秒的出入。这一条误报的话每一集都会被拦。"""
        self._run([("SH1", 0, 16.0), ("SH2", 16.2, 24), ("SH3", 24.0, 30)])

    def test_a_contiguous_timeline_is_quiet(self):
        self._run([("SH1", 0, 16), ("SH2", 16, 24), ("SH3", 24, 32)])

    def test_it_sorts_before_comparing(self):
        """★ timing_plan 不保证按顺序写。按写的顺序比 = 满屏假跳秒。"""
        self._run([("SH2", 16, 24), ("SH1", 0, 16), ("SH3", 24, 32)])

    def test_one_shot_has_no_neighbour_to_check(self):
        self._run([("SH1", 0, 16)])

    def test_it_does_not_fire_on_other_stages(self):
        R.check_timeline(self._PJ(), "n10",
                         self._plan([("SH1", 0, 16), ("SH2", 30, 38)]),
                         {}, "EP01", log=None)


class MissingSegmentTests(unittest.TestCase):
    """④ 缺一段的成片不是成片。"""

    def test_assemble_takes_an_expected_segment_list(self):
        import inspect
        sig = inspect.signature(S.assemble)
        self.assertIn("expect_segs", sig.parameters)

    def test_it_refuses_when_a_segment_never_produced_a_video(self):
        """★ 原来只拼「找得到的」，不查该有几段 —— 报的是「拼好 1 集」。"""
        import inspect
        src = inspect.getsource(S.assemble)
        self.assertIn("缺一段的成片不能交付", src)
        self.assertIn("lost", src)

    def test_both_systems_are_wired(self):
        """★ 只接一边的话，另一套体系照旧能拼出短一段的成片。"""
        import inspect
        self.assertIn("expect_segs", inspect.getsource(R.assemble))
        from core import pipeline as P61
        self.assertIn("expect_segs=S.segment_ids",
                      inspect.getsource(P61.run_all)
                      if hasattr(P61, "run_all") else
                      inspect.getsource(P61))

    def test_v61_knows_its_own_segment_source(self):
        """通用级的段来自环节2，不是第十环节。"""
        import inspect
        self.assertIn("s2_segments", inspect.getsource(S.segment_ids))

    def test_the_recursive_per_episode_branch_passes_it_down(self):
        """★ 逐集那一支不传下去的话，一键跑到底那条路径等于没接。"""
        import inspect
        src = inspect.getsource(S.assemble)
        i = src.index("outs.append(dict(assemble(")
        self.assertIn("expect_segs", src[i:i + 200])


class EpisodeCountTests(unittest.TestCase):
    """③ 集数得按时长算，不是数剧本里有几章。"""

    def test_a_wrong_count_stops_instead_of_warning(self):
        import inspect
        src = inspect.getsource(E.build)
        self.assertIn("raise RuntimeError(msg)", src)

    def test_it_explains_why_continuing_is_worse(self):
        """★ 只说「集数不对」的话，人会想「那我先跑着看看」。"""
        src = __import__("inspect").getsource(E.build)
        self.assertIn("一路崩到成片", src)
        self.assertIn("不是数剧本里有几章", src)

    def test_the_program_still_does_not_recut(self):
        """★ 集边界是内容判断。程序代切会切在错的地方 —— 比集数不对更难查。"""
        src = __import__("inspect").getsource(E.build)
        self.assertIn("不替它合并或拆开", src)


if __name__ == "__main__":
    unittest.main()
