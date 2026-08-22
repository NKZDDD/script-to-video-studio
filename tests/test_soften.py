# -*- coding: utf-8 -*-
"""被审核拒了就把提示词交给分析引擎优化一版再发。

短剧里打人、流血、伤口是常规戏。文字环节全程没问题，到出图这一端被拒，
后果是这一段没有画面、成片缺一块，前面十几个环节白跑。人工的做法就是
把那段提示词改一改再发，这里把它自动化。

**要的就是提示词正文，不套 JSON。** 早先是让模型回
`{"prompt": ..., "changed": [...], "kept": ...}`，结果多出一类纯粹是
格式造成的失败：它只把改动的那几句填进 `prompt`，一整段画面就没了
（实跑撞到，170 字 vs 原文 3384 字）。要纯文本之后这一类不存在了。

留下的护栏只有一道：改写结果要验过才用。因为模型真会把戏删掉，
而删掉**不报错** —— 图出来了、任务标 ok、成片是完整的，只是那场戏没了。
"""
import shutil
import unittest

from core import diagnose, soften
from test_v34_run import new_project

ORIG = """镜头：中景，手持轻微晃动。
Image 1 = C001 林南桥
Image 2 = C007 李想
李想一刀捅进林南桥的腹部，刀刃没入。林南桥低头看着伤口，
血从指缝间大量涌出，浸透衬衫下摆，顺着裤腿滴在地砖上。
她扶着墙缓缓滑坐下去，眼神从震惊转为了然。李想后退半步，握刀的手在抖。"""

GOOD = ORIG.replace("血从指缝间大量涌出", "深色湿痕在指缝间迅速扩开")


class Chat:
    """假模型：按给定的回复列表依次返回。"""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def chat(self, system, user, **kw):
        self.sent.append(user)
        r = self.replies[min(len(self.sent) - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


class CheckTests(unittest.TestCase):
    """能不能用。每一条都对应一种「不报错的坏结果」。"""

    def _ok(self, new, prev=None):
        return soften._check(ORIG, prev or ORIG, new)

    def test_a_real_rewrite_passes(self):
        self.assertEqual(self._ok(GOOD), "")

    def test_an_empty_rewrite_is_refused(self):
        self.assertIn("空", self._ok(""))

    def test_an_unchanged_rewrite_is_refused(self):
        """白花一次调用，还会让人以为改过了。"""
        self.assertIn("一模一样", self._ok(ORIG))

    def test_a_much_shorter_rewrite_is_refused(self):
        """★ 这就是「把戏删掉」的长相 —— 它过得了审核，而戏没了。"""
        bad = "Image 1 = C001 林南桥\nImage 2 = C007 李想\n两人在走廊里发生了冲突。"
        self.assertIn("删掉", self._ok(bad))

    def test_touching_the_identity_map_is_refused(self):
        """★ 那几行决定哪张参考图是谁 —— 动了会把别人的脸套上去。"""
        self.assertIn("身份映射", self._ok(ORIG.replace("Image 2 = C007",
                                                    "Image 2 = C009")))

    def test_dropping_an_identity_line_is_refused(self):
        self.assertIn("身份映射", self._ok(ORIG.replace("Image 2 = C007 李想\n", "")))

    def test_length_is_measured_against_the_original_not_the_last_round(self):
        """★ 多轮跑时最要命的一条：**逐轮蚕食**。

        每一步都只少一点点（单看合格），累计却掉到原文的六成以下 ——
        这正是「不报错、只是少」的典型长相。所以对着最初的原文比。
        """
        prev = ORIG[:int(len(ORIG) * 0.66)]
        new = prev[:int(len(prev) * 0.90)]
        self.assertEqual(soften._check(prev, prev, new), "",
                         "只对着上一版比的话，这一步是合格的")
        self.assertIn("删掉", self._ok(new, prev=prev),
                      "对着最初的原文比：累计已经少了四成，必须拦下")

    def test_each_round_may_not_shrink_either(self):
        """★ 单轮也要看，不然要等崩到底才发现，前几轮已经白跑了。"""
        new = ORIG[:int(len(ORIG) * 0.75)]
        self.assertEqual(soften._check(ORIG, "", new), "",
                         "只看「对着原文 60%」的话，75% 是过得去的")
        self.assertIn("一轮比一轮淡", soften._check(ORIG, ORIG, new))

    def test_a_normal_reword_is_not_blocked(self):
        """★ 别拦过头：换措辞本来就会有几个字的出入。"""
        self.assertEqual(soften._check(ORIG, ORIG, GOOD), "")


class DetectTests(unittest.TestCase):

    def test_a_rejection_is_recognised(self):
        for msg in ("content policy violation",
                    "输出内容涉及违规",
                    "Your prompt was flagged as sensitive",
                    "图片审核未通过"):
            self.assertTrue(soften.is_content_rejection(RuntimeError(msg)), msg)

    def test_other_failures_are_not(self):
        """★ 认错了就会拿别的错去白改一遍提示词，还多花一次调用。"""
        for msg in ("HTTP 524 A timeout occurred",
                    "No available image quota. Please try again later.",
                    "参考图文件不存在: ST001.png",
                    "Incorrect padding"):
            self.assertFalse(soften.is_content_rejection(RuntimeError(msg)), msg)

    def test_it_uses_the_same_rules_as_the_diagnosis(self):
        """★ 两份关键词表迟早对不上 —— 表现成「有时候会改、有时候不会」。"""
        import inspect
        self.assertIn("diagnose.code_of",
                      inspect.getsource(soften.is_content_rejection))


class SoftenTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _soften(self, llm, reason="content policy: graphic violence"):
        self.llm = llm
        return soften.soften(ORIG, reason, llm=llm, pj=self.pj, kind="video",
                             key="EP01-SEG03", round_no=1, log=lambda *a: None)

    def test_the_reply_is_the_prompt_itself(self):
        """★ 不套 JSON —— 模型回什么就是提示词。"""
        self.assertEqual(self._soften(Chat(GOOD)), GOOD)

    def test_a_fenced_reply_is_unwrapped(self):
        """模型有时会习惯性套一层围栏。别的一个字不动。"""
        self.assertEqual(self._soften(Chat(f"```\n{GOOD}\n```")), GOOD)

    def test_the_providers_own_words_are_sent_along(self):
        """★ 不告诉模型踩了哪一类，它只能瞎猜。"""
        self._soften(Chat(GOOD), reason="flagged: graphic_violence")
        self.assertIn("graphic_violence", self.llm.sent[0])

    def test_the_prompt_itself_is_sent(self):
        self._soften(Chat(GOOD))
        self.assertIn("一刀捅进", self.llm.sent[0])

    def test_a_bad_rewrite_is_thrown_away(self):
        """★ 宁可这一条失败让人自己改，也不能悄悄把戏改没。"""
        self.assertEqual(self._soften(Chat("两人发生冲突。")), "")

    def test_an_llm_failure_does_not_mask_the_real_error(self):
        self.assertEqual(self._soften(Chat(RuntimeError("模型也挂了"))), "")

    def test_the_rewrite_is_written_to_disk(self):
        """★ 自动改过的提示词必须看得见 —— 不然成片和剧本对不上时没有线索。"""
        self._soften(Chat(GOOD))
        body = open(self.pj.p("03_提示词", "自动改写", "EP01-SEG03_第1版.txt"),
                    encoding="utf-8").read()
        self.assertIn("深色湿痕", body)
        self.assertIn("一刀捅进", body, "原文也要留着，好对照")
        self.assertIn("graphic", body.lower(), "服务商的原话要留着")

    def test_it_shows_up_in_the_failure_panel(self):
        self._soften(Chat(GOOD))
        rows = [d for d in diagnose.load(self.pj.root)
                if d["code"] == "PROMPT_SOFTENED"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["level"], "warn", "这不是失败，东西做出来了")

    def test_nothing_is_recorded_when_the_rewrite_is_rejected(self):
        """没用上的改写不该留下「已改写」的痕迹。"""
        self._soften(Chat("冲突。"))
        self.assertEqual([d for d in diagnose.load(self.pj.root)
                          if d["code"] == "PROMPT_SOFTENED"], [])


class RunTests(unittest.TestCase):
    """整条路：出图 → 被拒 → 改写 → 重发。"""

    def setUp(self):
        self.pj = new_project()
        self.seen = []

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _gen(self, reject_times):
        def gen(prompt):
            self.seen.append(prompt)
            if len(self.seen) <= reject_times:
                raise RuntimeError("content policy: graphic violence")
            return {"ok": True}
        return gen

    def _rewriter(self):
        """每轮回一版不同的（回同一版会被判成「没有推进」）。"""
        class R:
            n = 0

            def chat(inner, system, user, **kw):             # noqa: N805
                inner.n += 1
                return ORIG.replace("大量涌出", f"缓缓渗开{'。' * inner.n}")
        return R()

    def _run(self, reject_times, llm=None, rounds=2):
        return soften.run_with_softening(
            self._gen(reject_times), ORIG, pj=self.pj,
            llm=llm or self._rewriter(), kind="video", key="K1",
            rounds=rounds, log=lambda *a: None)

    def test_a_clean_run_never_calls_the_llm(self):
        """★ 没被拒的时候一次都不该多花钱。"""
        llm = Chat(GOOD)
        soften.run_with_softening(self._gen(0), ORIG, pj=self.pj, llm=llm,
                                  kind="video", key="K1", log=lambda *a: None)
        self.assertEqual(llm.sent, [])

    def test_it_retries_with_the_rewritten_prompt(self):
        """★ 这就是这个功能本身。"""
        self.assertEqual(self._run(1), {"ok": True})
        self.assertEqual(len(self.seen), 2)
        self.assertIn("缓缓渗开", self.seen[1], "第二次要发改写后的")

    def test_it_gives_up_after_the_configured_rounds(self):
        with self.assertRaises(RuntimeError):
            self._run(99, rounds=2)
        self.assertEqual(len(self.seen), 3, "原文 1 次 + 改写 2 次")

    def test_rounds_zero_turns_it_off(self):
        with self.assertRaises(RuntimeError):
            self._run(99, rounds=0)
        self.assertEqual(len(self.seen), 1)

    def test_a_non_content_failure_is_raised_straight_away(self):
        """★ 524 拿去改提示词是白改，还多花一次调用。"""
        def gen(prompt):
            self.seen.append(prompt)
            raise RuntimeError("HTTP 524 A timeout occurred")

        with self.assertRaises(RuntimeError) as cm:
            soften.run_with_softening(gen, ORIG, pj=self.pj, llm=Chat(GOOD),
                                      kind="video", key="K1", log=lambda *a: None)
        self.assertIn("524", str(cm.exception))
        self.assertEqual(len(self.seen), 1)

    def test_without_an_llm_it_just_fails_as_before(self):
        with self.assertRaises(RuntimeError):
            soften.run_with_softening(self._gen(99), ORIG, pj=self.pj, llm=None,
                                      kind="video", key="K1", log=lambda *a: None)
        self.assertEqual(len(self.seen), 1)

    def test_an_unusable_rewrite_surfaces_the_original_error(self):
        """★ 报错必须还是「被审核拒了」，不能变成改写失败 —— 那会指错方向。"""
        with self.assertRaises(RuntimeError) as cm:
            self._run(99, llm=Chat("冲突。"))
        self.assertIn("content policy", str(cm.exception))

    def test_each_round_starts_from_the_previous_version(self):
        """★ 每次都从原文重来等于把上一轮的进展扔掉。"""
        sent = []

        class R:
            n = 0

            def chat(inner, system, user, **kw):             # noqa: N805
                sent.append(user)
                inner.n += 1
                return ORIG.replace("大量涌出", f"渗开{'。' * inner.n}")

        with self.assertRaises(RuntimeError):
            self._run(99, llm=R(), rounds=3)
        self.assertIn("渗开。", sent[1], "第二轮要拿第一轮改完的那版去改")
        self.assertIn("渗开。。", sent[2], "第三轮要拿第二轮那版")


class RoundsTests(unittest.TestCase):
    """轮数：可以从页面填，0 关掉。"""

    def test_the_default_is_five(self):
        self.assertEqual(soften.DEFAULT_ROUNDS, 5)

    def test_whatever_you_type_is_what_you_get(self):
        """★ 不设上限：防「越改越淡」靠的是验收那一关，不是限制次数。"""
        for n in (0, 1, 5, 8, 20):
            self.assertEqual(soften.clamp_rounds(n), n)

    def test_garbage_falls_back_instead_of_crashing(self):
        for junk in (None, "", "abc", {}):
            self.assertIsInstance(soften.clamp_rounds(junk), int, repr(junk))
        self.assertEqual(soften.clamp_rounds(-1), 0, "负数当成关掉")
        self.assertEqual(soften.clamp_rounds("abc"), soften.DEFAULT_ROUNDS)

    def test_the_worker_reads_the_global_default(self):
        """★ 页面上填了却没人读的话，那就是个假旋钮。"""
        from core.produce import _soften_rounds
        self.assertEqual(_soften_rounds({"defaults": {"soften_rounds": 4}}), 4)

    def test_a_provider_can_override_it(self):
        from core.produce import _soften_rounds
        self.assertEqual(_soften_rounds(
            {"soften_rounds": 1, "defaults": {"soften_rounds": 4}}), 1)

    def test_nothing_configured_means_the_default(self):
        from core.produce import _soften_rounds
        self.assertEqual(_soften_rounds({}), soften.DEFAULT_ROUNDS)

    def _html(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return io.open(os.path.join(root, "web", "index.html"),
                       encoding="utf-8").read()

    def test_the_page_offers_the_input(self):
        self.assertIn("'soften_rounds'", self._html())

    def test_the_input_shows_five_when_nothing_is_saved_yet(self):
        """★ 留空的代价：保存时 `+""` 是 0 —— 点一下保存就悄悄关掉了。"""
        html = self._html()
        self.assertIn("soften_rounds: 5", html)
        self.assertIn("RUN_DEFAULTS[k]", html)

    def test_the_global_default_reaches_the_worker(self):
        import inspect

        from server import app
        self.assertIn('out["defaults"]',
                      inspect.getsource(app.resolve_provider_cfg))


class TemplateTests(unittest.TestCase):
    """模板就该是短的 —— 划边界，别教它怎么写。"""

    def _text(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return io.open(os.path.join(root, "prompts", "_soften.md"),
                       encoding="utf-8").read()

    def test_it_says_the_three_things_that_matter(self):
        """★ 剧情边界是跨级不变的 —— 具体策略交给 TIER_RULE 那一格。"""
        t = self._text()
        self.assertIn("不许删事件", t)
        self.assertIn("不许改人物关系", t)
        self.assertIn("{{TIER_RULE}}", t)

    def test_it_asks_for_a_directly_usable_prompt(self):
        self.assertIn("直接可以使用的提示词", self._text())

    def test_it_stays_short(self):
        """★ 长模板不会让模型更听话，只会让人不敢改它。"""
        self.assertLess(len(self._text()), 400, "又写长了")

    def test_every_placeholder_is_filled(self):
        import re
        used = set(re.findall(r"\{\{(\w+)\}\}", self._text()))
        self.assertEqual(used, {"REJECT_REASON", "PROMPT", "TIER_RULE"})


class TierTests(unittest.TestCase):
    """降级阶梯：轮数越深，允许动的层越深；剧情边界任何一级都不放。"""

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _sent(self, round_no, reply=GOOD):
        """跑一轮改写，拿到实际发给模型的消息。"""
        sent = []

        class R:
            def chat(inner, system, user, **kw):              # noqa: N805
                sent.append(user)
                return reply
        soften.soften(ORIG, "content policy: graphic violence", llm=R(),
                      pj=self.pj, kind="video", key="K1", round_no=round_no,
                      log=lambda *a: None)
        return sent[0]

    def test_the_ladder_is_expression_visual_camera_event(self):
        """★ 顺序就是权限大小 —— 一开始就放开，模型会直奔把戏改没那档。"""
        names = [soften.tier_of(n)[1] for n in (1, 2, 3, 4)]
        self.assertEqual(names,
                         ["表达替换", "视觉降敏", "镜头调整", "事件表现方式调整"])

    def test_beyond_the_ladder_it_stays_on_the_deepest_tier(self):
        """轮数多给的是「同一级再换种写法」的机会，不是更深的权限。"""
        for n in (5, 8, 20):
            self.assertEqual(soften.tier_of(n)[1], "事件表现方式调整")

    def test_round_one_only_allows_rewording(self):
        """★ 第一轮只许换措辞 —— 事件、镜头、构图必须原样。"""
        sent = self._sent(1)
        self.assertIn("只换表达层的措辞", sent)
        self.assertIn("保持原样", sent)

    def test_each_round_sends_its_own_tier(self):
        self.assertIn("视觉降敏", self._sent(2))
        self.assertIn("镜头调整", self._sent(3))
        self.assertIn("事件表现方式", self._sent(4))

    def test_the_tier_survives_a_customised_template(self):
        """★ 自定义模板丢了 {{TIER_RULE}} 占位符，策略不能静默消失。

        {{MEDIUM_RULE}} 那次的教训：占位符被删，规则跟着没影，
        排查起来毫无线索。所以渲染完验一道，没进去就拼在最前面。
        """
        import os
        d = self.pj.p("00_项目说明", "提示词模板")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "_soften.md"), "w", encoding="utf-8") as f:
            f.write("服务商拒了：{{REJECT_REASON}}\n\n改一改：\n{{PROMPT}}")
        sent = self._sent(2)
        self.assertIn("视觉降敏", sent, "策略得拼进消息里，不能靠占位符运气")

    def test_the_tier_name_is_recorded_on_disk_and_panel(self):
        """★ 降到哪一级必须看得见 —— 降得深不深，人工复核的力度不一样。"""
        soften.soften(ORIG, "content policy: graphic violence", llm=Chat(GOOD),
                      pj=self.pj, kind="video", key="K1", round_no=3,
                      log=lambda *a: None)
        body = open(self.pj.p("03_提示词", "自动改写", "K1_第3版.txt"),
                    encoding="utf-8").read()
        self.assertIn("镜头调整", body)
        rows = [d for d in diagnose.load(self.pj.root)
                if d["code"] == "PROMPT_SOFTENED"]
        self.assertIn("镜头调整", rows[0]["raw"])


class WiringTests(unittest.TestCase):
    """三处构造 worker 的地方都要接上 —— 漏一处就是「有时候会改、有时候不会」。"""

    def test_both_workers_run_it(self):
        import inspect

        from core import produce
        for fn in (produce.make_image_worker, produce.make_video_worker):
            self.assertIn("soften.run_with_softening", inspect.getsource(fn),
                          fn.__name__)

    def test_all_three_call_sites_pass_the_llm(self):
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel, needle in (("core/pipeline.py", "llm_factory)"),
                            ("core/pipeline_v34.py", "llm_factory)"),
                            ("server/app.py", "_soften_llm)")):
            src = io.open(os.path.join(root, *rel.split("/")),
                          encoding="utf-8").read()
            i = src.index("make_image_worker(")
            self.assertIn(needle, src[i:i + 200], rel)


if __name__ == "__main__":
    unittest.main()
