# -*- coding: utf-8 -*-
"""不出图的资产被当成参考图 —— 等一个永远不会出现的文件。

用户实遇（0818_1610 那次全流程）：

    LK002  参考图不存在或者是个空文件：02_固定资产/服饰资产/CST002_R01.png

而 38 条执行记录里**只有 CST001 ok，没有 CST002 的任何痕迹** ——
不是失败，是压根没跑。查 n4 产物就明白了：

    CST002  decision='logical_only'
            decision_reason='普通成年日常服装，未命中关键服装物化触发器，
                              使用文字合同即可。'
    LK002   decision='must'  reference_assets=['PH002', 'CST002']

n4 判得对：skill 第七章那张表里 `logical_only` 一栏「出图：否」，
简单服装走文字契约。错的是装配这一层 —— 它把 `reference_assets` 里
每一项都变成参考**图**，于是给 LK002 派了一张不存在的文件。

这一类最难查的地方在于**缺的那一环没有失败记录**：
`_why_ref_missing` 翻遍失败记录也说不出 CST002 为什么缺，因为它从来没被派过。
所以修法不只是挑掉它，还要把「为什么没传这一张」说出来。
"""
import unittest

from core import produce as P
from core import run_v34 as R


class _Reg:
    """够 asset_out 用的假项目。"""

    root = "."

    def stage_data(self, *a, **kw):
        return {}

    def p(self, *parts):
        return "/".join(parts)


def _amap():
    return {
        "PH002": {"asset_id": "PH002", "family": "PHOTO", "decision": "must"},
        "CST001": {"asset_id": "CST001", "family": "COST", "decision": "must"},
        "CST002": {"asset_id": "CST002", "family": "COST",
                   "decision": "logical_only",
                   "decision_reason": "普通成年日常服装，使用文字合同即可。"},
    }


class SplitRefsTests(unittest.TestCase):

    def setUp(self):
        self.pj = _Reg()
        self.amap = _amap()

    def test_a_logical_only_asset_is_not_a_reference_image(self):
        """★ 这就是那个 bug。"""
        keep, no_img = R.split_refs(self.pj, self.amap, ["PH002", "CST002"])
        self.assertEqual([r["asset_id"] for r in keep], ["PH002"])
        self.assertEqual([r["asset_id"] for r in no_img], ["CST002"])

    def test_it_says_which_gear_and_why(self):
        """★ 挑掉了必须说得出理由 —— 悄悄少一张是最坏的一类。"""
        _, no_img = R.split_refs(self.pj, self.amap, ["PH002", "CST002"])
        self.assertEqual(no_img[0]["decision"], "logical_only")
        self.assertIn("文字合同", no_img[0]["reason"])

    def test_the_numbering_is_not_shifted(self):
        """★ 编号不重排：正文里那份映射是模型写的，挪号就跟它对不上了。"""
        keep, no_img = R.split_refs(self.pj, self.amap,
                                    ["CST002", "PH002", "CST001"])
        self.assertEqual([(r["image_n"], r["asset_id"]) for r in keep],
                         [(2, "PH002"), (3, "CST001")])
        self.assertEqual(no_img[0]["image_n"], 1)

    def test_assets_that_do_get_an_image_are_untouched(self):
        keep, no_img = R.split_refs(self.pj, self.amap, ["PH002", "CST001"])
        self.assertEqual(len(keep), 2)
        self.assertEqual(no_img, [])
        self.assertTrue(all(r["file_ref"] for r in keep))

    def test_an_unknown_id_stays_in_the_list_with_an_empty_file_ref(self):
        """★ 认不出的和判定不出图的是两回事，别混。

        认不出 → 留着、file_ref 空 → 出图那一层硬停「指不到文件」（该停）。
        判定不出图 → 挑出去 → 只提醒（本来就不该有图）。
        """
        keep, no_img = R.split_refs(self.pj, self.amap, ["PH002", "ZZZ999"])
        self.assertEqual([r["asset_id"] for r in keep], ["PH002", "ZZZ999"])
        self.assertEqual(keep[1]["file_ref"], "")
        self.assertEqual(no_img, [])

    def test_every_no_image_gear_counts(self):
        for gear in sorted(R.NO_IMAGE_DECISIONS):
            amap = dict(self.amap)
            amap["X001"] = {"asset_id": "X001", "decision": gear}
            keep, no_img = R.split_refs(self.pj, amap, ["X001"])
            self.assertEqual(keep, [], gear)
            self.assertEqual(len(no_img), 1, gear)

    def test_blank_ids_are_dropped_quietly(self):
        keep, no_img = R.split_refs(self.pj, self.amap, ["", None, "PH002"])
        self.assertEqual([r["asset_id"] for r in keep], ["PH002"])


PROMPT = """【参考图身份映射】
Image 1 = PH002 主角定妆
  是谁/是什么 + 画面可见内容：成年男性正面半身
  有权控制：五官
  无权控制：姿态
Image 2 = CST002 日常衬衫
  是谁/是什么 + 画面可见内容：浅蓝衬衫
  有权控制：服饰
  无权控制：脸

正文……
"""


class MapCheckTests(unittest.TestCase):
    """正文里有 Image 2、附件里没有第 2 张 —— 这是对的，不能判成错。"""

    def setUp(self):
        self.want = [{"image_n": 1, "asset_id": "PH002"}]
        self.no_img = [{"image_n": 2, "asset_id": "CST002",
                        "decision": "logical_only",
                        "reason": "使用文字合同即可。"}]

    def test_the_skipped_number_is_not_counted_as_extra(self):
        """★ 不改这里就是把一个报错换成另一个报错（「多写了 Image 2」）。"""
        bad, warn = P.check_image_map(PROMPT, self.want, "LK002", "r.txt",
                                     self.no_img)
        self.assertEqual(bad, "")
        self.assertIn("CST002", warn)
        self.assertIn("logical_only", warn)

    def test_a_genuinely_extra_number_still_hard_stops(self):
        """★ 别拦漏：没被判定不出图的多余编号照旧停。"""
        bad, _ = P.check_image_map(PROMPT, self.want, "LK002", "r.txt", [])
        self.assertIn("多写了 Image 2", bad)

    def test_a_real_mismatch_still_hard_stops(self):
        bad, _ = P.check_image_map(PROMPT, [{"image_n": 1, "asset_id": "PH009"}],
                                   "LK002", "r.txt", self.no_img)
        self.assertIn("Image 1", bad)

    def test_all_refs_skipped_still_says_so(self):
        """★ 一张都不传时也要说 —— 沉默才是问题。"""
        bad, warn = P.check_image_map(PROMPT, [], "LK002", "r.txt", self.no_img)
        self.assertEqual(bad, "")
        self.assertIn("CST002", warn)

    def test_nothing_skipped_means_no_note(self):
        bad, warn = P.check_image_map(
            PROMPT, [{"image_n": 1, "asset_id": "PH002"},
                     {"image_n": 2, "asset_id": "CST002"}], "LK002", "r.txt")
        self.assertEqual((bad, warn), ("", ""))

    def test_the_six_field_check_ignores_the_skipped_slot(self):
        """挑出去的那一张不参与六字段校验 —— 它不在上传列表里。"""
        bad, _ = P.check_identity_map(PROMPT, self.want, "LK002", "r.txt")
        self.assertEqual(bad, "")


class MissingPromptTests(unittest.TestCase):
    """★ 第二种成因：判它要出，但没人给它写生产提示词。

    用户实遇（通用级 EP01-SEG06）：参考图那栏「参4/5」，第 4 张 S003 标着「缺」。
    S003 在资产表里、档位也不是不出图 —— 它只是没有生产提示词，
    于是不会进出图任务，等于永远没有图。

    这一种**不是设计如此**，话必须和上一种分开说，不然人会以为「本来就不出图」
    而不去补。以前只有一条提醒说「引用到它们的故事板会因为缺参考图停下」——
    说对了，然后就真的停在那儿。
    """

    def setUp(self):
        self.pj = _Reg()
        self.amap = _amap()

    def test_an_asset_without_a_prompt_is_not_a_reference_image(self):
        keep, no_img = R.split_refs(self.pj, self.amap, ["PH002", "CST001"],
                                    prompts={"PH002": {}})
        self.assertEqual([r["asset_id"] for r in keep], ["PH002"])
        self.assertEqual(no_img[0]["asset_id"], "CST001")
        self.assertIn("缺生产提示词", no_img[0]["decision"])

    def test_it_says_this_one_is_a_hole_not_a_design(self):
        """★ 两种成因的措辞必须不同 —— 一个照常，一个要去补。"""
        _, no_img = R.split_refs(self.pj, self.amap, ["CST001"], prompts={})
        self.assertIn("窟窿", no_img[0]["reason"])
        self.assertIn("n4b", no_img[0]["reason"])
        _, byd = R.split_refs(self.pj, self.amap, ["CST002"], prompts={})
        self.assertEqual(byd[0]["decision"], "logical_only")
        self.assertNotIn("窟窿", byd[0]["reason"])

    def test_no_prompt_map_means_no_such_judgement(self):
        """不传 prompts 时不许凭空判 —— 有的调用点本来就没有这张表。"""
        keep, no_img = R.split_refs(self.pj, self.amap, ["CST001"])
        self.assertEqual([r["asset_id"] for r in keep], ["CST001"])
        self.assertEqual(no_img, [])


class ResolveTests(unittest.TestCase):
    """场景状态图不在资产表里，但确实会出 —— 不许把它当成「认不出」。"""

    def test_a_scstate_reference_is_kept(self):
        """★ 漏了这一条，故事板的参考图会被整批判成缺失。"""
        keep, no_img = R.split_refs(
            _Reg(), _amap(), [{"image_n": 1, "asset_id": "SCST001"}],
            prompts={}, resolve={"SCST001": "04_场景状态/SCST001.png"}.get)
        self.assertEqual(keep[0]["file_ref"], "04_场景状态/SCST001.png")
        self.assertEqual(no_img, [])

    def test_dict_rows_keep_their_own_image_n(self):
        """故事板那边的行自带 image_n，不许按位置重编。"""
        keep, _ = R.split_refs(_Reg(), _amap(),
                               [{"image_n": 3, "asset_id": "PH002"}])
        self.assertEqual(keep[0]["image_n"], 3)


class WiringTests(unittest.TestCase):
    """接上了才算修好 —— 算出来不传下去等于没做。"""

    def test_all_four_v34_sites_go_through_the_filter(self):
        """★ 上一版只堵了资产和场景状态图两处，

        而用户实遇的那条是**故事板**。四处都要走同一个筛子。
        """
        import inspect
        src = inspect.getsource(R.build_tasks)
        self.assertEqual(src.count("split_refs("), 4,
                         "资产 / 场景状态图 / 故事板 / 视频，四处都要走 split_refs")
        self.assertEqual(src.count('"no_image_refs"'), 4)

    def test_the_v61_side_too(self):
        """★ 通用级是另一套装配代码 —— 只改电影级等于只修了一半。"""
        import inspect

        from core import stages as ST
        src = inspect.getsource(ST._build_tasks)
        self.assertEqual(src.count("_split_refs("), 2,
                         "通用级的资产和故事板两处都要走筛子")
        self.assertEqual(src.count('"no_image_refs"'), 2)

    def test_the_v61_gears_match_that_system(self):
        """通用级只有 must/conditional/skip 三档，别照抄电影级的八档。"""
        import inspect

        from core import stages as ST
        src = inspect.getsource(ST._no_image_reason)
        self.assertIn('"skip"', src)
        self.assertIn("aprompts", src)

    def test_the_worker_passes_them_to_the_check(self):
        import inspect
        src = inspect.getsource(P.make_image_worker)
        self.assertIn('task.get("no_image_refs")', src)


if __name__ == "__main__":
    unittest.main()
