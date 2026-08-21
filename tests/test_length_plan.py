# -*- coding: utf-8 -*-
"""总时长 / 集数 / 每集时长 —— 三个量互相决定。

用户实遇：选了「每集 60 秒」，程序照着剧本里的 21 章切成 21 集 = 21 分钟。
而他要的是按**总时长**切。

用户原话：「节奏已经被我修改了，所以集数就要跟着逻辑变，
总时间和总集数、每集的时间应该是互相影响的逻辑才对」。

以前只有「每集多少秒」一个旋钮，而集数是环节1 **按剧本自带的章节**切的 ——
两者之间没有任何关联。所以 60 分钟 ÷ 60 秒 = 60 集这件事根本没人算。
"""
import unittest

from core import episodes as E
from core import settings as S


class PJ:
    def __init__(self, v):
        self.v = v


def _plan(**kw):
    return S.plan_lengths(kw)


def _text(**kw):
    orig = S.load
    S.load = lambda pj: kw
    try:
        return S.length_plan(PJ(kw))
    finally:
        S.load = orig


class ArithmeticTests(unittest.TestCase):
    """填两个算第三个。"""

    def test_the_real_case(self):
        """★ 用户那一档：总 60 分钟 + 每集 60 秒 → **60 集**，不是剧本的 21 章。"""
        p = _plan(total_seconds=3600, episode_seconds=60)
        self.assertEqual(p["count"], 60)
        self.assertEqual(p["per"], 60)

    def test_count_and_per_give_total(self):
        p = _plan(episode_count=40, episode_seconds=90)
        self.assertEqual(p["total"], 3600)

    def test_total_and_count_give_per(self):
        p = _plan(total_seconds=1800, episode_count=20)
        self.assertEqual(p["per"], 90)

    def test_nothing_given_stays_zero(self):
        """★ 默认必须什么都不定 —— 老项目的行为不许被这次改动改掉。"""
        p = _plan()
        self.assertEqual((p["total"], p["count"], p["per"]), (0, 0, 0))
        self.assertEqual(p["given"], [])

    def test_one_given_leaves_the_rest_to_stage_one(self):
        p = _plan(episode_seconds=60)
        self.assertEqual(p["per"], 60)
        self.assertEqual((p["total"], p["count"]), (0, 0))

    def test_rounding_does_not_lose_an_episode(self):
        """3600 ÷ 70 = 51.4 → 51 集。别向下丢到 0 或者算出 0 集。"""
        self.assertEqual(_plan(total_seconds=3600, episode_seconds=70)["count"], 51)
        self.assertGreaterEqual(_plan(total_seconds=10, episode_seconds=60)["count"], 1)

    def test_garbage_is_treated_as_unset(self):
        self.assertEqual(_plan(total_seconds="", episode_seconds="abc")["given"], [])
        self.assertEqual(_plan(episode_count=-5)["count"], 0)


class ConflictTests(unittest.TestCase):
    """三个都填而且乘不通。"""

    def test_it_reports_instead_of_silently_picking(self):
        """★ 悄悄改掉用户填的数字，比报错难查得多。"""
        p = _plan(total_seconds=3600, episode_count=21, episode_seconds=60)
        self.assertTrue(p["conflict"])
        self.assertIn("乘不通", p["conflict"])
        self.assertIn("3600", p["conflict"])

    def test_count_times_per_wins(self):
        """那两个直接决定怎么切，总时长只是结果 —— 但要说出来（上一条）。"""
        p = _plan(total_seconds=3600, episode_count=21, episode_seconds=60)
        self.assertEqual(p["total"], 21 * 60)

    def test_it_says_how_to_get_the_other_behaviour(self):
        p = _plan(total_seconds=3600, episode_count=21, episode_seconds=60)
        self.assertIn("清成 0", p["conflict"])

    def test_a_rounding_gap_is_not_a_conflict(self):
        """★ 别拦过头：整数除不尽时差几秒不算矛盾。"""
        self.assertFalse(_plan(total_seconds=3601, episode_count=60,
                               episode_seconds=60)["conflict"])


class PlanTextTests(unittest.TestCase):
    """发给环节1 的那段话。"""

    def test_it_says_the_count_is_computed_not_counted(self):
        """★ **这句话是整件事的重点。** 三个孤立的数字说不出这个意思。"""
        t = _text(total_seconds=3600, episode_seconds=60)
        self.assertIn("必须切成 60 集", t)
        self.assertIn("不是数剧本里有几章", t)

    def test_it_marks_which_numbers_the_user_chose(self):
        """算出来的和填的要分清 —— 否则人看不出自己填了什么。"""
        t = _text(total_seconds=3600, episode_seconds=60)
        self.assertIn("总时长 3600 秒（你指定）", t)
        self.assertIn("集数 60 集（算出来的）", t)

    def test_it_tells_stage_one_what_to_do_with_extra_chapters(self):
        t = _text(total_seconds=3600, episode_seconds=60)
        self.assertIn("合并", t)

    def test_nothing_given_hands_it_all_back(self):
        t = _text()
        self.assertIn("都没指定", t)
        self.assertIn("节奏速度", t)

    def test_pacing_shows_up_when_the_total_is_open(self):
        """★ 节奏只在总时长没定时才有意义 —— 那时候它是唯一的方向指示。"""
        self.assertIn("紧凑", _text(pacing="compact"))
        self.assertIn("舒展", _text(episode_seconds=60, pacing="unhurried"))

    def test_the_conflict_shows_up_in_the_text(self):
        t = _text(total_seconds=3600, episode_count=21, episode_seconds=60)
        self.assertIn("⚠", t)
        self.assertIn("乘不通", t)


class FieldTests(unittest.TestCase):

    def test_the_three_fields_exist_and_default_to_open(self):
        for k in ("total_seconds", "episode_count", "episode_seconds"):
            f = next(x for x in S.FIELDS if x["key"] == k)
            self.assertEqual(f["default"], 0, k)
            self.assertEqual(f["source"], "settings", k)

    def test_pacing_has_three_gears_and_defaults_to_standard(self):
        f = next(x for x in S.FIELDS if x["key"] == "pacing")
        self.assertEqual(f["options"], ["compact", "standard", "unhurried"])
        self.assertEqual(f["default"], "standard")

    def test_the_count_field_says_it_ignores_chapters(self):
        f = next(x for x in S.FIELDS if x["key"] == "episode_count")
        self.assertIn("不看剧本里有几章", f["why"])

    def test_pacing_says_when_it_matters(self):
        """★ 不说清的话，人会以为填了节奏就能改总长 —— 而总长填了它就不管用。"""
        f = next(x for x in S.FIELDS if x["key"] == "pacing")
        self.assertIn("只在总时长没指定时", f["why"])


class BuildTests(unittest.TestCase):
    """集数没切对要**停**，但程序不替它改边界。

    原来这里只记一条提醒然后照原样往下跑。用户原话：「我现在用 60 秒一集，
    他就把文档中的集和我要求的时间对上，他没进行切割」—— 剧本里写着
    「第一集」「第二集」，环节1 照抄那几个章节当集边界，我们再把每集秒数
    硬盖成 60，于是一集 60 秒里塞着原本好几分钟的剧情。往下每一个环节都
    建在错的集边界上，第九环节要把几分钟的戏压进 60 秒，一路崩到成片。
    """

    def test_the_mismatch_is_reported(self):
        import inspect
        src = inspect.getsource(E.build)
        self.assertIn("该切", src)
        self.assertIn("不替它合并或拆开", src)

    def test_it_says_which_direction_to_fix(self):
        import inspect
        src = inspect.getsource(E.build)
        self.assertIn("相邻章节", src)
        self.assertIn("剧情转折处再切开", src)

    def test_a_wrong_episode_count_stops_instead_of_warning(self):
        """★ 只提醒的话，人看不见 —— 而后面每一步都在错的集边界上工作。"""
        import inspect
        src = inspect.getsource(E.build)
        self.assertIn("raise RuntimeError(msg)", src)
        self.assertIn('"level": "error"', src)

    def test_it_still_saves_before_raising(self):
        """★ 抛之前先存盘 —— 不存的话 episodes.json 里看不到这条 issue，

        而页面上那一栏正是用户会去看的地方。
        """
        import inspect
        src = inspect.getsource(E.build)
        self.assertLess(src.index("pj.save_stage"), src.index("raise RuntimeError"),
                        "先抛后存等于没存")

    def test_the_plan_is_recorded_in_the_product(self):
        """★ 存一份，否则「为什么是这个集数」事后查不到。"""
        import inspect
        self.assertIn('res["length_plan"]', inspect.getsource(E.build))



class SegmentUpstreamTests(unittest.TestCase):
    """★ 用户实遇：第十二环节 5 段只成了 3 段就结束，第十三环节照样做了 5 段。

    根因是 `missing_deps` 只查「上游产物**存不存在**」，查不出「它有没有覆盖
    这一段」。3 条产物是存在的，于是那 2 段拿到的输入里没有自己的故事板 ——
    模型会自己编一个，**而且不报错**。

    这不是「就绪即派」的事：那一套只管出图出片。文字环节之间靠逐段对账。

    **通用级早就做对了**（s8 明确过滤没分镜的段），只有电影级漏了。
    """

    def test_v34_skips_segments_whose_upstream_is_missing(self):
        import inspect

        from core import run_v34 as R
        src = inspect.getsource(R.run_segment_stage)
        self.assertIn("done_segments(", src)
        self.assertIn("blocked", src)
        self.assertIn("SEG_UPSTREAM_MISSING", src)

    def test_it_only_checks_segment_scoped_upstreams(self):
        """全剧级/逐集级的上游由 missing_deps 管，别在这里查两遍。"""
        import inspect

        from core import run_v34 as R
        src = inspect.getsource(R.run_segment_stage)
        self.assertIn('!= "segment"', src)

    def test_it_says_why_it_does_not_force_it(self):
        """★ 这个项目的规矩：说清「硬做会怎样」，而且它不报错。"""
        import inspect

        from core import run_v34 as R
        src = inspect.getsource(R.run_segment_stage)
        self.assertIn("不硬做", src)
        self.assertIn("模型会自己编一个", src)

    def test_v61_already_did_this_and_now_records_it_too(self):
        """通用级一直有过滤，但只打日志 —— 日志会滚过去。"""
        import inspect

        from core import stages as S
        src = inspect.getsource(S)
        self.assertIn("no_shots", src)
        i = src.index("段还没排出分镜，这次不编")
        self.assertIn("SEG_UPSTREAM_MISSING", src[i:i + 900])

    def test_both_systems_use_the_same_code(self):
        """同一件事两处报，措辞和代号要一致 —— 否则查起来像两个问题。"""
        import inspect

        from core import run_v34 as R
        from core import stages as S
        self.assertIn("SEG_UPSTREAM_MISSING", inspect.getsource(R.run_segment_stage))
        self.assertIn("SEG_UPSTREAM_MISSING", inspect.getsource(S))


class BasicKeysTests(unittest.TestCase):
    """长度计划要默认展开 —— 折起来等于没做。"""

    def test_the_length_fields_are_expanded_by_default(self):
        """★ 用户实遇：选了「每集 60 秒」而集数还是剧本的 21 章，

        因为他没找到总时长那一栏 —— 它折在「生产参数」里。
        """
        for k in ("total_seconds", "episode_count", "episode_seconds", "pacing"):
            self.assertEqual(S.tier_of(k), "basic", k)

    def test_the_per_drama_switches_are_expanded_too(self):
        """字幕和对白呈现是每部剧要过一遍的，加的时候漏了这张表。

        `on_screen_text` 从这里去掉了 —— 剧情本身要求的文字改成一律允许，
        不再需要用户逐部剧声明（用户原话「画面上的字都是要有的，
        我需要控制的只是有没有字幕」）。
        """
        for k in ("subtitle", "dialogue_mode"):
            self.assertEqual(S.tier_of(k), "basic", k)

    def test_the_tuning_knobs_stay_folded(self):
        """★ 别全铺出来 —— 用户说过「过于复杂了」。73 项一屏铺开等于一个都不改。"""
        for k in ("storyboard_max_kf_per_sheet", "image_complexity_budget",
                  "view_batch_max_views", "video_reference_policy"):
            self.assertEqual(S.tier_of(k), "advanced", k)

    def test_the_expanded_set_stays_small(self):
        n = sum(1 for f in S.FIELDS if S.tier_of(f["key"]) == "basic")
        self.assertLessEqual(n, 24, f"默认展开 {n} 项，又开始堆了")

class PerOnlyTests(unittest.TestCase):
    """★ 只填每集秒数 —— 集数由环节1 看完剧本后估总时长反推。

    用户原话：「40 集的剧本就会因为我锁定60秒变成21集了，
    因为我总时长在拿到剧本的时候我是无法判断的但是我想要的每集秒数是可以知道的」
    """

    def test_per_only_flag_is_set(self):
        p = _plan(episode_seconds=60)
        self.assertTrue(p["per_only"])

    def test_per_only_flag_is_not_set_when_total_given(self):
        p = _plan(total_seconds=3600, episode_seconds=60)
        self.assertFalse(p.get("per_only"))

    def test_per_only_flag_is_not_set_when_nothing_given(self):
        p = _plan()
        self.assertFalse(p.get("per_only"))

    def test_text_tells_stage_one_to_estimate_total(self):
        t = _text(episode_seconds=60)
        self.assertIn("集数由你算", t)
        self.assertIn("估这部剧总该多长", t)

    def test_text_tells_stage_one_to_compute_count(self):
        t = _text(episode_seconds=60)
        self.assertIn("round(你估的总时长 ÷ 60)", t)

    def test_text_says_not_to_count_chapters(self):
        t = _text(episode_seconds=60)
        self.assertIn("不是数剧本里有几章", t)

    def test_text_says_each_episode_is_per_seconds(self):
        t = _text(episode_seconds=60)
        self.assertIn("duration_sec 填 60", t)


if __name__ == "__main__":
    unittest.main()
