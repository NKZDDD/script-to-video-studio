# -*- coding: utf-8 -*-
"""两个秒数不许合并成一个。

实跑一整集之后查出来的根因，用户那份 n9_shots.json 是这样的：

    SH_EP01_001   0.0 →  0.9
    ...
    SH_EP01_016  13.7 → 15.0     ← 整集 16 镜 = 15.0 秒，横跨 SC01–SC08

第九环节是**整集级**的，可它拿得到的唯一一个秒数是 DURATION=15 ——
那是 SEG 容器的容量（视频模型一次最多生成多久），不是这一集多长。
于是它把 8 个场次压成 15 秒，而且**自己知道**装不下，
在 shot_count_rationale 里写了「完整实时演出远超15秒」，还是压了。

往下一路不报错：第十环节照 15 秒装出 1 个 SEG（total_segs=1，
included_scenes 八个全在里面），第十二环节被迫在一张纸上画 16 格
（n12 模板上限 3×3=9 格），模型记不住八个场次的世界状态，
就把所有格子的 source_scstate 全填成第一个、道具状态和 CVS 打架。
第十四环节审计报的 7 条 BLOCK 里，5 条是这一个故障的下游。

V6.1 一侧一直有这套东西（episodes.seg_target + SEGMENTS_TARGET +
切段数核对）。搭 V5.6 的 stage 图时只搬了 DURATION，这三样都漏了。
"""
import io
import os
import shutil
import unittest

from core import diagnose, episodes as _eps, run_v34 as R
from test_v34_run import new_project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_eps(pj, rows):
    pj.save_stage(_eps.FILE[:-5], {"episodes": rows})


def _ep(pj, sec=90):
    """写一条集清单：EP01 共 sec 秒。"""
    _write_eps(pj, [{"episode": "EP01", "duration_sec": sec, "chars": 900,
                     "title": "第一集", "start": 0, "end": 900}])


def _ep_no_seconds(pj):
    """老项目：产物里根本没有 duration_sec 这个字段。"""
    _write_eps(pj, [{"episode": "EP01", "chars": 900, "title": "第一集",
                     "start": 0, "end": 900}])


class MappingTests(unittest.TestCase):
    """模板拿得到的两个秒数必须是两个。"""

    def setUp(self):
        self.pj = new_project()
        _ep(self.pj, 90)

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _map(self, params=None):
        return R.mapping(self.pj, "n9", params or {"duration": 15}, {}, "EP01")

    def test_the_episode_length_is_not_the_container_length(self):
        """★ 这就是那个 bug：以前只有 DURATION，n9 只能拿它当整集预算。"""
        m = self._map()
        self.assertEqual(m["DURATION"], 15)
        self.assertEqual(m["EPISODE_DURATION"], 90)

    def test_it_also_says_how_many_containers_to_expect(self):
        m = self._map()
        self.assertEqual(m["SEGMENTS_TARGET"], 6)      # 90 ÷ 15
        self.assertIn("90", m["SEGMENTS_WHY"])

    def test_an_old_project_without_the_number_still_runs(self):
        """产物里没有 duration_sec 的老项目：退回 0，模板自己兜底。"""
        _ep_no_seconds(self.pj)
        self.assertEqual(self._map()["EPISODE_DURATION"], 0)

    def test_series_stages_have_no_episode_length(self):
        """全剧级环节没有「本集」可言，别给它一个假的数。"""
        m = R.mapping(self.pj, "n1", {"duration": 15}, {}, "")
        self.assertEqual(m["EPISODE_DURATION"], 0)
        self.assertEqual(m["SEGMENTS_TARGET"], 0)


class CompressionCheckTests(unittest.TestCase):
    """排出来的总时长对不上就当场停，不许往下跑。"""

    def setUp(self):
        self.pj = new_project()
        _ep(self.pj, 90)

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _check(self, *ends):
        R.check_runtime(self.pj, "n9", {"timing_plan": [
            {"shot_id": f"SH{i}", "start": 0, "end": e}
            for i, e in enumerate(ends)]}, {"duration": 15}, "EP01",
            lambda *a, **k: None)

    def test_the_real_failure_is_caught(self):
        """★ 用户那一轮的真实数字：该 90 秒，排成了 15 秒。"""
        with self.assertRaises(RuntimeError) as cm:
            self._check(0.9, 8.1, 15.0)
        msg = str(cm.exception)
        self.assertIn("15", msg)
        self.assertIn("90", msg)
        # 光说「时长不对」没用 —— 得说清这两个数字分别是什么，
        # 否则人会去把本集时长改成 15 来「修好」它。
        self.assertIn("视频模型一次最多生成多久", msg)

    def test_a_plan_that_fills_the_episode_passes(self):
        self._check(30.0, 60.0, 90.0)

    def test_small_shortfalls_are_tolerated(self):
        """★ 镜头时长是估的，取整会有出入。差一点就报会天天误报。"""
        self._check(88.5)
        self._check(70.0)          # 差 22%，还在容忍范围内

    def test_a_genuinely_short_episode_is_not_flagged(self):
        _ep(self.pj, 15)
        self._check(15.0)

    def test_no_timing_plan_is_left_to_the_schema_layer(self):
        """没给时间计划是「缺字段」，不是「压缩」—— 别抢别人的错。"""
        R.check_runtime(self.pj, "n9", {"timing_plan": []}, {"duration": 15},
                        "EP01", lambda *a, **k: None)

    def test_an_old_project_without_the_number_is_not_flagged(self):
        _ep_no_seconds(self.pj)
        self._check(15.0)

    def test_other_stages_are_untouched(self):
        for sid in ("n1", "n7", "n10", "n12"):
            R.check_runtime(self.pj, sid, {"timing_plan": [{"end": 15.0}]},
                            {"duration": 15}, "EP01", lambda *a, **k: None)

    def test_it_gets_its_own_diagnosis_code(self):
        """★ 报成「没见过的错误」等于没查出来。"""
        try:
            self._check(15.0)
        except RuntimeError as exc:
            self.assertEqual(diagnose.code_of(str(exc)), "EPISODE_COMPRESSED")
        else:
            self.fail("该抛没抛")


class TemplateTests(unittest.TestCase):
    """程序拦得住但模板不教，等于每次都被拦。"""

    def _tpl(self, name):
        return io.open(os.path.join(ROOT, "prompts", f"{name}.md"),
                       encoding="utf-8").read()

    def test_n9_is_told_both_numbers_and_which_one_to_fill(self):
        t = self._tpl("n9_shots")
        self.assertIn("{{EPISODE_DURATION}}", t)
        self.assertIn("{{SEGMENTS_TARGET}}", t)
        # ★ 光给数字不够，得说清哪个是哪个 —— 犯过的错就是把两个当成一个
        self.assertIn("两个秒数", t)
        self.assertIn("绝对不许把整集压进", t)

    def test_n9_says_what_to_do_when_it_does_not_fit(self):
        """★ 不写这句，模型的默认解法就是压缩（它上次就是这么干的）。"""
        t = self._tpl("n9_shots")
        self.assertIn("不许压缩", t)
        self.assertIn("minimum_action_time", t)

    def test_n10_knows_how_many_boxes_to_expect(self):
        t = self._tpl("n10_segs")
        self.assertIn("{{SEGMENTS_TARGET}}", t)
        self.assertIn("只装出 1 箱是不正常的", t)


if __name__ == "__main__":
    unittest.main()
