# -*- coding: utf-8 -*-
"""导入「全剧生产材料」md：分割 → 验收 → 落成 tasks.json。

用户原话（2026-08-24）：「所以我需要一个 md 内容分割和验收导入器就直接查一个
固定项目路径或是点击导入生产材料即可，参考图小于等于目标模型上限再导入后
需要有提醒」。

这条路成立的依据是实测那份《烟火尽头》21 集材料：

    234 条【生产序号】= 150 个 png + 84 个 mp4（21 集 × 4 段）
    被引用的 150 个 ID **全部**在同一份文件里有产出 —— 引用链闭合
    每条都有 建议保存文件名 / production_prompt / Image N＝<ID>｜是谁｜控制｜…

出图出片要的四样它全给齐了（落哪、发什么、传哪几张、什么尺寸），
所以 27 个 LLM 环节可以整个跳过。
"""
import json
import os
import unittest

from core import matimport as M

# 按真实材料的格式写的两条（全角等号和竖线 —— 实际文件里用的就是它们）
MD = """# 《某剧》全剧最终生产材料

## 【生产序号 001】

生产目标：林溪身份根

完整Canonical Revision ID：PRJ_X__CHAR_001_R02

建议保存文件名：PRJ_X__CHAR_001_R02.png

### 【需要上传的参考图】

无需上传参考图。

### 【完整可复制production_prompt】

生成唯一Canonical原子资产：林溪身份根。9:16竖幅，高清。

### 【完成后返回】

请返回文件 PRJ_X__CHAR_001_R02.png。

---

## 【生产序号 002】

生产目标：EP01 SEG01 故事板

建议保存文件名：PRJ_X__SBSHEET_EP01_SEG01_A_R01.png

### 【需要上传的参考图】

Image 1＝PRJ_X__CHAR_001_R02｜19岁林溪｜控制：身份｜不控制：走位｜适用范围：全程

### 【完整可复制production_prompt】

生成 EP01 SEG01 的三格故事板。9:16。

---

## 【生产序号 003】

生产目标：EP01 SEG01 15秒成片

建议保存文件名：PRJ_X__VIDEO_EP01_SEG01_R01.mp4

### 【需要上传的参考图】

Image 1＝PRJ_X__SBSHEET_EP01_SEG01_A_R01｜时间骨架｜控制：15秒全程｜不控制：脸｜适用范围：0-15秒
Image 2＝PRJ_X__CHAR_001_R02｜19岁林溪｜控制：身份｜不控制：节奏｜适用范围：出现的时间窗

### 【完整可复制production_prompt】

一次生成完整15秒9:16成片。Image 1 是时间骨架（正文复述一遍，不该被数进张数）。
"""


class SplitTests(unittest.TestCase):

    def test_it_splits_by_production_number(self):
        self.assertEqual([u["no"] for u in M.parse(MD)], [1, 2, 3])

    def test_it_reads_the_four_things_produce_needs(self):
        """★ 落哪、发什么、传哪几张、什么尺寸 —— 缺一样这条路就不成立。"""
        u = M.parse(MD)[2]
        self.assertEqual(u["filename"], "PRJ_X__VIDEO_EP01_SEG01_R01.mp4")
        self.assertIn("一次生成完整15秒", u["prompt"])
        self.assertEqual([a for _n, a in u["refs"]],
                         ["PRJ_X__SBSHEET_EP01_SEG01_A_R01", "PRJ_X__CHAR_001_R02"])
        self.assertEqual(u["ratio"], "9:16")
        self.assertEqual(u["seconds"], 15)

    def test_fullwidth_separators_are_read(self):
        """★ 实际文件用的是全角 ＝ 和 ｜。只认半角的话一条参考图都解析不出来 ——

        而那会表现为「导入了 234 条、每条 0 张参考图」，出图时全部报没说哪张是谁。
        """
        self.assertEqual(len(M.parse(MD)[1]["refs"]), 1)

    def test_the_prompt_body_repeating_image_n_is_not_counted(self):
        """★ production_prompt 正文里也会写 `Image 1` —— 不分节的话同一条

        会数出两倍张数，然后「超上限」全员误报。
        """
        self.assertEqual(len(M.parse(MD)[2]["refs"]), 2)

    def test_episode_and_seg_come_from_the_filename(self):
        """★ 拼接按 `EP01-SEG01` 这个前缀挑本集的分段 —— 换个写法就找不到。"""
        u = M.parse(MD)[2]
        self.assertEqual((u["episode"], u["seg"]), ("EP01", "SEG01"))
        self.assertEqual(M.task_key(u), "EP01-SEG01")

    def test_no_reference_needed_is_not_a_missing_field(self):
        self.assertEqual(M.parse(MD)[0]["missing"], [])

    def test_a_unit_without_a_prompt_says_so(self):
        bad = MD.replace("生成 EP01 SEG01 的三格故事板。9:16。", "")
        self.assertIn("完整可复制production_prompt", M.parse(bad)[1]["missing"])


class OutPathTests(unittest.TestCase):
    """落点沿用现有目录 —— 产物页、拼接、指纹注册表都按它们找东西。"""

    def test_each_family_lands_in_its_folder(self):
        cases = {
            "PRJ_X__CHAR_001_R02.png": "02_固定资产/人物身份资产",
            "PRJ_X__LOC_001_VIEW_A01_R02.png": "02_固定资产/场景资产",
            "PRJ_X__PROP_SPEC_001_V01_R02.png": "02_固定资产/道具资产",
            "PRJ_X__SBSHEET_EP01_SEG01_A_R01.png": "04_故事板",
            "PRJ_X__VIDEO_EP01_SEG01_R01.mp4": "05_分段视频",
        }
        for fn, folder in cases.items():
            got = M.out_path({"stem": fn.rsplit(".", 1)[0], "filename": fn})
            self.assertTrue(got.startswith(folder + "/"), f"{fn} → {got}")


class AuditTests(unittest.TestCase):
    """能查的都查 —— 每一条都对应一种「导进去之后才发现」。"""

    def test_a_clean_material_has_no_issues(self):
        self.assertEqual(M.audit(M.parse(MD), {"image": 9, "video": 9}), [])

    def test_a_reference_nobody_produces_is_an_error(self):
        """★ 出图时才报「参考图不存在」，而那张图压根没人做。"""
        bad = MD.replace("Image 2＝PRJ_X__CHAR_001_R02｜19岁林溪｜控制：身份｜不控制：节奏｜适用范围：出现的时间窗",
                         "Image 2＝PRJ_X__CHAR_999_R01｜不存在的｜控制：无｜不控制：无｜适用范围：无")
        codes = [i["code"] for i in M.audit(M.parse(bad))]
        self.assertIn("REF_NOT_PRODUCED", codes)

    def test_over_the_model_limit_is_reported(self):
        """★ 用户点名要的那一条：服务商会截掉多的，而截掉的是排在后面的 ——

        画面用错参考，任务照样标成功。
        """
        iss = M.audit(M.parse(MD), {"image": 9, "video": 1})
        over = [i for i in iss if i["code"] == "REF_OVER_LIMIT"]
        self.assertTrue(over)
        self.assertIn("截掉的正是排在后面", over[0]["msg"])

    def test_an_unknown_limit_is_not_guessed(self):
        """★ 不知道上限时判「超了」是在猜 —— 而误报会让人开始无视提醒。"""
        self.assertEqual([i for i in M.audit(M.parse(MD), {}) 
                          if i["code"] == "REF_OVER_LIMIT"], [])

    def test_a_gap_in_the_seg_numbers_is_reported(self):
        """★ 缺一段 = 成片短一截，而拼接那一步不会说话。"""
        md = MD + MD.split("## 【生产序号 003】")[1].replace(
            "SEG01", "SEG03").replace("生产序号 003", "生产序号 004")
        iss = M.audit(M.parse("## 【生产序号 003】".join(
            [MD, MD.split("## 【生产序号 003】")[1].replace("SEG01", "SEG03")])))
        self.assertIn("SEG_GAP", [i["code"] for i in iss])

    def test_reference_numbering_must_be_continuous(self):
        bad = MD.replace("Image 1＝PRJ_X__SBSHEET", "Image 3＝PRJ_X__SBSHEET")
        self.assertIn("REF_NUMBER_GAP", [i["code"] for i in M.audit(M.parse(bad))])


class BuildTests(unittest.TestCase):

    def test_reference_file_refs_resolve_to_where_they_land(self):
        """★ 引用链闭合是前提，所以这里能直接算出落点 —— 不用等出图再解析。"""
        got = M.build(M.parse(MD))
        v = got["tasks"]["video_tasks"][0]
        self.assertTrue(all(r["file_ref"] for r in v["reference_images"]))
        self.assertEqual(v["reference_images"][0]["file_ref"],
                         "04_故事板/PRJ_X__SBSHEET_EP01_SEG01_A_R01.png")

    def test_the_storyboard_spine_is_picked_out_for_video(self):
        """★ 视频那一层读 `storyboard_refs` —— 不挑出来就是「缺故事板」。"""
        v = M.build(M.parse(MD))["tasks"]["video_tasks"][0]
        self.assertEqual(len(v["storyboard_refs"]), 1)
        self.assertTrue(v["storyboard_ref"])

    def test_every_unit_gets_a_prompt_file(self):
        got = M.build(M.parse(MD))
        self.assertEqual(len(got["prompts"]), 3)

    def test_the_video_duration_comes_from_the_material(self):
        v = M.build(M.parse(MD), duration=99)["tasks"]["video_tasks"][0]
        self.assertEqual(v["params"]["duration"], 15, "材料里写了 15 秒")

    def test_tasks_are_marked_as_coming_from_material(self):
        """★ 事后要分得出这一批是导进来的，不是环节跑出来的。"""
        got = M.build(M.parse(MD))
        self.assertTrue(got["tasks"]["asset_tasks"][0]["from_material"])


if __name__ == "__main__":
    unittest.main()


class LayoutTests(unittest.TestCase):
    """提示词落点要和 LLM 路径一致 —— 用户点名：「文件结构需要和 LLM 处理的一致」。

    那四个子目录不是装饰：任务明细页按 `prompt_ref` 显示并**就地改提示词**
    （改完立刻生效，worker 是出图那一刻才读文件的）、排错包按目录挑要打包哪些、
    人自己翻文件夹。全倒进一个目录的话，「这一集的视频提示词」要在 234 个
    文件里找。
    """

    def test_prompts_land_in_the_same_four_folders_as_the_llm_path(self):
        got = M.build(M.parse(MD))["prompts"]
        dirs = {k.rsplit("/", 1)[0] for k in got}
        self.assertEqual(dirs, {"03_提示词/资产生产提示词",
                                "03_提示词/故事板提示词",
                                "03_提示词/视频提示词"})

    def test_the_folders_match_run_v34_rel_exactly(self):
        """★ 两处各写一遍就会飘。这一条守着它们对得上。"""
        from core.run_v34 import _rel
        for kind in ("asset", "scstate", "storyboard", "video"):
            folder = _rel(kind, "x").rsplit("/", 1)[0]
            self.assertTrue(folder.startswith("03_提示词/"), folder)
        # 导入器用的就是这四个
        import inspect
        src = inspect.getsource(M.prompt_path)
        for folder in ("视频提示词", "故事板提示词", "场景状态提示词",
                       "资产生产提示词"):
            self.assertIn(folder, src)

    def test_long_ids_are_kept_in_filenames(self):
        """★ 短号化会撞：`CHAR_001_R02` 和 `CHAR_001_PH01_R02` 压成短号是同一个，

        而**撞了是静默覆盖** —— 两条任务写同一个文件，后一条盖前一条，
        只表现为少了一张图。
        """
        got = M.build(M.parse(MD))["prompts"]
        self.assertIn("03_提示词/资产生产提示词/PRJ_X__CHAR_001_R02_PROMPT.txt", got)


JSONL = (
    '{"kind":"image","key":"A__CHAR_001_R02","filename":"A__CHAR_001_R02.png",'
    '"size":"9:16","reference_images":[],"prompt":"人物身份根。"}\n'
    '{"kind":"image","key":"A__SB_EP01_SEG01_A","filename":"A__SB_EP01_SEG01_A.png",'
    '"size":"9:16","reference_images":[{"image_n":1,"key":"A__CHAR_001_R02"}],'
    '"prompt":"故事板。Image 1 = A__CHAR_001_R02 林溪"}\n'
    '{"kind":"video","key":"EP01-SEG01","episode":"EP01","seg":"SEG01",'
    '"filename":"A__VIDEO_EP01_SEG01.mp4","duration":15,"ratio":"9:16",'
    '"storyboard_refs":[{"image_n":1,"key":"A__SB_EP01_SEG01_A"}],'
    '"reference_images":[{"image_n":2,"key":"A__CHAR_001_R02"}],'
    '"prompt":"15秒成片。"}\n')


class ContractTests(unittest.TestCase):
    """契约格式（JSONL）是主路 —— md 散文是兼容。

    用户原话（2026-08-24）：「不应该是我去解析 codex 生产的结果，应该是我告诉他
    我要什么样的结构」。反过来做的代价已经付过：解析器要认全角等号、要分节、
    要剔 `---`，每一处猜错都是静默的。
    """

    def test_jsonl_is_detected_and_parsed(self):
        us = M.parse(JSONL)
        self.assertEqual([u["kind"] for u in us], ["image", "image", "video"])
        self.assertEqual(us[2]["seconds"], 15)

    def test_a_json_array_works_too(self):
        import json as _j
        arr = _j.dumps([_j.loads(l) for l in JSONL.strip().splitlines()],
                       ensure_ascii=False)
        self.assertEqual(len(M.parse(arr)), 3)

    def test_the_spine_comes_first_then_supplements(self):
        """★ image_n 就是上传顺序：骨架排前面，补图接着排。"""
        v = M.build(M.parse(JSONL))["tasks"]["video_tasks"][0]
        self.assertEqual([r["image_n"] for r in v["reference_images"]], [1, 2])
        self.assertEqual(v["storyboard_refs"][0]["sheet_id"],
                         "A__SB_EP01_SEG01_A")

    def test_the_spine_is_taken_from_the_declaration_not_guessed(self):
        """★ 契约明确说了哪几张是骨架 —— 猜（按 ID 里有没有 SBSHEET）

        在命名不同时会悄悄挑错，而挑错了视频就少了时间骨架。
        """
        v = M.build(M.parse(JSONL))["tasks"]["video_tasks"][0]
        self.assertEqual(len(v["storyboard_refs"]), 1,
                         "名字里没有 SBSHEET，靠猜是挑不出来的")

    def test_a_broken_line_is_reported_not_skipped(self):
        """★ 跳过坏行的话，「产了 234 条、导进来 230 条」不会有人发现。"""
        us = M.parse(JSONL + '{"kind":"image", 坏掉的\n')
        self.assertEqual(len(us), 4, "坏行也要占一条，不能悄悄少一条")
        iss = M.audit(us)
        self.assertIn("UNIT_INCOMPLETE", [i["code"] for i in iss])
        self.assertTrue(any("第 4 行" in i["msg"] for i in iss),
                        "要报出是第几行坏的")

    def test_a_video_without_episode_or_seg_is_reported(self):
        """★ 拼接靠 episode/seg 分集和排序 —— 没有它成片凑不起来。"""
        bad = ('{"kind":"video","key":"随便","filename":"x.mp4","duration":15,'
               '"prompt":"正文"}')
        self.assertTrue(any("episode" in m for m in M.parse(bad)[0]["missing"]))

    def test_comments_and_blank_lines_are_tolerated(self):
        us = M.parse("# 说明\n\n" + JSONL + "\n// 尾注\n")
        self.assertEqual(len(us), 3)


class SpecTests(unittest.TestCase):
    """契约必须和解析器自洽 —— 它自己的示例得吃得回来。"""

    def test_the_spec_example_parses(self):
        """契约示例必须是**一份完全合格的最小材料** —— 抄下来就能过。

        照它产出来的东西要合格，它自己先得合格。示例里申报 3 条而实际 2 条、
        或者引了一个自己没产出的 key，都会教出同样的毛病来。
        """
        from core import matspec as S
        raw = M.parse(S.jsonl_schema())
        # 视频带**两张**骨架 —— 样例给一张的话，codex 照样例产就是一张，
        # 而那正是「视频只有一个参考图」的来处（样例本身在教它）。
        self.assertEqual([u["kind"] for u in raw],
                         ["manifest", "image", "image", "image", "video"])
        self.assertEqual(len(M.units_of(raw)[-1]["spine"]), 2)
        for u in M.units_of(raw):
            self.assertEqual(u["missing"], [], f"契约示例自己都不合格：{u}")
        self.assertEqual(M.audit(raw, {"image": 6, "video": 7}), [],
                         "契约示例过不了它自己声明的验收")
        built = M.build(raw)
        self.assertEqual(built["skipped"], [])
        self.assertEqual(len(built["tasks"]["video_tasks"][0]
                             ["storyboard_refs"]), 2)

    def test_the_spec_says_the_real_limits(self):
        from core import matspec as S
        self.assertIn("出图一次最多 8 张", S.render({"image": 8, "video": 9}))

    def test_it_does_not_invent_a_limit(self):
        """★ 没配服务商时给个数字就是在猜 —— 而 codex 会照着那个数字产。"""
        from core import matspec as S
        self.assertIn("给不出具体数字", S.render(None))

    def test_the_spec_lists_every_audit_rule(self):
        """★ 让它**产之前**就知道会被怎么查 —— 事后才说等于白产一遍。"""
        from core import matspec as S
        txt = S.render({"image": 9, "video": 9})
        for name, _why in S.AUDIT:
            self.assertIn(name, txt)

    def test_the_spec_is_generated_not_handwritten(self):
        """★ 手写的规范和实际要求一定会飘：改了 produce 忘了改文档，

        然后 codex 照着过期规范产一份，导入时才发现。
        """
        from core import matspec as S
        import inspect
        self.assertIn("从代码生成", inspect.getdoc(S) or "")
        self.assertIn("从代码现算出来的", S.render(None))


class ContractComplianceTests(unittest.TestCase):
    """「codex 没照契约产」是一整类，不是一个 bug。

    用户原话（2026-08-25）：「同一个 key 出现两次的本质是 codex 没有正常按照
    你的需要给出」。对 —— 所以这一类要当一道门来做，而且**不自动兜**：
    不去重、不补号、不挑一条留下。自动兜过去正是这个项目里最贵的那类 bug。
    """

    def _rows(self, *rows):
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)

    def _img(self, key, fn=None, **kw):
        d = {"kind": "image", "key": key, "filename": fn or (key + ".png"),
             "size": "9:16", "prompt": "正文"}
        d.update(kw)
        return d

    def _codes(self, text, limits=None):
        us = M.parse(text)
        return [i["code"] for i in M.audit(us, limits)]

    def test_the_same_key_twice_is_refused(self):
        """★ 提示词按 key 落盘 —— 重号是**静默覆盖**，被盖那条永远丢了。

        以前：验收 0 条问题，建出 2 条任务却只落 1 份提示词，
        其中一条任务读的是别人的提示词，出图出得来、标成功，画面不对。
        """
        codes = self._codes(self._rows(self._img("A__C001"),
                                       self._img("A__C001")))
        self.assertIn("KEY_DUPLICATE", codes)

    def test_it_does_not_quietly_dedupe(self):
        """★ 重号不许自动去重 —— 去重就是替 codex 做判断，而它产错了这件事会消失。"""
        us = M.parse(self._rows(self._img("A__C001"), self._img("A__C001")))
        self.assertEqual(len(M.build(us)["tasks"]["asset_tasks"]), 2)

    def test_two_rows_sharing_a_filename_is_refused(self):
        """★ 产物落同一个路径 → 后出的盖先出的，而「做过没有」看产物在不在。"""
        codes = self._codes(self._rows(self._img("A__C001", "same.png"),
                                       self._img("A__C002", "same.png")))
        self.assertIn("FILENAME_DUPLICATE", codes)

    def test_declaring_more_than_it_gave(self):
        """★ 少产是**唯一**靠内部自洽查不出来的 —— 剩下的条目全绿。"""
        codes = self._codes(self._rows(
            {"kind": "manifest", "total": 3, "image": 3},
            self._img("A__C001"), self._img("A__C002")))
        self.assertIn("MANIFEST_MISMATCH", codes)

    def test_the_missing_ones_are_otherwise_perfectly_consistent(self):
        """★ 证明上一条的必要性：没有申报头时，同样的材料一句话都说不出来。"""
        codes = self._codes(self._rows(self._img("A__C001"),
                                       self._img("A__C002")))
        self.assertNotIn("MANIFEST_MISMATCH", codes)
        self.assertEqual([c for c in codes if c != "MANIFEST_MISSING"], [])

    def test_a_missing_manifest_only_warns(self):
        """没有申报头是 warn 不是 error —— 已经产出来的老材料不能变成死路。"""
        us = M.parse(self._rows(self._img("A__C001")))
        got = [i for i in M.audit(us) if i["code"] == "MANIFEST_MISSING"]
        self.assertEqual([i["level"] for i in got], ["warn"])

    def test_md_material_is_not_asked_for_a_manifest(self):
        """md 那条路压根没有申报头这回事，别去要求它。"""
        md = ("### 【生产序号 1】\n建议保存文件名：X__CHAR_001_R01.png\n"
              "【完整可复制 production_prompt】\n正文\n【完成后返回】\n")
        codes = self._codes(md)
        self.assertNotIn("MANIFEST_MISSING", codes)

    def test_per_episode_segment_count(self):
        """★ 申报每集 2 段而只给 1 段 —— 段号连续那一条查不出来（SEG01 不缺号）。"""
        vid = {"kind": "video", "key": "EP01-SEG01", "episode": "EP01",
               "seg": "SEG01", "filename": "v.mp4", "prompt": "正文",
               "storyboard_refs": [{"image_n": 1, "key": "A__SB01"}]}
        codes = self._codes(self._rows(
            {"kind": "manifest", "segs_per_episode": {"EP01": 2}},
            self._img("A__SB01"), vid))
        self.assertIn("MANIFEST_MISMATCH", codes)
        self.assertNotIn("SEG_GAP", codes)

    def test_a_video_without_a_spine_is_refused(self):
        """★ 视频那一层读 storyboard_refs，空的话到出片才报「缺故事板」。"""
        us = M.parse(self._rows(
            {"kind": "video", "key": "EP01-SEG01", "episode": "EP01",
             "seg": "SEG01", "filename": "v.mp4", "prompt": "正文"}))
        self.assertTrue(any("storyboard_refs" in m
                            for m in M.units_of(us)[0]["missing"]))

    def test_an_unknown_kind_is_not_guessed_to_be_an_image(self):
        """★ 猜成图片的话，一条视频会被当资产图出掉 —— 出得来、标成功、成片少一段。"""
        us = M.parse(self._rows({"kind": "asset", "key": "Z1",
                                 "filename": "z.png", "prompt": "正文"}))
        u = M.units_of(us)[0]
        self.assertEqual(u["kind"], "")
        self.assertTrue(any("kind" in m for m in u["missing"]))
        built = M.build(us)
        self.assertEqual(
            sum(len(v) for v in built["tasks"].values() if isinstance(v, list)),
            0)
        self.assertEqual(len(built["skipped"]), 1)

    def test_the_manifest_never_becomes_a_task(self):
        """申报头没有提示词也没有产物 —— 混进任务里就是一条永远做不完的活。"""
        us = M.parse(self._rows({"kind": "manifest", "total": 1},
                                self._img("A__C001")))
        built = M.build(us)
        self.assertEqual(len(built["tasks"]["asset_tasks"]), 1)
        self.assertEqual(M.summary(us)["total"], 1)

    def test_the_reproduce_list_names_the_bad_ones(self):
        """整份不导，但要能只让 codex 补坏的那几条 —— 234 条里坏 3 条不该重产整份。"""
        us = M.parse(self._rows(self._img("A__C001"), self._img("A__C001")))
        txt = M.reproduce_request(us, M.audit(us))
        self.assertIn("KEY_DUPLICATE", txt)
        self.assertIn("A__C001", txt)
        self.assertIn("整份没有导入", txt)

    def test_the_reproduce_list_repeats_what_cannot_be_checked(self):
        """残篇程序查不了（用户：「如果模型能力足够是不会出现残篇的」）——
        那就在清单里明写一句，靠它保证，而不是留一条抓不准的警告。"""
        us = M.parse(self._rows(self._img("A__C001"), self._img("A__C001")))
        txt = M.reproduce_request(us, M.audit(us))
        self.assertIn("同上", txt)

    def test_the_contract_declares_every_check(self):
        """★ 检查和声明必须同改 —— 只加检查不加声明，
        等于让 codex 在不知道规则的情况下被判不合格。"""
        from core import matspec as S
        txt = S.render({"image": 6, "video": 7})
        for word in ("key 全局唯一", "filename 全局唯一", "和申报头对得上",
                     "视频必须有骨架", "kind 只认三个值"):
            self.assertIn(word, txt, f"契约里没写「{word}」")


class PerSystemContractTests(unittest.TestCase):
    """契约按体系出两份（用户：「需要按照项目体系来给两份契约」）。

    同一份发给两套的代价很具体：通用十二环节压根没有场景状态图这一步，
    契约里写着它，codex 就会产一批这套体系用不上的东西 ——
    而且它还占着参考图的名额。
    """

    def test_the_general_system_has_no_scene_state(self):
        from core import matspec as S
        v61 = S.render({"image": 6}, "剧", "v61")
        self.assertNotIn("03b_场景状态图", v61)
        self.assertNotIn("场景状态提示词", v61)
        self.assertIn("没有场景状态图", v61)

    def test_the_general_system_has_only_its_six_asset_families(self):
        """★ 通用版自己只建六个资产目录（stages._CAT_DIR）——
        造型/服饰/载具/特效那几类产了它用不上。"""
        from core import matspec as S
        v61 = S.render({"image": 6}, "剧", "v61")
        for gone in ("人物造型资产", "服饰资产", "载具资产", "特效资产"):
            self.assertNotIn(gone, v61, f"通用版契约里不该有 {gone}")
        for keep in ("人物身份资产", "场景资产", "道具资产",
                     "连续状态资产", "群体资产", "生物资产"):
            self.assertIn(keep, v61)

    def test_the_family_table_matches_where_things_actually_land(self):
        """★ 契约说的落点必须就是实际落点 —— 说一套落一套是最贵的那种错。"""
        from core import matspec as S
        for sid in ("v34", "v61"):
            for fam, where in S.SYSTEMS[sid]["families"]:
                key = "PRJ__" + fam.split(" / ")[0] + "_001_R01"
                u = {"stem": key, "canonical_id": key, "kind": "image",
                     "filename": key + ".png", "episode": "", "seg": ""}
                got = M.out_path(u)
                self.assertIn(where.split("/")[-1], got,
                              f"{sid} 契约说 {fam} → {where}，实际落 {got}")

    def test_the_cinematic_contract_keeps_scene_state(self):
        from core import matspec as S
        self.assertIn("03b_场景状态图", S.render({"image": 6}, "剧", "v34"))

    def test_an_unknown_system_falls_back_instead_of_crashing(self):
        from core import matspec as S
        self.assertIn("生产材料契约", S.render(None, "", "什么鬼"))


class DeclaredParamsTests(unittest.TestCase):
    """项目参数由材料申报，**程序不拿它当限制**。

    用户原话（2026-08-25）：「项目参数也写进契约但是是他给你的
    不能做任何的限制」。和申报头同一个原则：查「你有没有兑现你自己说的」，
    不是「你符不符合我设的」。
    """

    def _mat(self, params):
        rows = [{"kind": "manifest", "total": 1, "params": params},
                {"kind": "video", "key": "EP01-SEG01", "episode": "EP01",
                 "seg": "SEG01", "filename": "v.mp4", "prompt": "正文",
                 "storyboard_refs": [{"image_n": 1, "key": "A__SB01"}]}]
        return M.parse("\n".join(json.dumps(r, ensure_ascii=False)
                                 for r in rows))

    def test_the_material_wins_over_the_project_settings(self):
        """★ 反过来的失败样子很难看：提示词按 20 秒写的，派出去的活是 15 秒，
        片子和提示词对不上而不报错。"""
        us = self._mat({"ratio": "4:5", "seg_duration": 20})
        got = M.build(us, ratio="9:16", duration=15)
        self.assertEqual(got["tasks"]["video_tasks"][0]["params"],
                         {"duration": 20, "ratio": "4:5"})

    def test_a_mismatch_is_not_an_error(self):
        """★ 不夹、不改、不拦 —— 它按剧情定的数不是给我们改的。"""
        us = self._mat({"seg_duration": 20})
        codes = [i["code"] for i in M.audit(us)]
        self.assertNotIn("MANIFEST_MISMATCH", codes)

    def test_a_per_unit_value_still_wins_over_the_manifest(self):
        """每条自己写了 duration 的，比申报头更近 —— 那是这一段的实际长度。"""
        rows = [{"kind": "manifest", "total": 1,
                 "params": {"seg_duration": 20}},
                {"kind": "video", "key": "EP01-SEG01", "episode": "EP01",
                 "seg": "SEG01", "filename": "v.mp4", "prompt": "正文",
                 "duration": 8,
                 "storyboard_refs": [{"image_n": 1, "key": "A__SB01"}]}]
        us = M.parse("\n".join(json.dumps(r, ensure_ascii=False)
                               for r in rows))
        self.assertEqual(M.build(us)["tasks"]["video_tasks"][0]
                         ["params"]["duration"], 8)

    def test_no_manifest_params_falls_back_to_the_project(self):
        us = self._mat({})
        got = M.build(us, ratio="16:9", duration=12)
        self.assertEqual(got["tasks"]["video_tasks"][0]["params"],
                         {"duration": 12, "ratio": "16:9"})

    def test_what_was_actually_used_is_reported(self):
        """★ 盖过项目参数可以，但不能悄悄地盖 ——
        否则「我明明设了 15 秒」这类问题永远查不到源头。"""
        got = M.build(self._mat({"seg_duration": 20}), duration=15)
        self.assertEqual(got["params"]["seg_duration"], 20)

    def test_the_contract_says_it_does_not_constrain(self):
        from core import matspec as S
        txt = S.render(None, "", "v34")
        self.assertIn("不拿它当限制", txt)
        self.assertIn("不夹、不改、不拦", txt)

    def test_the_system_is_written_into_tasks(self):
        """写死 "material" 的话，第一个来读这个字段的人就会挑错体系。"""
        got = M.build(self._mat({}), system="v61")
        self.assertEqual(got["tasks"]["system"], "v61")
        self.assertTrue(got["tasks"]["from_material"])


class VideoLandsInTheVideoFolderTests(unittest.TestCase):
    """★ 契约要求视频的 key 是 `EP01-SEG01`（拼接按这个前缀挑本集分段），
    而那个形状里没有 `VIDEO` 家族前缀。

    光按前缀猜的话每段视频都落进 `02_固定资产/其它资产/` —— 出片全成功、
    任务全绿，而拼接在 `05_分段视频` 里一个文件都找不到，
    报「这一集没有分段」。契约说的落点和实际落点对不上，是这里最贵的错。
    """

    def _vid(self, key, fn="v.mp4"):
        return {"no": 1, "stem": key, "canonical_id": key, "kind": "video",
                "filename": fn, "episode": "EP01", "seg": "SEG01"}

    def test_the_contract_shaped_key_still_lands_in_05(self):
        self.assertEqual(M.out_path(self._vid("EP01-SEG01")),
                         "05_分段视频/v.mp4")

    def test_the_long_canonical_key_also_lands_in_05(self):
        self.assertEqual(
            M.out_path(self._vid("PRJ__VIDEO_EP01_SEG01_R01",
                                 "PRJ__VIDEO_EP01_SEG01_R01.mp4")),
            "05_分段视频/PRJ__VIDEO_EP01_SEG01_R01.mp4")

    def test_its_prompt_follows_it(self):
        u = self._vid("EP01-SEG01")
        self.assertEqual(M.prompt_path(u, M.out_path(u)),
                         "03_提示词/视频提示词/EP01-SEG01_PROMPT.txt")

    def test_end_to_end_from_the_contract_shape(self):
        rows = [{"kind": "image", "key": "A__SBSHEET_EP01_SEG01_A_R01",
                 "filename": "sb.png", "prompt": "板"},
                {"kind": "video", "key": "EP01-SEG01", "episode": "EP01",
                 "seg": "SEG01", "filename": "v.mp4", "prompt": "片",
                 "storyboard_refs": [{"image_n": 1,
                                      "key": "A__SBSHEET_EP01_SEG01_A_R01"}]}]
        us = M.parse("\n".join(json.dumps(r, ensure_ascii=False)
                               for r in rows))
        v = M.build(us)["tasks"]["video_tasks"][0]
        self.assertTrue(v["output"].startswith("05_分段视频/"), v["output"])
        self.assertTrue(v["prompt_ref"].startswith("03_提示词/视频提示词/"))


class DeliveryTests(unittest.TestCase):
    """材料怎么交进来。

    用户原话（2026-08-25）：「应该也不用，实际上生产完丢进去 exe 来解析后
    就会到指定文件夹了」—— 对，codex 不需要知道项目在哪。
    契约只说「交一个 jsonl」，落盘和分发是程序的事。
    """

    def test_the_contract_says_only_one_file_comes_back(self):
        from core import matspec as S
        txt = S.render({"image": 6}, "剧", "v34")
        self.assertIn("生产材料.jsonl", txt)
        self.assertIn("不用建任何目录", txt)

    def test_the_contract_carries_no_machine_path(self):
        """★ 契约是要发出去的东西，别把本机路径写进去 ——
        换台机器就是错的，而它长得像对的。"""
        from core import matspec as S
        txt = S.render({"image": 6}, "剧", "v34")
        for bad in ("C:\\", "D:\\", "/Users/", "/home/"):
            self.assertNotIn(bad, txt)

    def test_the_scan_finds_what_the_contract_asks_for(self):
        """★ 契约推荐 JSONL，扫描口只认 md 的话 —— codex 照契约交了
        `生产材料.jsonl`，用户点扫描得到「这个目录里没有 md」。
        文件就在眼前，而程序说没有。"""
        import inspect
        from server import app as A
        src = inspect.getsource(A.api_post)
        i = src.index('/api/material/scan')
        blk = src[i:i + 900]
        self.assertIn(".jsonl", blk)


class ScanTests(unittest.TestCase):
    """约定目录扫描。**原来一有文件就崩** —— 用了 `time.strftime` 而
    app.py 模块级没导 `time`：空目录返回正常、放了材料反而 NameError，
    页面只看到一个「500」。空目录是唯一被走通过的路径，所以一直没人发现。
    """

    def _proj(self, files: dict):
        import tempfile
        from core.store import Project
        root = tempfile.mkdtemp()
        pj = Project(root); pj.init_dirs()
        pj.save_meta({"project_name": "扫描", "system": "v34"})
        d = pj.p("00_生产材料")
        os.makedirs(d, exist_ok=True)
        for name, body in files.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(body)
        return root

    def test_a_folder_with_a_file_does_not_crash(self):
        from server import app as A
        root = self._proj({"生产材料.jsonl":
                           '{"kind":"image","key":"A","filename":"a.png",'
                           '"prompt":"p"}'})
        r = A.api_post("/api/material/scan", {"project_root": root})
        self.assertEqual([f["name"] for f in r["files"]], ["生产材料.jsonl"])
        self.assertTrue(r["files"][0]["at"])

    def test_the_scanned_file_actually_imports(self):
        """★ 扫到了还得能导 —— 扫描给的 rel 要是 _read_material 认的形状。"""
        from server import app as A
        root = self._proj({"生产材料.jsonl":
                           '{"kind":"image","key":"A__C001",'
                           '"filename":"c.png","prompt":"正文"}'})
        r = A.api_post("/api/material/scan", {"project_root": root})
        got = A.api_post("/api/material/import",
                         {"project_root": root, "rel": r["files"][0]["rel"]})
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["counts"]["asset_tasks"], 1)

    def test_md_still_shows_up(self):
        from server import app as A
        root = self._proj({"老材料.md": "### 【生产序号 1】\n"})
        r = A.api_post("/api/material/scan", {"project_root": root})
        self.assertEqual([f["name"] for f in r["files"]], ["老材料.md"])


class CombinedPackageTests(unittest.TestCase):
    """综合包（不给 --system 打的那个）里，两套体系都能建项目 ——
    所以契约还是两份，跟的是**项目**的体系，不是 exe。

    两套体系的代码本来就始终都打进包里（`打包exe.py` 的注释说清了：
    真裁掉另一套，拿错包打开老项目会把产物全判成「还没做」，重跑花第二份钱）。
    单体系包限制的只是「新建项目能选哪套」。
    """

    def test_the_contract_follows_the_project_not_the_exe(self):
        """★ 同一个包、两个项目，各拿到自己那一份。"""
        import tempfile
        from core.store import Project
        from server import app as A
        got = {}
        for sid in ("v34", "v61"):
            root = tempfile.mkdtemp()
            pj = Project(root); pj.init_dirs()
            pj.save_meta({"project_name": "剧", "system": sid})
            r = A.api_post("/api/material/spec",
                           {"project_root": root, "save": True})
            got[sid] = r
            self.assertEqual(r["system"], sid)
        self.assertIn("03b_场景状态图", got["v34"]["text"])
        self.assertNotIn("03b_场景状态图", got["v61"]["text"])
        self.assertNotEqual(got["v34"]["saved"], got["v61"]["saved"])

    def test_without_a_project_the_page_choice_decides(self):
        """★ 没开项目时后端回落 NEW_SYSTEM（电影级）——
        综合包里你选着「通用十二环节」却会导出电影级那份，
        里面写着场景状态图和造型/服饰/载具/特效。
        单体系包里回落永远对，所以这个坑只在综合包里出现。
        """
        from server import app as A
        r = A.api_post("/api/material/spec", {"system": "v61"})
        self.assertEqual(r["system"], "v61")
        self.assertNotIn("03b_场景状态图", r["text"])

    def test_the_page_sends_its_choice(self):
        """★ 后端支持了不算完 —— 页面不发这个值，支持了也用不上。"""
        from core.store import read_text
        html = read_text("web/index.html")
        i = html.index("/api/material/spec")
        self.assertIn("defaultSystem()", html[i:i + 400])


class TwoSchemasTests(unittest.TestCase):
    """材料的字段和任务的字段是**两套**，名字不一样，不能混着写。

    codex 拿到契约后实测指出的：示例用顶层 `filename` / `size` / `duration` /
    `ratio`，而字段说明写的是 `output` / `params.size` / `params.duration`；
    示例参考图用 `key`，说明又写 `asset_id`。它的原话：
    「可能生成『语法正确、程序不认』的文件」。

    根因：`NEEDS` 是从 `produce` 的 `task.get` 现算的 —— 那是**导入之后**任务
    的形状，被当成「每一类各要什么」写进了契约。
    """

    def _schema(self):
        from core import matspec as S
        return json.loads(S.json_schema())

    def _branch(self, sch, kind):
        got = [o for o in sch["oneOf"]
               if o["properties"]["kind"].get("const") == kind]
        self.assertEqual(len(got), 1, f"{kind} 的分支不唯一")
        return got[0]

    def test_the_material_table_never_mentions_task_field_names(self):
        """★ 这四个名字只属于任务那一套，出现在「你要写的字段」里就是歧义。"""
        from core import matspec as S
        written = {f for rows in S.MATERIAL_FIELDS.values()
                   for f, _n, _w in rows} | {f for f, _n, _w in S.REF_FIELDS}
        for task_only in ("output", "prompt_ref", "params.size",
                          "params.duration", "asset_id"):
            self.assertNotIn(task_only, written)

    def test_the_contract_says_which_section_is_authoritative(self):
        from core import matspec as S
        txt = S.render({"image": 6}, "剧", "v34")
        self.assertLess(txt.index("## 你要写的字段"),
                        txt.index("## 程序会把它变成什么"))
        self.assertIn("不是让你写的", txt)

    def test_every_documented_field_is_actually_read(self):
        """★ 文档里写的字段，解析器必须真的认 —— 这是「契约从代码生成」
        的全部意义。写了个解析器不读的字段，codex 填了也没用，而且不报错。"""
        from core import matspec as S
        probe = {
            "kind": "image", "key": "A__C001", "filename": "c.png",
            "prompt": "正文", "size": "9:16", "name": "名",
            "family": "CHAR",
            "reference_images": [{"image_n": 1, "key": "A__C002",
                                  "who": "谁", "controls": "甲",
                                  "not_controls": "乙", "scope": "丙"}],
        }
        u = M.units_of(M.parse(json.dumps(probe, ensure_ascii=False)))[0]
        self.assertEqual(u["stem"], "A__C001")
        self.assertEqual(u["filename"], "c.png")
        self.assertEqual(u["prompt"], "正文")
        self.assertEqual(u["ratio"], "9:16")
        self.assertEqual(u["goal"], "名")
        self.assertEqual(u["refs"], [(1, "A__C002")])

    def test_asset_id_is_still_accepted_as_an_alias(self):
        """契约说 `asset_id` 是 `key` 的同义写法 —— 说了就得认。"""
        row = {"kind": "image", "key": "A", "filename": "a.png",
               "prompt": "p",
               "reference_images": [{"image_n": 1, "asset_id": "B"}]}
        u = M.units_of(M.parse(json.dumps(row, ensure_ascii=False)))[0]
        self.assertEqual(u["refs"], [(1, "B")])

    def test_ratio_is_accepted_where_size_is_documented(self):
        for name in ("size", "ratio"):
            row = {"kind": "image", "key": "A", "filename": "a.png",
                   "prompt": "p", name: "4:5"}
            u = M.units_of(M.parse(json.dumps(row, ensure_ascii=False)))[0]
            self.assertEqual(u["ratio"], "4:5", name)

    def test_the_schema_branches_are_discriminated_by_kind(self):
        """★ 三个分支都用 enum 的话，一条 image 也满足 manifest 分支
        （它只要求 kind），oneOf 匹配到两个 → 校验器判不合法。"""
        sch = self._schema()
        for kind in ("manifest", "image", "video"):
            self.assertEqual(
                self._branch(sch, kind)["properties"]["kind"], {"const": kind})

    def test_the_sample_validates_against_the_schema(self):
        """★ 样例、schema、解析器三样必须自洽 —— 它们是同一张表算出来的。"""
        from core import matspec as S
        sch = self._schema()
        for ln in S.jsonl_schema().splitlines():
            row = json.loads(ln)
            br = self._branch(sch, row["kind"])
            for need in br["required"]:
                self.assertIn(need, row, f"{row['kind']} 样例缺必填 {need}")
            for got in row:
                self.assertIn(got, br["properties"],
                              f"{row['kind']} 样例的 {got} 不在 schema 里")

    def test_the_required_video_fields_match_what_the_parser_demands(self):
        """★ schema 说必填的，解析器缺了要真的报缺 —— 否则「校验通过、导入报错」。"""
        sch = self._schema()
        base = {"kind": "video", "key": "EP01-SEG01", "episode": "EP01",
                "seg": "SEG01", "filename": "v.mp4", "prompt": "p",
                "storyboard_refs": [{"image_n": 1, "key": "A"}]}
        for need in self._branch(sch, "video")["required"]:
            if need == "kind":
                continue
            row = {k: v for k, v in base.items() if k != need}
            u = M.units_of(M.parse(json.dumps(row, ensure_ascii=False)))[0]
            self.assertTrue(u["missing"], f"缺 {need} 却没报缺")
