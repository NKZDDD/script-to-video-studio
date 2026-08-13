# -*- coding: utf-8 -*-
"""V5.6 六字段参考图身份映射。

用户实跑撞到的问题，原话是：
「使用了image 1，但是你没和模型说image1是谁，现在是有说控制什么，不控制什么」

出图模型收到的是几张没有标签的图。只告诉它「Image 1 控制服饰、不控制姿态」
是不够的 —— 它不知道 Image 1 是谁，多人场景必然张冠李戴。
图照出、任务标 ok，只能靠肉眼在几百张里发现。

V5.6 把这件事写成硬规矩，并给了两个不同的阻断码：
  REFERENCE_RESOLUTION_BLOCKED  图没解析出来（缺文件、编号对不上）
  REFERENCE_MAPPING_BLOCKED     图对了，但没说清这张图是谁、管什么
"""
import io
import os
import re
import unittest

from core import produce as PR, system_v34 as V

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFS = [{"image_n": 1, "asset_id": "C002"}, {"image_n": 2, "asset_id": "ST008"}]

FULL = """Image 1 = C002 甲
  是谁/是什么 + 画面可见内容：成年男性正面半身，中性表情，纯色背景
  故事时间 / 当前状态：全剧基准身份，Clean LOOK，未受伤
  有权控制：脸型、五官、肤色、发型
  无权控制：这张图的姿势、机位、构图、背景
  适用范围：本次生成 ST012 这一张
Image 2 = ST008 病房
  是谁/是什么 + 画面可见内容：重症监护病房夜间环境，无人物
  故事时间 / 当前状态：EP01 入院之后的持续环境状态
  有权控制：床品材质、冷白灯光
  无权控制：人物身份、肤色、手部结构
  适用范围：本次生成 ST012 这一张
"""


def _tpl(name):
    return io.open(os.path.join(ROOT, "prompts", f"{name}.md"),
                   encoding="utf-8").read()


class SixFieldTests(unittest.TestCase):

    def test_a_complete_map_passes(self):
        self.assertEqual(PR.check_identity_map(FULL, REFS), ("", ""))

    def test_controls_only_is_blocked(self):
        """★ 用户遇到的那种：写了控制范围，没说这张图是谁。"""
        only_controls = """Image 1 = C002 甲
  有权控制：脸型、发型
  无权控制：姿势、机位
Image 2 = ST008 病房
  有权控制：灯光
  无权控制：人物身份
"""
        bad, warn = PR.check_identity_map(only_controls, REFS)
        self.assertTrue(bad)
        self.assertIn("REFERENCE_MAPPING_BLOCKED", bad)
        self.assertIn("这张图是谁", bad)
        self.assertEqual(warn, "", "这条必须硬停，不能只提醒")

    def test_each_of_the_five_fields_is_required(self):
        """五项里少任何一项都要拦 —— 逐项验，别只验第一项。"""
        for label in ("是谁/是什么 + 画面可见内容", "故事时间 / 当前状态",
                      "有权控制", "无权控制", "适用范围"):
            hacked = "\n".join(l for l in FULL.splitlines()
                               if not l.strip().startswith(label))
            bad, _ = PR.check_identity_map(hacked, REFS)
            self.assertTrue(bad, f"删掉「{label}」竟然通过了")

    def test_fields_are_counted_per_image_not_per_prompt(self):
        """★ 五项在整篇里各出现一次也能匹配上，但可能全挂在 Image 1 下面。

        不按 Image 分段校验，就会把「只写了第一张」判成合格 ——
        而第二张恰恰是最容易张冠李戴的那张。
        """
        first_only = """Image 1 = C002 甲
  是谁/是什么 + 画面可见内容：成年男性正面半身
  故事时间 / 当前状态：基准身份
  有权控制：脸型
  无权控制：姿势
  适用范围：本次
Image 2 = ST008 病房
"""
        bad, _ = PR.check_identity_map(first_only, REFS)
        self.assertTrue(bad)
        self.assertIn("Image 2", bad)
        self.assertNotIn("Image 1（", bad, "第一张是全的，不该被报进来")

    def test_legacy_authority_labels_still_count(self):
        """MUST PRESERVE / MUST NOT COPY 是 Controls 的更细一层拆分。

        只认 V5.6 的新写法会把已经写对的提示词判成错的。
        """
        legacy = """Image 1 = C002 甲
  是谁/是什么 + 画面可见内容：成年男性正面半身
  故事时间 / 当前状态：基准身份
  MUST PRESERVE：脸型、发型
  MUST NOT COPY：姿势、机位
  适用范围：本次生成
"""
        self.assertEqual(
            PR.check_identity_map(legacy, [REFS[0]]), ("", ""))

    def test_english_labels_from_the_skill_doc_are_accepted(self):
        """用户可能直接从 skill 文档粘英文段落过来。"""
        eng = """Image 1 = C002
  Who / What + Visible Content: adult male, front half body
  Story Time / Current State: baseline identity, clean LOOK
  Controls: face, hair
  Does Not Control: pose, camera
  Applicable Scope: this call only
"""
        self.assertEqual(PR.check_identity_map(eng, [REFS[0]]), ("", ""))

    def test_no_refs_means_nothing_to_check(self):
        self.assertEqual(PR.check_identity_map("随便什么", []), ("", ""))

    def test_numbering_problems_are_left_to_the_other_check(self):
        """★ 两道校验不许互相盖住。

        编号完全没写时报「身份映射不全」是误导 —— 真正的问题是没写编号，
        而那条有自己的分级（单张只提醒、多张硬停）。
        """
        self.assertEqual(PR.check_identity_map("一段没有编号的提示词", REFS),
                         ("", ""))


class WiringTests(unittest.TestCase):

    def test_both_checks_run_before_generating(self):
        src = io.open(os.path.join(ROOT, "core", "produce.py"),
                      encoding="utf-8").read()
        i = src.index("check_image_map(prompt, want_refs, who, ref)")
        j = src.index("check_identity_map(prompt, want_refs, who, ref)")
        self.assertLess(i, j, "编号校验要排在六字段校验前面，否则会盖住真问题")

    def test_the_error_says_whose_prompt_is_at_fault(self):
        """★ 报错必须说清是**哪个资产**的提示词，不然人会去改错文件。

        真实误导：报错写「Image 1（PS001）缺…」，人就去找 PS001_PROMPT.txt ——
        而那是被引用的那张图，它自己完全正确（道具外观规格，原创设计，
        本来就没有参考图）。要改的是引用它的那个资产。
        """
        bad, _ = PR.check_identity_map(
            "Image 1 = PS001 奖杯\n"
            "  有权控制：外观\n  无权控制：机位\n",
            [{"image_n": 1, "asset_id": "PS001"}],
            who="PI001", ref="03_提示词/资产生产提示词/PI001_PROMPT.txt")
        self.assertIn("PI001", bad)
        self.assertIn("PI001_PROMPT.txt", bad)
        self.assertIn("不是被它引用的那张图", bad)

    def test_the_numbering_error_says_it_too(self):
        bad, _ = PR.check_image_map(
            "Image 1 = 别的东西\nImage 2 = 又一个",
            [{"image_n": 1, "asset_id": "C001"},
             {"image_n": 2, "asset_id": "C005"}],
            who="ST012", ref="03_提示词/资产生产提示词/ST012_PROMPT.txt")
        self.assertIn("ST012", bad)

    def test_a_prompt_with_no_image_lines_is_left_alone(self):
        """★ 原创设计的资产（道具规格、空镜）本来就没有参考图。

        模板也写着「没有参考图的资产整段省略」—— 拿它去要求六字段是错的。
        """
        real = ("资产名称：混双大满贯奖杯外观规格。输出结构：正、侧、45度三视图，"
                "纯色背景。身份绑定：大型银色双耳冠军杯。")
        self.assertEqual(PR.check_identity_map(real, [], "PS001"), ("", ""))

    def test_the_block_has_a_diagnosis_entry_and_no_failover(self):
        """换一家服务商收到的还是同一段提示词，只会把错误重复一遍还多花钱。"""
        from core import diagnose as D
        self.assertIn("REF_MAP_INCOMPLETE", D.CATALOG)
        self.assertIn("REF_MAP_INCOMPLETE", D.NO_FAILOVER_CODES)
        self.assertEqual(
            D.code_of("REFERENCE_MAPPING_BLOCKED　参考图的身份映射不完整：…"),
            "REF_MAP_INCOMPLETE")

    def test_ref_map_incomplete_is_matched_before_ref_missing(self):
        """两条文案里都有「参考图」，但改法完全不同。"""
        from core import diagnose as D
        order = [c for c, _ in D._PATTERNS]
        self.assertLess(order.index("REF_MAP_INCOMPLETE"),
                        order.index("REF_MISSING"))


class TemplateTests(unittest.TestCase):
    """模板要真的教模型写六项 —— 校验拦得住但模板不教，等于每次都被拦。"""

    MAPPERS = ("n4b_asset_prompts", "n12_storyboard", "n13_video")

    def test_templates_teach_all_six_fields(self):
        for name in self.MAPPERS:
            t = _tpl(name)
            for label in ("是谁/是什么", "故事时间", "有权控制",
                          "无权控制", "适用范围"):
                self.assertIn(label, t, f"{name} 没教「{label}」")

    def test_template_examples_pass_their_own_check(self):
        """★ 模板里的示例必须自己过得了校验。

        过不了的话，模型照着示例写出来的东西会被程序拦住 —— 死循环。
        """
        for name in self.MAPPERS:
            t = _tpl(name)
            hits = list(re.finditer(r"^Image 1 = (\S+)", t, re.M))
            self.assertTrue(hits, f"{name} 里没有 Image 1 示例")
            start = hits[0].start()
            blk = t[start:t.index("```", start)]
            ids = re.findall(r"^Image (\d+) = (\S+)", blk, re.M)
            refs = [{"image_n": int(n), "asset_id": a} for n, a in ids]
            bad, _ = PR.check_identity_map(blk, refs)
            self.assertEqual(bad, "", f"{name} 的示例自己都过不了：{bad[:160]}")

    def test_ref_limit_reaches_the_templates_that_pick_references(self):
        """★ max_refs 服务商注册表里一直有，模型一直不知道。

        LLM 按剧情需要引 5、6 张，到出图那步才撞上限。
        """
        self.assertIn("REF_LIMIT", V.COMMON_PLACEHOLDERS)
        for name in self.MAPPERS:
            self.assertIn("{{REF_LIMIT}}", _tpl(name), name)

    def test_video_forbids_storyboard_plus_scstate(self):
        """V5.6：SCSTATE 已经被故事板消费掉，默认不再传给视频。"""
        t = _tpl("n13_video")
        self.assertIn("故事板 + SCSTATE = 默认禁止同时上传", t)
        self.assertIn("REFERENCE_AUTHORITY_CONFLICT", t)

    def test_video_warns_against_clean_look_plus_future_ct(self):
        """同时上传干净全身图和未来带伤图会让伤口提前出现。"""
        t = _tpl("n13_video")
        self.assertIn("Clean LOOK", t)
        self.assertIn("伤口提前", t)


class RefLimitTests(unittest.TestCase):

    def test_unknown_limit_says_so_instead_of_inventing_a_number(self):
        """编大了会让它多引，编小了会让它漏掉真正需要的覆盖图。"""
        from core.run_v34 import _ref_limit_block
        for blank in ({}, {"ref_limit": 0}, {"ref_limit": None}):
            self.assertIn("未知", _ref_limit_block(blank), repr(blank))

    def test_a_known_limit_says_it_is_a_ceiling_not_a_target(self):
        from core.run_v34 import _ref_limit_block
        s = _ref_limit_block({"ref_limit": 9})
        self.assertIn("9", s)
        self.assertIn("不是推荐装满", s)
        self.assertIn("逐张写清", s, "超过 5 张的强制审计要提到")

    def test_a_tight_limit_does_not_mention_the_five_image_audit(self):
        from core.run_v34 import _ref_limit_block
        self.assertNotIn("逐张写清", _ref_limit_block({"ref_limit": 1}))

    def test_every_params_entry_carries_the_limit(self):
        """★ 五个入口以前各拼一遍 params，漏一处那条链就静默少这项输入。"""
        src = io.open(os.path.join(ROOT, "server", "app.py"),
                      encoding="utf-8").read()
        self.assertEqual(src.count('params = dict(cfg.get("defaults") or {})'), 1,
                         "还有地方在手拼 params，会漏掉 ref_limit")
        self.assertGreaterEqual(src.count("params_of(cfg, pj"), 5)


if __name__ == "__main__":
    unittest.main()
