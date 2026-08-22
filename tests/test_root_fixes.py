# -*- coding: utf-8 -*-
"""五项根源修复的回归测试（通用版 12 环节生产线）。

对应实跑报错包里反复出现的五类失败：

  ① LLM 输出的 JSON 字符串里有裸换行 → 以前整段判 LLM_SCHEMA_FAIL，
     重试三次全烧掉；现在 strict=False 自愈 + 日志留痕。
  ② 参考图引用写成 `C001+C002` 拼接 ID → 以前走 GHOST_REF，任务出不了图；
     现在装配时拆开（全部段真实存在才拆），提示词正文映射块同步重写。
  ③ 提示词正文里 Image 编号错位 → 以前硬停等人改；现在以上传列表为准
     自动纠正（缺编号/多写仍报错，那两种没有唯一正确答案）。
  ④ 环节5 漏写几个资产的提示词 → 以前这些资产永远没有图（claim 占着，
     同一趟重跑也补不上）；现在跑完自检、拿漏单当场补一轮。
  ⑤ upstream_rejected 被当成内容拒绝直接进降级改写 → 里面既有真被审的
     也有上游抖一下的；现在先裸重试一次，复现才进改写。
"""
import os
import shutil
import tempfile
import unittest

from core import llm, produce, soften, stages
from core.store import Project, read_text


def refs(*ids):
    return [{"image_n": i + 1, "asset_id": a, "file_ref": f"x/{a}.png"}
            for i, a in enumerate(ids)]


def _err_upstream():
    e = RuntimeError("任务失败：upstream rejected")
    e.err_code = "upstream_rejected"
    return e


# --------------------------------------------------------------------- ① JSON
class BareNewlineRescueTests(unittest.TestCase):

    def test_bare_newline_inside_fenced_json_is_rescued(self):
        msgs = []
        out = llm.extract_json('```json\n{"a": "第一行\n第二行"}\n```',
                               log=msgs.append)
        self.assertEqual(out, {"a": "第一行\n第二行"})
        self.assertTrue(any("宽松模式" in m for m in msgs), "救回来了要留痕")

    def test_bare_newline_without_fence_is_rescued(self):
        out = llm.extract_json('{"a": "x\ny", "b": 1}')
        self.assertEqual(out, {"a": "x\ny", "b": 1})

    def test_real_format_errors_are_still_raised(self):
        """宽松模式只放宽控制字符：引号/括号的真错误一个不许放行。"""
        with self.assertRaises(Exception):
            llm.extract_json('```json\n{"a": "unterminated\n```')
        with self.assertRaises(Exception):
            llm.extract_json('{"a": 1,,}')

    def test_clean_json_takes_the_strict_path(self):
        """干净的 JSON 不该触发宽松日志（正常路径零行为变化）。"""
        msgs = []
        out = llm.extract_json('```json\n{"a": "b"}\n```', log=msgs.append)
        self.assertEqual(out, {"a": "b"})
        self.assertEqual(msgs, [])

    def test_extra_data_path_also_rescues_bare_newlines(self):
        """`_first_json` 那条路（JSON 后面多写了东西）同样要自愈。"""
        body = '{"a": "x\ny"}\n}}}'
        out = llm.extract_json(body)
        self.assertEqual(out, {"a": "x\ny"})


# ------------------------------------------------------------- ② 拼接 ID 拆分
class SplitJoinedRefsTests(unittest.TestCase):

    def test_joined_id_splits_when_all_parts_exist(self):
        amap = {"C001": {}, "C002": {}}
        out, joined = stages._split_joined_refs(["C001+C002", "S001"], amap)
        self.assertEqual(out, ["C001", "C002", "S001"])
        self.assertEqual(joined, [{"orig": "C001+C002",
                                   "parts": ["C001", "C002"]}])

    def test_full_width_separators_are_recognized(self):
        amap = {"C001": {}, "C002": {}, "ST003": {}}
        for sep in ("＋", "、", "/", "，"):
            out, joined = stages._split_joined_refs([f"C001{sep}C002"], amap)
            self.assertEqual(out, ["C001", "C002"], f"分隔符 {sep} 没认出来")
            self.assertEqual(len(joined), 1)

    def test_unknown_part_keeps_the_joined_form(self):
        """★ 有一段查不到就不拆 —— 硬拆只会把「模型想引的东西本来就有错」
        藏起来，原样留着走 GHOST_REF 让人看到。"""
        out, joined = stages._split_joined_refs(["C001+XX9"], {"C001": {}})
        self.assertEqual(out, ["C001+XX9"])
        self.assertEqual(joined, [])

    def test_dict_refs_get_renumbered_after_split(self):
        refs_in = [{"asset_id": "C001"}, {"asset_id": "C002+ST003", "image_n": 2}]
        amap = {"C001": {}, "C002": {}, "ST003": {}}
        out, joined = stages._split_joined_refs(refs_in, amap)
        self.assertEqual([r["asset_id"] for r in out], ["C001", "C002", "ST003"])
        self.assertEqual([r["image_n"] for r in out], [1, 2, 3],
                         "拆开后编号必须整体重排，正文映射才能对上")

    def test_no_split_leaves_numbering_untouched(self):
        """没发生拆分时编号一律不动 —— 尊重「编号不重排」的既有约定。"""
        refs_in = [{"asset_id": "C001", "image_n": 5}]
        out, joined = stages._split_joined_refs(refs_in, {"C001": {}})
        self.assertEqual(out[0]["image_n"], 5)
        self.assertEqual(joined, [])

    def test_dedup_when_parts_overlap_existing_refs(self):
        out, _ = stages._split_joined_refs(
            ["C001", "C001+C002"], {"C001": {}, "C002": {}})
        self.assertEqual(out, ["C001", "C002"])


class SplitJoinedRefsInBuildTests(unittest.TestCase):
    """装配层的端到端：参考列表拆开 + 提示词正文映射块同步重写。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="split-refs-")
        self.pj = Project(self.root)
        self.pj.init_dirs()
        self.pj.save_stage("episodes", {"episodes": [{"episode": "EP01"}]})
        self.pj.save_stage("s1_global", {"visual_tone": {}})
        assets = [
            {"asset_id": "C002", "category": "identity", "name": "Rizky",
             "parent_asset_id": "", "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "S001", "category": "environment", "name": "病房",
             "parent_asset_id": "", "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "ST001", "category": "state", "name": "拔针后状态",
             "parent_asset_id": "C002",
             "reference_assets": ["C002+S001"],
             "output_spec": "state_asset", "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
        ]
        self.pj.save_stage("s4_assets", {"assets": assets}, "EP01")
        self.pj.save_stage("s5_asset_prompts", {"asset_prompts": [
            {"asset_id": "C002", "prompt": "c2", "reference_assets": []},
            {"asset_id": "S001", "prompt": "s1", "reference_assets": []},
            {"asset_id": "ST001", "prompt": "st1",
             "parent_asset_id": "C002", "reference_assets": ["C002+S001"]},
        ]}, "EP01")
        d = self.pj.p("03_提示词", "资产生产提示词")
        os.makedirs(d, exist_ok=True)
        for aid in ("C002", "S001"):
            with open(os.path.join(d, f"{aid}_PROMPT.txt"), "w",
                      encoding="utf-8") as f:
                f.write(aid)
        # 正文映射块是模型按拼接 ID 写的 —— 拆分后必须同步重写
        with open(os.path.join(d, "ST001_PROMPT.txt"), "w",
                  encoding="utf-8") as f:
            f.write("参考图角色映射：\nImage 1 = C002+S001 Rizky与病房\n"
                    "画面：手背特写。\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_build_splits_refs_and_rewrites_prompt_map(self):
        tasks = stages._build_tasks(self.pj, {"project_code": "T",
                                              "image_size": "1x1"})
        st = next(t for t in tasks["asset_tasks"] if t["key"] == "ST001")
        self.assertEqual([r["asset_id"] for r in st["reference_images"]],
                         ["C002", "S001"], "拼接 ID 没有拆开")
        self.assertTrue(all(r["file_ref"] for r in st["reference_images"]),
                        "拆开后每一段都要指得到文件")
        txt = read_text(self.pj.p("03_提示词", "资产生产提示词",
                                  "ST001_PROMPT.txt"))
        self.assertIn("Image 1 = C002　Rizky", txt)
        self.assertIn("Image 2 = S001　病房", txt)
        self.assertNotIn("C002+S001", txt, "正文里不该再有拼接写法")
        self.assertIn("画面：手背特写。", txt, "映射块之外的正文一个字都不能动")

    def test_build_is_idempotent(self):
        """装配每次都会跑：第二次跑不许再改文件（内容一致直接跳过）。"""
        stages._build_tasks(self.pj, {"project_code": "T", "image_size": "1x1"})
        p = self.pj.p("03_提示词", "资产生产提示词", "ST001_PROMPT.txt")
        first = read_text(p)
        mtime = os.path.getmtime(p)
        stages._build_tasks(self.pj, {"project_code": "T", "image_size": "1x1"})
        self.assertEqual(read_text(p), first)
        self.assertEqual(os.path.getmtime(p), mtime, "幂等：不许重复写盘")


# ------------------------------------------------------- ③ Image 映射自动纠正
class AutoFixImageMapTests(unittest.TestCase):

    def test_swapped_ids_are_corrected_in_place(self):
        p = "Image 1 = ST008 病房状态\nImage 2 = C002 Rizky"
        fixed, fixes = produce._auto_fix_image_map(p, refs("C002", "ST008"))
        self.assertIn("Image 1 = C002", fixed)
        self.assertIn("Image 2 = ST008", fixed)
        self.assertEqual(len(fixes), 2, "两行都错位，都要报出来")

    def test_full_width_punctuation_is_handled(self):
        for sep in ("=", "＝", ":", "："):
            fixed, fixes = produce._auto_fix_image_map(
                f"Image 1 {sep} C009 x", refs("C001"))
            self.assertNotIn("C009", fixed, f"分隔符 {sep} 没认出来")
            self.assertIn("C001", fixed)
            self.assertEqual(len(fixes), 1)

    def test_missing_number_is_not_invented(self):
        """正文没写 Image 2 → 没有唯一正确答案，不补，留给校验报错。"""
        fixed, fixes = produce._auto_fix_image_map("Image 1 = C001",
                                                   refs("C001", "C002"))
        self.assertEqual((fixed, fixes), ("Image 1 = C001", []))

    def test_extra_number_is_not_touched(self):
        """多写的编号不删 —— 里面混着「不出图但正文保留编号」的约定。"""
        p = "Image 1 = C001\nImage 2 = S003"
        fixed, fixes = produce._auto_fix_image_map(p, refs("C001"))
        self.assertEqual(fixed, p)
        self.assertEqual(fixes, [])

    def test_correct_map_is_untouched(self):
        p = "Image 1 = C001 林小雨\nImage 2 = S001 教室"
        fixed, fixes = produce._auto_fix_image_map(p, refs("C001", "S001"))
        self.assertEqual((fixed, fixes), (p, []))

    def test_fixed_prompt_passes_check_image_map(self):
        """纠正之后，原来的硬错误应该消失（闭环验证）。"""
        p = "Image 1 = ST008 病房\nImage 2 = C002 Rizky"
        want = refs("C002", "ST008")
        fixed, _ = produce._auto_fix_image_map(p, want)
        bad, _ = produce.check_image_map(fixed, want)
        self.assertEqual(bad, "", "自动纠正后不该再被 check_image_map 拦下")


# ------------------------------------------------------------ ⑤ upstream_rejected
UPSTREAM_ORIG = """镜头：中景，手持轻微晃动。
Image 1 = C001 林南桥
Image 2 = C007 李想
李想一刀捅进林南桥的腹部，刀刃没入。林南桥低头看着伤口，
血从指缝间大量涌出，浸透衬衫下摆，顺着裤腿滴在地砖上。
她扶着墙缓缓滑坐下去，眼神从震惊转为了然。李想后退半步，握刀的手在抖。"""

UPSTREAM_GOOD = UPSTREAM_ORIG.replace("血从指缝间大量涌出",
                                      "深色湿痕在指缝间迅速扩开")


class Chat:
    """假改写引擎：按给定的回复列表依次返回。"""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def chat(self, system, user, **kw):
        self.sent.append(user)
        r = self.replies[min(len(self.sent) - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


class UpstreamRejectedTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="upstream-")
        self.pj = Project(self.root)
        self.pj.init_dirs()
        self.seen = []

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _gen(self, times):
        def gen(prompt):
            self.seen.append(prompt)
            if len(self.seen) <= times:
                raise _err_upstream()
            return {"ok": True}
        return gen

    def test_transient_upstream_rejection_is_rescued_without_softening(self):
        """★ 上游抖一下：原样重发就过，不该花一次改写、更不该降级。"""
        llm = Chat(UPSTREAM_GOOD)
        out = soften.run_with_softening(
            self._gen(1), UPSTREAM_ORIG, pj=self.pj, llm=llm,
            kind="video", key="K1", log=lambda *a: None)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(self.seen), 2, "原样重发一次就该过")
        self.assertEqual(llm.sent, [], "不该进改写")

    def test_persistent_upstream_rejection_enters_softening(self):
        """★ 复现的 upstream_rejected 按审核拒收处理，进降级改写。"""
        llm = Chat(UPSTREAM_GOOD)
        out = soften.run_with_softening(
            self._gen(2), UPSTREAM_ORIG, pj=self.pj, llm=llm,
            kind="video", key="K1", rounds=1, log=lambda *a: None)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(llm.sent), 1, "改写引擎被调用一次")
        self.assertEqual(len(self.seen), 3, "原文+裸重试+改写版")
        self.assertIn("深色湿痕", self.seen[-1], "重发失败后要发改写版")

    def test_softened_version_also_gets_one_bare_retry(self):
        """改写版撞上 upstream_rejected 同样先裸重试，别直接算改写失败。"""
        llm = Chat(UPSTREAM_GOOD)
        soften.run_with_softening(
            self._gen(3), UPSTREAM_ORIG, pj=self.pj, llm=llm,
            kind="video", key="K1", rounds=1, log=lambda *a: None)
        # seen: 原文失败 → 裸重试失败 → 改写版失败 → 改写版裸重试成功
        self.assertEqual(len(self.seen), 4)

    def test_non_upstream_non_content_still_raises_unchanged(self):
        def gen(prompt):
            self.seen.append(prompt)
            raise RuntimeError("HTTP 524 A timeout occurred")
        with self.assertRaises(RuntimeError):
            soften.run_with_softening(
                gen, UPSTREAM_ORIG, pj=self.pj, llm=Chat(UPSTREAM_GOOD),
                kind="video", key="K1", log=lambda *a: None)
        self.assertEqual(len(self.seen), 1, "别的错误不该被裸重试")


# ------------------------------------------------------------- ④ 环节5 补漏
class FakeLLM:
    model = "fake"

    def __init__(self, *outs):
        self.outs = list(outs)
        self.calls = []

    def json_call(self, system, user, **kw):
        self.calls.append(user)
        out = self.outs[min(len(self.calls) - 1, len(self.outs) - 1)]
        if isinstance(out, Exception):
            raise out
        return out


class S5RefillTests(unittest.TestCase):

    def setUp(self):
        # _CLAIMED 是模块级全局：上一条测试成功写完的资产还占着，
        # 不清掉的话这条测试的 _s5_filter 会全跳过（和真实流水线
        # 每趟开跑前 reset_claims 的约定一致）。
        stages.reset_claims()
        self.root = tempfile.mkdtemp(prefix="s5-refill-")
        self.pj = Project(self.root)
        self.pj.init_dirs()
        self.pj.save_stage("episodes", {"episodes": [
            {"episode": "EP01", "script": "第一集正文。"}]})
        self.pj.save_stage("s1_global", {"visual_tone": {}})
        assets = [
            {"asset_id": "S001", "category": "environment", "name": "病房",
             "parent_asset_id": "", "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "C002", "category": "identity", "name": "Rizky",
             "parent_asset_id": "", "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "C005", "category": "identity", "name": "Dewi",
             "parent_asset_id": "", "reference_assets": [], "decision": "must",
             "used_by_segs": ["EP01-SEG01"]},
            {"asset_id": "SKIP1", "category": "prop", "name": "路人甲",
             "parent_asset_id": "", "reference_assets": [],
             "decision": "skip", "decision_reason": "一次性路人",
             "used_by_segs": ["EP01-SEG01"]},
        ]
        self.pj.save_stage("s4_assets", {"assets": assets}, "EP01")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _missing(self, merged, txts=()):
        for aid in txts:
            d = self.pj.p("03_提示词", "资产生产提示词")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{aid}_PROMPT.txt"), "w",
                      encoding="utf-8") as f:
                f.write(aid)
        return stages._s5_missing_after_run(self.pj, merged, "EP01")

    def test_missing_means_json_and_txt_both_absent(self):
        merged = {"asset_prompts": [{"asset_id": "S001"}]}
        # C002 有 txt（跨集共享写过）→ 不算漏；C005 两个都没有 → 漏
        self.assertEqual([a["asset_id"] for a in self._missing(merged, ("C002",))],
                         ["C005"])

    def test_skip_assets_are_never_missing(self):
        self.assertEqual(
            [a["asset_id"] for a in self._missing({"asset_prompts": []})],
            ["S001", "C002", "C005"])

    def test_present_in_json_is_not_missing(self):
        merged = {"asset_prompts": [{"asset_id": "S001"},
                                    {"asset_id": "C002"},
                                    {"asset_id": "C005"}]}
        self.assertEqual(self._missing(merged), [])

    def test_run_s5_refills_assets_the_llm_left_out(self):
        """★ 端到端：第一轮漏写 C005 → 自检发现 → 补一轮 → 三个都有。"""
        llm = FakeLLM(
            {"asset_prompts": [
                {"asset_id": "S001", "prompt": "room", "reference_assets": []},
                {"asset_id": "C002", "prompt": "rizky", "reference_assets": []},
            ]},
            {"asset_prompts": [
                {"asset_id": "C005", "prompt": "dewi", "reference_assets": []},
            ]},
        )
        logs = []
        out = stages.run_llm_stage(self.pj, "s5", llm,
                                   {"project_code": "T", "script": "x",
                                    "episode": "EP01"},
                                   log=logs.append, episode="EP01")
        self.assertEqual(len(llm.calls), 2, "漏了要当场再跑一轮")
        got = {ap["asset_id"] for ap in out["asset_prompts"]}
        self.assertEqual(got, {"S001", "C002", "C005"})
        self.assertTrue(any("漏写" in m for m in logs), "补漏要留下日志")
        # txt 三个都要落盘（补漏轮的 fresh 也走同一条落盘路）
        d = self.pj.p("03_提示词", "资产生产提示词")
        for aid in ("S001", "C002", "C005"):
            self.assertTrue(os.path.isfile(os.path.join(d, f"{aid}_PROMPT.txt")),
                            f"{aid} 的提示词 txt 没落盘")

    def test_refill_round_does_not_recurse(self):
        """补漏那轮再漏也不许递归补 —— 记诊断让人来，防死循环。"""
        llm = FakeLLM(
            {"asset_prompts": [{"asset_id": "S001", "prompt": "room",
                                "reference_assets": []}]},
            {"asset_prompts": [{"asset_id": "C002", "prompt": "rizky",
                                "reference_assets": []}]},
        )       # 两轮都漏 C005
        stages.run_llm_stage(self.pj, "s5", llm,
                             {"project_code": "T", "script": "x",
                              "episode": "EP01"},
                             log=lambda *a: None, episode="EP01")
        self.assertEqual(len(llm.calls), 2, "只补一轮，不许递归")
